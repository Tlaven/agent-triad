---
title: Executor 结果格式与错误处理
source: project_seed
created_at: '2026-05-06T00:00:00+08:00'
metadata:
  category: conventions
---

Executor 结果通过 [EXECUTOR_RESULT] meta JSON 标记传递，包含：
- status（completed/failed/paused）
- summary（执行摘要）
- updated_plan_json（步骤级状态，Mode 2 可空）
- snapshot_json（paused 时结构化快照，含 trigger_type、confidence_score、suggestion）

失败处理遵循决策 4/5/5.1：
- completed → 用 summary 收束，结束
- failed + updated_plan_json 非空 → summary 作为 task_core 重规划
- failed + 空 → 可升级 Mode 3（基于 summary 判断）
- failed 且 replan_count >= MAX_REPLAN → 失败分析，终止

正常失败由 Executor 写 status/failure_reason；异常由 _mark_plan_steps_failed() 兜底。
MAX_REPLAN 限制防止无限重规划循环。

超时保护（三层）：
- executor_call_model_timeout（180s）：单次 LLM 调用超时 → 抛异常终止进程
- executor_tool_timeout（300s）：tools_node 超时 → 返回部分结果让 LLM 摘要
- executor_wait_timeout（300s）：Supervisor 侧等待超时 → 终止 executor 进程并标记失败

Observation 治理（V2-a）：工具返回进入历史前统一截断，超大输出外置为文件并返回引用路径。
