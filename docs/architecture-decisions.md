# 架构设计决策详解

> 本文件保留 AgentTriad 所有设计决策的**完整背景、原因分析和细节说明**。  
> 精简版规则见 [`CLAUDE.md`](../CLAUDE.md)；本文件供项目成员回顾设计意图时参考，AI Agent 执行时无需阅读。

---

## 决策 1：call_planner / call_executor 使用结构化参数传递

`call_executor` 接受 LLM 传入的结构化参数：
- `task_description`: 纯文本，Mode 2（Executor-use ReAct）下只需要该参数
- `plan_id`：Mode 3（Plan → Execute）下只需要该参数
- `wait_for_result`（默认 `True`）：控制是否阻塞等待执行结果。`True` 时派发后自动等待并返回 `[EXECUTOR_RESULT]`，Supervisor 无需额外调用 `manage_executor`，减少一次工具调用和 token 消耗。`False` 时为异步派发，需后续调用 `manage_executor(action="get_result", plan_id=...)` 获取结果（适用于并行派发场景）。
- `manage_executor(action="get_result")` 可选参数 `detail`（默认 `overview`）：与 `wait_for_result=false` 配套时仍为阻塞收束并返回 `[EXECUTOR_RESULT]`。`detail=full` 时，若任务仍在 `active_executor_tasks` 中与 `overview` 同路径等待；终态后由 `dynamic_tools_node` 把 `last_executor_full_output`（步骤级摘要）拼入给 LLM 的 ToolMessage。若任务已不在 active 且 `plan_id` 与会话中 `plan_json` 顶层一致，则只读会话缓存中的步骤级正文（返回体不含 `[EXECUTOR_RESULT]`，不重复跑会话合并）。原独立工具 `get_executor_full_output` 已并入本参数语义。

`call_planner` 接受 LLM 传入的结构化参数：
- `task_core`：
    初始 Plan 生成时：Supervisor 提炼后的 intent，应输入足够的有用信息。
    Plan 修改时：Supervisor 读取 ExecutorResult 中的 summary 指出修改方向。
- `plan_id`：当前 Plan 的编号，指向最新 Version 的本次执行对应 Plan。（仅在 Plan 修改时需要）

**原因**：
- 通过 `plan_id` 传递实现极低 token 消耗
- `task_description` 极简传参保证 Mode 2 情况下 Supervisor → Executor 高效通信
- 重规划时通过 `plan_id` 间接传递带状态的 plan，避免冗余传参

**V3 实现约定（`plan_id` 与 Executor 子进程）**：
- **`plan_id` 是 Executor 侧任务与子进程调度的主键**：`call_executor(plan_id=…)`使用该 id；进程管理器对「运行中」的同一 id **复用**子进程，否则 **新建**。
- **Mode 2不显式传 `plan_id`**：实现上为本次派发**生成新的 `plan_id`**（与临时 Plan JSON 内字段一致），并**新建**子进程；产品语义即「没有沿用已有 plan id → 新 executor」。
- **与 Planner 会话复用（决策 9）区分**：Planner 按 `plan_id` 复用的是 **Planner 对话线程**；Executor 单次运行仍是独立 `run_executor`，**不在子进程内跨任务保留 ReAct 消息历史**。
- **Supervisor 仍保留完整 `messages`**：多轮工具返回会进入 Supervisor 上下文，这与「每次可起新 Executor 子进程」并行成立。

**同一 `plan_id` 再次调用时，「在之前上下文后面继续」指什么**（避免与决策 9 混淆）：
- **`call_planner` + 同一 `plan_id`**：**是**。预期在**同一 Planner 会话**中接续对话，围绕已有执行状态做增量修订（见决策 9）。
- **`call_executor` + 同一 `plan_id`**：**不是**接续 Executor 内部的 ReAct 聊天记录；**是**基于 `PlannerSession` 中**当前最新的 `plan_json`**（通常已含上轮 `updated_plan_json` 写回的步骤状态）再启动**新一轮**执行。进度与「之前干了什么」主要由 **Plan JSON 快照 + Supervisor 侧摘要**承载。
- **Supervisor**：同一线程消息历史始终累积，不依赖 `plan_id` 索引。

---

## 决策 2：ExecutorResult 结构化返回值，Planner 返回 JSON

```python
@dataclass
class ExecutorResult:
    status: Literal["completed", "failed", "paused"]
    updated_plan_json: str   # 字符串；Mode 2 下允许为空（表示无可复用 plan 状态）
    summary: str             # 给 Supervisor LLM 读的自然语言摘要
    snapshot_json: str = ""  # status=paused（如 V2-c Reflection 中途检查点）时的结构化快照 JSON
```

- `completed` / `failed`：常规结束；`paused`：Executor 在中途检查点停止本轮，等待 Supervisor 决策（重规划权仍在 Supervisor，与决策 4 一致）。
- `snapshot_json` 仅在 `paused` 等需要结构化快照时使用；解析逻辑见 `executor_agent/graph.py` 中 `_parse_executor_output`。

**原因**：结构化返回使 Supervisor 能可靠解析执行状态，而不是从自然语言中猜测是否成功。

---

## 决策 3：Plan 是"意图层"，不包含工具名

Planner **不知道 Executor 有哪些工具**，Plan 的每个 step 只描述**意图（intent）和期望产出（expected_output）**，不指定工具名称。Executor 自主根据 intent 选择合适工具。

### Plan step 字段

`step_id` 在语义上为步骤标识；**落地时** `call_planner` 返回经 `_normalize_plan_json()` 归一化后，统一为**字符串**（数字也会被转成字符串，缺省时为 `"step_1"`、`"step_2"` …）。

```json
{
  "step_id": "step_1",
  "intent": "意图描述",
  "expected_output": "完成验收标准",
  "status": "pending | completed | failed | skipped",
  "result_summary": null,
  "failure_reason": null,
  "parallel_group": null
}
```

### Plan 顶层字段

```json
{
  "plan_id": "plan_v20260331",
  "version": 2,
  "goal": "任务总体目标",
  "steps": [...]
}
```

### 旧版本处理（归档）

- 保持 `plan_id` 不变，只递增 `version`。
- 每次产生新 `version` 前，把当前 `PlannerSession.plan_json`（旧版本的完整 Plan JSON，包含步骤执行状态）追加到 **`PlannerSession.plan_archive_by_plan_id[plan_id]`**（`list[str]`，每项为一份完整 JSON 快照），然后再用新版本更新 `plan_json`（见 `supervisor_agent/graph.py` 中 `dynamic_tools_node` 对 `call_planner` 的处理）。
- 这样 `Plan JSON` 结构示例/字段定义无需修改，默认读取 `plan_json` 即拿到最新 `version`。

**原因**：Planner 与 Executor 工具集完全解耦，更换/新增工具无需修改 Planner 提示词。`version` 字段用于追踪重规划历史。

---

## 决策 4：Executor 遇阻直接停止，不内部重规划

Executor 遇到无法继续的情况时**直接停止**，把带执行状态的 updated_plan（即 `updated_plan_json`，包含每一步的 `status/failure_reason`）返回给 Supervisor，**不在 Executor 内部主动重规划**。

### 重规划决策权在 Supervisor

```
Supervisor 收到 Executor 结果
  ├── status=completed → 基于 summary 合成最终答案，结束
  └── status=failed
        ├── updated_plan_json 非空 + replan_count < MAX_REPLAN → 调 call_planner（传 task_core，plan_id）→ 再 call_executor（传 plan_id）
        ├── updated_plan_json 为空 → 直接把 `summary` 作为 Supervisor 最终回答生成（call_model）的输入（不使用 Planner；必要时由 Supervisor 显式切换为 Mode 3）
        └── 多次失败无法推进 → 告知用户，附上失败分析
```

### Supervisor 收到 ExecutorResult 后的完整处理逻辑

- 若 `status=completed`：Supervisor LLM 仅基于 `ExecutorResult.summary` 生成最终回复并结束流程；`updated_plan_json` 仅用于状态同步/审计，不作为成功分支的 LLM 输入。
- 若 `status=failed`：
  - 若 `ExecutorResult.updated_plan_json` 非空：读取 `ExecutorResult.summary`（自然语言失败与修改方向），将其转化为下一轮重规划的 `task_core`，并依赖 `session.plan_json` 中已有的失败步骤执行状态来避免重复执行；随后如果 `replan_count < MAX_REPLAN`，调用 `call_planner` 生成新 `version` 的 plan 并写回 `session`，再调用 `call_executor`（传 `plan_id`）继续执行。
  - 若 `ExecutorResult.updated_plan_json` 为空：直接使用 `ExecutorResult.summary` 完成本轮 Supervisor 的失败解释/反馈，并结束或由 Supervisor 决定显式切换为 Mode 3。
