---
type: entity
title: "Planner Agent"
complexity: intermediate
domain: multi-agent-system
aliases:
  - "Planner"
created: 2026-04-19
updated: 2026-04-19
tags:
  - entity
  - agent
  - planner
  - planning
status: mature
related:
  - "[[Three-Agent Architecture]]"
  - "[[Plan JSON]]"
  - "[[Supervisor Agent]]"
sources: []
---

# Planner Agent

Planner 是意图层规划器，接收任务核心描述，输出结构化 Plan JSON。关键约束：**只读工具、不知工具名、同 plan_id 复用对话线程**。

---

## Key Files

| Path | Role |
|------|------|
| `src/planner_agent/graph.py` | Planner graph, `PlannerOutput`, `run_planner()` |
| `src/planner_agent/prompts.py` | System prompt and planning instructions |
| `src/planner_agent/tools.py` | Read-only workspace tools |

---

## Read-Only Tools (Decision 12)

- `read_workspace_text_file` -- Read file contents
- `list_workspace_entries` -- List directory entries
- `search_files` -- Glob pattern search
- `grep_content` -- Regex content search
- `read_file_structure` -- Directory tree

Plus: read-only MCP tools (if enabled).

---

## Output Format

```
[PLANNER_REASONING]
Analysis of task, decomposition reasoning, dependency mapping...
[/PLANNER_REASONING]

{Plan JSON with plan_id, version, goal, steps[]}
```

`dynamic_tools_node` in Supervisor separates reasoning from plan_json.

---

## Session Behavior

- Same `plan_id` reuses the planning conversation thread (Decision 9)
- Replanning: receives failed summary as `task_core`, outputs updated Plan JSON
- Version increments on each replan

---

## Connections

See [[Plan JSON]] for the output format specification.
See [[Three-Agent Architecture]] for Planner's role in the system.
