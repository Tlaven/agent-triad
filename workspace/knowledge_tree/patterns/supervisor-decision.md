---
title: Supervisor Mode 1/2/3 决策路由
source: project_seed
created_at: '2026-05-06T00:00:00+08:00'
metadata:
  category: patterns
---

Supervisor 通过分析用户意图自动选择三种模式：

Mode 1（Direct Response）：无需工具，直接回答用户问题（如"解释 RAG 原理"）。
适用场景：简单问答、知识解释。

Mode 2（Tool-use ReAct）：单步或短流程工具执行。
调用方式：call_executor(task_description="具体任务描述")。
每次调用生成新 plan_id 并 spawn 新子进程。wait_for_result=True 时自动阻塞等待结果。
适用场景：目标明确的短任务（如"读取配置文件并解释"）。

Mode 3（Plan → Execute → Summarize）：多步骤有依赖的任务。
调用方式：先 call_planner(task_core="目标") 生成 Plan JSON，
再 call_executor(plan_id="xxx") 按 plan 执行。
同 plan_id 复用 Planner 对话线程和 Executor 子进程。
适用场景：复杂分析、多步修复、代码重构。

Mode 2 失败且 summary 表明需计划层重构时，Supervisor 可升级到 Mode 3。
重规划权只在 Supervisor，Executor 遇阻即停。MAX_REPLAN 限制防止无限循环。
