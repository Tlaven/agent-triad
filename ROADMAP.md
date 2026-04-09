# ROADMAP

---

## V1.0 — 单线程闭环 MVP

**目标**：验证 Supervisor → Planner → Executor 基础架构可行性，实现完整的单轮任务执行闭环。

**核心特性**：

- [x] **Supervisor ReAct 主循环**：call_model + dynamic_tools_node + 路由判断
- [x] **Supervisor 三种回复模式**：
  - 模式1：Direct Response（简单事实、知识内化）
  - 模式2：Tool-use ReAct（需少量工具、短流程）
  - 模式3：Plan → Execute → Summarize（多步骤、长流程）
- [x] **Supervisor 结构化决策输出**：mode + reason + confidence
- [x] **call_planner 工具**：接受 LLM 传参（task_core；计划修改时可附带 plan_id），内部从 session.plan_json 获取 previous_plan，调 Planner 生成 Plan JSON
- [x] **call_executor 工具**：接受 LLM 传参（Mode 2：`task_description`；Mode 3：`plan_id`），调 Executor 执行
- [x] **Planner Agent**：单次 LLM 调用，输出意图层 Plan JSON（含 version 字段，不含工具名）
- [x] **Executor Agent**：ReAct 循环（Thought → Action → Observation），自主选工具
- [x] **ExecutorResult 结构化返回**：status / updated_plan_json（含 version） / summary
- [x] **失败处理双重保障**：正常失败上报 + 异常崩溃保底标记（`_mark_plan_steps_failed`）
- [x] **重规划闭环**：最多 MAX_REPLAN 次，通过 plan_id 参数传递状态（内部从 session.plan_json 获取 previous_plan）
- [x] **dynamic_tools_node 双向同步**：仅在 `updated_plan_json` 非空时保持 `session.plan_json` 最新（含 version）
- [x] **基础 Executor 工具**：`write_file` + `run_local_command`
- [x] **基础单元测试**：JSON 提取 / 失败标记 / State 解析

**V1 校对记录（2025-04-02）**：三种模式与 `SupervisorDecision` 主要由 `graph.py` 中 `_infer_supervisor_decision` 根据 `tool_calls` 推断，并由 `call_model` 内分支（如达最大重规划、Mode2→Mode3 升级）补充，非要求模型单独输出一段 JSON。`ExecutorResult / updated_plan_json` 中 `version` 依赖 Plan JSON 一致携带，Executor 未单独强制校验 `version` 字段必存在。与 `CLAUDE.md` 的细微偏差（如 `step_id` 类型示例、`plan_id` 格式归一化、Planner 失败时会话上下文）已在代码实现中对齐并持续维护。

**验收标准**：  
给定一个多步骤任务，系统能完成"计划生成 → 工具执行 → 失败时重规划（最多 3 次）→ 最终答案"完整流程，无隐性崩溃，执行状态在 updated_plan_json 中完整可读。

---

## V2.0 — 运行时边界 + Planner 扩展（先于「执行中反思」）

**状态**: ✅ **已完成 (2026-04-09)**
**验证**: 全部 V2-a/b/c 功能已实现并通过 331 项测试（新增 107 项 V2 专项测试）

**定位说明（与旧 ROADMAP 的差异）**：原先把 **Reflection + Snapshot + 干预分级** 整块塞进 V2，与 `CLAUDE.md` **决策 4**（重规划权只在 Supervisor、Executor 遇阻即停）相比，容易做成第二套「小 Supervisor」，验收面过大，且**未覆盖**工程上更紧迫的 **工具返回体撑爆上下文** 问题。  
因此 V2 拆为两条清晰主线：**先解决「消息边界」与 Planner 能力落地**，再在同一版本内用**精简**方式落地决策 10 的 Reflection（见下节「V2-b」）。

### V2-a — 上下文与工具输出治理（横切基础）

**目标**：工具调用后的 Observation、以及后续注入 LLM 的片段，在长度与成本上**可预测、可配置**，避免因单次 `run_local_command` / 读文件等返回巨型文本导致隐式截断或请求失败。

**核心特性**：

