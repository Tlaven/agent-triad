# PRD：通用三层 Multi-Agent 自主任务系统

> **文档性质**：产品需求文档（Product Requirements Document）  
> **版本**：v0.1  
> **日期**：2026-03-30  

---

## 0. 文档边界（避免重复）

- `PRD.md`：定义产品目标、功能范围、阶段边界与验收标准
- `CLAUDE.md`：记录工程架构与实现决策，不在本文重复实现细节
- `ROADMAP.md`：维护任务级拆解与执行进度（勾选项）

> 本文不维护文件级开发清单，避免与 `ROADMAP.md` 重复。

---

## 1. 产品定位

### 1.1 一句话定义

一个通用目的的三层 Multi-Agent 自主任务系统，接受自然语言查询，自动完成**任务理解 → 计划生成 → 工具执行 → 结果融合**全流程，可作为垂直领域 AI Agent 应用的基础框架使用。

### 1.2 适用场景

| 场景类型 | 描述 | 典型示例 |
|---|---|---|
| 简单问答 | 无需工具，直接从知识中作答 | "解释 RAG 的原理" |
| 单步工具任务 | 调用单个工具完成一次操作 | "搜索最新 LLM benchmark 结果" |
| 多步复杂任务 | 需要计划拆解 + 多工具协作 | "研究 X 技术并生成报告" |
| 长链任务（V3） | 多 Executor 并行，需融合多路结果 | "同时分析 A、B、C 三个方向并给出推荐" |

### 1.3 设计原则

1. **职责分离**：Supervisor 调度、Planner 规划、Executor 执行，三者各司其职
2. **意图解耦**：计划（Plan）是意图层，不含工具名，Planner 与 Executor 工具集独立演化
3. **失败可见**：每一步执行状态（completed / failed / skipped）强制结构化上报，无隐性失败
4. **重规划闭环**：Executor 遇阻直接停止上报，重规划决策权归 Supervisor，不在子 Agent 内部自决
5. **分阶段交付**：V1 单线程闭环，V2 引入 Reflection，V3 支持并行

---

## 2. 角色职责

### 2.1 Supervisor Agent

**职责**：系统主循环，负责理解用户意图、调度子 Agent、管理干预与重规划、合成最终答案。

**输入**：用户自然语言查询  
**输出**：最终自然语言答案（含证据链摘要）

**核心行为**：

| 行为 | 触发条件 | 动作 | 参数传递 |
|---|---|---|---|
| 模式1：Direct Response | 简单事实、知识内化、无需工具 | 内部思考后直接输出最终答案 | 无 |
| 模式2：Tool-use ReAct | 需要少量工具、短流程、目标明确 | 调 Executor 走 ReAct | 由工具内部通过 `InjectedState` 读取 |
| 模式3：Plan → Execute → Summarize | 多步骤、长流程、有依赖、需一致性 | 先调 Planner 生成 Plan → 再调 Executor 执行 → 融合总结 | 由工具内部通过 `InjectedState` 读取 |
| 轻干预 | 收到 Executor Snapshot，偏差小 | 局部调整 Plan 文本，发回 Executor 继续 | - |
| 中/重干预 | 偏差大 或 里程碑阻塞 | 调 `generate_plan` 局部或全局重规划 | - |
| 融合输出 | 收到所有 ExecutorResult | ReAct 循环融合多路结果，生成最终答案 | - |
| 归档 Memory（V3） | 最终答案生成后 | 归档 Strategic Memory + Episodic Memory | - |

**决策输出**：Supervisor 在 Thought 阶段输出结构化决策（mode + reason + confidence）

**模式选择原则**：
- 能用模式 1 就绝不用 2，能用模式 2 就尽量不用 3（Occam's Razor）
- 优先考虑 token 消耗：模式1（最低）< 模式2（中等）< 模式3（较高）

**工具（对外暴露）**：`generate_plan`、`execute_plan`

**最大重规划次数**：默认 3 次（可通过环境变量 `MAX_REPLAN` 配置）

---

### 2.2 Planner Agent

**职责**：将任务需求（含历史执行状态）转化为结构化意图层 JSON 执行计划。