- 若 `status=failed` 且 `updated_plan_json` 非空 且 `replan_count >= MAX_REPLAN`：基于 `summary`（以及必要时的最后错误信息）向用户返回失败分析并终止。

**原因**：避免 Executor 自行决策范围扩大（越权），保证系统行为可预测、可审计。

---

## 决策 5：失败处理双重保障

- **正常失败**（Executor LLM 主动停止）：`updated_plan_json` 由 Executor 自行填写各步骤 `status/failure_reason`
- **异常崩溃**（Python Exception）：`call_executor` 捕获所有异常，调用 `_mark_plan_steps_failed()` 把所有 `pending` 步骤标记为 `failed` 并写入 `failure_reason`

保证（分场景）：
- Mode 3：`updated_plan_json` **永不为空**（保证 Supervisor 始终能读取到可复用的 plan 执行状态）
- Mode 2：允许 `updated_plan_json` 为空；Supervisor 通过 `summary` 完成失败反馈（必要时由 Supervisor 显式切换为 Mode 3 以获得可复用的 plan 状态）

---

## 决策 5.1：Mode 2 → Mode 3 切换（仅由 Supervisor 决定）

**触发场景**：Supervisor 初始选择 Mode 2（Tool-use ReAct：仅调用 Executor，传 `task_description`）；当 Executor 返回 `status=failed` 时，其 `summary` 明确表达"当前执行路径无法完成/需要从计划层面重构"，此时 Supervisor 判断 Mode 2 不足以继续。

**本决策的唯一目标**：规定当 Supervisor 接收到 Executor 的失败结果后，如何"考虑/进入 Mode 3"；最终是否切换、切换到哪一步、是否复用当前失败状态等细节，全部由 Supervisor 自行决定（本决策不改变 Executor 的权限边界）。

Supervisor 的决策规则（单一入口）：

1. 仅当 `status == "failed"` 时进入本决策逻辑。
2. Supervisor 解析 `summary`：若其语义信号满足"需要计划层重构/无法继续沿现有意图-执行路径推进"的判定，则认为 Mode 2 需要升级到 Mode 3。
3. 当且仅当步骤计数满足 `replan_count < MAX_REPLAN` 时，Supervisor 可以在下一轮改用 Mode 3（Plan → Execute → Summarize）：
   - 若 `updated_plan_json` 非空：允许基于当前执行状态进行有状态重规划（更推荐）。
   - 若 `updated_plan_json` 为空：允许仅凭 `summary` 进行显式的 Mode 3 升级，但前提是 Supervisor 判断"仅靠 summary 无法形成可复用的 plan 执行状态"，因此需要重新生成意图层 Plan。

---

## 决策 6：dynamic_tools_node 双向同步 session

- `call_planner` 执行后：将新 `plan_json`（含 plan_id + version）写入 `PlannerSession`。
- `call_executor` 执行后：**每一轮**都会根据工具返回更新执行侧字段；`plan_json` 的替换规则如下：
  - 若从 `[EXECUTOR_RESULT]` 解析出的 `updated_plan_json`**非空**：将其作为当前**带执行状态**的最新计划写回 `plan_json`。
  - 若**为空**：**保留** `PlannerSession` 中上一份 `plan_json` 不变（常见于 Mode 2 未序列化 plan、或失败时未带回 JSON）。若此前尚无 `PlannerSession` 且本次仍为空，则 `plan_json` 可能为 `None`。

因此「非空则刷新执行快照，空则保留会话内旧计划」与 Mode 2 / 文档中「允许 `updated_plan_json` 为空」一致，避免误清空仍可用的 Planner 计划。

`dynamic_tools_node` 同时提取 `status` 与 `error_detail`，写入 `last_executor_status` / `last_executor_error`，并写入 `last_executor_summary`（`[EXECUTOR_RESULT]` 标记前的正文），供 Supervisor 与后续重规划使用。

`replan_count`：`failed` 时递增，`completed` 时清零，`paused` 时不因本轮递增（与 `supervisor_agent/graph.py` 一致）。

### 补充约束（token 优化）

- 当 `status=completed` 时，Supervisor LLM 仅接收精简执行反馈（以 `summary` 为核心），不接收完整 `updated_plan_json`。
- `updated_plan_json` 仅在系统内部用于状态写回、重规划上下文与可审计性。

---

## 决策 7：重规划时传入带执行状态的 Plan

重规划时，`call_planner` 工具通过 LLM 传入的 `plan_id` 参数，内部从 `session.plan_json` 获取带执行状态的 plan 传给 Planner，让 Planner 在修订时能看到：
- 哪些步骤已完成（跳过重复执行）
- 哪步失败及原因（有针对性地修订）
- 当前 `plan_id` 和 `version`（用于追踪重规划历史）

**原因**：避免 Planner 在重规划时"失忆"，重复生成已完成步骤造成浪费。通过 `plan_id` 传递比直接传 JSON 更节省 token。

---

## 决策 8：Supervisor 三种回复模式（核心决策机制）

Supervisor 在每次主 ReAct 的 Thought 阶段，必须精准选择以下三种模式之一：

| 模式 | 名称 | 适用场景 | Supervisor 的行为 | token 消耗 |
|------|-------------------------|------------------------------------|--------------------------------------------------------|------------|
| 1 | Direct Response | 简单事实、知识内化、无需工具 | 内部思考后直接输出最终答案 | 最低 |
| 2 | Tool-use ReAct | 需要少量工具、短流程、目标明确 | 调用 Executor（只传 task_description）走 ReAct；不自动使用 Planner | 中等 |
| 3 | Plan → Execute → Summarize | 多步骤、长流程、有依赖、需一致性 | 先调用 Planner 生成 Plan → 再调用 Executor 执行 → 融合总结 | 较高 |

**模式选择原则**：
- Supervisor 须输出结构化决策（mode + reason + confidence），方便人工审查

### 参数传递规范

**Mode 2** — 调用 Executor：`{ "task_description": "..." }`

**Mode 3** — 调用 Planner：
- 初次：`{ "task_core": "..." }`
- 修改 Plan：`{ "task_core": "修改方向建议", "plan_id": "plan_v20260331" }`

**Mode 3** — 调用 Executor：`{ "plan_id": "plan_v20260331" }`

**原因**：简化决策逻辑，优化 token 消耗，增强可观测性和可调试性。

---

## 决策 9：Planner 会话留存与复用

**目标**：保证同一份 Plan（固定 `plan_id`，仅递增 `version`）在重规划过程中始终复用同一个 Planner 上下文（等价于"同一条 Planner 对话线程"），避免 Planner 因会话重置而重复推理或忽略历史执行状态。

### 1) 索引键选择
- Planner 会话的唯一索引键：`plan_id`
- `version` 变化不改变索引键；同一个 `plan_id` 下的所有 `version` 都复用同一 Planner 会话记录

### 2) 会话内容存储（建议最小集）
- `messages`：Planner 与系统之间的对话消息序列（至少包含每次 `call_planner` 的输入摘要与 Planner 输出的 plan_json）
- `last_version`：最近一次成功写回的 `plan_id/version`，用于校验复用是否正确
- `last_planner_output`（可选）：最近一次原始 LLM 输出（用于解析失败时定位问题）

### 3) 存储介质（落地策略）
- V1：仅存于当前 `AgentSession` 的内存结构中（不做跨任务/跨进程的"Memory 归档"），满足"同一份 Plan 复用 Planner"的闭环目标
- 未来可选（调试/排障）：提供环境变量（如 `PERSIST_PLANNER_SESSION=true`）将 `planner_session_by_plan_id[plan_id]` 序列化到磁盘

### 4) 生命周期规则
- 首次调用 `call_planner` 且该 `plan_id` 不存在：创建 Planner 会话记录并写入 `session`
- 后续调用 `call_planner`（Supervisor 传入相同 `plan_id`）：读取既有 Planner 会话记录，将其 `messages` 注入到本次 Planner 调用上下文中
- 当该 `plan_id` 对应的任务流程结束（completed / 放弃）：可选择清理 `session` 中的该条 Planner 会话记录

