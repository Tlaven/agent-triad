# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

> AI 助手必读：**执行硬规则**；背景与论证见 [`docs/architecture-decisions.md`](docs/architecture-decisions.md)。产品与里程碑见 [`docs/product-roadmap.md`](docs/product-roadmap.md)；`docs/` 导航见 [`docs/README.md`](docs/README.md)。

---

## 定位与架构

三层框架：自然语言 → 任务计划 → 工具执行 → 结果融合。

```
用户 → Supervisor（ReAct 主循环）
         ├── call_planner → Planner（意图层 Plan JSON，不含工具名）
         └── call_executor → Executor（自主选工具）
```

入口：`langgraph.json` → `src/supervisor_agent/graph.py:graph`。各 Agent 默认模型见 `config/agent_models.toml`；三 Agent 分模型策略：

| Agent | 默认模型 | 定位 |
|-------|---------|------|
| Supervisor | `openai:kimi-k2.6` | 工具调度 + 决策路由 |
| Planner | `anthropic:qwen3.7-max` | 最强推理做意图分解 |
| Executor | `openai:deepseek-v4-flash` | 快速工具执行 |

Supervisor 和 Executor 走 OpenAI 兼容接口（`OPENAI_BASE_URL`）；Planner 走 Anthropic 兼容接口（`ANTHROPIC_BASE_URL`）。不同 provider 的模型通过 `load_chat_model("provider:model")` 统一加载。

长期目标之一：让 Agent 能够管理自己的上下文。V4 知识树是该目标的核心承载，负责记忆沉淀、检索、结构演化与后续上下文治理；当前权威设计见 `docs/v4-kt-core-design.md`。

### V3 进程分离架构

每个 `call_executor` 派发时 spawn 独立子进程（`python -m src.executor_agent`），子进程启动 FastAPI + uvicorn，动态分配端口。

```
Supervisor 进程
  ├── V3LifecycleManager（懒加载单例）
  │     ├── ExecutorProcessManager  — spawn/stop per-task 子进程
  │     ├── Mailbox                 — 线程安全结果缓存（plan_id → MailboxItem）
  │     ├── MailboxHTTPServer       — 后台线程，Executor POST /inbox 推结果
  │     └── ExecutorPoller          — 后台 asyncio Task，轮询 Executor /result
  └── call_executor → pm.start_for_task(plan_id) → POST /execute
                          ↓
                    Executor 子进程（FastAPI）
                      ├── POST /execute   — 接收任务
                      ├── GET  /result    — 返回 ExecutorResult
                      ├── POST /stop      — 软中断
                      └── _push_result_to_mailbox → POST {mailbox_url}/inbox
```

通信双路径：**Push**（Executor 完成后推 Mailbox）+ **Pull**（Poller 定时 GET /result 兜底）。Supervisor 的 `_wait_for_executor_result` 阻塞读取 Mailbox。

---

## I/O 契约

**`call_planner`**：`task_core`（意图或修改方向）；`plan_id` 仅重规划时传，状态来自 `session.plan_json`。返回含 `[PLANNER_REASONING]...[/PLANNER_REASONING]` 标记的推理分析 + 规范化 Plan JSON。`dynamic_tools_node` 拆分后分别存入 `planner_session.planner_reasoning` 和 `planner_session.plan_json`。

**`call_executor`**：Mode 2 仅 `task_description`；Mode 3 仅 `plan_id`。默认 `wait_for_result=True`，自动阻塞等待并返回执行结果（`[EXECUTOR_RESULT]`），省去额外调用 `manage_executor`。设 `wait_for_result=False` 时为异步派发，需后续调用 `manage_executor(action="get_result", plan_id=...)` 获取结果。

**`plan_id` 与 Executor（V3）**：Mode 3 以 `plan_id` 为键关联子进程，**同 id 且执行未结束时复用**同一子进程。Mode 2 不显式传 `plan_id` 时内部生成新 id 并**新起**子进程。Supervisor 的 `state.messages` 仍整条累积，与是否多子进程不矛盾。

