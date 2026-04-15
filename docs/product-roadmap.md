# 产品需求与版本路线

> **文档性质**：合并原根目录 `PRD.md` 与 `ROADMAP.md`（2026-04-15）。  
> **执行硬规则**见 [`../CLAUDE.md`](../CLAUDE.md)；**设计决策背景**见 [`architecture-decisions.md`](architecture-decisions.md)。  
> **V3 拓扑与数据流**以 [`v3-architecture-diagrams.md`](v3-architecture-diagrams.md) 为准（含 Push Mailbox、动态端口等；旧版「固定端口 + Callback Server」叙述已废弃）。

---

## 1. 产品定位

### 1.1 一句话定义

一个通用目的的三层 Multi-Agent 系统：接受自然语言输入，完成 **任务理解 → 意图层计划 → 工具执行 → 结果总结** 全流程，可作为垂直场景 Agent 应用底座。

### 1.2 适用场景

| 场景类型 | 描述 | 典型示例 |
|---|---|---|
| 简单问答 | 无需工具，直接回答 | "解释 RAG 的原理" |
| 短流程任务 | 少量工具即可完成 | "读取项目配置并解释差异" |
| 多步任务 | 需计划拆解和状态回写 | "分析代码问题并给出修复方案" |

### 1.3 核心原则

1. **职责分离**：Supervisor 负责决策与调度，Planner 只做意图层规划，Executor 只做执行。
2. **计划解耦**：Plan 不携带工具名，Planner 与 Executor 工具集独立演化。
3. **失败显式化**：执行状态结构化返回，失败可追踪、可重放、可重规划。
4. **重规划中心化**：重规划权只在 Supervisor，Executor 遇阻即停并上报。
5. **成本可控**：优先低 token 路径；工具输出进入模型前必须可预算、可裁剪。

---

## 2. 系统角色与职责

### 2.1 Supervisor Agent

**职责**：主循环决策，选择模式，调用 `call_planner` / `call_executor`，管理重规划并输出最终答复。

**三种模式**：

| 模式 | 触发场景 | 行为 |
|---|---|---|
| Mode 1 `Direct Response` | 简单问题，无需工具 | 直接回答 |
| Mode 2 `Tool-use ReAct` | 短流程、目标明确 | 调 Executor（传 `task_description`） |
| Mode 3 `Plan → Execute → Summarize` | 多步骤、强依赖 | 调 Planner 生成 Plan，再调 Executor 执行 |

**结构化参数约定**：
- Mode 2 调 Executor：`{"task_description": "..."}`
- Mode 3 调 Planner：`{"task_core": "..."}`
- Mode 3 重规划调 Planner：`{"task_core": "...", "plan_id": "..."}`
- Mode 3 调 Executor：`{"plan_id": "..."}`

**失败分支约定**：
- `status=failed` 且有 `updated_plan_json`：可在 `MAX_REPLAN` 内重规划。
- `status=failed` 且无 `updated_plan_json`：基于 `summary` 做失败反馈或显式切到 Mode 3。

### 2.2 Planner Agent

**职责**：把任务目标（含历史执行状态）转换为意图层 Plan JSON。

**输入**：
- `task_core`：任务核心目标/修订方向
- `plan_id`：重规划时用于索引已有会话与执行状态

**输出**：
- 合法 Plan JSON（包含 `plan_id` / `version` / `steps`）

**边界**：
- 不输出具体工具名/API 名
- 仅描述 `intent` 与 `expected_output`
- 在同一 `plan_id` 下复用 Planner 会话上下文（`version` 递增）

### 2.3 Executor Agent

**职责**：按意图层步骤自主选工具执行并回写状态。

**输入**：
- Mode 2：`task_description`
- Mode 3：`plan_id`

**输出**：
- `ExecutorResult(status, updated_plan_json, summary)`（及 V2-c 等扩展字段见 `CLAUDE.md`）

**边界**：
- 可自主选择工具，但**不**做内部重规划
- 遇阻即停并返回失败状态
- V2-c Reflection 仍是「停并上报」，由 Supervisor 决定续跑/重规划

---

## 3. Plan 与执行状态规范

### 3.1 Plan JSON（意图层）

```json
{
  "plan_id": "plan_v20260402",
  "version": 1,
  "goal": "任务总体目标",
  "steps": [
    {
      "step_id": "step_1",
      "intent": "意图描述",
      "expected_output": "可验证完成标准",
      "status": "pending",
      "result_summary": null,
      "failure_reason": null
    }
  ]
}
```

### 3.2 字段要求

| 字段 | 说明 |
|---|---|
| `plan_id` | 同一任务重规划期间保持不变 |
| `version` | 每次重规划递增 |
| `steps[].intent` | 不允许具体工具名 |
| `steps[].status` | `pending / completed / failed / skipped` |
| `result_summary` | 步骤成功后的摘要 |
| `failure_reason` | 步骤失败原因 |

### 3.3 重规划规则

- Supervisor 通过 `plan_id` 定位 `session.plan_json` 的最新执行状态。
- Planner 看到历史状态后做增量修订，避免重复已完成步骤。
- 新版本写回 `session.plan_json`，旧版本归档到 `plan_archive`（如已启用）。

---

## 4. 工具与权限模型

### 4.1 工具分层（V2-b 起）

1. **共享只读层（MCP）**：文件读取、检索、文档查询等无副作用能力。  
   - Planner：可用  
   - Executor：可用
2. **执行副作用层（Executor-only）**：写文件、命令执行、外部写操作。  
   - Planner：不可用  
   - Executor：可用（受安全策略约束）

### 4.2 输出治理（V2-a）

