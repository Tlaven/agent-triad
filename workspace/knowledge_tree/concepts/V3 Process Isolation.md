---
type: concept
title: "V3 Process Isolation"
complexity: advanced
domain: process-management
aliases:
  - "Process Separation"
  - "Subprocess Architecture"
created: 2026-04-19
updated: 2026-04-19
tags:
  - concept
  - v3
  - process
  - isolation
  - lifecycle
status: mature
related:
  - "[[Three-Agent Architecture]]"
  - "[[Executor Agent]]"
sources: []
---

# V3 Process Isolation

V3 架构将每个 `call_executor` 派发到独立子进程中执行，实现进程级隔离。每个子进程启动自己的 FastAPI + uvicorn 服务器，动态分配端口。

---

## Architecture

```
Supervisor Process
  |-- V3LifecycleManager (lazy-load singleton)
  |     |-- ExecutorProcessManager  -- spawn/stop per-task subprocess
  |     |-- Mailbox                 -- thread-safe result cache (plan_id -> MailboxItem)
  |     |-- MailboxHTTPServer       -- background thread, Executor POST /inbox
  |     `-- ExecutorPoller          -- background asyncio task, polls GET /result
  `-- call_executor -> pm.start_for_task(plan_id) -> POST /execute
                          |
                    Executor Subprocess (FastAPI)
                      |-- POST /execute   -- receive task
                      |-- GET  /result    -- return ExecutorResult
                      |-- POST /stop      -- soft interrupt
                      `-- _push_result_to_mailbox -> POST {mailbox_url}/inbox
```

---

## Dual-Path Communication

1. **Push** (primary): Executor completes task -> POST result to Mailbox HTTP server
2. **Pull** (fallback): ExecutorPoller periodically GETs /result as safety net

Supervisor's `_wait_for_executor_result` blocks on Mailbox read.

---

## Subprocess Lifecycle

- **Spawn**: `python -m src.executor_agent` with dynamic port allocation
- **Port discovery**: Health check loop to find assigned port
- **Cleanup**: atexit + SIGTERM/SIGINT signal handlers ensure subprocess exits with main process
- **Terminate strategy**: `sync_terminate` uses terminate -> kill escalation

---

## Key Modules

| Module | Role |
|--------|------|
| `src/common/process_manager.py` | Per-task subprocess lifecycle |
| `src/common/mailbox.py` | Thread-safe per-plan result cache |
| `src/common/mailbox_server.py` | HTTP background thread for push |
| `src/common/polling.py` | Unified background polling |
| `src/executor_agent/server.py` | FastAPI subprocess server |

---

## Connections

See [[Executor Agent]] for the executor's internal architecture.
See [[Three-Agent Architecture]] for how process isolation fits the overall design.