- [x] **统一工具结果策略**：对 Executor（及后续 Planner 若绑定工具）的 tool 返回做规范化（例如：硬上限截断 + 明确提示「已截断」、或超大结果写入 workspace 仅回传路径；具体策略可配置）
- [x] **与 `CLAUDE.md` 决策 6 一致**：成功分支仍以 `ExecutorResult.summary` 等精简反馈进 Supervisor LLM；治理层保证**进入 ReAct 消息历史**的 observation 不越界
- [x] **配置项**（示例）：单条 observation 最大字符数、是否启用「落盘引用」模式、可选摘要（若引入二次 LLM 调用需单独开关）

**验收标准**：  
构造「工具返回远超上下文安全长度」的用例，系统行为确定（不静默丢失败信息：用户或模型能感知截断/外置），且主循环不因单条 observation 崩溃。

### V2-b — Planner 辅助工具按需接入

**目标**：落实 `CLAUDE.md` 模块速查表：**Planner 规划辅助工具在 V1 仅定义，V2 按需绑定到 Planner graph**；同时引入 **MCP 只读能力复用**，减少 Planner/Executor 重复造轮子（如读取文件、代码检索）。

**核心特性**：

- [x] 从业务需要出发，将 `src/planner_agent/tools.py` 中工具接入 Planner 的 ReAct 图（保持 Plan 仍为意图层、自身工具只为提升 Plan 质量）
- [x] **MCP 只读能力接入**：优先把“读取文件/检索/文档查询”等无副作用能力封装为共享 MCP（Planner 与 Executor 可共用）
- [x] **工具权限分层**：Planner 仅可用只读工具；`write_file` / `run_local_command` 等副作用工具保持 Executor-only（避免规划层越权）
- [x] **避免重复定义**：Planner 不再维护与 Executor 语义重复的本地读工具；通过同一 MCP 接口保证行为一致
- [x] Planner 侧若产生大段检索/阅读结果，复用 **V2-a** 的边界策略

**验收标准**：  
至少一条规划路径可在 Planner 内调用辅助工具完成「查资料 → 再产出 Plan JSON」，且 Plan 结构仍符合决策 3；同时存在一项只读能力被 Planner/Executor 共同复用（同一 MCP 接口），并通过权限配置保证 Planner 无法调用副作用工具。

### V2-c — Executor Reflection + Snapshot（精简版，对应 `CLAUDE.md` 决策 10）

**状态**: ✅ **已完成 (2026-04-09)**
**测试**: 46 项专项测试（26 单元 + 20 集成），全部通过

**目标**：在**不扩大 Executor 重规划权限**的前提下，让 Supervisor 在「正常 completed / failed 之外」多一种**结构化中间信号**：执行中途自检偏离并**暂停上报**，由 Supervisor 沿用既有 **call_planner / call_executor** 管线决策（**不**单独实现「轻/中/重干预」三套并行逻辑，避免与决策 4 打架）。

**核心特性**：

- [x] **步骤计数器** + 可配置 **`REFLECTION_INTERVAL`**
- [x] **Reflection 节点**：LLM 自评路径是否偏离、置信度（0.0~1.0）；低于 **`CONFIDENCE_THRESHOLD`** 时可额外触发
- [x] **Snapshot 最小结构**：当前进度摘要 + Reflection 结论 + 建议（结构化，便于 Supervisor 解析）
- [x] **上报语义**：Executor **停止当前轮次**，将 Snapshot 经与 `ExecutorResult` **可并列或可扩展**的通道交给 Supervisor；Supervisor **只**决定：继续执行（传 `plan_id`）/ 重规划（`task_core` + `plan_id`）/ 结束——与 V1 失败分支一致，**不**在 V2 引入新的「干预分级」状态机
- [x] **默认配置**: `REFLECTION_INTERVAL=0`（关闭），按需配置为正整数启用周期性 Reflection
- [ ] **可选（降低范围）**：Plan **milestone** 字段与「到达必上报」可列为 V2-c 的 stretch，未做则不影响 V2-a/V2-b 验收

**验收标准**：
在到达 Reflection 触发条件时，Executor 产出 Snapshot 并停止；Supervisor 能基于 Snapshot + 现有 session 状态完成一轮续跑或重规划，全程仍满足决策 4（Executor 不内部改 Plan）。

---

## V2 测试覆盖总结（2026-04-09）

