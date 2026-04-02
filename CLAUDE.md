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
  - 模型：siliconflow:stepfun-ai/Step-3.5-Flash（响应最快）
  - 工具：generate_plan 和 execute_plan
  - 职责：理解用户意图，调度 Planner/Executor，管理重规划，合成最终答案
  - 决策机制：三种回复模式（Direct Response / Executor-use ReAct / Plan → Execute）
  │
  ├── generate_plan ──▶ Planner Agent    src/planner_agent/
  │                       - 框架：自定义 StateGraph（ReAct 模式，可查找信息辅助）
  │                       - 模型：siliconflow:Pro/zai-org/GLM-5（推理最强）
  │                       - 职责：把任务需求（+ 含历史执行状态Plan）转化为意图层 JSON 计划
  │                       - 输入参数：task_core (+ plan_id)
  │                       - 返回值：plan_json
  │
  └── execute_plan ───▶ Executor Agent   src/executor_agent/
                          - 框架：自定义 StateGraph（ReAct 模式，含 ExecutorState）
                          - 模型：siliconflow:stepfun-ai/Step-3.5-Flash（性价比最高）
                          - 职责：按Plan，意图层 JSON 计划自主选工具执行，完成后返回带步骤状态的 updated_plan
                          - 输入参数：task_description / plan_id
                          - 返回值：ExecutorResult(status, updated_plan_json, summary)（其中 `updated_plan_json` 内含 `plan_id/version`）
```

### 入口

`langgraph.json` 注册的唯一图：`src/supervisor_agent/graph.py:graph`

---

## 已确定的关键设计决策

### 决策 1：generate_plan / execute_plan 使用结构化参数传递

`execute_plan` 接受 LLM 传入的结构化参数：
- `task_description`: 纯文本，Mode 2（Executor-use ReAct）下只需要该参数
- `plan_id`：Mode 3（Plan → Execute）下只需要该参数

`generate_plan` 接受 LLM 传入的结构化参数：
- `task_core`：
    初始Plan生成时：Supervisor 提炼后的 intent，应输入足够的有用信息。
    Plan修改时：Supervisor 读取 ExecutorResult 中的 summary 指出修改方向。
- `plan_id`：当前 Plan 的编号，指向最新 Version 的本次执行对应 Plan。（仅在Plan修改时需要）

**原因**：
- 通过 `plan_id` 传递实现极低 token 消耗
- `task_description` 极简传参保证Mode 2（Executor-use ReAct）情况下， Supervisor → Executor 高效通信
- 重规划时通过 `plan_id` 间接传递带状态的 plan，避免冗余传参

---

### 决策 2：ExecutorResult 结构化返回值，Planner 返回 JSON

`run_executor()` 返回 `ExecutorResult`：
`run_planner()` 返回 `PlanJSON`：

```python
@dataclass
class ExecutorResult:
    status: Literal["completed", "failed"]
    updated_plan_json: str   # 字符串；Mode 2 下允许为空（表示无可复用 plan 状态）
    summary: str             # 给 Supervisor LLM 读的自然语言摘要
```

**原因**：结构化返回使 Supervisor 能可靠解析执行状态，而不是从自然语言中猜测是否成功。

---

### 决策 3：Plan 是"意图层"，不包含工具名

Planner **不知道 Executor 有哪些工具**，Plan 的每个 step 只描述**意图（intent）和期望产出（expected_output）**，不指定工具名称。Executor 自主根据 intent 选择合适工具。

**Plan step 字段**：

```json
{
  "step_id": 1,
  "intent": "意图描述",
  "expected_output": "完成验收标准",
  "status": "pending | completed | failed | skipped",
  "result_summary": null,
  "failure_reason": null
}
```

**Plan 顶层字段**：

```json
{
  "plan_id": "plan_v20260331",
  "version": 2,
  "goal": "任务总体目标",
  "steps": [...]
}
```

**旧版本处理（归档）**：
- 保持 `plan_id` 不变，只递增 `version`。
- 每次产生新 `version` 前，把当前 `session.plan_json`（旧版本的完整 Plan JSON，包含步骤执行状态）归档保存为只读快照（例如写入 `session.plan_archive[plan_id].versions[]`），然后再用新版本更新 `session.plan_json`。
- 这样 `Plan JSON` 结构示例/字段定义无需修改，默认读取 `session.plan_json` 即拿到最新 `version`。

**原因**：Planner 与 Executor 工具集完全解耦，更换/新增工具无需修改 Planner 提示词。`version` 字段用于追踪重规划历史。

---

### 决策 4：Executor 遇阻直接停止，不内部重规划

Executor 遇到无法继续的情况时**直接停止**，把带执行状态的 updated_plan（即 `updated_plan_json`，包含每一步的 `status/failure_reason`）返回给 Supervisor，**不在 Executor 内部主动重规划**。

**重规划决策权在 Supervisor**：

```
Supervisor 收到 Executor 结果
  ├── status=completed → 基于 summary 合成最终答案，结束
  └── status=failed
        ├── updated_plan_json 非空 + replan_count < MAX_REPLAN → 调 generate_plan（传 task_core，plan_id）→ 再 execute_plan（传 plan_id）
        ├── updated_plan_json 为空 → 直接把 `summary` 作为 Supervisor 最终回答生成（call_model）的输入（不使用 Planner；必要时由 Supervisor 显式切换为 Mode 3）
        └── 多次失败无法推进 → 告知用户，附上失败分析