**再次进入同一 `plan_id`**：Planner **复用**该 id 下规划对话线程。Executor **不复用**上一轮内部 ReAct 消息链；续跑靠最新 **`plan_json` / `updated_plan_json`** 快照。Supervisor 消息历史始终累积。

**`ExecutorResult`**：

```python
@dataclass
class ExecutorResult:
    status: Literal["completed", "failed", "paused"]
    updated_plan_json: str   # Mode 2 可空
    summary: str
    snapshot_json: str = ""  # paused（如 Reflection）时结构化快照
```

**Plan JSON**：顶层 `plan_id`、`version`、`goal`、`steps[]`；每步 `step_id`（`call_planner` 归一化为字符串）、`intent`、`expected_output`、`status`（pending|completed|failed|skipped）、`result_summary`、`failure_reason`、`parallel_group`（可选，同值步骤可并行执行，`null` 表示顺序执行）。

**`snapshot_json`（paused）**：JSON，含如 `trigger_type`、`current_step`、`confidence_score`、`reflection_analysis`、`suggestion`（continue|replan|abort）、`progress_summary` 等；解析见 `executor_agent/graph.py`。

---

## Supervisor 三种模式（决策 8）

| 模式 | 场景 | 行为 |
|------|------|------|
| 1 Direct Response | 无需工具 | 直接答 |
| 2 Tool-use ReAct | 短流程 | Executor + `task_description` |
| 3 Plan → Execute | 多步依赖 | Planner → Executor + `plan_id` |

---

## 失败处理（决策 4 / 5 / 5.1 / 33）

| status | updated_plan_json | replan_count | Supervisor |
|--------|-------------------|--------------|------------|
| completed | — | — | 用 `summary` 收束，结束 |
| paused | 视检查点 | 不变 | 读快照；续跑或重规划 |
| failed | 非空 | < MAX_REPLAN | `summary`→`task_core`，`call_planner(plan_id)`→`call_executor` |
| failed | 空 | < MAX_REPLAN | 依 `summary`；可升 Mode 3 |
| failed | 任意 | ≥ MAX_REPLAN | 失败分析，终止 |

正常失败由 Executor 写 `status`/`failure_reason`；异常由 `_mark_plan_steps_failed()` 兜底。Mode 3 下 `updated_plan_json` 非空；Mode 2 可空。Mode 2→3：仅 `failed` 且 `summary` 表明需计划层重构时由 Supervisor 升级。**MAX_REPLAN 触发后（决策 33）早返回同时重置 `replan_count=0` + `last_executor_status=None`，避免 thread bricked**。

---

## Session 同步（决策 6）

- `call_planner` 后：`plan_json` 写入 Planner 会话。
- `call_executor` 后：更新 `last_executor_*`；`updated_plan_json` 非空则刷新 `plan_json`，**空则保留**原 `plan_json`。
- `completed` 或 `failed` 后，Entry A 自动从 Executor 结果提取知识：`_try_auto_ingest_executor_result()`（`asyncio.to_thread` 包裹，避免 LangGraph dev server 的 BlockingError）调用 `extract_knowledge_from_executor_result()`（提取 summary + 步骤 result_summary + failure_reason）+ `extract_experience_from_executor_result()`（提取结构化经验节点，`node_type=experience`），过滤通用模板后摄入知识树。**摄入时写入 `metadata.executor_status`（决策 32），检索 inject 时 `failed` 节点带 `[失败教训]` 前缀**。全程 try/except 包裹，KT 失败不影响主图。
- `completed` 时 Supervisor 默认只收 `summary`；步骤级细节用 `manage_executor(action="get_result", plan_id=..., detail="full")`（任务已结束且 `plan_id` 与会话计划一致时读缓存；异步仍在跑时与 `overview` 相同先等待，再由图节点附带详情）。

---

## 硬约束