### 5) 失败与一致性
- 即使 Planner 输出解析失败/返回异常，也应把"失败原因 + 原始 LLM 输出摘要"追加进该 `plan_id` 的 Planner 会话记录，确保下一次重试能基于真实失败上下文继续
- 由于 V1 单线程约束，原则上同一时刻不会并发写同一个 `plan_id` 的 Planner 会话

**最终效果**：Supervisor 每次重规划时只需保证传入同一个 `plan_id`，Planner 就能"激活以前的上下文"，并围绕上一版执行状态增量修订 plan。

**与 Executor 的对照**（同一 `plan_id`）：Planner 侧「上下文」= 会话内消息历史；Executor 侧「接续」= **新的一次运行 + 最新 plan 正文中的步骤状态**，不恢复上一轮 Executor ReAct 消息链。详见决策 1 末尾「同一 plan_id 再次调用时」。

---

## 决策 10：Executor Reflection 步骤计数（V2-c）

Executor ReAct 循环中内置步骤计数器，触发 Reflection 的条件：
- 已执行步骤数达到 `REFLECTION_INTERVAL`（默认 3）的倍数
- LLM 自评置信度低于 `CONFIDENCE_THRESHOLD`（默认 0.6）

Reflection 输出：当前路径是否偏离目标、建议调整方向。

偏差大或到达里程碑时，Executor **主动停止**并打包 Snapshot 上报给 Supervisor，而不是盲目继续执行。**重规划仍仅由 Supervisor 发起**（与决策 4 一致）：不在 Executor 内实现与 Supervisor 并行的「干预分级」状态机；Supervisor 收到 Snapshot 后沿用既有 `call_planner` / `call_executor` 与 session 状态决策续跑或重规划。

> 当前状态：V2 已完成精简落地。默认 `REFLECTION_INTERVAL=0`（关闭周期触发）；按需配置为正整数即可启用。

---

## 决策 11：单线程执行（V1 明确约束）

V1 阶段明确为**单线程**，Supervisor 每次只调用一个 Executor。

**原因**：并行引入额外的状态同步、冲突解决复杂度。先验证基础闭环，稳定后再扩展。

**与 V3 的关系**：决策 11 指 **Supervisor 编排上不并行驱动多个 Executor会话**（一次仍走一条 `call_executor` 工具语义）。V3 可为**不同 `plan_id`** 各起独立 Executor 子进程；顺序多次派发后可有多个后台任务并存，详见决策 1 末尾「V3 实现约定」。

---

## 决策 12：MCP 工具分层与复用（V2-b 起）

**目标**：减少 Planner/Executor 工具重复定义（例如文件读取），同时保持权限边界清晰，避免规划层越权执行副作用操作。

### 1) 能力分层
- **共享只读层（MCP）**：读取文件、代码检索、文档查询等无副作用能力，作为 Planner 与 Executor 可复用能力。
- **执行副作用层（Executor-only）**：写文件、执行本地命令、外部系统写操作等，仅允许 Executor 使用。

### 2) 权限约束
- Planner 默认仅挂载只读能力（工作区文件读取、glob 搜索、正则搜索、目录树浏览 + 只读 MCP），不暴露 `write_file`、`run_local_command` 等副作用工具。
- Executor 可挂载只读 + 副作用能力，但仍受现有安全校验约束。

### 3) 与意图层 Plan 的关系
- 引入 MCP 不改变决策 3：Planner 产物仍是意图层 Plan，不在步骤中写入具体工具名。
- 复用目标是"能力接口一致"，不是"在 Plan 中显式绑定同名工具"。

### 4) 与上下文治理协同
- MCP 返回结果同样受 V2-a 的 Observation 边界策略约束（截断/外置/可选摘要）。
- 不论结果来源是本地工具还是 MCP，进入 ReAct 消息历史前都走统一规范化流程。

**最终效果**：常见只读能力一处接入、两端复用；高风险能力收敛在 Executor；减少重复实现与语义偏差。

---

## 决策 13：Executor 子进程安全与超时保护

**问题**：Executor 是独立 OS 子进程，存在两类风险：
1. 进程崩溃（网络/系统/Python 异常导致进程死亡）
2. 进程卡住（LLM 不响应或工具执行挂起）

**保护机制（三层）**：

### 1) Executor 内部超时（节点级）
- **`call_model` 节点**（`executor_call_model_timeout`，默认 180s）：单次 LLM 调用超时后抛出 `RuntimeError`，由 `_run_executor_task` 捕获为 `failed`，推送结果到邮箱后自关闭。
- **`tools_node`**（`executor_tool_timeout`，默认 300s）：工具执行超时后返回超时警告 `ToolMessage`，LLM 仍有机会基于部分结果生成摘要。不强制终止，避免丢失已有执行状态。

**原因**：LLM 不响应意味着无法继续，应视为进程级故障；工具卡住可能只是某一轮慢，给 LLM 一次总结机会更合理。

### 2) Supervisor 侧超时与崩溃处理
- **`_wait_for_executor_result`**（默认 120s）：阻塞等待结果，超时后：
  - 调用 `_cleanup_dead_executor` 终止卡住的 executor 进程（HTTP shutdown → terminate → kill）
  - 返回 `[EXECUTOR_RESULT] status=failed`，触发 `_process_executor_completion` 正确更新 state
- **executor 不可达**（进程崩溃）：探测到 `unreachable`/`not_found` 后同样构造 `[EXECUTOR_RESULT] status=failed` 并清理进程资源。
- **新增 `_cleanup_dead_executor`**：统一封装进程终止逻辑，复用 `process_manager.stop_task()`。

### 3) 主进程退出保护
- **atexit**：`_sync_cleanup` 调用 `sync_terminate()`（terminate + 3s wait → kill）。
- **信号处理**：SIGTERM/SIGINT 触发 `_sync_cleanup`，确保 Ctrl+C 或外部 kill 时子进程也被清理。
- **`sync_terminate` 升级**：从单次 `terminate()` 改为 `terminate() → wait(3s) → kill()` 升级策略。

**配置项**（`Context` 字段）：
- `executor_startup_timeout`（30s）：进程启动等待
- `executor_call_model_timeout`（180s）：单次 LLM 调用超时
- `executor_tool_timeout`（300s）：tools_node 执行超时
- `_wait_for_executor_result` timeout 参数（120s）：Supervisor 等待结果超时

以上均为 0 禁用。

---

## 决策 14：manage_executor(action="list_tasks") 时间显示格式

**问题**：LLM 对绝对时间戳（如 `22:35:17` 或 `2026-04-15T22:35:17`）缺乏直觉感知。与人类类似，LLM 对时间的理解以"多久之前"为锚点。

**决策**：`manage_executor(action="list_tasks")` 面向 LLM 的输出中，`last_updated` 列使用相对时间格式：

| 距离 | 显示 |
|------|------|
| < 5 秒 | 刚刚 |
| 5–59 秒 | N秒前 |
| 1–59 分钟 | N分钟前 |
| 1–23 小时 | N小时前 |
| 1–6 天 | N天前 |
| ≥ 7 天 | MM-DD HH:MM |

**内部存储不变**：`ExecutorTaskRecord.last_updated` 仍为 ISO 格式绝对时间戳，用于排序和计算。仅在最终输出给 LLM 时由 `_relative_time_ago()` 转换。

**原因**：LLM 通过相对时间能更准确地判断任务先后顺序和超时状态（例如"5分钟前派发但仍在 dispatched"vs"22:35派发"），辅助其决策是否需要重试或重新规划。

---

## 决策 15：Supervisor 工具面收缩 — 4 合并为 manage_executor（已实施）

**背景**：原 `stop_executor`、`check_executor_progress`、`list_executor_tasks`、`get_executor_result` 四个工具在实现上都属于 Supervisor 经 HTTP 直连 Executor 进程或查询 Mailbox 的路径。工具名多一条、模型多一次「该点哪个」的选择，且提示词中需要分别描述每个工具。

**决策**：合并为**单一工具 `manage_executor` + `Literal` 枚举参数**（`action`: `stop` | `get_result` | `check_progress` | `list_tasks`），由模型在调用时显式选择模式。先例见决策 1：`get_executor_result` 的 `detail` 参数吸收原 `get_executor_full_output` 的语义。

**实施状态**：已完成。Supervisor 从 6 核心工具（+ 2 KT）缩减为 3 核心工具（+ 2 KT），每次 LLM 请求节省约 40% 工具描述 token。

**取舍**（对 LLM 友好度无绝对优劣）：

