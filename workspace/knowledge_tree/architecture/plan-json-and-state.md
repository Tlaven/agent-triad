---
title: Plan JSON 结构与 State 管理
source: project_seed
created_at: '2026-05-06T00:00:00+08:00'
metadata:
  category: architecture
---

Plan JSON 是意图层规划的核心数据结构，由 Planner 生成，Supervisor 和 Executor 共用。

Plan JSON 结构：
- plan_id：同一任务重规划期间保持不变
- version：每次重规划递增
- goal：任务总体目标
- steps[]：步骤数组，每步含 step_id、intent（意图描述，不含工具名）、
  expected_output（可验证完成标准）、status（pending/completed/failed/skipped）、
  result_summary、failure_reason、parallel_group（可选，同值步骤可并行）
- overall_expected_output：任务最终产出定义

State 管理（src/supervisor_agent/state.py）：
- State TypedDict：messages、plan_json、planner_session、executor_task_history 等
- PlannerSession：plan_json、planner_reasoning、规划对话消息列表，按 plan_id 索引
- ActiveExecutorTask：plan_id、status、派发时间，追踪进行中的执行任务

会话同步规则：
- call_planner 后：plan_json 写入 PlannerSession
- call_executor 后：更新 executor_task_history；updated_plan_json 非空则刷新 plan_json
- completed 时 Supervisor 默认只收 summary

Step 归一化：Planner 输出的 step_id 统一为字符串格式（如 "step_1"）。