**输入**（当前实现）：
- LLM 调用 `generate_plan(task_core=..., plan_id=...)`：Supervisor 提供 **足够详细**的 `task_core`（目标、约束、验收标准、关键上下文等）；重规划时传 `plan_id` 并由工具从 `PlannerSession` 注入带执行状态的 Plan 正文。
- Planner 模型侧消息顺序：**第一条**为完整系统提示（`_PLANNER_SYSTEM_PROMPT_TEMPLATE` 经 `get_planner_system_prompt` 渲染）；**第二条**为 `task_core` 纯文本；**重规划时第三条**为当前 Plan JSON（含步骤状态）。

**输出**：结构化 JSON 计划（Plan JSON，放在 ` ```json ``` ` 代码块中）

**约束**：
- Planner **不知道** Executor 有哪些工具，不在 Plan 中出现工具名
- 每个 step 只描述意图（intent）和期望产出（expected_output）
- 重规划时，Planner 能看到哪些 step 已完成（跳过），哪步失败及原因（继续）

---

### 2.3 Executor Agent

**职责**：按 Plan 中每个 step 的意图，自主选择工具执行，输出带执行状态的 updated_plan。

**输入**（当前实现）：
- 通过 `InjectedState` 读取当前 `planner_session.plan_json`
- 不依赖 LLM 传入业务参数

**输出**：`ExecutorResult(status, updated_plan_json, summary)`

**核心行为**：

| 行为 | 触发条件 | 动作 |
|---|---|---|
| 工具选择 | 处理每个 step | 按 intent 自主选合适工具，不受 Planner 约束 |
| Reflection（V2） | 每 N 步（默认 3）或置信度低 | 进行自我批评，评估当前路径是否偏离目标 |
| Snapshot 上报（V2） | Reflection 后偏差大 或 到达里程碑 | 停止执行，打包 Snapshot 上报给 Supervisor |
| 正常完成 | 所有 step 通过验收准则 | 输出 Completion Report（含证据链）给 Supervisor |
| 遇阻停止 | 无法继续（工具失败 / 权限不足） | 停止，标记失败步骤，返回 updated_plan 给 Supervisor |

**不做的事**：Executor **不主动重规划**，也**不擅自决定放弃任务**。

---

## 3. Plan JSON Schema

### 3.1 完整 Schema

```json
{
  "plan_id": "string",
  "version": "integer",
  "goal": "string",
  "task_summary": "string",
  "milestones": ["string"],
  "risks": ["string"],
  "steps": [
    {
      "step_id": "step_1",
      "intent": "意图描述（不含工具名，面向人类可理解）",
      "expected_output": "完成验收标准（用于 Executor 自检）",
      "milestone": "optional: 此步骤对应的里程碑标签",
      "status": "pending | completed | failed | skipped",
      "result_summary": "成功时的结果摘要（初始为 null）",
      "failure_reason": "失败时的原因（初始为 null）",
      "confidence": "optional: 0.0~1.0，Executor Reflection 用（V2）"
    }
  ]
}
```

### 3.2 字段约束

| 字段 | 类型 | 约束 |
|---|---|---|
| `plan_id` | string | 唯一标识，格式 `plan_<date>_<sequence>`（如 `plan_v20260331_002`） |
| `version` | integer | 版本号，初始为 1，每次重规划递增 |
| `goal` | string | 任务总体目标，面向人类可读 |
| `task_summary` | string | ≤ 200 字，面向人类可读 |
| `milestones` | string[] | 可选，≥ 1 个里程碑 |
| `risks` | string[] | 可选，提前识别阻塞点 |
| `step_id` | string | 格式 `step_1`, `step_2`, ... |
| `intent` | string | **禁止**出现具体工具名或 API 名 |
| `expected_output` | string | 可量化或可验证的完成标准 |
| `status` | enum | `pending`（初始）/ `completed` / `failed` / `skipped` |
| `result_summary` | string \| null | 初始 `null`，Executor 完成后写入 |
| `failure_reason` | string \| null | 初始 `null`，Executor 失败后写入 |

### 3.3 重规划时的 Plan 传递规则

重规划时，Supervisor 将**当前带执行状态的 Plan JSON** 一并传给 Planner，Planner 的处理规则：