```

当 Supervisor 收到 `ExecutorResult` 后：
- 若 `status=completed`：Supervisor LLM 仅基于 `ExecutorResult.summary` 生成最终回复并结束流程；`updated_plan_json` 仅用于状态同步/审计，不作为成功分支的 LLM 输入。
- 若 `status=failed`：
  - 若 `ExecutorResult.updated_plan_json` 非空：读取 `ExecutorResult.summary`（自然语言失败与修改方向），将其转化为下一轮重规划的 `task_core`，并依赖 `session.plan_json` 中已有的失败步骤执行状态来避免重复执行；随后如果 `replan_count < MAX_REPLAN`，调用 `generate_plan` 生成新 `version` 的 plan 并写回 `session`，再调用 `execute_plan`（传 `plan_id`）继续执行。
  - 若 `ExecutorResult.updated_plan_json` 为空：直接使用 `ExecutorResult.summary` 完成本轮 Supervisor 的失败解释/反馈，并结束或由 Supervisor 决定显式切换为 Mode 3。
- 若 `status=failed` 且 `updated_plan_json` 非空 且 `replan_count >= MAX_REPLAN`：基于 `summary`（以及必要时的最后错误信息）向用户返回失败分析并终止。

**原因**：避免 Executor 自行决策范围扩大（越权），保证系统行为可预测、可审计。

---


### 决策 5：失败处理双重保障

- **正常失败**（Executor LLM 主动停止）：`updated_plan_json` 由 Executor 自行填写各步骤 `status/failure_reason`
- **异常崩溃**（Python Exception）：`execute_plan` 捕获所有异常，调用 `_mark_plan_steps_failed()` 把所有 `pending` 步骤标记为 `failed` 并写入 `failure_reason`

保证（分场景）：
- Mode 3：`updated_plan_json` **永不为空**（保证 Supervisor 始终能读取到可复用的 plan 执行状态）
- Mode 2：允许 `updated_plan_json` 为空；Supervisor 通过 `summary` 完成失败反馈（必要时由 Supervisor 显式切换为 Mode 3 以获得可复用的 plan 状态）

---

### 决策 5.1：Mode2 -> Mode3 切换（仅由 Supervisor 决定）

**触发场景**：Supervisor 初始选择 **Mode 2（Tool-use ReAct：仅调用 Executor，传 `task_description`）**；当 Executor 返回 **`status=failed`** 时，其 `summary` 明确表达“当前执行路径无法完成/需要从计划层面重构（例如重新拆解意图、调整后续步骤、缺少可复用计划状态等）”，此时 Supervisor 判断 Mode2 不足以继续。

**本决策的唯一目标**：规定当 Supervisor 接收到 Executor 的失败结果后，如何“考虑/进入 Mode3”；**最终是否切换、切换到哪一步、是否复用当前失败状态等细节，全部由 Supervisor 自行决定**（本决策不改变 Executor 的权限边界）。

Supervisor 的决策规则（单一入口）如下：

1) 仅当 `status == "failed"` 时进入本决策逻辑。  
2) Supervisor 解析 `summary`：若其语义信号满足“需要计划层重构/无法继续沿现有意图-执行路径推进”的判定，则认为 Mode2 需要升级到 Mode3。  
3) 当且仅当步骤计数满足 `replan_count < MAX_REPLAN` 时，Supervisor 可以在下一轮改用 **Mode 3（Plan → Execute → Summarize）**：  
   - 若 `updated_plan_json` 非空：允许基于当前执行状态进行有状态重规划（更推荐）。  
   - 若 `updated_plan_json` 为空：允许仅凭 `summary` 进行显式的 Mode3 升级，但前提是 Supervisor 判断“仅靠 summary 无法形成可复用的 plan 执行状态”，因此需要重新生成意图层 Plan。  

---

### 决策 6：dynamic_tools_node 双向同步 session（仅在非空 updated_plan_json 时保持 plan 最新）

- `generate_plan` 执行后：将新 `plan_json`（含 plan_id + version）写入 `session`
- `execute_plan` 执行后：当 `updated_plan_json` 非空时，将其（带执行状态，version 已递增）写回 `session`

`session.plan_json` 在 `updated_plan_json` 非空时始终是**最新版本的 plan**（含执行进度、plan_id、version）。

`dynamic_tools_node` 同时提取 `status` 和 `error_detail`，写入 `session.last_executor_status / last_executor_error`，供 Supervisor 决策用。

补充约束（token 优化）：
- 当 `status=completed` 时，Supervisor LLM 仅接收精简执行反馈（以 `summary` 为核心），不接收完整 `updated_plan_json`。
- `updated_plan_json` 仅在系统内部用于状态写回、重规划上下文与可审计性。

---

### 决策 7：重规划时传入带执行状态的 Plan

重规划时，`generate_plan` 工具通过 LLM 传入的 `plan_id` 参数，内部从 `session.plan_json` 获取带执行状态的 plan 传给 Planner，让 Planner 在修订时能看到：
- 哪些步骤已完成（跳过重复执行）
- 哪步失败及原因（有针对性地修订）
- 当前 `plan_id` 和 `version`（用于追踪重规划历史）

**原因**：避免 Planner 在重规划时"失忆"，重复生成已完成步骤造成浪费。通过 `plan_id` 传递比直接传 JSON 更节省 token。

---

### 决策 8：Supervisor 三种回复模式（核心决策机制）

Supervisor 在每次主 ReAct 的 **Thought** 阶段，必须精准选择以下三种模式之一：

| 模式 | 名称                    | 适用场景                           | Supervisor 的行为                                      | token 消耗 |
|------|-------------------------|------------------------------------|--------------------------------------------------------|------------|
| 1    | Direct Response        | 简单事实、知识内化、无需工具       | 内部思考后直接输出最终答案                             | 最低       |
| 2    | Tool-use ReAct         | 需要少量工具、短流程、目标明确     | 调用 Executor（只传 task_description）走 ReAct；不自动使用 Planner（除非 Supervisor 显式切换为 Mode 3） | 中等       |
| 3    | Plan → Execute → Summarize | 多步骤、长流程、有依赖、需一致性   | 先调用 Planner 生成 Plan → 再调用 Executor 执行 → 融合总结 | 较高       |

**模式选择原则**：
- 能用模式 1 就绝不用 2，能用模式 2 就尽量不用 3（Occam's Razor）
- Supervisor 必须输出结构化决策（mode + reason + confidence）
- 决策依据写在 Supervisor System Prompt 中（明确表格或条件）

**Mode 2 参数传递**：
- 调用 Executor：
```python
{
  "task_description": "..."
}
```

**Mode 3 参数传递**：
- 调用 Planner：
  ```python
  {
    "task_core": "..."
  }
  ```
  修改Plan时：  ```python
                {
                  "task_core": "修改方向建议"，
                  "plan_id": "plan_v20260331"
                }
                ```
- 调用 Executor：
  ```python
  {
    "plan_id": "plan_v20260331"
  }
  ```

**原因**：简化决策逻辑，优化 token 消耗，增强可观测性和可调试性。

---

### 决策 9：Planner 会话留存与复用

目标：保证同一份 Plan（固定 `plan_id`，仅递增 `version`）在重规划过程中始终复用同一个 Planner 上下文（等价于“同一条 Planner 对话线程”），避免 Planner 因会话重置而重复推理或忽略历史执行状态。

1) 索引键选择
- Planner 会话的唯一索引键：`plan_id`
- `version` 变化不改变索引键；同一个 `plan_id` 下的所有 `version` 都复用同一 Planner 会话记录

2) 会话内容存储（建议最小集）
- `messages`：Planner 与系统之间的对话消息序列（至少包含每次 `generate_plan` 的输入摘要与 Planner 输出的 plan_json）
- `last_version`：最近一次成功写回的 `plan_id/version`，用于校验复用是否正确
- `last_planner_output`（可选）：最近一次原始 LLM 输出（用于解析失败时定位问题）

3) 存储介质（V1 落地策略）
- V1：仅存于当前 `AgentSession` 的内存结构中（不做跨任务/跨进程的“Memory 归档”），满足“同一份 Plan 复用 Planner”的闭环目标
- 可选（调试/排障）：提供环境变量（如 `PERSIST_PLANNER_SESSION=true`）将 `planner_session_by_plan_id[plan_id]` 序列化到磁盘，以便重启后复现；该能力不参与 V1 验收逻辑

4) 生命周期规则
- 首次调用 `generate_plan` 且该 `plan_id` 不存在：创建 Planner 会话记录并写入 `session`
- 后续调用 `generate_plan`（Supervisor 传入相同 `plan_id`）：读取既有 Planner 会话记录，将其 `messages` 注入到本次 Planner 调用上下文中
- 当该 `plan_id` 对应的任务流程结束（completed / 放弃）：可选择清理 `session` 中的该条 Planner 会话记录（或保留用于调试）

5) 失败与一致性
- 即使 Planner 输出解析失败/返回异常，也应把“失败原因 + 原始 LLM 输出摘要”追加进该 `plan_id` 的 Planner 会话记录，确保下一次重试能基于真实失败上下文继续
- 由于 V1 单线程约束，原则上同一时刻不会并发写同一个 `plan_id` 的 Planner 会话；如未来引入并行（V3），需要在 Planner 会话写入处加顺序化/锁，避免消息乱序

最终效果：Supervisor 每次重规划时只需保证传入同一个 `plan_id`，Planner 就能“激活以前的上下文”，并围绕上一版执行状态增量修订 plan。

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
| `src/supervisor_agent/state.py` | `State`、`InputState`、`AgentSession`（含 `last_executor_status/last_executor_error/replan_count`）、`ExecutorRef` |
| `src/supervisor_agent/prompts.py` | `SUPERVISOR_SYSTEM_PROMPT`，读懂自身职能|
| `src/supervisor_agent/tools.py` | `generate_plan`、`execute_plan`、`_mark_plan_steps_failed` |
| `src/planner_agent/graph.py` | Planner 图，`call_planner` 节点，`run_planner()` 对外接口（含 JSON 提取） |
| `src/planner_agent/prompts.py` | `PLANNER_SYSTEM_PROMPT`，含意图层 Plan JSON 格式要求（含步骤状态字段）|
| `src/planner_agent/tools.py` | 规划辅助工具（V1 定义但不绑定到 graph，V2 按需绑定） |
| `src/executor_agent/graph.py` | Executor StateGraph，`ExecutorState`、`ExecutorResult`、`call_executor` + `tools_node` + `route_executor_output`、`run_executor()` 对外接口 |
| `src/executor_agent/prompts.py` | `EXECUTOR_SYSTEM_PROMPT`，按 intent 自主选工具，遇阻停止，输出 updated_plan |
| `src/executor_agent/tools.py` | Executor 工具集合（`write_file`、`run_local_command` 等） |
| `src/common/context.py` | 运行时配置，`Context` dataclass，支持环境变量覆盖 |
| `src/common/prompts.py` | `SYSTEM_PROMPT`，Supervisor 全局系统提示（暂时为空） |
| `src/common/utils.py` | `load_chat_model("provider:model")` 统一入口 |
| `src/common/basemodel.py` | `AgentBaseModel` Pydantic 基类 |

---


## 环境配置

```bash
# 必须
SILICONFLOW_API_KEY=sk-...      # Planner / Executor 使用 DeepSeek-V3.2
DASHSCOPE_API_KEY=sk-...        # Supervisor 使用 Qwen

# 可选
REGION=prc                      # prc/cn 或 international/en（默认 prc）
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