- **拆成两枚工具**：读/写名字即约束，误触破坏性操作的概率更低；工具列表略长。
- **合并为一枚工具**：工具数少、schema 集中；必须在 docstring 中**强烈区分**只读与停止，并在实现上对 `stop` 分支做参数校验（如必填 `reason`），降低误停风险。

**结论**：默认维持拆分即可；当出现上述「冗余度 / 歧义」症状时再落地合并，并同步 `CLAUDE.md`、`prompts.py` 与序列图文档中的工具名。

---

## 决策 16：Planner 完整输出传递（reasoning + plan_json）

**问题**：此前 `run_planner()` 仅返回提取后的 Plan JSON 字符串，Planner 的分析推理（如步骤拆分理由、依赖判断、风险评估）被丢弃。Supervisor 无法理解 Planner 为何如此规划，重规划时缺少设计意图上下文。

**决策**：

### 1) PlannerOutput 数据类

`run_planner()` 返回 `PlannerOutput` 而非纯字符串：

```python
@dataclass
class PlannerOutput:
    plan_json: str   # 规范化后的 Plan JSON
    reasoning: str   # Planner 的分析推理原文（JSON 代码块之前的部分）
```

### 2) 拆分逻辑

`_split_reasoning_and_json(content)` 从 Planner 原始输出中分离：
- `reasoning`：```json``` 代码块之前的所有文字
- `json_text`：代码块内的 JSON 内容

### 3) 传递格式

`call_planner` 工具返回给 Supervisor 的格式：
```
[PLANNER_REASONING]
...推理分析...
[/PLANNER_REASONING]

{规范化 Plan JSON}
```

`dynamic_tools_node` 通过 `_split_planner_output()` 解析：
- `planner_reasoning` 存入 `PlannerSession.planner_reasoning`
- `plan_json` 存入 `PlannerSession.plan_json`（与原有逻辑一致）

### 4) PlannerSession 更新

新增字段 `planner_reasoning: str`（默认空字符串）。Executor 完成后重建 `PlannerSession` 时保留上一轮 reasoning 不变。

**原因**：Supervisor 看到推理后能更好地调度（理解步骤设计意图、判断并行可行性、做更精准的重规划），同时 Plan JSON 仍作为系统级主数据独立存储和传递。

---

## 决策 17：parallel_group 并行执行标注

**问题**：某些任务中多个步骤之间无依赖关系，可以并行执行以减少总耗时。此前 Plan JSON 中无并行标注，Supervisor 只能顺序派发。

**决策**：

### 1) Plan step 新增可选字段

```json
{
  "step_id": "step_2",
  "intent": "...",
  "expected_output": "...",
  "parallel_group": "group_a"
}
```

- `parallel_group` 为 `null`（默认）时：顺序执行
- `parallel_group` 为非空字符串时：同组步骤可并行执行

### 2) 标注职责

- **Planner** 负责判断步骤间依赖关系并标注 `parallel_group`
- **Supervisor** 负责根据标注将同组步骤拆为独立子任务，用 `call_executor(task_description, wait_for_result=false)` 并行派发
- Planner 仅在**确信步骤完全独立**时才标注并行组

### 3) 规范化处理

`_normalize_plan_json()` 确保每个 step 都有 `parallel_group` 字段（缺失时补 `null`），与 `step_id`、`status` 等字段处理方式一致。

### 4) 与现有并行机制的关系

此决策与决策 1 中 `wait_for_result=false` 异步派发机制互补：
- `wait_for_result=false` 是 Supervisor 的执行能力（如何并行派发）
- `parallel_group` 是 Planner 的规划能力（哪些步骤应并行）

Supervisor 提示词明确指导：当计划中有 `parallel_group` 时，将同组步骤拆为 Mode 2 子任务并行派发；无标注的步骤用 Mode 3 同步执行。

**原因**：将并行性判断交给规划层（Planner 更理解步骤语义），而非让 Supervisor 从步骤文本中猜测依赖关系，减少误判。

---


## 决策 18：知识树两层存储 + Overlay 架构

知识树采用**两层存储 + 轻量 Overlay**架构，以文件系统为核心：

| 层 | 载体 | 职责 |
|----|------|------|
| Layer 1 | 文件系统（Source of Truth + 结构） | 目录层级 = 父子关系；Markdown 文件 = 知识节点；人类可读写、git 可版本化 |
| Layer 2 | 向量索引（内存） | stored_vector = α·content_embedding + β·structural_vector；同目录文件聚簇 |
| Overlay | 轻量 JSON 文件 | 跨目录关联边（is_primary=False），表达多领域归属 |

**与旧方案（三层分离）的关键区别**：
- 旧方案：独立 Graph 数据库存 DAG 边 + Markdown 存内容 + 向量索引，三层需同步
- 新方案：文件系统目录层级 = primary 父子关系，干掉独立 Graph 层，消除同步负担
- 向量不再是纯内容嵌入，而是 content + structural 混合向量
- 结构变更只需移动文件 + 重算向量，不需要维护 Graph ↔ Markdown 一致性

**原因**：三层分离引入了不必要的同步复杂度。文件系统天然支持层级结构和持久化，直接用目录作为树结构消除了 Graph 层与 Markdown 层不一致的风险。Overlay JSON 仅处理少量跨目录关联，不承担主结构职责。

---

## 决策 19：（废弃）P1 图数据库选型——Kùzu

> 已废弃。文件系统替代 Graph 层，不再需要独立图数据库。
> `BaseGraphStore` / `InMemoryGraphStore` / `KuzuGraphStore` 相关代码可删除。
> 未来如需图查询能力（P3+），可基于 Overlay JSON 扩展或引入 DuckDB。
>
> **现行方案**：文件系统即树结构，见 [v4-kt-core-design.md](v4-kt-core-design.md) 与决策 20。

---

## 决策 20：文件系统即树结构——主路径与目录层级

知识树的**主结构**直接由文件系统目录层级表达，无需独立 DAG。

### 1) 目录 = primary 父子关系

```
knowledge_tree/              ← 根节点
  development/               ← 中间节点（目录）
    debugging.md             ← 叶子节点（文件）
    async_pattern.md
  skills/
    code_review.md
    scripts/
      review.sh              ← 可执行代码（被 .md 引用）
```

- 目录包含关系 = `is_primary=True` 的父子边
- 每个文件/目录有且仅有一个父目录 = 单亲树

### 2) 跨目录关联 = Overlay JSON

一个知识点可能属于多个领域（如"调试技巧"同时属于"Python"和"Agent 工作流"）。
这类 `is_primary=False` 的关联边存储在轻量 Overlay JSON 中。

### 3) node_id = 文件相对路径

节点 ID 直接使用文件相对于知识树根目录的路径（如 `development/debugging.md`），
天然唯一且包含结构信息，不需要 UUID。

**原因**：文件系统的目录包含关系天然就是有向无环树。用目录层级替代 Graph 层的 primary edges，消除了"两份数据必须保持一致"的工程负担。node_id 用路径则不需要额外的 ID↔路径映射。

---

## 决策 21：检索策略——RAG 优先，手动搜索兜底

知识树采用**RAG 快速检索优先，Agent 手动搜索文件系统兜底**的双阶段检索。

### 1) 检索流程

```
Agent 需要知识
  → ① RAG 向量检索（stored_vector 相似度）
  → ② 满意？拿走结束
  → ③ 不满意？Agent 手动搜索文件系统
```

### 2) RAG 检索

- 查询文本 → content_embedding（P1）/ stored_vector（P2）
- 与所有文件的向量计算余弦相似度
- 超过阈值（默认 0.7）返回 Top-K 结果
- P2 的 stored_vector 含 structural 信息，同目录文件更容易被一起召回

### 3) Agent 手动搜索

RAG 不满意时，Agent 使用现有工作区工具直接搜索文件系统：
- `list_workspace_entries` — 列目录结构
- `read_workspace_text_file` — 读文件内容
- `search_files` — glob 模式搜索
- `grep_content` — 正则内容搜索

Agent 根据每一步观察自主决策下一步，不是盲目遍历。
如果 README.md 存在，Agent 可先读摘要快速定位；不存在也不影响搜索。

### 4) 检索日志

每次检索记录结构化日志：查询、结果、Agent 满意度、是否触发了手动搜索。
日志是优化闭环的数据燃料。

**原因**：RAG 提供快速语义匹配，手动搜索提供精确结构导航。两者互补而非竞争。不再需要 LLM 路由树导航（旧方案的 router.py），因为文件系统的目录结构对 Agent 来说是直接可读的。