- **意图层 Plan**（决策 3）：Planner 不知工具名，只写 `intent` / `expected_output`。
- **Executor 遇阻即停**（决策 4）：不重规划；重规划仅 Supervisor。
- **工作区**：副作用工具仅在 `AGENT_WORKSPACE_DIR`（默认 `workspace`）内。
- **单 Executor 调用**（决策 11）：Supervisor 每次只派一个 Executor。
- **`plan_id`↔子进程**（V3）：见上 I/O；Mode 2 每次新 id 新进程。
- **Planner 只读**（决策 12）：只读工具/MCP；无副作用工具。可用工具：`read_workspace_text_file`、`list_workspace_entries`、`search_files`（glob 搜索）、`grep_content`（正则搜索）、`read_file_structure`（目录树）+ 只读 MCP。
- **Planner 会话**（决策 9）：同 `plan_id` 复用规划对话线程。
- **Observation**（V2-a）：工具返回进历史前统一截断/外置。
- **Reflection**（决策 10）：`REFLECTION_INTERVAL=0` 默认关；正整数启用。
- **MCP**：`enable_deepwiki` / `enable_filesystem_mcp` 等须在 `.env` 显式开启方生效。
- **消息历史限制**：`SUPERVISOR_MAX_HISTORY_MESSAGES`（默认 100）；`call_model` 构造 LLM 输入时截断，保持工具调用序列完整性。0 = 不限制。截断算法：保留末尾 N 条消息，扫描窗口内所有 AI 消息的 `tool_calls` 声明，过滤掉无父 AI 引用的孤立 `ToolMessage`（含散布在中间的孤立消息）。
- **LLM 调用超时保护**（决策 30）：三个 Agent 各自独立——`supervisor_call_model_timeout`/`planner_call_model_timeout`（默认 120s）、`executor_call_model_timeout`（默认 180s）。Supervisor 超时返回友好提示；Planner 超时抛 RuntimeError；Executor 超时终止进程。0 禁用。`invoke_chat_model` 自动记录每次调用耗时。
- **Mode 纪律**（决策 31，已撤销 2026-07-03）：探测触发 0 次，谓词为死代码，已删除 strip 逻辑与 `_looks_like_final_answer`。mode 路由脱节问题由 N4 修复（见 `docs/n4-diagnosis-result.md` / 实施计划 Task 1）。
- **子进程生命周期**：atexit + SIGTERM/SIGINT 信号处理确保 executor 子进程随主进程退出；`sync_terminate` 使用 terminate → kill 升级策略。
- **Mailbox 驱逐**：`_MAX_BOXES=80` 触发驱逐，保留 `_RETAIN_BOXES=50`；优先驱逐 `has_completion=True` 的 box，必要时驱逐未完成 box 防止无限堆积。
- **ExecutorPoller 注册上限**：`_MAX_ACTIVE_TASKS=100`；`register()` 时自动驱逐 `registered_at` 最旧的条目，防止长时间运行后内存泄漏。

> **环境变量完整参考**（30+ 变量：Provider 接口、分 Agent LLM 参数、超时、KT 阈值、MCP 开关等）见 [`docs/environment-variables.md`](docs/environment-variables.md)。**常见错误排查**见 [`docs/troubleshooting.md`](docs/troubleshooting.md)。

---

## 模块速查

