---
title: Observation 治理与 Reflection 快照
source: project_seed
created_at: '2026-05-06T00:00:00+08:00'
metadata:
  category: patterns
---

Observation 治理（V2-a）：工具返回进入 ReAct 消息历史前统一规范化。
src/common/observation.py 负责：长输出截断并显式标注、超大输出外置为文件并返回引用路径。
目标：避免单条 observation 撑爆上下文，保证行为可预测。
配置：observation_max_length、observation_external_threshold 等 Context 字段。

Reflection（V2-c，决策 10）：Executor 执行过程中的自我检查机制。
- REFLECTION_INTERVAL：每隔 N 步触发一次反思（默认 0 关闭）
- 置信度触发：Executor 对当前步骤不确定时主动暂停
- 暂停后通过 snapshot_json 上报结构化快照

snapshot_json 格式（paused 时）：
- trigger_type：触发类型（interval/confidence/manual）
- current_step：当前步骤 ID
- confidence_score：置信度分数
- reflection_analysis：反思分析文本
- suggestion：建议动作（continue/replan/abort）
- progress_summary：进度摘要

Supervisor 收到 paused 后决定：继续执行、重规划、或终止任务。
Reflection 默认关闭（REFLECTION_INTERVAL=0），需要显式启用。