---

## 决策 22：（重定义）编辑 → Agent 主动重组

> 旧决策 22（Change Mapping / JSON Patch）已不适用于新架构。
> 编辑操作改为 Agent 通过编号树重组表达结构意图，系统自动执行。
>
> **现行方案**：`edit_file` 工具 + 编号树重组，见 [CLAUDE.md](../CLAUDE.md) §I/O 契约 与 [kt-subsystems.md](kt-subsystems.md) §editing 子包。

### 1) P2 重组机制

**Step 1**：系统展示带编号的当前目录树

```
01 development/
    01 debugging.md
    02 async_pattern.md
02 skills/
    01 code_review.md
03 domain/
    01 architecture.md
    02 design_decisions.md
```

**Step 2**：Agent 输出重组后的带编号目录树

Agent 按自己的理解输出新结构。编号体现 Agent 认为的关联性。

**Step 3**：系统自动执行

1. 解析新旧结构差异（文件移动、目录合并/拆分/创建）
2. Python 程序在文件系统中实际执行移动、创建、删除
3. 从位置变化提取关系信号：
   - 被放到同一目录 → 强关联
   - 保持在一起 → 确认关联
   - 被分开 → 弱化关联
4. 重算受影响目录的锚点
5. 更新被移动文件的 structural_vector 和 stored_vector

### 2) 向量调整

重组后向量自动跟随结构更新：
- 目录锚点从 content_embedding 推导（不变量）
- structural_vector 更新为新目录锚点
- stored_vector 重算 = α·content + β·new_structural

### 3) P1 限制

P1 不含重组工具。Agent 只能通过直接操作工作区文件来手动调整（与 Executor 配合）。
P2 引入 `knowledge_tree_reorganize` + `knowledge_tree_apply_reorganization` 工具。

**原因**：Agent 不直接写文件系统命令，而是表达结构意图（新的编号树）。系统解析差异后自动执行，保证文件操作的正确性和原子性。从位置变化提取的关联信号反馈到向量空间，实现"结构变更 → 向量调整"的闭环。

---

## 决策 23：异步优化闭环与防震荡

知识树通过检索信号驱动优化，并设防震荡机制。

### 1) 优化信号

| 信号类型 | 触发条件 | 优化动作 | 优先级 |
|----------|----------|----------|--------|
| 整体失败 | RAG + 手动搜索均无结果，累积达阈值 | Agent 创建新节点/目录 | 1（最高） |
| 检索不满意 | RAG 返回结果但 Agent 标记不满意 | 记录信号，供重组参考 | 2 |
| 目录内方差过高 | 同目录文件 content_embedding 差异过大 | Agent 考虑拆分目录 | 3 |
| 内容不足 | 找到文件但内容不充分 | Agent 更新文件内容 | 4（最低） |

### 2) 防震荡

- 每种信号独立阈值
- 全局频率上限（总优化动作限额）
- 优先级排序

所有优化动作异步批量执行，不阻塞检索路径。

**原因**：4 种信号覆盖检索失败的主要模式。防震荡防止过度优化导致树结构不稳定。

---

## 决策 24：P1 信息范围——领域知识

P1 阶段知识树仅承载**领域知识**。

### 1) 叶子节点

对应文件系统中的一个 Markdown 文件：
- `node_id`：文件相对路径
- `title`：文件标题
- `content`：文件正文
- `source`：来源标识
- `created_at`：创建时间

### 2) 分阶段扩展

| 阶段 | 信息类型 | 新增能力 |
|------|---------|---------|
| P1 | 领域知识 | 基础 Markdown 文件 |
| P2 | Agent 记忆 | 衰减分数、访问计数 |
| P3 | 技能/Skill + 参考资料 | 可执行脚本绑定、外部链接 |

### 3) 可执行代码

叶节点目录下可包含可执行脚本，被同目录 Markdown 引用和解释。
Agent 检索到该知识时可直接使用脚本，无需自己编写。

**原因**：领域知识结构性强、边界清晰。碎片化记忆和技能的引入需要树结构先稳定运行。

---

## 决策 25：知识树定位——Supervisor 内嵌模块

知识树作为 Supervisor 内嵌组件，物理位于 `src/common/knowledge_tree/`，通过工具注册暴露。

### 1) 模块定位

- 不是独立 Agent，不引入新子图
- 不是独立服务，不启动额外进程
- 是共享基础设施，类似 `src/common/tools.py` 的定位

### 2) 工具注册

```python
# src/supervisor_agent/tools.py
if runtime_context.enable_knowledge_tree:
    from src.common.knowledge_tree import build_knowledge_tree_tools
    tools.extend(build_knowledge_tree_tools(runtime_context))
```

P1 对外工具：`knowledge_tree_retrieve`、`knowledge_tree_ingest`（初始化与状态检查为内部能力，不暴露为 Supervisor 工具）
P2 新增：`knowledge_tree_reorganize`、`knowledge_tree_apply_reorganization`

### 3) 配置

通过 `Context` dataclass 添加字段，遵循现有 env-var 覆盖模式。

### 4) 包结构

```
src/common/knowledge_tree/
    __init__.py          # KnowledgeTree 门面类
    config.py            # KnowledgeTreeConfig
    bootstrap.py         # 种子目录建树
    storage/
        markdown_store.py  # 文件系统读写
        vector_store.py    # 向量索引 + 目录锚点
        overlay.py         # Overlay JSON 关联边
        sync.py            # 文件系统 → 向量派生
    dag/
        node.py            # KnowledgeNode
    retrieval/
        rag_search.py      # RAG 检索
        log.py             # 检索日志
    ingestion/
        chunker.py, filter.py, ingest.py
    editing/
        re_embed.py        # 重嵌入
    optimization/
        signals.py         # 信号检测（P3）
```

**原因**：内嵌模块定位最轻量，不改变现有架构拓扑。条件注册通过 feature flag 控制。

---

## 决策 26：知识摄入管道——Agent 运行时新知识入树

### 核心问题

Agent 执行中产生的新知识需要自动回流到知识树，否则树会过时失效。

### 管道设计

```
事件触发 → 原子切分 → 轻量过滤 → 向量去重 → 增量嫁接 → 目录锚点更新
```

#### 1. 事件触发

| 触发源 | 时机 | 内容 |
|--------|------|------|
| 任务完成 | `ExecutorResult.status == "completed"` | summary + observations |
| 用户显式指令 | 用户说"记住/记下来" | 指定内容 |
| 任务失败（P2） | `ExecutorResult.status == "failed"` | failure_reason |

#### 2. 原子切分

- 粒度 < 512 tokens
- P1：按 `\n\n` + 对话轮边界
- P2：SemanticChunker

#### 3. 轻量过滤

规则判断"是否值得记忆"，**宁缺毋滥**（V4 hardening 起改为严格策略，见决策 29）：
- 含决策/结论关键词（发现/重要/规则/失败/超时等）
- 含数字 + 合理长度（> 20 字）
- 技术内容模式（URL/路径/代码/技术术语）
- 用户显式指令始终通过；task_complete 需关键词 + len > 15

#### 4. 向量去重

`vector_store.search(top-1)` 检查相似度：
- > 0.95 → 跳过
- 否则 → 进入摄入

#### 5. 增量嫁接

```
for each candidate:
    embed → content_embedding
    search → 找最相似的目录锚点
    if similarity > threshold:
        放入该目录，局部更新锚点
    else:
        创建新目录，挂到语义最近的父目录下
    更新向量索引
```

#### 6. 来源元数据

```python
source="agent:supervisor"
metadata={"plan_id": "xxx", "trigger": "task_complete", "filter_confidence": 0.8}
```

### 新增配置字段

| 字段 | 默认值 | 说明 |
|------|--------|------|
| `kt_ingest_chunk_max_tokens` | 512 | 切分粒度 |
| `kt_dedup_threshold` | 0.88 | 去重阈值（0.88 = 结构高度相似即合并，保留语义差异节点）|
| `kt_ingest_enabled` | True | 管道开关 |

### 集成方式

P1：Supervisor 在 `call_executor` 结果处理中内部调用。
P2：暴露 `knowledge_tree_ingest` 工具让 Agent 自主判断。

### 完整闭环

```
Agent 执行任务 → 产出新知识
      ↓
摄入管道（切分 → 过滤 → 去重 → 嫁接）
      ↓
知识树增长 → 检索日志积累
      ↓
优化信号 → Agent 重组树结构
      ↓
文件移动 → 向量调整 → 检索质量提升
      ↓
检索时命中新知识 ←── Agent 自主整理 ←── 优化闭环
```

