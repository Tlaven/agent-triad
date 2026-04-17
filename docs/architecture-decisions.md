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

## 决策 18：知识树三层分离存储架构

知识树采用三层分离的存储架构，各层职责明确：

| 层 | 载体 | 职责 |
|----|------|------|
| Layer 1 | Markdown 文件（Source of Truth） | Agent 直接读写、人类可审查、git 可版本化 |
| Layer 2 | 图数据库（DAG 结构层） | 节点元数据 + 关系边、主父节点标记 + 关联边 |
| Layer 3 | 向量索引（检索层，图数据库内置） | 语义相似度计算、模糊召回、受树结构约束排序 |

同步规则：
- **写入顺序**：Markdown 先写（SoT），再同步到图数据库和向量索引
- **读取路径**：图数据库提供结构查询，向量索引提供语义检索，Markdown 供 Agent 直接编辑
- **冲突解决**：Markdown 为准，图数据库和向量索引为派生物

节点 Markdown 文件约定：
- 文件名：`{node_id}.md`
- YAML frontmatter 存储元数据（title、source、created_at、summary 等）
- 正文存储 content

```yaml
---
node_id: abc123
title: "LangGraph 状态管理"
source: "官方文档"
created_at: "2026-04-17T10:00:00Z"
summary: "LangGraph StateGraph 的状态传递模式"
parent_ids:
  - parent_node_id  # 第一个为主父节点
---
LangGraph 使用 TypedDict 定义状态模式...
```

**原因**：三层分离让每层可独立演进（存储格式 vs 查询引擎 vs 检索算法），同时 Markdown SoT 保证人类可审计和 git 可追溯。与决策 12（MCP 工具分层）类似，分层隔离关注点。

---

## 决策 19：P1 图数据库选型——Kùzu

P1 原型阶段选用 **Kùzu v0.11.x** 作为嵌入式图数据库。

选型考量：

| 方案 | 优势 | 劣势 |
|------|------|------|
| **Kùzu**（选用） | Cypher 查询 + 内置 HNSW 向量索引 + 全文检索，嵌入式单进程 | 2025 年 10 月已归档，不再积极维护 |
| DuckDB + 自定义图层 | 生态成熟、向量扩展活跃 | 图遍历需自研，原型速度慢 |
| RyuGraph | Kùzu 继承者，理念匹配 | 社区待验证，API 不稳定 |

迁移策略：
- 存储层通过**抽象基类**（`BaseGraphStore`）隔离 Kùzu 具体实现
- 长期首选 **DuckDB + 自定义图层**，积极备选 **RyuGraph**
- 迁移成本可控：只需实现新的 `BaseGraphStore` 子类

```python
class BaseGraphStore(ABC):
    @abstractmethod
    def upsert_node(self, node: KnowledgeNode) -> None: ...
    @abstractmethod
    def get_children(self, parent_id: str) -> list[KnowledgeNode]: ...
    @abstractmethod
    def similarity_search(self, query_vec: list[float], top_k: int, threshold: float) -> list[tuple[KnowledgeNode, float]]: ...

class KuzuGraphStore(BaseGraphStore):
    """P1 实现：基于 Kùzu v0.11.x"""
    ...
```

**原因**：P1 目标是快速验证端到端闭环，Kùzu 的 Cypher + 向量一体化最大程度减少移动部件。归档风险通过抽象接口缓解，不影响原型验证。与决策 1 的"先用最简单方案验证"原则一致。

---

## 决策 20：DAG 结构与主路径遍历

知识树物理存储为**有向无环图（DAG）**，允许节点有多个父节点（跨领域关联），但导航使用**主路径遍历**。

### 1) 边类型

```python
@dataclass
class KnowledgeEdge:
    edge_id: str
    parent_id: str
    child_id: str
    is_primary: bool       # True = 主父节点，用于遍历
    edge_type: str         # "parent_child" | "association"
```

- 每个子节点有且仅有一个 `is_primary=True` 的父边——定义遍历主路径
- 其余父边 `is_primary=False`，作为关联引用保留 DAG 语义

### 2) 遍历策略分阶段

| 阶段 | 策略 | 说明 |
|------|------|------|
| P1 | 主路径遍历 | 从根节点出发，每步沿 `is_primary=True` 的子节点前进 |
| P2 | 多路径并行探索 | 允许沿多个父边探索，加深度/置信度剪枝防分支爆炸 |

### 3) 主路径缓存

- 首次遍历后缓存从根到每个节点的主路径（`node_id → [root, ..., node]`）
- 节点编辑/移动时失效并重算
- P2 多路径探索时，缓存的主路径作为优先探索路径