所有工具返回（本地工具或 MCP）在进入 ReAct 消息历史前统一规范化：
- 长输出截断并显式标注
- 超大输出可外置为文件并返回引用路径
- 可选摘要（额外 LLM 成本受开关控制）

目标：避免 observation 爆上下文，保证行为可预测。

---

## 5. 非功能性需求

### 5.1 可观测性

| 需求 | 要求 |
|---|---|
| 链路追踪 | 支持 LangSmith tracing（可配置开关） |
| 状态审计 | `updated_plan_json` 可回溯步骤状态变化 |
| 重规划监控 | 记录 `replan_count`、失败摘要与最终原因 |

### 5.2 安全与治理

| 需求 | 要求 |
|---|---|
| 命令执行安全 | 黑名单、超时、路径校验、最小权限原则 |
| 密钥安全 | API Key 仅环境变量注入，禁止硬编码 |
| 权限边界 | Planner 不接触副作用工具；副作用能力集中在 Executor |

### 5.3 关键配置（摘要）

| 配置项 | 说明 |
|---|---|
| `MAX_REPLAN` | Supervisor 最大重规划次数 |
| `MAX_EXECUTOR_ITERATIONS` | Executor 最大 ReAct 轮次 |
| `MAX_PLANNER_ITERATIONS` | Planner ReAct 最大轮次（含工具循环） |
| `REFLECTION_INTERVAL` | V2-c 反思步长（`0` 关闭） |
| `CONFIDENCE_THRESHOLD` | V2-c 低置信触发阈值 |
| V2-a 相关 | 单条 observation 长度、外置阈值、落盘与摘要开关等（见 `Context` / `.env`） |

---

## 6. 当前不做

- 不在 Executor 内部实现自动重规划状态机
- 不在 Plan 中写入工具名
- 不让 Planner 直接执行副作用操作

---

## 7. 版本里程碑与验收

### 7.1 V1.0 — 单线程闭环 MVP

**目标**：验证 Supervisor → Planner → Executor 基础架构可行性，实现完整任务执行闭环。

**核心交付（节选）**：

- Supervisor ReAct 主循环；三种回复模式（Direct / Mode 2 / Mode 3）
- `call_planner` / `call_executor` 与结构化参数；Planner 意图层 Plan；Executor ReAct
- `ExecutorResult`；失败双重保障（含 `_mark_plan_steps_failed`）；`MAX_REPLAN` 重规划；`session.plan_json` 与 `updated_plan_json` 同步
- 基础工具与单元测试基线

**实现说明**：三种模式与 `SupervisorDecision` 主要由 `graph.py` 中 `_infer_supervisor_decision` 根据 `tool_calls` 推断，并由 `call_model` 内分支补充；不要求模型单独输出一段决策 JSON。`version`、`step_id` 归一化等以代码与 `CLAUDE.md` 为准。

**验收标准**：  
给定多步骤任务，能完成「计划生成 → 工具执行 → 失败时重规划（最多 N 次）→ 最终答案」，无隐性崩溃，执行状态在 `updated_plan_json` 中完整可读。

---

### 7.2 V2.0 — 运行时边界 + Planner 扩展 + Reflection（精简）

**状态**：已完成（约 2026-04-09）。

**为何拆分 V2-a / b / c**：原先把 Reflection、干预分级等整块塞进 V2，易与「重规划权只在 Supervisor、Executor 遇阻即停」（`CLAUDE.md` 决策 4）冲突，且未优先解决 **工具返回撑爆上下文**。因此先落地 **消息边界** 与 **Planner 能力**，再以精简方式落地决策 10 的 Reflection。

#### V2-a — 上下文与工具输出治理

- 工具返回规范化：截断、外置引用、可配置预算；进入 ReAct 历史的 observation 不越界
- 验收：超长工具输出下行为确定、主循环不因单条 observation 崩溃

#### V2-b — Planner 辅助工具 + MCP 只读复用

- Planner 图绑定规划辅助工具；只读 MCP 与 Executor 复用；Planner 不可用副作用工具
- 验收：存在共享只读能力且 Planner 无法调用写命令/写文件类工具

#### V2-c — Executor Reflection + Snapshot（精简）

- `REFLECTION_INTERVAL`、置信度触发；Snapshot 结构化上报；Executor 停轮次，由 Supervisor 续跑/重规划
- 默认 `REFLECTION_INTERVAL=0` 关闭
- **专项测试与用例索引**：见 [`../tests/V2_TESTING.md`](../tests/V2_TESTING.md)

---

### 7.3 V3.0 — 进程分离执行

**状态**：已完成（约 2026-04-11 起迭代；具体以仓库主分支为准）。

**架构**：Executor 以子进程 + HTTP 与 Supervisor 侧协同；**Push** 结果到 Mailbox、`ExecutorPoller` 兜底拉取、**动态端口**、进程生命周期与 `plan_id` 关联等——**一律以** [`v3-architecture-diagrams.md`](v3-architecture-diagrams.md) **与代码为准**，勿沿用早期「固定 Callback 端口」草图。

**能力概要**：

- 进程分离与异步派发；Mailbox + 轮询兜底；软中断；与既有 `ExecutorResult` / `[EXECUTOR_RESULT]` 消费方式对齐
- 相关实现分布在 `src/common/`（如 `mailbox`、`process_manager`、`polling`）、`src/executor_agent/server.py`、`src/supervisor_agent/v3_lifecycle.py` 等（以当前树为准）

---

### 7.4 里程碑时间线（简）

```
[V1] 单线程闭环 MVP           ✅
[V2] V2-a → V2-b → V2-c      ✅
[V3] 进程分离（Mailbox 等）    ✅
[V4] 待定义
```