**原因**：知识树若无新内容入口，初始 bootstrap 后即成死树。摄入管道是"涌现"的核心——Agent 执行中产生的知识自动回流，再经重组工具自主整理，形成自进化闭环。

---

## 决策 27：Supervisor 等待 Executor 结果超时配置化

**背景**：原 `_wait_for_executor_result` 硬编码 120s 超时，而 `executor_call_model_timeout` 为 180s。当 Executor 的 LLM 调用耗时超过 120s 但未达 180s 时，Supervisor 会提前终止 Executor 子进程，导致任务失败。

**决策**：新增 `executor_wait_timeout` 配置字段（`Context` dataclass），默认值 300s，由 `call_executor` 和 `manage_executor(action="get_result")` 统一使用。

**约束**：`executor_wait_timeout` 应 **大于** `executor_call_model_timeout`（180s），否则仍会出现提前终止。

**实现**：

```
Context.executor_wait_timeout: float = 300.0
  ↓
call_executor(wait_for_result=True)
  → _wait_for_executor_result(timeout=runtime_context.executor_wait_timeout)
  ↓
manage_executor(action="get_result")
  → _wait_for_executor_result(timeout=runtime_context.executor_wait_timeout)
```

**超时层级关系**（从内到外）：

| 超时 | 默认值 | 作用域 |
|------|--------|--------|
| `executor_call_model_timeout` | 180s | Executor 子进程内单次 LLM 调用 |
| `executor_tool_timeout` | 300s | Executor 子进程内 tools_node 执行 |
| `executor_wait_timeout` | 300s | Supervisor 等待 Executor 结果返回 |

**原因**：超时值必须由内到外递增——内层先超时并优雅降级，外层再超时才终止进程。硬编码的外层超时小于内层，等同于人为制造竞态。配置化后可通过环境变量 `EXECUTOR_WAIT_TIMEOUT` 按需调整。

---

## 决策 28：KT 元规则治理

**背景**：元规则通过系统提示通道以"必须遵守"的方式注入，绕过 RAG 质量过滤。压力测试表明：20 条矛盾元规则导致 Supervisor 推理崩溃（幻觉 + 超时），20 条元规则 + 50 条事实洪水导致完全崩溃。

**根因**：元规则通道标记为"必须严格遵守的硬约束"。当硬约束互相矛盾时（如"禁止使用工具" vs "每次必须调用所有工具"），LLM 面临不可满足的约束满足问题——注意力分散、概率分布均匀化、工具瘫痪。

**决策**：四层治理架构，从存储到注入逐层收紧：

### 第 1 层：存储层硬限制

| 机制 | 位置 | 作用 |
|------|------|------|
| `MAX_META_RULES = 15` | `config.py` | 添加时计数检查，到达上限拒绝创建 |
| 冲突 warning | `tools.py` `_sync_add_meta_rule()` | 新规则与已有规则 embedding 相似度 > 0.7 时返回 warning（不阻断） |
| 种子上限 | `bootstrap.py` `seed_meta_rules()` | 启动时种子加载也遵守上限 |

### 第 2 层：注入时矛盾消解

`graph.py` `_resolve_meta_rule_conflicts()` 在每次注入前执行：

1. **别名互斥分组**：共享至少一个 alias 的规则归入同一互斥组（BFS 连通分量）
2. **优先级仲裁**：互斥组内优先级不同 → 保留最高优先级
3. **同优先级全抑制**：互斥组内最高优先级相同 → **全部抑制**，注入 neutral note `[同优先级矛盾已抑制（别名列表）→ 使用默认行为]`
4. **无别名规则**：始终保留

**为什么不保留同优先级中的某一条**：任意选择比不选择更危险——"禁止工具" vs "必须工具"选哪条都是错的。抑制后 LLM 使用默认行为，反而是最安全的路径。

### 第 3 层：感知层

| 机制 | 位置 | 说明 |
|------|------|------|
| 消解报告 | `kt_retrieve()` | `[消解: 15→1 条（14 条矛盾已抑制）]` 前缀注入 |
| RAG `[矛盾]` 标签 | `kt_retrieve()` | 检索结果 title 相似度高 + content 相似度低时标注 |
| Header 软化 | `call_model()` | 消解后用"互不矛盾，请遵守"替代"必须严格遵守" |

### 第 4 层：自救工具

`knowledge_tree_delete_meta_rule(title)` — Supervisor 到达上限后可按标题删除旧规则释放空间。

**治理流程**：

```
用户添加元规则
  → 存储层：计数 < 15？→ 否：拒绝
  → 存储层：冲突检测 → warning（不阻断）
  → 每次 LLM 调用前：
      → 注入层：别名互斥消解 → 同优先级全抑制
      → 感知层：消解报告 + 矛盾标签
      → Header：软化措辞
  → Supervisor 自救：delete_meta_rule 释放空间
```

**压力测试验证**：

| 测试级别 | 条件 | 治理前 | 治理后 |
|----------|------|--------|--------|
| L2 | 20 条矛盾元规则 | 幻觉（声称文件已创建但未调用工具） | 15 条上限 + 消解 → 无幻觉 |
| L3 | 50 条事实洪水 | 完全崩溃（首条查询超时） | 慢但正确 |
| L6 | 15 规则 + 20 事实 + 溢出 | 不可达 | 12/12 全通过（deepseek-v4-pro） |

**已知局限**：

1. 无别名规则不参与消解——两条矛盾规则如果没有共享 alias，都会被注入
2. 别名由创建者指定——攻击者可以故意使用不同 alias 绕过互斥分组
3. `MAX_META_RULES = 15` 是经验值，不同模型崩溃阈值不同
4. 消解后注入的 neutral note 仍占用系统提示 token

---

## 决策 29：V4 知识树加固——摄入质量门槛提升

**背景**：压力测试暴露 KT 摄入管道存在"垃圾进垃圾出"问题。自动摄入（Entry A）使用"宁多勿漏"策略，导致通用模板文本（"所有步骤执行完成"、"执行成功"）和低信息量 Executor 输出大量涌入知识树，污染检索结果。同时，经验提取器对所有失败任务无条件提取经验，包括测试框架错误（TypeError、mock 相关）等非项目知识。

**决策**：摄入管道从"宁多勿漏"切换为"宁缺毋滥"，具体措施：

### 1. Filter 质量门槛提升

`filter.py` 策略变更为严格模式：

| 变更 | 旧策略 | 新策略 |
|------|--------|--------|
| 总体策略 | 宁多勿漏 | **宁缺毋滥** |
| `task_complete` | 无条件通过 | 需关键词 + len > 15，或 len > 100 |
| `has_number` | 无条件通过 | 需 len > 20 |
| `sufficient_length` | > 50 字 | > 100 字 |
| 通用模板 | 无检测 | 正则匹配 → 直接过滤 |
| 低信息量 | 无检测 | "成功列出/执行/完成…目录/文件" → 过滤 |
| 重复任务描述 | 无检测 | "步骤 step_N 完成/成功" → 过滤 |
| 技术内容 | 无检测 | URL/路径/代码/JSON/技术术语 → 独立通过路径 |

新增检测模式：

- `_GENERIC_PATTERNS`：匹配"所有步骤执行完成"等通用模板
- `_LOW_VALUE_PATTERNS`：匹配"成功列出了 X 目录下所有文件"等低信息量文本
- `_REDUNDANT_TASK_PATTERNS`：匹配"步骤 step_1 完成"等重复任务描述
- `_TECHNICAL_PATTERNS`：匹配 URL、函数调用、文件路径、exit code、环境变量、技术缩写

### 2. 经验提取质量门控

`extractor.py` `extract_experience_from_executor_result()` 增加：

- **信息密度检查**：goal < 5 字且 intents < 10 字 → 不提取（经验无实际价值）
- **框架错误过滤**：failure_reason 含 mock/TypeError/await 等测试框架关键词 → 不提取
- **发现性内容检查**：completed 状态需要 summary 或 result_summary 含发现性关键词（发现/确认/需要先/必须/关键/导致…原因）
- **最小长度检查**：completed 状态 combined text < 50 字 → 不提取

### 3. RAG 矛盾密度截断

`graph.py` `kt_retrieve()` 增加矛盾密度检测：

