# CLAUDE.md

> 本文件是给 AI 助手读的工程文档，记录项目真实现状、架构设计意图和已确定的决策。  
> **每次对话开始前必须先读此文件。**

---

## 项目定位

一个通用目的的三层 Multi-Agent 自主任务系统框架，支持将自然语言查询自动转化为**任务计划 → 工具执行 → 结果融合**全流程。可作为垂直领域 AI Agent 应用（AutoML、代码生成、研究助手等）的基础底座。

---

## 架构概览（三层 Multi-Agent）

```
用户
 │
 ▼
Supervisor Agent（主循环）          src/supervisor_agent/
  - 框架：自定义 StateGraph（ReAct 模式）
  - 模型：qwen:qwen-flash（可通过 SUPERVISOR_MODEL 配置）
  - 工具：仅 generate_plan 和 execute_plan 两个
  - 职责：理解用户意图，调度 Planner/Executor，管理重规划，合成最终答案
  │
  ├── generate_plan ──▶ Planner Agent    src/planner_agent/
  │                       - 框架：自定义 StateGraph（单节点，单次 LLM 调用）
  │                       - 模型：siliconflow:Pro/deepseek-ai/DeepSeek-V3.2
  │                       - 职责：把用户需求（含历史执行状态）转化为意图层 JSON 计划
  │                       - 特性：PLANNER_SYSTEM_PROMPT 严格放在消息列表最后（HumanMessage）
  │
  └── execute_plan ───▶ Executor Agent   src/executor_agent/
                          - 框架：自定义 StateGraph（ReAct 模式，含 ExecutorState）
                          - 模型：siliconflow:Pro/deepseek-ai/DeepSeek-V3.2
                          - 职责：按意图层 JSON 计划自主选工具执行，完成后返回带步骤状态的 updated_plan
                          - 返回值：ExecutorResult(status, updated_plan_json, summary)
```

### 入口

`langgraph.json` 注册的唯一图：`src/supervisor_agent/graph.py:graph`

---

## 已确定的关键设计决策

### 决策 1：generate_plan / execute_plan 使用 InjectedState（不接受 LLM 传参）

`execute_plan` 工具**不接受 LLM 传入参数**，使用 `InjectedState` 注入 State，自行从 `state.session.plan_json` 取计划。  
`generate_plan` 同样使用 `InjectedState`，从 `state.messages` 取完整历史传给 Planner。

**原因**：避免 LLM 错误传参（幻觉参数、截断 JSON）导致计划丢失，保证计划来源可信。

---

### 决策 2：Plan 是"意图层"，不包含工具名

Planner **不知道 Executor 有哪些工具**，Plan 的每个 step 只描述**意图（intent）和期望产出（expected_output）**，不指定工具名称。Executor 自主根据 intent 选择合适工具。

**Plan step 字段**：

```json
{
  "step_id": "step_1",
  "intent": "意图描述（不含工具名）",
  "expected_output": "完成验收标准",
  "status": "pending | completed | failed | skipped",
  "result_summary": null,
  "failure_reason": null
}
```

**原因**：Planner 与 Executor 工具集完全解耦，更换/新增工具无需修改 Planner 提示词。

---

### 决策 3：Executor 遇阻直接停止，不内部重规划

Executor 遇到无法继续的情况时**直接停止**，把带执行状态的 updated_plan 返回给 Supervisor，**不在 Executor 内部主动重规划**。

**重规划决策权在 Supervisor**：

```
Supervisor 收到 Executor 结果
  ├── status=completed → 合成最终答案，结束
  └── status=failed
        ├── replan_count < MAX_REPLAN → 调 generate_plan（Planner 看到带状态的 plan）→ 再 execute_plan
        └── 多次失败无法推进 → 告知用户，附上失败分析
```

**原因**：避免 Executor 自行决策范围扩大（越权），保证系统行为可预测、可审计。

---

### 决策 4：ExecutorResult 结构化返回值

`run_executor()` 不返回裸 `AIMessage`，改为返回 `ExecutorResult`：

```python
@dataclass
class ExecutorResult:
    status: Literal["completed", "failed"]
    updated_plan_json: str   # 带步骤执行状态的完整 plan JSON
    summary: str             # 给 Supervisor LLM 读的自然语言摘要
```

