# 架构设计决策详解

> 本文件保留 AgentTriad 所有设计决策的**完整背景、原因分析和细节说明**。  
> 精简版规则见 [`CLAUDE.md`](../CLAUDE.md)；本文件供项目成员回顾设计意图时参考，AI Agent 执行时无需阅读。

---

## 决策 1：call_planner / call_executor 使用结构化参数传递

`call_executor` 接受 LLM 传入的结构化参数：
- `task_description`: 纯文本，Mode 2（Executor-use ReAct）下只需要该参数
- `plan_id`：Mode 3（Plan → Execute）下只需要该参数
- `wait_for_result`（默认 `True`）：控制是否阻塞等待执行结果。`True` 时派发后自动等待并返回 `[EXECUTOR_RESULT]`，Supervisor 无需额外调用 `get_executor_result`，减少一次工具调用和 token 消耗。`False` 时为异步派发，需后续调用 `get_executor_result(plan_id)` 获取结果（适用于并行派发场景）。
- `get_executor_result` 可选参数 `detail`（默认 `overview`）：与 `wait_for_result=false` 配套时仍为阻塞收束并返回 `[EXECUTOR_RESULT]`。`detail=full` 时，若任务仍在 `active_executor_tasks` 中与 `overview` 同路径等待；终态后由 `dynamic_tools_node` 把 `last_executor_full_output`（步骤级摘要）拼入给 LLM 的 ToolMessage。若任务已不在 active 且 `plan_id` 与会话中 `plan_json` 顶层一致，则只读会话缓存中的步骤级正文（返回体不含 `[EXECUTOR_RESULT]`，不重复跑会话合并）。原独立工具 `get_executor_full_output` 已并入本参数语义。

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

## 决策 14：list_executor_tasks 时间显示格式

**问题**：LLM 对绝对时间戳（如 `22:35:17` 或 `2026-04-15T22:35:17`）缺乏直觉感知。与人类类似，LLM 对时间的理解以"多久之前"为锚点。

**决策**：`list_executor_tasks` 面向 LLM 的输出中，`last_updated` 列使用相对时间格式：

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

## 决策 15：Supervisor 工具面收缩与「直连 Executor」工具合并（备忘）

**背景**：当前 `stop_executor` 与 `check_executor_progress` 在实现上都属于 Supervisor 经 HTTP **直连 Executor 进程**的路径（与 `call_executor` 经派发、邮箱收结果的主路径并列）。二者能力不同（写：请求停止；读：查进度），但**工具名多一条、模型多一次「该点哪个」的选择**。

**何时值得考虑合并**（无硬编码阈值，按症状判断即可）：

- Supervisor 暴露的工具继续增加，且**多枚工具共享同一资源边界**（例如都对着「按 `plan_id` 找子进程 / 调同一组 REST」），导致提示词里难以用一句话区分职责；或
- 实测中 **tool 选择错误率**（该查却停、该停却反复查）或 **无效 tool 轮次** 明显上升。

此时可考虑合并为**单一工具 + 枚举参数**（例如 `action`: `status` | `stop`），由模型在调用时显式选择模式。先例见决策 1：`get_executor_result` 的 `detail` 参数吸收原 `get_executor_full_output` 的语义。

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

P1 工具：`knowledge_tree_retrieve`、`knowledge_tree_ingest`、`knowledge_tree_status`
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

规则判断"是否值得记忆"，低阈值（宁多勿漏）：
- 含决策/结论关键词
- 含数字或专有名词
- 用户显式指令 / 任务完成 summary

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
| `kt_dedup_threshold` | 0.95 | 去重阈值 |
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

**决策**：新增 `executor_wait_timeout` 配置字段（`Context` dataclass），默认值 300s，由 `call_executor` 和 `get_executor_result` 统一使用。

**约束**：`executor_wait_timeout` 应 **大于** `executor_call_model_timeout`（180s），否则仍会出现提前终止。

**实现**：

```
Context.executor_wait_timeout: float = 300.0
  ↓
call_executor(wait_for_result=True)
  → _wait_for_executor_result(timeout=runtime_context.executor_wait_timeout)
  ↓
get_executor_result()
  → _wait_for_executor_result(timeout=runtime_context.executor_wait_timeout)
```

**超时层级关系**（从内到外）：

| 超时 | 默认值 | 作用域 |
|------|--------|--------|
| `executor_call_model_timeout` | 180s | Executor 子进程内单次 LLM 调用 |
| `executor_tool_timeout` | 300s | Executor 子进程内 tools_node 执行 |
| `executor_wait_timeout` | 300s | Supervisor 等待 Executor 结果返回 |

**原因**：超时值必须由内到外递增——内层先超时并优雅降级，外层再超时才终止进程。硬编码的外层超时小于内层，等同于人为制造竞态。配置化后可通过环境变量 `EXECUTOR_WAIT_TIMEOUT` 按需调整。