| 功能 | 单元测试 | 集成测试 | 总计 | 状态 |
|---------|---------|---------|------|------|
| V2-a: 工具输出治理 | ✅ | ✅ | 多项 | 完成 |
| V2-b: Planner 工具 + MCP | ✅ 30项 | ✅ 32项 | 62项 | 完成 |
| V2-c: Reflection/Snapshot | ✅ 26项 | ✅ 20项 | 46项 | 完成 |
| **总计** | **266项** | **65项** | **331项** | **全部通过** |

**测试改进**：
- 从基线 224 项测试增至 331 项（+107 项）
- V2-c Reflection 从完全未测试到 46 项专项测试
- V2-b MCP 从基础测试到 62 项全面测试
- 覆盖边界情况、错误处理、并发场景

**V2 实施备注**（2026-04）：V2-a/V2-b/V2-c 已完成代码落地并通过 unit + integration 测试；`REFLECTION_INTERVAL` 默认值为 `0`（关闭），按需配置为正整数即可开启周期性 Reflection。

---

## V3.0 — 多 Executor 并行与规模化

**状态**: ✅ **已完成 (2026-04-10)**
**验证**: 全部 V3 功能已实现并通过 380 项测试（17 项 V3 专项测试）

**目标**：落实 `CLAUDE.md` **决策 11**（fan-out / 多实例），并在并行下补齐 **决策 9** 末尾已提示的「Planner 会话顺序化 / 锁」，避免多路写同一 `plan_id` 时消息乱序。

**核心特性**：

- [x] **Supervisor fan-out**：将 Plan 拆为多个子 Plan（或子目标），并行分发给多个 Executor 实例
- [x] **并行 Executor 管理**：asyncio.gather、LangGraph Parallel 等，各 Executor 独立 State
- [x] **Completion 融合**：Supervisor 收集各 Executor 结果（含 summary / updated_plan 片段），ReAct 融合与冲突处理
- [x] **fan-in 策略**：多路输出冲突时的合并优先级（可配置或提示词约束）
- [x] **Planner 会话与并行**：`plan_id` 索引的会话在并发写入下的顺序化或锁（决策 9）
- [x] **上下文治理延伸**：并行时多路 observation 叠加，**必须**复用并加固 V2-a 的预算策略（否则 V3 必爆 token）

**验收标准**：
给定可拆为 N 条独立路径的任务，能并行执行并融合为单一用户可见答案；并发下无 Planner 会话错乱；在故意制造大工具输出时仍符合 V2-a 的边界行为。

---

## V3 测试覆盖总结（2026-04-10）

| 功能 | 单元测试 | 集成测试 | 总计 | 状态 |
|---------|---------|---------|------|------|
| V3: 基础并行执行 | ✅ 4项 | ✅ 3项 | 7项 | 完成 |
| V3: 批次构建与依赖解析 | ✅ 2项 | ✅ 2项 | 4项 | 完成 |
| V3: 结果融合与策略 | ✅ 2项 | ✅ 4项 | 6项 | 完成 |
| **总计** | **8项** | **9项** | **17项** | **全部通过** |

**测试改进**：
- 新增 V3 专项测试 17 项（单元 8 项 + 集成 9 项）
- 覆盖并行执行、批次构建、依赖解析、结果融合
- 验证 fan-out/fan-in 策略与边界控制
- 确保与 V2-a 上下文治理的兼容性

**V3 实施备注**（2026-04）：V3 已完成代码落地并通过测试；并行执行在 `call_executor` 中自动检测可并行步骤并启用；通过子计划隔离避免了 Planner 会话并发写入问题；融合结果时应用 V2-a 的字符预算策略。

---

## 里程碑时间线（参考）

```
[V1] 单线程闭环 MVP
[V2] 运行时边界（V2-a）→ Planner 工具（V2-b）→ Reflection/Snapshot 精简（V2-c）
[V3] 并行 + 融合 + 并行下的会话与上下文治理
```

> 推荐在 V2 内按 **a → b → c** 顺序交付：先可运维、再增强 Planner、最后加执行中检查点。  
> 若资源有限，可**先发布 V2-a 单独小版本**，再合入 b/c。  
> 每个版本发布前须通过对应的验收标准测试。
>
> 实施备注（2026-04）：V2-a/V2-b/V2-c 已完成代码落地并通过 unit + integration 测试；`REFLECTION_INTERVAL` 默认值为 `0`（关闭），按需配置为正整数即可开启周期性 Reflection。
