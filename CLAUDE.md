# CLAUDE.md

> 本文件是给 AI 助手读的工程文档，记录项目真实现状、架构设计意图和已确定的决策。  
> **每次对话开始前必须先读此文件。**

---

## 文档边界（避免重复）

- `CLAUDE.md`：工程实现与架构决策的唯一来源（给 AI 助手读）
- `PRD.md`：产品目标、范围与验收标准（讲清楚做什么、为什么）
- `ROADMAP.md`：版本任务拆解与推进状态（讲清楚先做什么、何时做）

> 本文档不维护版本任务清单；任务进度统一在 `ROADMAP.md` 更新。

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
  - 模型：siliconflow:stepfun-ai/Step-3.5-Flash（响应最快）
  - 工具：仅 generate_plan 和 execute_plan 两个（以后可能补）
  - 职责：理解用户意图，调度 Planner/Executor，管理重规划，合成最终答案
  - 决策机制：三种回复模式（Direct Response / Tool-use ReAct / Plan → Execute）
  │
  ├── generate_plan ──▶ Planner Agent    src/planner_agent/
  │                       - 框架：自定义 StateGraph（ReAct 模式，可查找信息辅助）
  │                       - 模型：siliconflow:Pro/zai-org/GLM-5（推理最强）
  │                       - 职责：把任务需求（含历史执行状态）转化为意图层 JSON 计划/将之前执行失败的 plan 更新调整
  │                       - 输入参数：task_core / plan_id
  │                       - 返回值：plan_json, plan_id
  │
  └── execute_plan ───▶ Executor Agent   src/executor_agent/
                          - 框架：自定义 StateGraph（ReAct 模式，含 ExecutorState）
                          - 模型：siliconflow:stepfun-ai/Step-3.5-Flash（性价比最高）
                          - 职责：按意图层 JSON 计划自主选工具执行，完成后返回带步骤状态的 updated_plan
                          - 输入参数：Mode 2: task_description / Mode 3: plan_id
                          - 返回值：ExecutorResult(status, updated_plan_json, summary)
```

### 入口

`langgraph.json` 注册的唯一图：`src/supervisor_agent/graph.py:graph`

---

## Prompt 设计契约（高优先级）

系统提示词是每个 Agent 的行为内核，不是文案装饰。修改提示词时，必须同时检查对应 `graph.py` 与 `tools.py` 是否仍与提示词契约一致。

### 三层分工（不可混淆）

- Supervisor Prompt：负责模式选择、调度策略、失败收敛与用户输出质量
- Planner Prompt：只负责意图层计划，不执行、不选工具、不写命令
- Executor Prompt：只负责按计划执行与状态回写，不重规划、不改任务目标

### 统一哲学（所有 Prompt 必须遵守）

- 最小必要复杂度：能直答不用工具，能直执行不先规划
- 意图与工具解耦：Plan 不出现工具名/API 名/命令名
- 失败可收敛：失败要可诊断，可重试次数受上限约束
- 可审计：关键决策与执行结果都应结构化可追踪

### 修改守则（每次改 Prompt 都要做）

1. 对齐状态字段：`status/result_summary/failure_reason` 语义一致
2. 对齐消息约束：系统消息位置、HumanMessage 注入策略与模型接口兼容
3. 对齐停止条件：步数上限、重规划上限、失败停止条件一致
4. 至少补一条对应单测（解析逻辑或路由/收敛逻辑）

---

## 已确定的关键设计决策

### 决策 1：generate_plan 传 task_core/plan_id；execute_plan 仅用 InjectedState

- **`generate_plan`**：LLM 传入 **`task_core`**（首次规划必填）与可选 **`plan_id`**（重规划时必填，且须与 `PlannerSession.plan_json` 内 `plan_id` 一致）；`InjectedState` 用于**校验**并从 session 取出**完整**带执行状态的 plan。Planner **不**接收 Supervisor 全量 `messages`。
- **`execute_plan`**：仍仅通过 `InjectedState` 读取 `state.planner_session.plan_json` 执行（与架构「Mode 3 用 plan」一致，无需 LLM 传 plan 正文）。

**原因**：
- 规划输入与主循环对话解耦，token 可控、语义清晰（与架构图「输入参数：task_core / plan_id」一致）
- 计划正文仍只来自 `PlannerSession`，避免 LLM 粘贴 JSON 导致分叉
- `plan_id` 与 session 内 JSON 交叉校验，减少误传

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

**Plan 顶层字段**：

```json
{
  "plan_id": "plan_v20260331_002",
  "version": 2,
  "goal": "任务总体目标",
  "steps": [...]
}
```

**原因**：Planner 与 Executor 工具集完全解耦，更换/新增工具无需修改 Planner 提示词。`version` 字段用于追踪重规划历史。

---

### 决策 3：Executor 遇阻直接停止，不内部重规划

Executor 遇到无法继续的情况时**直接停止**，把带执行状态的 updated_plan 返回给 Supervisor，**不在 Executor 内部主动重规划**。

**重规划决策权在 Supervisor**：

```
Supervisor 收到 Executor 结果
  ├── status=completed → 合成最终答案，结束
  └── status=failed
        ├── replan_count < MAX_REPLAN → 调 generate_plan → 再 execute_plan
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
    updated_plan_json: str   # 带步骤执行状态的完整 plan JSON（含 version）
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