- `status=completed` 的步骤：**保持不变**（新计划不重复执行已完成步骤）
- `status=failed` 的步骤：**根据 failure_reason 修订**（修改 intent 或拆分为子步骤）
- `status=pending` 的步骤：**按需调整**（可能因前置失败而修改）

---

## 4. 分阶段功能边界

### V1：单线程闭环 MVP（当前阶段）

**目标**：最小可运行的 Supervisor → Planner → Executor 闭环，验证基础架构可行性。

**包含（产品能力级）**：
- Supervisor / Planner / Executor 三层闭环
- 三种响应模式与基础重规划能力
- 结构化执行结果与失败可见（无隐性失败）
- 单 Executor 串行执行

**不包含**：
- Executor Reflection / Snapshot 上报
- 多 Executor 并行 fan-out
- Memory 归档
- Supervisor 干预分级（所有失败均触发 Replan）

**验收标准**：给定一个多步骤任务，系统能完成"计划生成 → 工具执行 → 失败重规划 → 最终答案"完整流程，无隐性崩溃。

**任务拆解与进度**：见 `ROADMAP.md`（本文不重复文件级清单）

---

### V2：Reflection + Snapshot 上报

**目标**：引入 Executor 自我监控能力，使 Supervisor 可在任务执行中途干预。

**新增（产品能力级）**：
- Executor Reflection 与 Snapshot 上报
- Supervisor 干预分级（轻干预 / 中重干预）
- 反思触发参数可配置（步数阈值 + 置信度阈值）
- 可选 MCP 能力接入与失败语义标准化

**验收标准**：Executor 在执行第 N 步后触发 Reflection，检测到偏差时上报 Snapshot，Supervisor 正确识别干预级别并局部调整 Plan 后 Executor 继续执行。

---

### V3：多 Executor 并行 + Memory 归档

**目标**：支持复杂任务的并行执行与长期记忆，完成完整流程图设计目标。

**新增（产品能力级）**：
- 多 Executor 并行（fan-out / fan-in）
- 多路结果融合与冲突解决
- Strategic / Episodic Memory 归档与检索
- MCP 治理与 Skill 产品化

**验收标准**：给定一个可拆分为 N 条独立路径的任务，系统能并行执行并融合结果，最终答案质量优于单线程顺序执行。

---

## 5. 非功能性需求

### 5.1 可观测性

| 需求 | 实现方式 |
|---|---|
| LLM 调用追踪 | LangSmith Tracing（通过 `LANGCHAIN_TRACING_V2=true` 启用） |
| 执行状态可读 | ExecutorResult + updated_plan_json 在 Supervisor 日志中完整输出 |
| 重规划次数记录 | `session.replan_count` 字段记录，超上限时告知用户 |

### 5.2 安全约束

| 需求 | 实现方式 |
|---|---|
| 工具调用沙箱 | `run_local_command` 在 V1 为直接执行，V2+ 迁移为沙箱隔离 |
| 密钥安全 | 所有 API Key 仅通过 `.env` 文件注入，禁止硬编码 |
| 最大执行轮次 | Executor ReAct 循环默认最多 20 轮（`MAX_EXECUTOR_ITERATIONS`） |

### 5.3 配置项汇总

| 环境变量 | 默认值 | 说明 |
|---|---|---|
| `SUPERVISOR_MODEL` | `qwen:qwen-flash` | Supervisor 使用的模型 |
| `PLANNER_MODEL` | `siliconflow:Pro/deepseek-ai/DeepSeek-V3.2` | Planner 使用的模型 |
| `EXECUTOR_MODEL` | `siliconflow:Pro/deepseek-ai/DeepSeek-V3.2` | Executor 使用的模型 |
| `MAX_REPLAN` | `3` | Supervisor 最大重规划次数 |
| `MAX_EXECUTOR_ITERATIONS` | `20` | Executor ReAct 最大轮次 |
| `REFLECTION_INTERVAL` | `3` | Executor 每 N 步触发 Reflection（V2） |
| `CONFIDENCE_THRESHOLD` | `0.6` | 低于此置信度强制触发 Reflection（V2） |
| `REGION` | `prc` | 模型接入区域（prc / international） |
| `LANGCHAIN_TRACING_V2` | `false` | 启用 LangSmith 追踪 |
