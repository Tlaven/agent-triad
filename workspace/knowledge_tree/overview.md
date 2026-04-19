---
type: concept
title: "AgentTriad Overview"
complexity: intermediate
domain: multi-agent-system
aliases:
  - "AgentTriad System"
  - "Triad Framework"
created: 2026-04-19
updated: 2026-04-19
tags:
  - concept
  - architecture
  - overview
status: mature
related:
  - "[[Three-Agent Architecture]]"
  - "[[Execution Modes]]"
  - "[[Plan JSON]]"
sources: []
---

# AgentTriad Overview

AgentTriad 是一个三层多 Agent 框架，将自然语言任务分解为计划-执行-融合的闭环流程。核心设计原则：**意图与执行分离、渐进式复杂度、进程级隔离**。

---

## Architecture

```
User -> Supervisor (ReAct Loop)
         |-> call_planner -> Planner (intent-layer Plan JSON)
         `-> call_executor -> Executor (autonomous tool selection)
```

入口：`langgraph.json` -> `src/supervisor_agent/graph.py:graph`

---

## Three Execution Modes

| Mode | Scenario | Behavior |
|------|----------|----------|
| 1 Direct Response | No tools needed | Direct answer |
| 2 Tool-use ReAct | Short tasks | Executor + task_description |
| 3 Plan -> Execute | Multi-step with dependencies | Planner -> Executor + plan_id |

Mode selection is automatic based on task complexity analysis.

---

## Key Design Decisions

- **Intent-layer Plan** (Decision 3): Planner does not know tool names, only writes intent/expected_output
- **Executor stops on failure** (Decision 4): No replanning inside Executor; replanning is Supervisor-only
- **Single Executor call** (Decision 11): Supervisor dispatches one Executor at a time
- **Planner is read-only** (Decision 12): Planner only has read-only workspace tools + MCP

---

## V3 Process Isolation

Each `call_executor` spawns an independent subprocess with its own FastAPI + uvicorn server. Communication is dual-path: Push (Executor -> Mailbox) + Pull (Poller GET /result fallback).

---

## Connections

See [[Three-Agent Architecture]] for detailed agent roles and interactions.
See [[Execution Modes]] for mode selection decision logic.
See [[Plan JSON]] for the Plan JSON specification.
