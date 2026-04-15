"""V3: Executor FastAPI server — runs in Process B.

Wraps executor_graph.ainvoke() as async background tasks.
Communication: Supervisor POSTs /execute, GETs /result, POSTs /stop.
Results stored in _results dict; Supervisor polls to retrieve them.
"""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from dataclasses import asdict
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel

from src.common.context import Context
from src.common.executor_protocol import ExecuteStatus, StopRequest
from src.executor_agent.graph import ExecutorResult, run_executor

logger = logging.getLogger(__name__)

# Process-level state: keyed by plan_id
_running_tasks: dict[str, asyncio.Task[Any]] = {}
_stop_events: dict[str, asyncio.Event] = {}
_results: dict[str, ExecutorResult] = {}
_statuses: dict[str, ExecuteStatus] = {}

# Maximum number of completed results to keep in memory
_MAX_STORED_RESULTS = 50


class ExecuteRequestBody(BaseModel):
    plan_json: str
    plan_id: str
    executor_session_id: str = ""
    config: dict[str, Any] = {}


class StopRequestBody(BaseModel):
    reason: str = ""


def _cleanup_old_results() -> None:
    """Evict oldest results when the dict exceeds the cap."""
    if len(_results) <= _MAX_STORED_RESULTS:
        return
    # Remove oldest entries (dict preserves insertion order in Python 3.7+)
    to_remove = len(_results) - _MAX_STORED_RESULTS
    keys = list(_results.keys())[:to_remove]
    for k in keys:
        _results.pop(k, None)


async def _run_executor_task(
    plan_json: str,
    plan_id: str,
    ctx: Context,
    trace_headers: dict[str, str] | None = None,
) -> None:
    """Background task: run executor_graph and store result in _results.

    After completion, pushes result to the Supervisor's mailbox (if MAILBOX_URL
    is configured) and schedules self-shutdown for per-task processes.

    If trace_headers is provided (langsmith-trace / baggage headers forwarded
    from the Supervisor), all executor nodes are nested under the Supervisor's
    LangSmith trace via tracing_context.
    """
    import langsmith as ls

    stop_event = _stop_events.get(plan_id)

    _statuses[plan_id].status = "running"

    try:
        # Mock mode for checkpoint testing — returns immediately without LLM.
        # Set EXECUTOR_MOCK_MODE=completed|failed to control behavior.
        _mock_mode = os.environ.get("EXECUTOR_MOCK_MODE", "")
        with ls.tracing_context(
            parent=trace_headers or {},
            metadata={"plan_id": plan_id},
        ):
            if _mock_mode:
                await asyncio.sleep(0.1)  # Simulate brief work
                if _mock_mode == "failed":
                    raise RuntimeError("Mock executor failure")
                result = ExecutorResult(
                    status="completed",
                    updated_plan_json=plan_json,
                    summary="Mock executor completed successfully",
                )
            else:
                result = await run_executor(plan_json, context=ctx)

        _results[plan_id] = result

        if stop_event and stop_event.is_set():
            actual_status = "stopped"
        else:
            actual_status = result.status

        _statuses[plan_id].status = actual_status

    except Exception as e:
        logger.error("Executor task failed for %s: %s", plan_id, e, exc_info=True)
        _results[plan_id] = ExecutorResult(
            status="failed",
            updated_plan_json="",
            summary=f"Executor crashed: {e}",
        )
        _statuses[plan_id].status = "failed"

    except asyncio.CancelledError:
        logger.warning("Executor task cancelled for %s", plan_id)
        _results[plan_id] = ExecutorResult(
            status="stopped",
            updated_plan_json="",
            summary=f"Executor task cancelled: {plan_id}",
        )
        _statuses[plan_id].status = "stopped"
    finally:
        _running_tasks.pop(plan_id, None)
        _stop_events.pop(plan_id, None)
        _statuses.pop(plan_id, None)
        _cleanup_old_results()

        # Push result to Supervisor's mailbox and schedule self-shutdown
        await _push_result_to_mailbox(plan_id)
        _schedule_self_shutdown()


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Executor server started (PID=%d)", os.getpid())
    yield
    # Cancel running tasks on shutdown
    for plan_id, task in _running_tasks.items():
        task.cancel()
        logger.info("Cancelled task for plan_id=%s during shutdown", plan_id)
    _running_tasks.clear()


app = FastAPI(title="AgentTriad Executor", lifespan=lifespan)


# ---------------------------------------------------------------------------
# Mailbox push + self-shutdown (per-task process lifecycle)
# ---------------------------------------------------------------------------

