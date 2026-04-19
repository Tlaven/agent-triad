---
type: concept
title: "Three-Agent Architecture"
complexity: intermediate
domain: multi-agent-system
aliases:
  - "Triad Pattern"
  - "SPE Architecture"
created: 2026-04-19
updated: 2026-04-19
tags:
  - concept
  - architecture
  - supervisor
  - planner
  - executor
status: mature
related:
  - "[[overview]]"
  - "[[Execution Modes]]"
  - "[[Supervisor Agent]]"
  - "[[Planner Agent]]"
  - "[[Executor Agent]]"
sources: []
---

# Three-Agent Architecture

AgentTriad 采用三层协作架构：**Supervisor**（调度）、**Planner**（意图层规划）、**Executor**（自主执行）。每层职责明确隔离，通过结构化数据契约（Plan JSON / ExecutorResult）通信。

---

## Agent Roles

### Supervisor Agent
- **Role**: ReAct 主循环，负责模式决策和工具派发
- **Entry**: `src/supervisor_agent/graph.py`
- **Key behaviors**:
  - Mode selection (Direct / Tool-use / Plan-Execute)
  - Failure handling and replanning orchestration
  - Session state management across calls

### Planner Agent
- **Role**: Intent-layer task planning (does NOT know tool names)
- **Entry**: `src/planner_agent/graph.py`
- **Output**: PlannerOutput (reasoning + plan_json)
- **Constraints**: Read-only tools only (Decision 12)
- **Session**: Reuses planning conversation thread per plan_id (Decision 9)

### Executor Agent
- **Role**: Autonomous task execution with tool selection
- **Entry**: `src/executor_agent/graph.py`
- **Subprocess**: Independent FastAPI process per task (V3)
- **Timeouts**: LLM call 180s, tool execution 300s

---

## Communication Contracts

```
Supervisor -> Planner:   task_core (intent or modification direction)
Planner -> Supervisor:   [PLANNER_REASONING]...[/PLANNER_REASONING] + Plan JSON
Supervisor -> Executor:  task_description (Mode 2) or plan_id (Mode 3)
Executor -> Supervisor:  ExecutorResult (status + summary + updated_plan_json)
```

---

## Isolation Principles

1. **Intent vs Execution**: Planner writes WHAT (intent), Executor decides HOW (tool selection)
2. **Replanning authority**: Only Supervisor can trigger replanning
3. **Process isolation**: Each Executor call runs in independent subprocess (V3)
4. **Session continuity**: Planner reuses conversation per plan_id; Executor starts fresh per call

---

## Connections

See [[Supervisor Agent]], [[Planner Agent]], [[Executor Agent]] for individual agent details.
See [[Execution Modes]] for how Supervisor selects between modes.
See [[Plan JSON]] for the structured plan format.