`execute_plan` 工具把 `updated_plan_json` 嵌入返回文本（`[EXECUTOR_RESULT] {...}`），`dynamic_tools_node` 解析后写回 `session.plan_json`。

**原因**：结构化返回使 Supervisor 能可靠解析执行状态，而不是从自然语言中猜测是否成功。

---

### 决策 5：失败处理双重保障

- **正常失败**（Executor LLM 主动停止）：`updated_plan_json` 由 Executor 自行填写各步骤 `status/failure_reason`
- **异常崩溃**（Python Exception）：`execute_plan` 捕获所有异常，调用 `_mark_plan_steps_failed()` 把所有 `pending` 步骤标记为 `failed` 并写入 `failure_reason`

保证：`updated_plan_json` **永不为空**，Supervisor 始终有完整的执行状态可读。

---

### 决策 6：dynamic_tools_node 双向同步 session（plan 始终最新）

- `generate_plan` 执行后：将新 `plan_json` 写入 `session`
- `execute_plan` 执行后：将 `updated_plan_json`（带执行状态）写回 `session`

`session.plan_json` 始终是**最新版本的 plan**（含执行进度）。

`dynamic_tools_node` 同时提取 `status` 和 `error_detail`，写入 `session.last_executor_status / last_executor_error`，供 Supervisor 决策用。

---

### 决策 7：重规划时传入带执行状态的 Plan

重规划时，`generate_plan` 工具会把 `session.plan_json`（已含执行状态）作为 `HumanMessage` 拼入消息末尾传给 Planner，让 Planner 在修订时能看到：
- 哪些步骤已完成（跳过重复执行）
- 哪步失败及原因（有针对性地修订）

**原因**：避免 Planner 在重规划时"失忆"，重复生成已完成步骤造成浪费。

---

### 决策 8：Planner 提示词结构与消息过滤

Planner 调用时的消息处理逻辑：
1. 过滤掉所有带 `tool_calls` 的 `AIMessage`（对 Planner 无意义，防止误解）
2. 若消息列表未包含 `SYSTEM_PROMPT`，则插入 `SystemMessage(SYSTEM_PROMPT)` 到开头
3. 追加 `HumanMessage(PLANNER_SYSTEM_PROMPT)` 到最后

> **注意**：`PLANNER_SYSTEM_PROMPT` 必须用 `HumanMessage` 而不是 `SystemMessage`。  
> DeepSeek / SiliconFlow API 要求 system 消息只能出现在列表第一条，放末尾会返回 400 错误。

---

### 决策 9：Executor Reflection 步骤计数（V2 阶段引入）

Executor ReAct 循环中内置步骤计数器，触发 Reflection 的条件：
- 已执行步骤数达到 `REFLECTION_INTERVAL`（默认 3）的倍数
- LLM 自评置信度低于 `CONFIDENCE_THRESHOLD`（默认 0.6）

Reflection 输出：当前路径是否偏离目标、建议调整方向。

偏差大或到达里程碑时，Executor **主动停止**并打包 Snapshot 上报给 Supervisor，而不是盲目继续执行。

> V1 阶段不实现此决策，Executor 直线执行到完成或失败。

---

### 决策 10：单线程执行（V1 明确约束）

V1 阶段明确为**单线程**，Supervisor 每次只调用一个 Executor。

V3 阶段再引入 fan-out 并行：Supervisor 将 Plan 拆分为多个子 Plan，并行分发给多个 Executor 实例，最后融合所有 CompletionReport。

**原因**：并行引入额外的状态同步、冲突解决复杂度。V1 先验证基础闭环，稳定后再扩展。

---

## 模块速查表

