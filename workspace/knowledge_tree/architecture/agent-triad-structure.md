---
title: AgentTriad 三层架构
source: project_seed
created_at: '2026-05-06T00:00:00+08:00'
metadata:
  category: architecture
---

AgentTriad 采用三层智能体架构：Supervisor（ReAct 主循环）→ Planner（意图层规划）→ Executor（工具执行）。

Supervisor 是决策中心，通过分析用户意图自动选择三种模式：
- Mode 1（Direct Response）：简单问答，无需工具，直接回复
- Mode 2（Tool-use ReAct）：短流程任务，传 task_description 给 Executor 执行
- Mode 3（Plan → Execute）：多步依赖任务，先 call_planner 生成 Plan JSON，再 call_executor 按 plan_id 执行

Planner 只读工具生成 Plan JSON（不含工具名，只有 intent 和 expected_output），
通过 plan_id 复用规划对话线程。可用工具：read_workspace_text_file、search_files、grep_content 等。

Executor 自主选择工具执行具体步骤，遇阻即停（不内部重规划），返回 ExecutorResult 给 Supervisor。
Mode 3 失败时 Supervisor 可重规划（最多 MAX_REPLAN 次）。

入口：langgraph.json → supervisor_agent/graph.py:graph。
模块速查见 CLAUDE.md，各 Agent 默认模型见 config/agent_models.toml。
