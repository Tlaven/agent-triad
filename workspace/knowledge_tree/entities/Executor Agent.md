---
type: entity
title: "Executor Agent"
complexity: intermediate
domain: multi-agent-system
aliases:
  - "Executor"
  - "Task Executor"
created: 2026-04-19
updated: 2026-04-19
tags:
  - entity
  - agent
  - executor
  - subprocess
status: mature
related:
  - "[[Three-Agent Architecture]]"
  - "[[V3 Process Isolation]]"
  - "[[Supervisor Agent]]"
sources: []
---

# Executor Agent

Executor 是自主任务执行器，接收 Plan JSON 或 task_description，自主选择工具完成执行。运行在独立子进程中（V3），通过双路径通信返回结果。

---

## Key Files

| Path | Role |
|------|------|
| `src/executor_agent/graph.py` | Executor graph, Observation, Reflection, `run_executor()` |
| `src/executor_agent/server.py` | FastAPI subprocess server (/execute, /result, /stop) |
| `src/executor_agent/__main__.py` | Subprocess entry: dynamic port + uvicorn |
| `src/executor_agent/interrupt.py` | Soft interrupt (stop event, `run_with_interrupt_check`) |

---

## ExecutorResult

```python
@dataclass
class ExecutorResult:
    status: Literal["completed", "failed", "paused"]
    updated_plan_json: str   # May be empty in Mode 2
    summary: str
    snapshot_json: str = ""  # Structured snapshot when paused (e.g., Reflection)
```

---

## Timeout Protection

- `executor_call_model_timeout` (default 180s): Single LLM call timeout -> exception terminates process
- `executor_tool_timeout` (default 300s): Tools node timeout -> return partial result for LLM summary
- Supervisor `_wait_for_executor_result` timeout (default 120s) -> terminate executor, mark failed

---

## Subprocess API

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/execute` | POST | Receive task |
| `/result` | GET | Return ExecutorResult |
| `/stop` | POST | Soft interrupt |

---

## Connections

See [[V3 Process Isolation]] for subprocess lifecycle details.
See [[Three-Agent Architecture]] for Executor's role in the system.
