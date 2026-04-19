---
type: entity
title: "Supervisor Agent"
complexity: intermediate
domain: multi-agent-system
aliases:
  - "Supervisor"
  - "Main Loop"
created: 2026-04-19
updated: 2026-04-19
tags:
  - entity
  - agent
  - supervisor
  - react
status: mature
related:
  - "[[Three-Agent Architecture]]"
  - "[[Execution Modes]]"
  - "[[Planner Agent]]"
  - "[[Executor Agent]]"
  - "[[Plan JSON]]"
sources: []
---

# Supervisor Agent

Supervisor 是 AgentTriad 的主控 Agent，运行 ReAct 循环，负责模式决策、工具派发、失败处理和会话管理。

---

## Key Files

| Path | Role |
|------|------|
| `src/supervisor_agent/graph.py` | Main loop, `call_model`, `dynamic_tools_node` |
| `src/supervisor_agent/state.py` | `State`, `PlannerSession`, `ActiveExecutorTask` |
| `src/supervisor_agent/tools.py` | `call_planner`, `call_executor`, `get_executor_result` |

---

## Available Tools

- `call_planner(task_core, plan_id?)` -- Invoke Planner for plan creation/replan
- `call_executor(task_description | plan_id, wait_for_result)` -- Dispatch Executor
- `get_executor_result(plan_id, detail)` -- Retrieve execution result
- `list_executor_tasks()` -- List active/recent executor tasks

---

## Failure Handling

| Executor status | Action |
|----------------|--------|
| completed | Use summary, end |
| paused | Read snapshot; continue or replan |
| failed + updated_plan_json | Replan with plan_id |
| failed + empty plan_json | May escalate Mode 2 -> 3 |
| failed + replan_count >= MAX | Failure analysis, terminate |

---

## Session Sync

- After `call_planner`: plan_json written to Planner session
- After `call_executor`: Update last_executor_*; refresh plan_json if non-empty
- Messages accumulate across all calls (full conversation history)

---

## Connections

See [[Execution Modes]] for mode selection logic.
See [[Three-Agent Architecture]] for overall system context.