**原因**：DAG 提供比严格树更灵活的关联表达能力（如"调试技巧"同时属于"Python"和"Agent 工作流"），但 P1 先用主路径遍历降低复杂度，积累失败案例作为 P2 多路径探索的数据基础。与决策 11（单线程先行）的渐进思路一致。

---

## 决策 21：检索策略——树优先，RAG 兜底

知识树采用双路径检索机制：**LLM 路由树导航优先，高阈值向量检索兜底**。

### 1) 检索流程

```
查询文本 → 向量化
         → ② LLM 路由树导航（置信度 ≥ 0.7 继续下钻）
         → ③ RAG 兜底（树导航失败时，相似度 ≥ 0.85）
         → ④ 结果融合
         → ⑤ Agent 反馈
         → ⑥ 结构化检索日志
```

### 2) 树导航（主路径）

从根节点开始，每层将子节点摘要列表与查询一起提交给 LLM，由 LLM 执行路由决策：
- LLM 返回选中的子节点 ID 及决策置信度（0.0-1.0）
- 置信度 ≥ 0.7：继续向深层导航
- 置信度 < 0.7 或无合适子节点：树导航失败

### 3) RAG 兜底

仅在树导航失败时触发：
- 使用查询向量在向量索引中执行相似度检索
- 仅保留相似度 ≥ 0.85 的结果，返回 Top-K（K=5）

### 4) 结果融合

| 场景 | 来源标记 | 处理 |
|------|---------|------|
| 树导航成功且内容充分 | `tree` | 直接采纳 |
| 树导航成功但内容不足 | `tree+rag` | 树结果 + RAG 结果合并，LLM 综合排序 |
| 树导航失败，RAG 有结果 | `rag` | 采纳 RAG Top-1，记录导航失败信号 |
| 两者均无结果 | `none` | 返回"未找到" |

### 5) Agent 反馈与检索日志

- Agent 反馈结构化字段：`{satisfaction: bool, reason: str}`
- 每次查询自动记录结构化 JSON 日志：查询向量、导航路径、各层置信度、RAG 候选、最终结果、来源标记、Agent 反馈
- 日志从 P1 起强制输出，为后续小模型路由器训练预留数据基础

**原因**：树导航利用 LLM 的语义理解处理歧义查询（优于纯向量匹配），RAG 高阈值兜底保障召回底线。双路径互补而非竞争。结构化日志是闭环优化的数据燃料。

---

## 决策 22：Change Mapping P1——JSON Patch

P1 阶段 Agent 编辑操作限定为**内容编辑 + merge/split**，使用 **JSON Patch (RFC 6902)** 作为 Delta 格式。

### 1) P1 支持的操作

| 操作 | 语义 | JSON Patch 映射 |
|------|------|----------------|
| `update_content` | 修改节点内容/摘要 | `[{op: "replace", path: "/content", value: "..."}]` |
| `merge` | 合并多个节点为一个 | 创建新节点 + 删除旧节点 + 继承所有边 |
| `split` | 拆分一个节点为多个 | 创建新子节点 + 更新父节点摘要 |

### 2) 强约束防幻觉

Agent 输出 Delta 时需双重约束：
- **Prompt 模板**：明确指定输出格式和允许的操作类型
- **解析校验**：系统解析 Agent 输出后验证 JSON Patch 格式合法性和操作范围

```python
# 解析校验示例
ALLOWED_OPS = {"replace", "add", "remove"}
ALLOWED_PATHS = {"/content", "/summary", "/title"}

def validate_delta(patches: list[dict]) -> bool:
    for p in patches:
        if p["op"] not in ALLOWED_OPS:
            return False
        if not any(p["path"].startswith(prefix) for prefix in ALLOWED_PATHS):
            return False
    return True
```

### 3) 分阶段演进

| 阶段 | Delta 格式 | 审计 |
|------|-----------|------|
| P1 | JSON Patch (RFC 6902) | 日志记录每次 Delta |
| P2 | + 语义层（merge/split/move 操作映射到 JSON Patch） | + 语义 Delta 审计日志供人类/Supervisor 审查 |
| P3 | 完整语义层（创建抽象层/跨层重组） | 通用 Delta 描述格式 |

**原因**：JSON Patch 是标准、可验证、工具链丰富的格式。强约束防止 Agent 幻觉产生无效 Delta。渐进式引入语义层，P1 先验证流程可行性。

---

## 决策 23：异步优化闭环与防震荡

知识树通过 4 种检索信号驱动异步批量优化，并设防震荡机制。

### 1) 四种优化信号