| 文件 | 职责 |
|---|---|
| `src/supervisor_agent/graph.py` | 主循环图定义，`call_model` + `dynamic_tools_node` + 路由逻辑 |
| `src/supervisor_agent/state.py` | `State`、`InputState`、`AgentSession`（含 `last_executor_status/last_executor_error/replan_count`）、`ExecutorRef` |
| `src/supervisor_agent/tools.py` | `generate_plan`、`execute_plan`、`_mark_plan_steps_failed` |
| `src/planner_agent/graph.py` | Planner 图，`call_planner` 节点，`run_planner()` 对外接口（含 JSON 提取） |
| `src/planner_agent/prompts.py` | `PLANNER_SYSTEM_PROMPT`，含意图层 Plan JSON 格式要求（含步骤状态字段）|
| `src/planner_agent/tools.py` | 规划辅助工具（V1 定义但不绑定到 graph，V2 按需绑定） |
| `src/executor_agent/graph.py` | Executor StateGraph，`ExecutorState`、`ExecutorResult`、`call_executor` + `tools_node` + `route_executor_output`、`run_executor()` 对外接口 |
| `src/executor_agent/prompts.py` | `EXECUTOR_SYSTEM_PROMPT`，按 intent 自主选工具，遇阻停止，输出 updated_plan |
| `src/executor_agent/tools.py` | Executor 工具集合（`write_file`、`run_local_command` 等） |
| `src/common/context.py` | 运行时配置，`Context` dataclass，支持环境变量覆盖 |
| `src/common/prompts.py` | `SYSTEM_PROMPT`，Supervisor 全局系统提示 |
| `src/common/utils.py` | `load_chat_model("provider:model")` 统一入口 |
| `src/common/basemodel.py` | `AgentBaseModel` Pydantic 基类 |
| `src/common/models/qwen.py` | Qwen/QwQ/QvQ 模型，支持国内/国际端点 |
| `src/common/models/siliconflow.py` | SiliconFlow 模型，支持国内/国际端点 |

---

## 当前实现状态（V1 进度）

> 新项目初始化阶段，以下为待实现清单

### 已设计 / 待实现

- [ ] `src/common/` 模块（basemodel / context / utils / prompts）
- [ ] `src/supervisor_agent/state.py`（State / AgentSession 数据结构）
- [ ] `src/supervisor_agent/tools.py`（generate_plan / execute_plan + InjectedState）
- [ ] `src/supervisor_agent/graph.py`（主循环 + dynamic_tools_node）
- [ ] `src/planner_agent/graph.py`（Planner 图 + run_planner）
- [ ] `src/planner_agent/prompts.py`（PLANNER_SYSTEM_PROMPT）
- [ ] `src/executor_agent/graph.py`（Executor ReAct 图 + run_executor）
- [ ] `src/executor_agent/prompts.py`（EXECUTOR_SYSTEM_PROMPT）
- [ ] `src/executor_agent/tools.py`（write_file + run_local_command）
- [ ] 基础单元测试（JSON 提取 / 失败标记 / State 解析）

### V2 待实现（勿提前实现）

- [ ] Executor Reflection 节点（步骤计数器 + 置信度自评）
- [ ] Snapshot 数据结构 + 上报通道
- [ ] Supervisor 干预分级逻辑

---

## 环境配置

```bash
# 必须
SILICONFLOW_API_KEY=sk-...      # Planner / Executor 使用 DeepSeek-V3.2
DASHSCOPE_API_KEY=sk-...        # Supervisor 使用 Qwen

# 可选
REGION=prc                      # prc/cn 或 international/en（默认 prc）
SUPERVISOR_MODEL=qwen:qwen-flash
PLANNER_MODEL=siliconflow:Pro/deepseek-ai/DeepSeek-V3.2
EXECUTOR_MODEL=siliconflow:Pro/deepseek-ai/DeepSeek-V3.2
MAX_REPLAN=3                    # Supervisor 最大重规划次数
MAX_EXECUTOR_ITERATIONS=20      # Executor ReAct 最大轮次
REFLECTION_INTERVAL=3           # Executor Reflection 触发间隔（V2）
CONFIDENCE_THRESHOLD=0.6        # Reflection 置信度阈值（V2）
LANGCHAIN_TRACING_V2=true       # 启用 LangSmith 追踪（可选）
LANGCHAIN_API_KEY=lsv2_sk_...
LANGCHAIN_PROJECT=...
```

---

## 常用命令

```bash
make dev          # 启动 LangGraph 开发服务器（无 UI）
make dev_ui       # 启动 LangGraph Studio（有 UI）
make lint         # ruff + mypy 检查
make format       # ruff 自动格式化
make test_unit    # 运行单元测试
make test_all     # 运行所有测试
uv sync --dev     # 安装所有依赖（含 dev 依赖）
```
