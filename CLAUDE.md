# CLAUDE.md

> AI 助手必读。仅含执行时必须遵守的硬规则；设计背景与详细原因见 [`docs/architecture-decisions.md`](docs/architecture-decisions.md)。

---

## 项目定位

三层 Multi-Agent 自主任务系统框架：自然语言 → 任务计划 → 工具执行 → 结果融合。

---

## 架构（三层 Agent）

```
用户 → Supervisor（主循环，ReAct）
         ├── call_planner → Planner（意图层 Plan JSON）
         └── call_executor → Executor（自主选工具执行）
```

| Agent | 模型 | 职责 |
|-------|------|------|
| Supervisor | 见config\agent_models.toml | 理解意图、调度 Planner/Executor、管理重规划、合成答案 |
| Planner | 见config\agent_models.toml | 将任务转化为意图层 Plan JSON（不含工具名） |
| Executor | 见config\agent_models.toml | 按 Plan 自主选工具执行，返回 `ExecutorResult` |

入口：`langgraph.json` → `src/supervisor_agent/graph.py:graph`

---

## I/O 契约

### call_planner 参数
- `task_core`：任务意图（初次）或修改方向（重规划）
- `plan_id`：仅重规划时传入，内部从 `session.plan_json` 获取带状态的 plan

### call_executor 参数
- Mode 2：`{ "task_description": "..." }`
- Mode 3：`{ "plan_id": "..." }`

### ExecutorResult 返回值
```python
@dataclass
class ExecutorResult:
    status: Literal["completed", "failed", "paused"]
    updated_plan_json: str   # Mode 2 下允许为空
    summary: str             # 给 Supervisor 读的自然语言摘要
    snapshot_json: str = ""  # paused（如 Reflection 检查点）时的结构化快照
```

### Plan JSON 结构
```json
{
  "plan_id": "plan_v20260331", "version": 2, "goal": "...",
  "steps": [{
    "step_id": "step_1", "intent": "...", "expected_output": "...",
    "status": "pending|completed|failed|skipped",
    "result_summary": null, "failure_reason": null
  }]
}
```
（`step_id` 经 `call_planner` 归一化后为字符串。）

---

## Supervisor 三种模式（决策 8）

| 模式 | 适用场景 | 行为 |
|------|---------|------|
| 1 Direct Response | 简单事实、无需工具 | 直接回答 |
| 2 Tool-use ReAct | 少量工具、短流程 | 调 Executor（传 `task_description`） |
| 3 Plan → Execute | 多步骤、有依赖 | 调 Planner → 调 Executor（传 `plan_id`） |

---

## 失败处理状态机（决策 4 / 5 / 5.1）

| status | updated_plan_json | replan_count | Supervisor 动作 |
|--------|-------------------|--------------|-----------------|
| completed | — | — | 基于 `summary` 合成最终答案，结束 |
| paused | 视检查点输出 | 不变 | 读 checkpoint 摘要；续跑或重规划由 Supervisor 决定 |
| failed | 非空 | < MAX_REPLAN | `summary` → `task_core`，调 `call_planner`（传 plan_id）→ `call_executor` |
| failed | 为空 | < MAX_REPLAN | 基于 `summary` 反馈；可升级为 Mode 3 |
| failed | 任意 | ≥ MAX_REPLAN | 向用户返回失败分析，终止 |

**双重保障**：正常失败由 Executor 填写 `status/failure_reason`；异常崩溃由 `_mark_plan_steps_failed()` 兜底。  
**Mode 3 下 `updated_plan_json` 永不为空**；Mode 2 下允许为空。  
**Mode 2→3 切换**：仅当 `status=failed` 且 `summary` 表明需要计划层重构时，由 Supervisor 决定升级。

---

## Session 同步（决策 6）

- `call_planner` 后：新 `plan_json` 写入 `PlannerSession`
- `call_executor` 后：始终更新 `last_executor_*`；`updated_plan_json` 非空则用它刷新 `plan_json`，**为空则保留**上一份 `plan_json`
- 有非空回填时，`plan_json` 为当前最新执行快照（含进度）
- `status=completed` 时 Supervisor LLM 默认仅收 `summary`，不收完整 plan；可通过 `get_executor_full_output` 按需查阅步骤级详情

