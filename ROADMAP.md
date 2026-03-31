# ROADMAP

---

## V1.0 — 单线程闭环 MVP

**目标**：验证 Supervisor → Planner → Executor 基础架构可行性，实现完整的单轮任务执行闭环。

**核心特性**：

- [ ] **Supervisor ReAct 主循环**：call_model + dynamic_tools_node + 路由判断
- [ ] **Supervisor 三路决策**：
  - 简单无工具任务：直接合成答案
  - 简单需工具任务：生成简化 Plan 调单 Executor
  - 复杂任务：先调 Planner 再调 Executor
- [ ] **generate_plan 工具**（InjectedState）：从 state.messages 取历史，调 Planner 生成 Plan JSON
- [ ] **execute_plan 工具**（InjectedState）：从 state.session.plan_json 取计划，调 Executor 执行
- [ ] **Planner Agent**：单次 LLM 调用，输出意图层 Plan JSON（不含工具名）
- [ ] **Executor Agent**：ReAct 循环（Thought → Action → Observation），自主选工具
- [ ] **ExecutorResult 结构化返回**：status / updated_plan_json / summary
- [ ] **失败处理双重保障**：正常失败上报 + 异常崩溃保底标记（`_mark_plan_steps_failed`）
- [ ] **重规划闭环**：最多 MAX_REPLAN 次，带状态 Plan 传给 Planner 修订
- [ ] **dynamic_tools_node 双向同步**：session.plan_json 始终最新
- [ ] **基础 Executor 工具**：`write_file` + `run_local_command`
- [ ] **基础单元测试**：JSON 提取 / 失败标记 / State 解析

**验收标准**：  
给定一个多步骤任务，系统能完成"计划生成 → 工具执行 → 失败时重规划（最多 3 次）→ 最终答案"完整流程，无隐性崩溃，执行状态在 updated_plan_json 中完整可读。

---

## V2.0 — Executor Reflection + Snapshot 上报

**目标**：引入 Executor 自我监控能力，使 Supervisor 可在任务中途干预，避免 Executor 偏离目标后才发现。

**核心特性**：

- [ ] **Executor 步骤计数器**：内置计数，每 `REFLECTION_INTERVAL` 步触发 Reflection 节点
- [ ] **Reflection 节点**：LLM 自评当前执行路径是否偏离目标，输出置信度评分（0.0~1.0）
- [ ] **置信度触发**：低于 `CONFIDENCE_THRESHOLD` 时强制触发 Reflection（即使未达步数间隔）
- [ ] **Snapshot 数据结构**：当前已完成步骤 + Reflection 结论 + 建议（结构化）
- [ ] **Snapshot 上报通道**：Executor → Supervisor（在 Completion Report 之前可多次触发）
- [ ] **Supervisor 干预分级**：
  - 轻干预：局部调整 Plan 文本（发回原 Executor 继续）
  - 中/重干预：调 Planner 局部或全局重规划
- [ ] **里程碑触发**：到达 Plan 中的 milestone step 时，无论 Reflection 结果如何，均上报一次 Snapshot
- [ ] **新增配置项**：`REFLECTION_INTERVAL`、`CONFIDENCE_THRESHOLD`

**验收标准**：  
Executor 在执行第 N 步后触发 Reflection，检测到偏差时上报 Snapshot，Supervisor 正确识别干预级别：轻干预时局部调整 Plan 后 Executor 继续执行，中/重干预时触发 Planner 重规划。

---

## V3.0 — 多 Executor 并行 + Memory 归档

**目标**：支持复杂任务并行分解执行，并建立跨会话长期记忆，完成完整流程图设计目标。

**核心特性**：

- [ ] **Supervisor fan-out**：将 Plan 拆分为多个子 Plan，并行分发给多个 Executor 实例
- [ ] **并行 Executor 管理**：asyncio.gather 或 LangGraph Parallel 节点，各 Executor 独立 State
- [ ] **Completion Report 融合**：Supervisor 收集所有 CompletionReport，进行 ReAct 循环融合（冲突解决 + 质量把关）
- [ ] **Strategic Memory 归档**：任务类型 + 成功执行路径摘要，跨会话持久化（向量数据库或文件存储）
- [ ] **Episodic Memory 归档**：完整执行轨迹（含 Snapshot 历史），用于调试和 Eval
- [ ] **Memory 检索接口**：Supervisor 在新任务开始时查询相似历史经验，纳入 Planner 上下文
- [ ] **Supervisor fan-in 冲突解决策略**：定义多 Executor 输出冲突时的合并优先级规则

**验收标准**：  
给定一个可拆分为 N 条独立路径的任务，系统能并行执行并融合结果；最终答案质量优于单线程顺序执行；执行轨迹正确归档并可被后续任务检索。

---

## 里程碑时间线（参考）

```
[V1] 单线程闭环 MVP          ← 当前阶段
[V2] Reflection + Snapshot   ← V1 稳定后
[V3] 并行 + Memory           ← V2 稳定后
```

> 版本之间严格串行推进，不跨版本抢先实现功能。  
> 每个版本发布前须通过对应的验收标准测试。