| 路径 | 职责 |
|------|------|
| `src/supervisor_agent/graph.py` | 主循环、`call_model`、`dynamic_tools_node` |
| `src/supervisor_agent/state.py` | `State`、`PlannerSession`、`ActiveExecutorTask` |
| `src/supervisor_agent/tools.py` | `call_planner`、`call_executor`（`wait_for_result`）、`manage_executor`（`action`：stop/get_result/check_progress/list_tasks）、`_mark_plan_steps_failed` |
| `src/supervisor_agent/v3_lifecycle.py` | V3 基础设施单例（Mailbox + ProcessManager + Poller + 信号处理） |
| `src/planner_agent/graph.py` | Planner 图、`PlannerOutput`、`run_planner()`（返回 reasoning + plan_json） |
| `src/executor_agent/graph.py` | Executor 图、Observation、Reflection、`run_executor()` |
| `src/executor_agent/tools.py` | Executor 工具（8 个）：`write_file`、`edit_file`、`run_local_command` + 5 个共享只读工具 |
| `src/executor_agent/server.py` | Executor FastAPI 子进程服务器（/execute、/result、/stop） |
| `src/executor_agent/__main__.py` | 子进程入口：动态端口 + uvicorn 启动 |
| `src/executor_agent/interrupt.py` | 软中断（stop event、`run_with_interrupt_check`） |
| `src/common/context.py` | `Context`（所有运行时配置） |
| `src/common/process_manager.py` | 每任务子进程生命周期（spawn/port-discovery/health/stop） |
| `src/common/mailbox.py` | 线程安全 per-plan 结果缓存。驱逐策略：`_MAX_BOXES=80` 触发，保留 `_RETAIN_BOXES=50`，优先驱逐已完成 box，必要时驱逐未完成 box |
| `src/common/mailbox_server.py` | HTTP 后台线程接收 Executor 推送 |
| `src/common/polling.py` | `ExecutorPoller`：统一后台轮询 + `force_poll_once`。`_MAX_ACTIVE_TASKS=100` 注册上限，超出时按 `registered_at` 驱逐最旧条目 |
| `src/common/executor_protocol.py` | 跨进程数据结构（`ExecuteRequest`、`ExecuteStatus` 等） |
| `src/common/observation.py` | Observation 规范化 |
| `src/common/capabilities.py` | Executor 能力描述（Planner/Executor 共享） |
| `src/common/utils.py` | `load_chat_model("provider:model")` |

| `src/common/tools.py` | 共享只读工作区工具（`read_workspace_text_file`、`list_workspace_entries`、`search_files`、`grep_content`、`read_file_structure`） |

| `src/common/knowledge_tree/` | V4 涌现式知识树：两层存储（文件系统 + 向量索引）+ Overlay JSON 跨目录关联。文件系统目录层级 = 树结构，向量通过目录锚点聚簇。元规则通过 alias embedding（`alias:{node_id}:{i}`）扩展 RAG 检索可达性，RRF 4 路径融合（content + title + alias + anchor）。**向量持久化**：`.vector_index.json` + manifest 新鲜度检测，重启后 O(1) 加载；.md 文件变更自动回退重建。**元规则治理**（决策 28）：`MAX_META_RULES=15` 硬上限 + 注入时别名互斥消解（同优先级全抑制）+ `knowledge_tree_delete_meta_rule` 自救工具。详见 `docs/architecture-decisions.md` |

各层 `prompts.py` / `tools.py` 见同包。

---

## 开发命令

包管理器：**`uv`**（所有命令前缀 `uv run`）。`make setup` 安装依赖。

```bash
make dev              # LangGraph 开发服务器（端口 2024）
make dev_ui           # 同上 + 打开 LangGraph Studio UI
make test_unit        # 单元测试
make test_integration # 集成测试
make test_automated   # 单元 + 集成（无真实 LLM）
make test_e2e         # E2E（需 API key，-m live_llm）
make test_coverage    # 覆盖率（阈值 80%）
make lint             # ruff check + mypy --strict src
make format           # ruff format + import 排序
```

运行单个测试：

```bash
uv run pytest tests/unit_tests/supervisor_agent/test_dynamic_tools_node.py::test_name -q
```

## 运行与环境

测试命令入口：[`tests/README.md`](tests/README.md)；环境、代理与分层细节：[`tests/TESTING.md`](tests/TESTING.md)。环境变量示例：[`.env`](.env) / `.env.example`。

### 多模型环境变量

```bash
# Supervisor + Executor（OpenAI 兼容接口）
OPENAI_API_KEY=sk-xxx
OPENAI_BASE_URL=https://opencode.ai/zen/go/v1

# Planner（Anthropic 兼容接口）
ANTHROPIC_API_KEY=sk-xxx        # 同一 Go 密钥
ANTHROPIC_BASE_URL=https://opencode.ai/zen/go
```