---

## 硬约束

- **意图层 Plan**（决策 3）：Planner 不知道工具名，只描述 intent + expected_output
- **Executor 遇阻即停**（决策 4）：不内部重规划，重规划权只在 Supervisor
- **Executor 工作区边界**：内置副作用工具仅在 `AGENT_WORKSPACE_DIR`（默认 `workspace/agent`）内执行/写入
- **单线程**（决策 11）：Supervisor 每次只调用一个 Executor
- **Planner 只读**（决策 12）：Planner 仅可用只读工具/MCP，不可调用副作用工具
- **Planner 会话复用**（决策 9）：同一 `plan_id` 复用同一 Planner 对话线程
- **Observation 治理**（V2-a）：所有工具返回进入消息历史前走统一规范化（截断/外置）
- **Reflection**（决策 10）：`REFLECTION_INTERVAL=0` 默认关闭；配置为正整数启用
- **只读 MCP 可共享**：Planner/Executor 可按配置启用 `enable_deepwiki` / `enable_filesystem_mcp`
- **MCP 生效条件**：需在 `.env` 显式开启对应开关（如 `ENABLE_DEEPWIKI=true`）
- **分 Agent LLM 参数**：支持 `SUPERVISOR_*` / `PLANNER_*` / `EXECUTOR_*`（`TEMPERATURE`、`TOP_P`、`MAX_TOKENS`、`SEED`）独立配置；未设置时沿用模型默认
- **Thinking（推理）**：
  - `ENABLE_IMPLICIT_THINKING`：是否向兼容接口请求 `enable_thinking`（默认 `true`；名称沿用，与「是否把思考写进对外 `content`」无关）。
  - `SUPERVISOR_THINKING_VISIBILITY`：`visible` | `implicit`（默认 **`implicit`**）。仅 **Supervisor** 在 `call_model` 中可将推理拼入用户侧 `content`（`[思考过程]` / `[最终回答]`）；**Planner / Executor 永不拼接**，以免破坏 Plan JSON 与 Executor 结构化输出的解析。
  - 兼容：未设置 `SUPERVISOR_THINKING_VISIBILITY` 时仍读取弃用名 `THINKING_VISIBILITY`。

---

## 模块速查表

| 文件 | 职责 |
|---|---|
| `src/supervisor_agent/graph.py` | 主循环图：`call_model` + `dynamic_tools_node` + 路由 |
| `src/supervisor_agent/state.py` | `State`、`AgentSession`（含 `last_executor_status/error/replan_count`） |
| `src/supervisor_agent/prompts.py` | `SUPERVISOR_SYSTEM_PROMPT` |
| `src/supervisor_agent/tools.py` | `call_planner`、`call_executor`、`get_executor_full_output`、`_mark_plan_steps_failed` |
| `src/planner_agent/graph.py` | Planner ReAct 图 + 只读 MCP，`run_planner()` |
| `src/planner_agent/prompts.py` | `PLANNER_SYSTEM_PROMPT`（含 Plan JSON 格式要求） |
| `src/planner_agent/tools.py` | 规划辅助工具 |
| `src/executor_agent/graph.py` | Executor StateGraph + Observation 规范化 + Reflection，`run_executor()` |
| `src/executor_agent/prompts.py` | `EXECUTOR_SYSTEM_PROMPT` |
| `src/executor_agent/tools.py` | Executor 工具（`write_file`、`run_local_command` 等） |
| `src/common/context.py` | `Context` dataclass，运行时配置 |
| `src/common/observation.py` | Observation 规范化（截断/外置/文本化） |
| `src/common/utils.py` | `load_chat_model("provider:model")` |

---

## 运行与环境（按需查阅）

本文件仅保留执行时硬规则，不重复维护环境与命令清单。  
如需查看安装、环境变量、启动与测试命令，请阅读：

- [`tests/TESTING.md`](tests/TESTING.md)
- [`.env`](.env)