- 当 top-3 检索结果中 > 50% 互相矛盾时，截断到 top-1（避免注入噪声）
- 检索质量阈值提升：semantic 0.60→0.65，hash 0.25→0.35
- 高可信标记阈值提升：semantic 0.65→0.75，hash 0.50→0.55

### 4. 知识树垃圾清理

`workspace/knowledge_tree/` 从 73 文件清理至 25 文件，删除：
- 压力测试产生的垃圾节点（executor_crashed、step_1 等）
- 4 条压力测试元规则（强制完整对话存储、强制英文回复、禁止 Ingest、错误即停）
- 零价值自动摄入产物

### 效果

- 980 unit tests 全通过
- 知识树节点数从 73 降至 25（清理率 66%）
- 压力测试 L3 从 1/2 提升至 5/6，L5 从 0/1 提升至 8/8
- 摄入管道过滤效率显著提升：通用模板、低信息量文本不再进入知识树

**原因**：KT 的核心价值在于检索质量。宁多勿漏策略在初期验证阶段可行，但压力测试表明低质量知识的危害远大于漏掉少量有价值知识的损失——一条垃圾知识可能污染整类检索结果。"不添乱"是"有价值"的前提。

---

## 决策 30：三 Agent LLM 调用超时保护

**背景**：V4 端到端长对话测试（17 轮次，10 个维度）暴露了外部 API 冷启动导致的系统性阻塞。测试首轮 Supervisor 首次 LLM 调用耗时 258.6s（正常 < 5s），导致 Mode 3 链路（Supervisor→Planner→Executor 多步调用）总时间超过 300s turn_timeout 而超时失败。

**根因分析**：

| 证据 | 数值 | 说明 |
|------|------|------|
| R1-T1（Mode 1 纯问答） | 258.6s | 单次 Supervisor LLM 调用，kimi-k2.6 冷启动 |
| R2-T7（Mode 1 纯问答，API 已热） | 5.6s | 同类型调用，46 倍差距 |
| R1-T4（Mode 3 超时） | 300.1s | 258s 耗在首次 call_model，剩余 42s 不够完成 Planner+Executor |
| 当前 API 正常延迟 | 1.8-5.5s | 非系统性问题，仅冷启动时触发 |

Executor 侧已有 `executor_call_model_timeout=180s` 保护（决策 13），但 **Supervisor 和 Planner 的 LLM 调用完全没有超时保护**——外部 API 卡住时整个系统无限挂起。

**决策**：三 Agent 统一配置独立的单次 LLM 调用超时，并增加 API 预热机制。

### 1. 超时配置

新增两个 Context 字段：

| 字段 | 默认值 | 作用域 |
|------|--------|--------|
| `supervisor_call_model_timeout` | 120s | Supervisor `call_model()` 内单次 LLM 调用 |
| `planner_call_model_timeout` | 120s | Planner `call_planner()` 内单次 LLM 调用 |
| `executor_call_model_timeout` | 180s（已有） | Executor 子进程内单次 LLM 调用 |

三者均支持 0 禁用。完整超时层级（由内到外）：

| 超时 | 默认值 | 作用域 |
|------|--------|--------|
| `supervisor_call_model_timeout` | 120s | Supervisor 单次 LLM 调用 |
| `planner_call_model_timeout` | 120s | Planner 单次 LLM 调用 |
| `executor_call_model_timeout` | 180s | Executor 单次 LLM 调用 |
| `executor_tool_timeout` | 300s | Executor tools_node 执行 |
| `executor_wait_timeout` | 300s | Supervisor 等待 Executor 结果 |
| chat.py `turn_timeout` | 用户配置 | 单轮对话总超时 |

### 2. 超时行为差异

| Agent | 超时行为 | 原因 |
|-------|---------|------|
| Supervisor | 返回友好提示消息，不崩溃 | 直接面向用户，需优雅降级 |
| Planner | 抛 `RuntimeError` | 由 Supervisor `call_planner` 工具捕获并决定重试或告知用户 |
| Executor | 终止子进程 | 独立进程，无状态可保留 |

### 3. API 预热（chat.py）

启动时对三个模型各发一次 `ainvoke("Hi")`，触发 API 连接建立和模型加载。

- 预热结果实时显示（模型名 + 耗时），方便诊断 API 状态
- `--no-warmup` 参数跳过预热（脚本模式 / 调试时使用）
- 预热本身有 60s 超时保护

### 4. 可观测性

`invoke_chat_model` 新增耗时日志：`LLM ainvoke completed in X.Xs`，每次调用自动记录，便于定位慢请求。

### 效果

- 冷启动 258.6s → 预热后 2.5s（100 倍改善）
- 即使预热后 API 仍慢，Supervisor 也会在 120s 后优雅返回而非无限挂起
- 1130 unit tests 全通过
- E2E 测试 16/17 轮次通过（唯一失败由本决策修复）

**原因**：外部 API 的响应时间不受系统控制。冷启动、限流、网络抖动都可能导致单次调用从 2s 飙到 250s+。没有超时保护意味着系统正确性完全依赖外部服务的稳定性——这是不可接受的。三层防护（预热消除 → 逐调用超时 → 可观测日志）将外部不可控因素隔离在系统边界之外。

**关联决策**：决策 13（Executor 超时保护）、决策 27（Executor 等待超时配置化）。

---

## 决策 31：Supervisor mode 纪律——strip 冗余 tool_calls

> ⚠️ **已撤销（2026-07-03）**：07-01+07-02 两次探测中谓词触发 0 次（mode1 时 content 完整但 tool_calls=[]，谓词不适用；mode2 时 content 为空，content<80 不命中）。底层 mode 路由脱节问题已被更深的 N4（LLM content/tool_calls 解耦）取代。strip 逻辑、`_looks_like_final_answer`、`_FINAL_STRUCT_RE`、`_PROCESS_MARKERS`、env `SUPERVISOR_STRIP_REDUNDANT_TOOL_CALLS`、测试 `test_mode_discipline.py` 均已删除。N4 修复见 `docs/n4-diagnosis-result.md` / 实施计划 `docs/superpowers/plans/2026-07-03-probe-followup-fixes.md` Task 1。原决策文本保留于下，仅供历史溯源。

**背景**：2026-06-29 夜间探测（`docs/probe-analysis-2026-06-29.md`，69 turns / 7 sessions）发现 P0——LLM 在中间轮已写出完整最终答案（mode-1 语义）时，同一响应仍带 `call_executor` tool_calls。`route_model_output` 只看 tool_calls 是否为空，无条件路由到 tools 节点执行，导致本应直接结束的请求被路由到 Executor 链路。

**根因分析**（详见 `docs/p0-beta-diagnosis-2026-06-30.md`）：

| 证据 | 数值 | 说明 |
|------|------|------|
| s002-t5 "查找 timeout 配置" | 182s | 完整 markdown 答案 + `mode=1`，但 thread 累计 16 个工具调用（含本轮 `call_executor`） |
| 复现 trigger "Executor 都有哪些内置工具" | 181s | `mode=1`，中间 3 次工具执行 |
| Mode 1 纯推理路径（s003/s007） | 35 turns / 0 bad | 证明 LLM 能力本身无退化 |

`route_model_output`（`graph.py:1408`）与 `_infer_supervisor_decision`（`graph.py:1353`）**完全解耦**——前者只看 tool_calls 是否为空，后者纯 tool_calls 驱动推断 mode，两者都不看 content 语义。LLM "想 mode A 却调 Executor" 是行为纪律问题，prompt 治不了——必须在路由层硬约束。

**决策**：在 `call_model` LLM 响应返回后、`_infer_supervisor_decision` 之前，加 content 语义判别 + strip 冗余 tool_calls（方案 2）。

### 1. 判别函数 `_looks_like_final_answer`

判别信号组合（基于 9 个 probe 真实样本提炼，参数化测试覆盖）：

| 信号类型 | 内容 | 命中后行为 |
|---------|------|-----------|
| 必要条件 | 长度 ≥ 80 字符 **AND** 含 markdown 结构（`##` / 表格 / 有序无序列表） | 不满足 → False |
| 风险黑名单 | 过程性措辞（`接下来`/`我将`/`让我先`/`恢复后`/`我可以帮你`）/ 内部标记（`[PLANNER_REASONING]`/`[EXECUTOR_RESULT]`/`[EXECUTOR_DISPATCH]`/`[STALE]`） | 任一命中 → False |
| Mode-3 短路 | `tool_calls` 含 `call_planner` | 命中 → False |

### 2. call_model 插入逻辑

