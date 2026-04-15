# 架构设计决策详解

> 本文件保留 AgentTriad 所有设计决策的**完整背景、原因分析和细节说明**。  
> 精简版规则见 [`CLAUDE.md`](../CLAUDE.md)；本文件供项目成员回顾设计意图时参考，AI Agent 执行时无需阅读。

---

## 决策 1：call_planner / call_executor 使用结构化参数传递

`call_executor` 接受 LLM 传入的结构化参数：
- `task_description`: 纯文本，Mode 2（Executor-use ReAct）下只需要该参数
- `plan_id`：Mode 3（Plan → Execute）下只需要该参数
- `wait_for_result`（默认 `True`）：控制是否阻塞等待执行结果。`True` 时派发后自动等待并返回 `[EXECUTOR_RESULT]`，Supervisor 无需额外调用 `get_executor_result`，减少一次工具调用和 token 消耗。`False` 时为异步派发，需后续调用 `get_executor_result(plan_id)` 获取结果（适用于并行派发场景）。

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
  "failure_reason": null
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
- Planner 默认仅挂载只读能力，不暴露 `write_file`、`run_local_command` 等副作用工具。
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