async def _push_result_to_mailbox(plan_id: str) -> None:
    """Push the task result to Supervisor's mailbox via HTTP POST.

    Retries up to 3 times on failure. If all retries fail, the result
    remains in _results for direct polling fallback.
    """
    mailbox_url = os.environ.get("MAILBOX_URL", "")
    if not mailbox_url:
        return

    result = _results.get(plan_id)
    if result is None:
        return

    payload = {
        "plan_id": plan_id,
        "item_type": "completion",
        "payload": {
            "plan_id": plan_id,
            "status": result.status,
            "updated_plan_json": result.updated_plan_json,
            "summary": result.summary,
            "snapshot_json": result.snapshot_json,
        },
    }

    for attempt in range(3):
        try:
            import httpx
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.post(f"{mailbox_url}/inbox", json=payload)
                if resp.status_code == 200:
                    logger.info("Pushed result for %s to mailbox (attempt %d)", plan_id, attempt + 1)
                    return
                logger.warning(
                    "Mailbox returned %d for %s (attempt %d)",
                    resp.status_code, plan_id, attempt + 1,
                )
        except (httpx.ConnectError, httpx.TimeoutException) as e:
            logger.warning(
                "Failed to push %s to mailbox (attempt %d): %s",
                plan_id, attempt + 1, e,
            )

        if attempt < 2:
            await asyncio.sleep(1.0)

    logger.error("All mailbox push attempts failed for %s — result available via /result", plan_id)


def _schedule_self_shutdown() -> None:
    """Schedule graceful self-shutdown after result push (per-task lifecycle).

    Only activates when MAILBOX_URL is set (per-task mode).
    """
    mailbox_url = os.environ.get("MAILBOX_URL", "")
    if not mailbox_url:
        return

    async def _do_shutdown():
        await asyncio.sleep(2.0)
        logger.info("Per-task self-shutdown: stopping Executor server")
        # Cancel all remaining tasks
        for pid, task in list(_running_tasks.items()):
            task.cancel()
        _running_tasks.clear()
        # Stop the uvicorn server
        import uvicorn
        for task in asyncio.all_tasks():
            if task is not asyncio.current_task():
                task.cancel()

    asyncio.create_task(_do_shutdown())


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/tasks")
async def list_tasks():
    """List all running tasks with brief status (for Supervisor context injection)."""
    result = {}
    # Active running tasks
    for pid, task in _running_tasks.items():
        status = _statuses.get(pid)
        if status:
            result[pid] = {
                "status": status.status,
                "current_step": status.current_step or "",
                "tool_rounds": status.tool_rounds,
            }
        else:
            result[pid] = {"status": "running", "current_step": "", "tool_rounds": 0}
    return {"tasks": result, "count": len(result)}


@app.post("/execute")
async def execute(req: ExecuteRequestBody, request: Request):
    """Start plan execution as background task. Returns immediately."""
    if req.plan_id in _running_tasks:
        raise HTTPException(status_code=409, detail=f"Plan {req.plan_id} already running")

    # Set up state tracking
    stop_event = asyncio.Event()
    _stop_events[req.plan_id] = stop_event
    _statuses[req.plan_id] = ExecuteStatus(plan_id=req.plan_id, status="running")
    _results.pop(req.plan_id, None)
    # Pre-populate result so /result endpoint always returns data after a successful dispatch.
    # Overwritten by _run_executor_task once execution begins / completes.
    _results[req.plan_id] = ExecutorResult(
        status="accepted",
        updated_plan_json=req.plan_json,
        summary="Task accepted, execution pending",
    )

    # Extract LangSmith distributed trace headers forwarded from Supervisor.
    # Only propagate recognised tracing headers to avoid forwarding arbitrary headers.
    trace_headers: dict[str, str] = {
        k: v for k, v in request.headers.items()
        if k.startswith("langsmith-") or k == "baggage"
    }

    # Build context from remaining config fields
    ctx = Context()
    if req.config:
        for k, v in req.config.items():
            if hasattr(ctx, k):
                setattr(ctx, k, v)

    # Create and track background task
    task = asyncio.create_task(
        _run_executor_task(
            plan_json=req.plan_json,
            plan_id=req.plan_id,
            ctx=ctx,
            trace_headers=trace_headers,
        ),
        name=f"executor_{req.plan_id}",
    )
    _running_tasks[req.plan_id] = task

    return {"plan_id": req.plan_id, "status": "accepted"}


@app.get("/status/{plan_id}")
async def get_status(plan_id: str):
    """Quick status overview for a running/completed execution."""
    status = _statuses.get(plan_id)
    if status is None:
        raise HTTPException(status_code=404, detail=f"Plan {plan_id} not found")
    return asdict(status)


@app.get("/result/{plan_id}")
async def get_result(plan_id: str):
    """Full ExecutorResult after completion."""
    result = _results.get(plan_id)
    if result is None:
        raise HTTPException(status_code=404, detail=f"No result for plan {plan_id}")
    return {
        "status": result.status,
        "updated_plan_json": result.updated_plan_json,
        "summary": result.summary,
        "snapshot_json": result.snapshot_json,
    }


@app.post("/stop/{plan_id}")
async def stop(plan_id: str, body: StopRequestBody | None = None):
    """Set stop flag for a running execution (graceful)."""
    stop_event = _stop_events.get(plan_id)
    if stop_event is None:
        raise HTTPException(status_code=404, detail=f"No stop event for plan {plan_id}")
    stop_event.set()
    logger.info("Stop flag set for plan_id=%s (reason: %s)", plan_id, (body.reason if body else ""))
    return {"plan_id": plan_id, "acknowledged": True}


@app.post("/shutdown")
async def shutdown():
    """Cancel all running tasks. The Supervisor will terminate this process afterwards."""
    logger.info("Shutdown requested — cancelling all running tasks")
    for plan_id, task in _running_tasks.items():
        task.cancel()
    _running_tasks.clear()
    return {"status": "shutting_down"}