```python
if response.tool_calls and _looks_like_final_answer(response.content, response.tool_calls):
    if os.getenv("SUPERVISOR_STRIP_REDUNDANT_TOOL_CALLS", "1") != "0":
        logger.info("[MODE-DISCIPLINE] strip tool_calls=%s content_len=%d", ...)
        response = response.model_copy(update={"tool_calls": []})
```

strip 后 `route_model_output` 看到 `tool_calls=[]` → 自然走 `__end__`。

### 3. 灰度与回滚

- env `SUPERVISOR_STRIP_REDUNDANT_TOOL_CALLS` 默认 `"1"`（开启）
- 设 `"0"` 即时关闭，每轮读 env，无需重启
- 上线第 1 周观测：grep `[MODE-DISCIPLINE]` 统计 strip 频次；单 thread >2 次 strip 说明判别偏松

### 效果

- s002-t5 类场景：182s → 预期 < 30s（无需启动 Executor 子进程）
- 210 supervisor 单元测试全通过（含 10 个新增 mode-discipline case）
- 不影响 mode 2/3 正常路径（`call_planner` 短路 + 过程词黑名单保护中间 ReAct 轮）

**原因**：mode 推断与工具调用路由是两个独立机制——前者事后打标签，后者只看 tool_calls 是否为空。LLM 在中间轮的"行为纪律"无法靠 prompt 解决（trigger 词覆盖规则），必须在路由层用 content 语义判别 + strip。方案 2（`call_model` 内 strip）比方案 1（`route` 内短路）更稳——后者依赖 `_infer_supervisor_decision` 反转，但该函数纯 tool_calls 驱动，短路条件永远不成立。

**关联决策**：决策 8（Supervisor 三种回复模式）、决策 4（Executor 遇阻即停）。

---

## 决策 32：Entry A Provenance Tagging — 失败教训标记

**背景**：夜间探测（`docs/probe-analysis-2026-06-29.md` §九）发现 P1-β：Executor 失败输出（假阴性）被 Entry A 自动摄入 KT，形成错误记忆自我强化循环。s002-t5 案例完整链路：Executor unreachable → Supervisor 基于失败结果报假阴性（声称 `.env.example` 和 `config/` 不存在，实际均存在）→ 假阴性被自动归档 → 下次检索被 `[相关知识]` 注入 → Supervisor 基于错误记忆决策。

**根因分析**：

| 证据 | 位置 | 说明 |
|------|------|------|
| 摄入时 status 丢失 | `graph.py:1157-1176` 两处 `kt.ingest` | `_try_auto_ingest_executor_result` 接收 `exec_status` 但没传给 `kt.ingest` |
| inject 路径不看 metadata | `graph.py:368-373` 注入循环 | 只看相似度 score 决定 tag，完全没用 `node.metadata` |
| KT 节点已有 metadata dict | `dag/node.py:32` | `metadata: dict[str, Any]` 是自由 dict，零迁移扩展 |

`completed` 和 `failed` 在摄入元数据层完全无区分——这是 P1-β 根因。

**决策**：A（摄入层加 metadata）+ C（inject 层加 `[失败教训]` tag）组合。

### 1. 摄入层：`metadata={"executor_status": exec_status}`

普通 chunk 摄入（`graph.py:1157-1163`）：
```python
kt.ingest(chunk, trigger="task_complete", source="auto:executor",
          metadata={"executor_status": exec_status})
```

经验节点摄入（`graph.py:1169-1176`）：保留 `node_type` + 加 `executor_status`：
```python
kt.ingest(exp, trigger="task_complete", source="auto:executor_experience",
          metadata={"node_type": "experience", "executor_status": exec_status})
```

### 2. inject 层：`[失败教训]` tag 前缀

`graph.py:368-373` 注入循环加判别：
```python
exec_status_meta = node.metadata.get("executor_status") if node.metadata else None
if exec_status_meta == "failed":
    tag = "[失败教训]" + tag
```

tag 累积顺序：`[失败教训]` + `[矛盾]` + `[高可信]/[参考]`。

**tag 选择理由**：`[失败教训]` 比 `[低可信]` 更准确——节点是真实记录但源自失败执行；`[未验证]` 语义不准（其实被记录了）。

### 3. 不做 B（检索层调相似度阈值）

留作后续观察期优化——需要调参周期，且需要先观测 `[失败教训]` tag 出现频率再决定是否加严。

### 效果

- 6 个新增单元测试 + 既有 entry A 集成测试全通过
- 已存 KT 节点（无 metadata）安全降级——`.get()` 返回 None，按普通节点处理
- 失败教训节点在 inject 时显式标记，Supervisor 不会再当事实引用

**原因**：Executor 失败的假阴性是 LLM 基于 unreachable 状态的推测，不是真实事实。把它们无差别摄入 KT 会让"错误记忆"在后续检索中持续污染决策。在摄入层加 source 标记 + inject 层加 tag 是双层防护——前者提供数据基础（零迁移成本），后者即时止血（LLM 看到标记不再当事实）。

**关联决策**：决策 26（知识摄入管道）、决策 6（Session 同步，Entry A 摄入时机）。

---

## 决策 33：Thread Bricked 自愈 — MAX_REPLAN 早返回重置状态

**背景**：夜间探测（`docs/probe-analysis-2026-06-29.md` §七）发现 P1-α：MAX_REPLAN 触发后 thread 永久 bricked。3 个 session（s001/s004/s006）因同一模式 bricked，共浪费 12 turns（17%）。即使决策 31 修了 P0-α 上游放大器（减少进入 MAX_REPLAN 的频率），一旦进入 MAX_REPLAN，thread 仍然不可恢复。

**根因分析**：

| 证据 | 位置 | 说明 |
|------|------|------|
| 早返回 guard 永不重置 | `graph.py:443-464` | guard 条件基于 `last_executor_status=="failed"` + `replan_count >= max_replan`，但 return dict 不写这两个字段 |
| 下一轮 deterministic 早返回 | `call_model` 入口 | guard 条件仍然成立 → 直接返回固定 AIMessage，不到 LLM 调用 |
| "2.3s 秒回 byte-identical" | probe s001-t5 / s004-t3-5 | 不是 LLM 推理结果，是常量字符串 |

`call_model` 早返回分支只读 guard 条件、不写"已处理"标志——这是 P1-α 根因。下一轮 user message 进入时 guard 仍命中，LLM 永远拿不到新用户消息。

**决策**：在 MAX_REPLAN 早返回的 return dict 里加状态重置。

### 1. return dict 加 `replan_count=0` + `planner_session`

```python
import dataclasses  # 顶部新增

# 早返回分支
reset_session = dataclasses.replace(
    state.planner_session, last_executor_status=None
)
return {
    "messages": [AIMessage(...)],
    "supervisor_decision": decision,
    "replan_count": 0,                  # 新增：重置计数器
    "planner_session": reset_session,   # 新增：清理 last_executor_status
}
```

### 2. 为什么安全

- MAX_REPLAN 触发轮已经汇报用户失败（AIMessage 含 "执行已多次失败..."），用户看到完整信息
- 重置 `replan_count=0` 和 `last_executor_status=None` 不影响本轮返回的 messages
- 下一轮 user message 进入时 guard 不命中 → 正常走 LLM 分支
- `dataclasses.replace` 只覆盖 `last_executor_status`，其他 PlannerSession 字段（`plan_json` / `planner_history_by_plan_id` 等）保留——与 `_build_executor_updates` 同样的修改模式

### 3. 不做的事

- **不引入** 新的 state 字段（如"已汇报"标志）——重置 guard 条件本身更直接
- **不加** `manage_executor(reset_thread)` 工具——bricked 时 LLM 根本不被调用，工具没用
- **不动** probe 客户端——修源码 bug 比探测端绕过更彻底

### 效果

- 2 个新增单元测试 + 既有 max_replan 测试全通过（含跨轮恢复测试）
- s001-t5 / s004-t3-5 类场景：下一轮 user message 进入时走 LLM 分支，获得新响应（非 stale）
- thread 不再 bricked，无需 session switch

**原因**：MAX_REPLAN 是"放弃重规划"的终态语义，但早返回的实现把"终态"做成了"永久锁死"——下一轮的 user message 是新对话意图，不应该继承上一轮的失败状态。在早返回里清 guard 条件，让"放弃"只影响当前轮、不污染 thread。

**关联决策**：决策 5（失败处理双重保障）、决策 5.1（Mode 2→3 切换）、决策 31（mode 路由脱节，P0-α 上游放大器）。
