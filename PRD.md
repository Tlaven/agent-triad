# PRD：通用三层 Multi-Agent 自主任务系统

> **文档性质**：产品需求文档（Product Requirements Document）  
> **版本**：v0.3  
> **日期**：2026-04-06  
> **说明**：本版本与 `CLAUDE.md`（架构决策）和 `ROADMAP.md`（版本路线）对齐，替换已过时描述。

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
- `ExecutorResult(status, updated_plan_json, summary)`

**边界**：
- 可自主选择工具，但**不**做内部重规划
- 遇阻即停并返回失败状态
- V2-c 引入 Reflection 时，仍是“停并上报”，由 Supervisor 决定续跑/重规划

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

## 5. 版本范围与验收

### 5.1 V1（已完成）

**范围**：
- 三模式 Supervisor
- Planner/Executor 闭环
- `ExecutorResult` 结构化返回
- 失败双重保障与重规划闭环
- session 状态双向同步

**验收**：
多步骤任务可完成“计划 → 执行 → 失败重规划（最多 N 次）→ 最终答复”闭环，无隐性崩溃。

### 5.2 V2（已完成）

**V2-a：上下文与工具输出治理（已完成）**
- 工具输出预算、截断、外置引用、可选摘要
- 验收：超长工具输出不导致主循环崩溃，且截断/外置可感知

**V2-b：Planner 工具接入 + MCP 复用（已完成）**
- Planner 辅助工具接入 graph
- 只读 MCP 在 Planner/Executor 复用
- 验收：至少一项只读能力共享复用，且 Planner 无法调用副作用工具

**V2-c：Reflection + Snapshot（精简版，已完成）**
- 步骤计数与低置信触发 Reflection
- Snapshot 上报后由 Supervisor 决策续跑/重规划
- 验收：触发条件下能稳定上报并走现有决策闭环
 - 当前默认：`REFLECTION_INTERVAL=0`（关闭）；配置为正整数后启用

---

## 6. 非功能性需求

### 6.1 可观测性

| 需求 | 要求 |
|---|---|
| 链路追踪 | 支持 LangSmith tracing（可配置开关） |
| 状态审计 | `updated_plan_json` 可回溯步骤状态变化 |
| 重规划监控 | 记录 `replan_count`、失败摘要与最终原因 |

### 6.2 安全与治理

| 需求 | 要求 |
|---|---|
| 命令执行安全 | 黑名单、超时、路径校验、最小权限原则 |
| 密钥安全 | API Key 仅环境变量注入，禁止硬编码 |
| 权限边界 | Planner 不接触副作用工具；副作用能力集中在 Executor |

### 6.3 关键配置（摘要）

| 配置项 | 说明 |
|---|---|
| `MAX_REPLAN` | Supervisor 最大重规划次数 |
| `MAX_EXECUTOR_ITERATIONS` | Executor 最大 ReAct 轮次 |
| `MAX_PLANNER_ITERATIONS` | Planner ReAct 最大轮次（含工具循环） |
| `REFLECTION_INTERVAL` | V2-c 反思步长 |
| `CONFIDENCE_THRESHOLD` | V2-c 低置信触发阈值 |
| （V2-a 新增）observation 预算项 | 单条 observation 长度、外置阈值、落盘开关、摘要开关 |

---

## 7. 当前不做

- 不在 Executor 内部实现自动重规划状态机
- 不在 Plan 中写入工具名
- 不让 Planner 直接执行副作用操作