| 信号类型 | 触发条件 | 优化动作 | 优先级 |
|----------|----------|----------|--------|
| 整体失败 | 树 + RAG 均无结果，累积达阈值 | Agent 创建新节点，失败查询作为种子 | 1（最高） |
| 导航失败 | 某父节点下频繁导航失败 | 标记"结构薄弱点"，Agent 分裂/重组/摘要重写 | 2 |
| RAG 假阳性 | RAG 返回节点被标记为不相关 | 对比学习负样本，调整相似度权重 | 3 |
| 内容不足 | 树导航成功但内容不充分 | Agent 更新节点内容/摘要 | 4（最低） |

### 2) 防震荡：分层控制

- **独立阈值**：每种信号类型独立配置触发条件（如导航失败 N 次/时间窗口）
- **全局频率上限**：无论信号类型，总优化动作受全局限额约束（初期保守值如每小时最多 10 次），超出排队到下个窗口
- **满意度反馈动态调整**：结合检索日志中的 Agent 满意度反馈，动态调整全局频率上限

### 3) 执行模式

所有优化动作**异步批量**执行，不阻塞检索路径。定期扫描检索日志提取信号，按优先级排序后在频率限额内执行。

**原因**：4 种信号覆盖了检索失败的主要模式，每种都有明确的优化动作。防震荡机制防止过度优化导致树结构不稳定。异步批量执行与 AgentTriad 现有的异步模式（决策 1 的 `wait_for_result`）一致。

---

## 决策 24：P1 信息范围——领域知识

P1 阶段知识树仅承载**领域知识**作为初始种子。

### 1) 叶子节点模式

```python
@dataclass
class KnowledgeNode:
    node_id: str
    title: str
    content: str
    source: str         # 来源标识（如"官方文档"、"代码注释"）
    created_at: str     # ISO 格式时间戳
    summary: str = ""   # 摘要，用于树导航路由
    embedding: list[float] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
```

### 2) 分阶段扩展

| 阶段 | 信息类型 | 新增字段 |
|------|---------|---------|
| P1 | 领域知识 | 基础字段（上表） |
| P2 | + Agent 记忆 | `decay_score`、`access_count`（指数衰减遗忘机制） |
| P3 | + 技能/Skill + 参考资料 | 技能含可执行定义（可绑定 Supervisor 工具调用）；参考资料含外部链接 |

**原因**：领域知识结构性强、边界清晰，最适合验证"Bootstrap 建树 → Agent 编辑 → 向量校准"闭环。碎片化记忆和可执行技能的引入需要树结构先稳定运行。

---

## 决策 25：知识树定位——Supervisor 内嵌模块

知识树作为 **Supervisor 内嵌组件**，物理位于 `src/common/knowledge_tree/` 子包，通过工具注册暴露给 Supervisor。

### 1) 模块定位

- **不是独立 Agent**：不引入新的 LangGraph 子图，不改变现有三层架构
- **不是独立服务**：不启动额外进程或 HTTP 端口
- **是共享基础设施**：类似 `src/common/tools.py`（工作区工具）和 `src/common/mcp.py`（MCP 客户端）的定位

### 2) 工具注册方式

```python
# src/supervisor_agent/tools.py
async def get_tools(runtime_context: Context | None = None) -> List[Callable[..., Any]]:
    tools = [
        _build_call_planner_tool(runtime_context),
        _build_call_executor_tool(runtime_context),
        # ... 现有工具 ...
    ]
    # V4: 条件注册知识树工具
    if runtime_context.enable_knowledge_tree:
        from src.common.knowledge_tree import build_knowledge_tree_tools
        tools.extend(build_knowledge_tree_tools(runtime_context))
    return tools
```

知识树工具列表：
- `knowledge_tree_retrieve(query: str) -> str` — 主检索工具
- `knowledge_tree_edit(operation: str, params_json: str) -> str` — merge/split 编辑
- `knowledge_tree_status() -> str` — 树健康/结构概览

### 3) 配置集成

通过 `src/common/context.py` 的 `Context` dataclass 添加配置字段，遵循现有的 env-var 覆盖模式（字段名大写化为环境变量名）。

### 4) 包结构

```
src/common/knowledge_tree/
    __init__.py              # 公共 API
    config.py                # KnowledgeTreeConfig
    bootstrap.py             # 建树
    storage/                 # 三层存储
    dag/                     # DAG 数据模型
    retrieval/               # 检索逻辑
    editing/                 # 编辑 + Change Mapping
    optimization/            # 优化闭环
```

**原因**：内嵌模块定位最轻量，不改变现有架构拓扑（与决策 8 的三种模式兼容），工具注册方式与现有 Supervisor 工具一致（factory pattern）。条件注册通过 feature flag 控制，不影响 V3 及以前的功能。
