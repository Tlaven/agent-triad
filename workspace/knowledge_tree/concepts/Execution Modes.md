---
type: concept
title: "Execution Modes"
complexity: intermediate
domain: multi-agent-system
aliases:
  - "Mode Selection"
  - "Three Modes"
created: 2026-04-19
updated: 2026-04-19
tags:
  - concept
  - execution
  - supervisor
  - decision-logic
status: mature
related:
  - "[[overview]]"
  - "[[Three-Agent Architecture]]"
  - "[[Supervisor Agent]]"
sources: []
---

# Execution Modes

Supervisor 根据任务复杂度自动选择三种执行模式之一。决策逻辑基于任务是否需要工具、是否多步依赖等因素。

---

## Mode Comparison

| Mode | Trigger | Tools | plan_id | Complexity |
|------|---------|-------|---------|-----------|
| 1 Direct Response | No tools needed | None | N/A | Simplest |
| 2 Tool-use ReAct | Short single-task | Executor | Auto-generated new | Moderate |
| 3 Plan -> Execute | Multi-step dependencies | Planner + Executor | Explicit, reused on replan | Complex |

---

## Mode 1: Direct Response

- **When**: Task can be answered without any tool calls
- **Behavior**: Supervisor responds directly
- **Examples**: Knowledge questions, simple clarifications

## Mode 2: Tool-use ReAct

- **When**: Task requires tool execution but is short and single-step
- **Behavior**: `call_executor(task_description="...")` with `wait_for_result=True`
- **plan_id**: Auto-generated new ID per call; each call spawns a new subprocess
- **Failure**: Can escalate to Mode 3 if summary indicates need for plan-level restructuring

## Mode 3: Plan -> Execute

- **When**: Multi-step task with dependencies between steps
- **Behavior**: `call_planner(task_core)` -> `call_executor(plan_id)`
- **plan_id**: Explicit; reused across replanning iterations
- **Subprocess**: Same plan_id reuses existing subprocess if still running
- **Parallel**: Steps with same `parallel_group` can run concurrently

---

## Mode Escalation

```
Mode 1 -> Mode 2: Task needs tools
Mode 2 -> Mode 3: Executor fails, summary indicates need for plan restructuring
Mode 3 -> replan: Executor fails, updated_plan_json available, replan_count < MAX_REPLAN
```

---

## Connections

See [[Supervisor Agent]] for the mode selection implementation.
See [[Plan JSON]] for the Mode 3 plan format.
See [[Three-Agent Architecture]] for agent role context.
