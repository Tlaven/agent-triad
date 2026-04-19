---
type: source
title: "Architecture Decisions"
source: docs/architecture-decisions.md
ingested: 2026-04-19
tags:
  - source
  - architecture
  - decisions
status: seed
related:
  - "[[Three-Agent Architecture]]"
  - "[[Execution Modes]]"
  - "[[Plan JSON]]"
---

# Architecture Decisions

Key decisions extracted from the AgentTriad architecture decisions document.

---

## Decision 3: Intent-Layer Plan
Planner does not know tool names. Only writes `intent` / `expected_output` per step. This decouples planning from execution implementation details.

## Decision 4: Executor Stops on Failure
Executor does not replan internally. It stops and returns failure status. Replanning is exclusively a Supervisor responsibility.

## Decision 5/5.1: Failure Handling
Structured failure handling matrix based on ExecutorResult status, updated_plan_json availability, and replan_count.

## Decision 6: Session Sync
- After `call_planner`: plan_json written to Planner session
- After `call_executor`: Update last_executor_*; refresh plan_json only if non-empty
- Empty plan_json -> preserve original

## Decision 8: Three Execution Modes
Mode 1 (Direct), Mode 2 (Tool-use), Mode 3 (Plan-Execute) with automatic mode selection.

## Decision 9: Planner Session
Same plan_id reuses the planning conversation thread. Enables iterative refinement.

## Decision 10: Reflection
REFLECTION_INTERVAL=0 disables by default. Positive integer enables periodic reflection during execution.

## Decision 11: Single Executor Call
Supervisor dispatches only one Executor at a time. No concurrent executor dispatch.

## Decision 12: Planner Read-Only
Planner only has read-only workspace tools and read-only MCP. No side-effect tools.