- `generate_plan` 执行后：将新 `plan_json`（含 plan_id + version）写入 `session`
- `execute_plan` 执行后：将 `updated_plan_json`（带执行状态，version 已递增）写回 `session`

`session.plan_json` 始终是**最新版本的 plan**（含执行进度、plan_id、version）。

`dynamic_tools_node` 同时提取 `status` 和 `error_detail`，写入 `session.last_executor_status / last_executor_error`，供 Supervisor 决策用。

---

### 决策 7：重规划时由 plan_id 定位，正文从 session 注入 Planner

重规划时，Supervisor 在 `generate_plan` 中传入 **`plan_id`**（与当前 `session.plan_json` 内字段一致）；工具内部用其**校验**后，将 **`session.plan_json` 全文**（带执行状态）作为 `replan_plan_json` 交给 Planner。LLM **不**在参数里粘贴整份 JSON（省 token、防漂移）。

Planner 修订时仍能看到：已完成步骤、失败原因、`plan_id` / `version` 等。

---

### 决策 8：Supervisor 三种回复模式（核心决策机制）

Supervisor 在每次主 ReAct 的 **Thought** 阶段，必须精准选择以下三种模式之一：

| 模式 | 名称                    | 适用场景                           | Supervisor 的行为                                      | token 消耗 |
|------|-------------------------|------------------------------------|--------------------------------------------------------|------------|
| 1    | Direct Response        | 简单事实、知识内化、无需工具       | 内部思考后直接输出最终答案                             | 最低       |
| 2    | Tool-use ReAct         | 需要少量工具、短流程、目标明确     | 调用 Executor（只传 task_description）走 ReAct        | 中等       |
| 3    | Plan → Execute → Summarize | 多步骤、长流程、有依赖、需一致性   | 先调用 Planner 生成 Plan → 再调用 Executor 执行 → 融合总结 | 较高       |

**模式选择原则**：
- 能用模式 1 就绝不用 2，能用模式 2 就尽量不用 3（Occam's Razor）
- Supervisor 必须输出结构化决策（mode + reason + confidence）
- 决策依据写在 System Prompt 中（明确表格或条件）

**当前实现说明（V1）**：
- Supervisor 通过工具调用触发 `generate_plan` / `execute_plan`
- `generate_plan` 要求 LLM 传入**足够详细**的 **`task_core`**，重规划时另传 **`plan_id`**；完整 plan 正文由状态注入
- `execute_plan` 仍仅从 `InjectedState` 读取当前计划，不要求 LLM 传参

**原因**：规划意图由 Supervisor 显式压缩为 `task_core`/`plan_id`，与架构一致；执行侧仍以 session 为唯一事实来源。

---

### 决策 9：Planner 提示词结构与消息组装

Planner 的 LLM 输入**仅**由 `run_planner(task_core, replan_plan_json=...)` 经 `build_planner_messages` 组装，**不包含** Supervisor 全量对话：

