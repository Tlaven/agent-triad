# CLAUDE.md

> AI 助手必读：**执行硬规则**；背景与论证见 [`docs/architecture-decisions.md`](docs/architecture-decisions.md)。产品与里程碑见 [`docs/product-roadmap.md`](docs/product-roadmap.md)；`docs/` 导航见 [`docs/README.md`](docs/README.md)。

---

## 定位与架构

三层框架：自然语言 → 任务计划 → 工具执行 → 结果融合。

```
用户 → Supervisor（ReAct 主循环）
         ├── call_planner → Planner（意图层 Plan JSON，不含工具名）
         └── call_executor → Executor（自主选工具）
```

入口：`langgraph.json` → `src/supervisor_agent/graph.py:graph`。各 Agent 默认模型见 `config/agent_models.toml`。

---

## I/O 契约

**`call_planner`**：`task_core`（意图或修改方向）；`plan_id` 仅重规划时传，状态来自 `session.plan_json`。

**`call_executor`**：Mode 2 仅 `task_description`；Mode 3 仅 `plan_id`。

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

**Plan JSON**：顶层 `plan_id`、`version`、`goal`、`steps[]`；每步 `step_id`（`call_planner` 归一化为字符串）、`intent`、`expected_output`、`status`（pending|completed|failed|skipped）、`result_summary`、`failure_reason`。

**`snapshot_json`（paused）**：JSON，含如 `trigger_type`、`current_step`、`confidence_score`、`reflection_analysis`、`suggestion`（continue|replan|abort）、`progress_summary` 等；解析见 `executor_agent/graph.py`。

---

## Supervisor 三种模式（决策 8）

| 模式 | 场景 | 行为 |
|------|------|------|
| 1 Direct Response | 无需工具 | 直接答 |
| 2 Tool-use ReAct | 短流程 | Executor + `task_description` |
| 3 Plan → Execute | 多步依赖 | Planner → Executor + `plan_id` |

---

## 失败处理（决策 4 / 5 / 5.1）

| status | updated_plan_json | replan_count | Supervisor |
|--------|-------------------|--------------|------------|
| completed | — | — | 用 `summary` 收束，结束 |
| paused | 视检查点 | 不变 | 读快照；续跑或重规划 |
| failed | 非空 | < MAX_REPLAN | `summary`→`task_core`，`call_planner(plan_id)`→`call_executor` |
| failed | 空 | < MAX_REPLAN | 依 `summary`；可升 Mode 3 |
| failed | 任意 | ≥ MAX_REPLAN | 失败分析，终止 |

正常失败由 Executor 写 `status`/`failure_reason`；异常由 `_mark_plan_steps_failed()` 兜底。Mode 3 下 `updated_plan_json` 非空；Mode 2 可空。Mode 2→3：仅 `failed` 且 `summary` 表明需计划层重构时由 Supervisor 升级。

---

## Session 同步（决策 6）

- `call_planner` 后：`plan_json` 写入 Planner 会话。
- `call_executor` 后：更新 `last_executor_*`；`updated_plan_json` 非空则刷新 `plan_json`，**空则保留**原 `plan_json`。
- `completed` 时 Supervisor 默认只收 `summary`；步骤级细节用 `get_executor_full_output`。

---

## 硬约束

- **意图层 Plan**（决策 3）：Planner 不知工具名，只写 `intent` / `expected_output`。
- **Executor 遇阻即停**（决策 4）：不重规划；重规划仅 Supervisor。
- **工作区**：副作用工具仅在 `AGENT_WORKSPACE_DIR`（默认 `workspace`）内。
- **单 Executor 调用**（决策 11）：Supervisor 每次只派一个 Executor。
- **`plan_id`↔子进程**（V3）：见上 I/O；Mode 2 每次新 id 新进程。
- **Planner 只读**（决策 12）：只读工具/MCP；无副作用工具。
- **Planner 会话**（决策 9）：同 `plan_id` 复用规划对话线程。
- **Observation**（V2-a）：工具返回进历史前统一截断/外置。
- **Reflection**（决策 10）：`REFLECTION_INTERVAL=0` 默认关；正整数启用。
- **MCP**：`enable_deepwiki` / `enable_filesystem_mcp` 等须在 `.env` 显式开启方生效。
- **分 Agent LLM 参数**：`SUPERVISOR_*` / `PLANNER_*` / `EXECUTOR_*`（`TEMPERATURE`、`TOP_P`、`MAX_TOKENS`、`SEED`）。
- **Thinking**：`ENABLE_IMPLICIT_THINKING`；仅 Supervisor 可用 `SUPERVISOR_THINKING_VISIBILITY`（`visible`|`implicit`，默认 implicit）把推理拼入对用户 `content`；Planner/Executor **不**拼。未设置时兼容旧名 `THINKING_VISIBILITY`。

---

## 模块速查

| 路径 | 职责 |
|------|------|
| `src/supervisor_agent/graph.py` | 主循环、`call_model`、`dynamic_tools_node` |
| `src/supervisor_agent/state.py` | `State`、`AgentSession` |
| `src/supervisor_agent/tools.py` | `call_planner`、`call_executor`、`get_executor_full_output`、`_mark_plan_steps_failed` |
| `src/planner_agent/graph.py` | Planner 图、`run_planner()` |
| `src/executor_agent/graph.py` | Executor 图、Observation、Reflection、`run_executor()` |
| `src/common/context.py` | `Context` |
| `src/common/observation.py` | Observation 规范化 |
| `src/common/utils.py` | `load_chat_model("provider:model")` |

各层 `prompts.py` / `tools.py` 见同包。

---

## 运行与环境

测试命令入口：[`tests/README.md`](tests/README.md)；环境、代理与分层细节：[`tests/TESTING.md`](tests/TESTING.md)。环境变量示例：[`.env`](.env) / `.env.example`。
