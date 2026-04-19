---
type: concept
title: "Plan JSON"
complexity: intermediate
domain: task-planning
aliases:
  - "Plan Format"
  - "Intent Layer Plan"
created: 2026-04-19
updated: 2026-04-19
tags:
  - concept
  - planning
  - json
  - contract
status: mature
related:
  - "[[Three-Agent Architecture]]"
  - "[[Planner Agent]]"
  - "[[Executor Agent]]"
sources: []
---

# Plan JSON

Plan JSON 是 Planner 输出的意图层任务计划格式，是 Supervisor 和 Executor 之间的核心数据契约。关键原则：**Planner 不知工具名，只写 intent / expected_output**（决策 3）。

---

## Schema

```json
{
  "plan_id": "string",
  "version": 1,
  "goal": "Overall task goal",
  "steps": [
    {
      "step_id": "s1",
      "intent": "What to accomplish (NOT how)",
      "expected_output": "Expected result description",
      "status": "pending | completed | failed | skipped",
      "result_summary": "Filled after execution",
      "failure_reason": "Filled on failure",
      "parallel_group": "optional-group-name or null"
    }
  ]
}
```

---

## Key Rules

- **step_id**: Normalized to string by `call_planner`
- **intent**: Describes WHAT to do, never names specific tools
- **parallel_group**: Steps with the same non-null value can be executed in parallel; `null` means sequential
- **status transitions**: pending -> completed | failed | skipped
- **version**: Incremented on replan

---

## Replanning

When replanning occurs:
- `plan_id` is reused (same planning conversation thread)
- Planner receives the failed `task_core` (summary of failure)
- `updated_plan_json` is returned with new version and revised steps
- Supervisor tracks `replan_count` and enforces `MAX_REPLAN` limit

---

## Connections

See [[Three-Agent Architecture]] for how Plan JSON flows between agents.
See [[Execution Modes]] for when Plan JSON is used (Mode 3 only).