1. **第一条**：`SystemMessage`，全文为 `get_planner_system_prompt(...)`，即 **`_PLANNER_SYSTEM_PROMPT_TEMPLATE`** 注入 Executor 能力后的完整 Planner 系统提示（规则、输出格式、质量标准等均在此条，**不再**使用单独的 `PLANNER_ROLE` 或末尾重复注入）。
2. **第二条**：`HumanMessage`，内容为 Supervisor 经 `generate_plan(task_core=...)` 传入的 **`task_core` 纯文本**。**`task_core` 必须足够详细**（目标、约束、验收标准、关键上下文与用户原话要点），使 Planner 能独立规划而无需对话全历史。
3. **重规划时第三条**（可选）：`HumanMessage`，内容为当前 `session.plan_json` 中带执行状态的 Plan 全文（由工具注入）。
4. `call_planner` 中仅过滤带 `tool_calls` 的 `AIMessage`（防御性），然后调用模型。

> **注意**：单条 `SystemMessage` 即完整 Planner 系统提示；与旧版「首条短角色 + 末条长规则」不同。

Planner 图为单次调用编译，**不使用** checkpoint，避免同会话多次规划时消息累加。

---

### 决策 10：Executor Reflection 步骤计数（V2 阶段引入）

Executor ReAct 循环中内置步骤计数器，触发 Reflection 的条件：
- 已执行步骤数达到 `REFLECTION_INTERVAL`（默认 3）的倍数
- LLM 自评置信度低于 `CONFIDENCE_THRESHOLD`（默认 0.6）

Reflection 输出：当前路径是否偏离目标、建议调整方向。

偏差大或到达里程碑时，Executor **主动停止**并打包 Snapshot 上报给 Supervisor，而不是盲目继续执行。

> V1 阶段不实现此决策，Executor 直线执行到完成或失败。

---

### 决策 11：单线程执行（V1 明确约束）

V1 阶段明确为**单线程**，Supervisor 每次只调用一个 Executor。

V3 阶段再引入 fan-out 并行：Supervisor 将 Plan 拆分为多个子 Plan，并行分发给多个 Executor 实例，最后融合所有 CompletionReport。

**原因**：并行引入额外的状态同步、冲突解决复杂度。V1 先验证基础闭环，稳定后再扩展。

---

## 模块速查表

| 文件 | 职责 |
|---|---|
| `src/supervisor_agent/graph.py` | 主循环图定义，`call_model` + `dynamic_tools_node` + 路由逻辑 |
| `src/supervisor_agent/state.py` | `State`、`InputState`、`PlannerSession`、`SupervisorDecision`（含 `replan_count` / `last_executor_status` / `last_executor_error`）、`ExecutorRef` |
| `src/supervisor_agent/tools.py` | `generate_plan`、`execute_plan`、`_mark_plan_steps_failed` |
| `src/planner_agent/graph.py` | Planner 图，`call_planner` 节点，`run_planner()` 对外接口（含 JSON 提取） |
| `src/planner_agent/prompts.py` | `_PLANNER_SYSTEM_PROMPT_TEMPLATE` / `get_planner_system_prompt`，作为 Planner **首条**系统消息全文；含意图层 Plan JSON 格式要求（含步骤状态字段）|
| `src/planner_agent/tools.py` | 规划辅助工具（V1 定义但不绑定到 graph，V2 按需绑定） |
| `src/executor_agent/graph.py` | Executor StateGraph，`ExecutorState`、`ExecutorResult`、`call_executor` + `tools_node` + `route_executor_output`、`run_executor()` 对外接口 |
| `src/executor_agent/prompts.py` | `EXECUTOR_SYSTEM_PROMPT`，按 intent 自主选工具，遇阻停止，输出 updated_plan |
| `src/executor_agent/tools.py` | Executor 工具集合（`write_file`、`run_local_command` 等） |
| `src/common/context.py` | 运行时配置，`Context` dataclass，支持环境变量覆盖 |
| `src/common/prompts.py` | `SYSTEM_PROMPT`，Supervisor 全局系统提示 |
| `src/common/utils.py` | `load_chat_model("provider:model")` 统一入口 |
| `src/common/basemodel.py` | `AgentBaseModel` Pydantic 基类 |

---

## 当前实现状态（引用）

- 版本任务清单与勾选进度：见 `ROADMAP.md`
- 产品阶段边界与验收定义：见 `PRD.md`
- 本文仅维护“已确定工程决策”和“模块职责”，避免与上述文档重复

---

## 环境配置

```bash
# 必须
SILICONFLOW_API_KEY=sk-...      # Planner / Executor 使用 DeepSeek-V3.2
DASHSCOPE_API_KEY=sk-...        # Supervisor 使用 Qwen

# 可选
REGION=prc
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
