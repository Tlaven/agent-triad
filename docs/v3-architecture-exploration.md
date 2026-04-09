# V3 架构深度探索（fan-out / fan-in）

本文档给出 V3 的可落地设计，目标是基于已完成 V2 的稳定基础，逐步演进到多 Executor 并行执行。

## 1. Plan Schema 扩展（V3-a）

在保持 V2 兼容的前提下，允许 Planner 输出以下可选字段：

- `depends_on: string[]`：当前步骤的前置步骤 `step_id` 列表
- `parallel_group: string`：同一层内可并行且可归组执行的标签

示例：

```json
{
  "goal": "完成多源调研并汇总结论",
  "steps": [
    {
      "step_id": "step_1",
      "intent": "收集 A 源信息",
      "expected_output": "A 源关键事实清单",
      "depends_on": [],
      "parallel_group": "research",
      "status": "pending",
      "result_summary": null,
      "failure_reason": null
    },
    {
      "step_id": "step_2",
      "intent": "收集 B 源信息",
      "expected_output": "B 源关键事实清单",
      "depends_on": [],
      "parallel_group": "research",
      "status": "pending",
      "result_summary": null,
      "failure_reason": null
    },
    {
      "step_id": "step_3",
      "intent": "融合 A/B 结果并生成结论",
      "expected_output": "统一结论与依据",
      "depends_on": ["step_1", "step_2"],
      "status": "pending",
      "result_summary": null,
      "failure_reason": null
    }
  ]
}
```

兼容策略：

- 没有 `depends_on` 时视为 `[]`
- 没有 `parallel_group` 时按单步独立批次执行

## 2. 并行执行模型（V3-b）

推荐分两阶段：

1. **Phase 1**：`asyncio.gather`（最小改动）
   - 在 Supervisor 侧将可并行步骤批次化
   - 每个批次并发调用 `run_executor()`
2. **Phase 2**：LangGraph fan-out 节点（更高可观测性）
   - 用图级并行节点替换工具内并行
   - 支持更细粒度的 tracing 与重试

并行上限通过 `Context.max_parallel_executors` 控制。

## 3. Fan-in 融合策略（V3-c）

融合目标：

- 合并多路 `summary`
- 合并多路 `updated_plan.steps` 状态
- 保证失败优先级高于成功（冲突时保留失败原因）

建议优先级：

- `failed` > `completed` > `skipped` > `pending`

合并后输出：

- 一份规范化 `updated_plan_json`
- 一份有长度预算的融合摘要（受 `fanin_summary_max_chars` 限制）

## 4. 并发安全与预算治理（V3-d）

### 4.1 Planner 会话并发安全

避免同一 `plan_id` 并发写：

- 默认策略：`single_replanner`（同一时刻只允许一个重规划写操作）
- 可选策略：`queued_replanner`（失败分支入队，顺序消费）

配置项：`Context.planner_parallel_replan_mode`

### 4.2 Observation 预算在并行下的扩展

- 继续复用 V2-a 规范化机制（截断/外置）
- 引入双层预算：
  - 单 Executor 预算：`max_observation_chars`
  - Fan-in 聚合预算：`fanin_summary_max_chars`

### 4.3 工作目录隔离建议

并行写入场景推荐使用：

- `workspace/.observations/<executor_id>/...`
- 或外层调度统一分配子目录，防止文件名冲突

## 5. 代码落地点

- `src/supervisor_agent/parallel.py`
  - `build_execution_batches()`
  - `merge_parallel_step_states()`
  - `merge_fanin_summaries()`
- `src/common/context.py`
  - `max_parallel_executors`
  - `fanin_summary_max_chars`
  - `planner_parallel_replan_mode`
- `src/planner_agent/prompts.py`
  - 明确 `depends_on` 与 `parallel_group` 为可选输出字段

## 6. 验收建议

1. 构造 2-3 个独立步骤 + 1 个依赖聚合步骤，验证分批顺序正确
2. 构造并行分支中的成功/失败冲突，验证 fan-in 优先级
3. 构造超长多路摘要，验证聚合摘要可截断且可感知
4. 构造并发重规划请求，验证单写策略不乱序
