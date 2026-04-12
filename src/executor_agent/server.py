"""V3: Executor FastAPI server — runs in Process B.

Wraps executor_graph.ainvoke() as async background tasks.
Communication: Supervisor POSTs /execute, GETs /status, POSTs /stop.
Callbacks: Executor POSTs /callback/snapshot and /callback/completed back to Supervisor.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
from dataclasses import asdict
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from src.common.context import Context
from src.common.executor_protocol import ExecuteRequest, ExecuteStatus, SnapshotPayload, StopRequest
from src.executor_agent.graph import ExecutorResult, ExecutorState, executor_graph, run_executor

logger = logging.getLogger(__name__)

# Process-level state: keyed by plan_id
_running_tasks: dict[str, asyncio.Task[Any]] = {}
_stop_events: dict[str, asyncio.Event] = {}
_results: dict[str, ExecutorResult] = {}
_statuses: dict[str, ExecuteStatus] = {}

# Callback URL set at startup
_callback_base_url: str = ""


class ExecuteRequestBody(BaseModel):
    plan_json: str
    plan_id: str
    executor_session_id: str = ""
    callback_url: str = ""
    config: dict[str, Any] = {}


class StopRequestBody(BaseModel):
    reason: str = ""


def _set_callback_url(url: str) -> None:
    global _callback_base_url
    _callback_base_url = url


async def _send_callback(path: str, payload: dict) -> None:
    """Fire-and-forget POST to Supervisor callback server."""
    if not _callback_base_url:
        return
    url = f"{_callback_base_url}{path}"
    try:
        async with httpx.AsyncClient() as client:
            await client.post(url, json=payload, timeout=10.0)
    except Exception:
        logger.debug("Callback to %s failed (non-critical)", url, exc_info=True)


async def _extract_lightweight_snapshot(state: ExecutorState, tool_rounds: int) -> SnapshotPayload:
    """Parse plan JSON from messages and extract step counts."""
    plan_id = ""
    completed = 0
    total = 0
    current_step = ""

    for msg in state.messages:
        if not hasattr(msg, "content") or not isinstance(msg.content, str):
            continue
        try:
            data = json.loads(msg.content)
            if isinstance(data, dict) and "steps" in data:
                plan_id = data.get("plan_id", "")
                steps = data.get("steps", [])
                total = len(steps)
                completed = sum(1 for s in steps if s.get("status") == "completed")
                for s in steps:
                    if s.get("status") not in ("completed", "failed", "skipped"):
                        current_step = s.get("step_id", "")
                        break
                break
        except (json.JSONDecodeError, AttributeError):
            continue

    return SnapshotPayload(
        plan_id=plan_id,
        tool_rounds=tool_rounds,
        current_step=current_step,
        completed_steps=completed,
        total_steps=total,
    )


async def _run_executor_task(
    plan_json: str,
    plan_id: str,
    ctx: Context,
    callback_url: str,
    snapshot_interval: int,
) -> None:
    """Background task: run executor_graph and send callbacks."""
    _callback_base_url_local = callback_url

    async def snapshot_callback(payload: SnapshotPayload) -> None:
        """Fire-and-forget snapshot POST + update live status."""
        data = asdict(payload)
        # Update live status fields
        status = _statuses.get(plan_id)
        if status is not None:
            status.tool_rounds = payload.tool_rounds
            status.current_step = payload.current_step
        await _send_callback("/callback/snapshot", data)

    # Wire snapshot callback into context
    ctx_v3 = ctx
    ctx_v3._snapshot_callback = snapshot_callback
    ctx_v3.snapshot_interval = snapshot_interval

    stop_event = _stop_events.get(plan_id)
    # Store stop event reference for graph nodes to check
    # (the call_executor node will access _stop_events directly)

    _statuses[plan_id].status = "running"

    try:
        result = await run_executor(plan_json, context=ctx_v3)
        _results[plan_id] = result

        if stop_event and stop_event.is_set():
            actual_status = "stopped"
        else:
            actual_status = result.status

        _statuses[plan_id].status = actual_status

        # Send completion callback (must-read)
        completion_payload = {
            "plan_id": plan_id,
            "status": actual_status,
            "updated_plan_json": result.updated_plan_json,
            "summary": result.summary,
            "snapshot_json": result.snapshot_json,
        }
        # Use direct HTTP call for completion (must-read)
        if _callback_base_url_local:
            try:
                async with httpx.AsyncClient() as client:
                    await client.post(
                        f"{_callback_base_url_local}/callback/completed",
                        json=completion_payload,
                        timeout=15.0,
                    )
            except Exception:
                logger.error("Failed to send completion callback for %s", plan_id, exc_info=True)

    except Exception as e:
        logger.error("Executor task failed for %s: %s", plan_id, e, exc_info=True)
        _results[plan_id] = ExecutorResult(
            status="failed",
            updated_plan_json="",
            summary=f"Executor crashed: {e}",
        )
        _statuses[plan_id].status = "failed"

        if _callback_base_url_local:
            try:
                async with httpx.AsyncClient() as client:
                    await client.post(
                        f"{_callback_base_url_local}/callback/completed",
                        json={
                            "plan_id": plan_id,
                            "status": "failed",
                            "summary": f"Executor crashed: {e}",
                            "updated_plan_json": "",
                            "snapshot_json": "",
                        },
                        timeout=15.0,
                    )
            except Exception:
                logger.error("Failed to send failure callback for %s", plan_id, exc_info=True)
    finally:
        _running_tasks.pop(plan_id, None)


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


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/execute")
async def execute(req: ExecuteRequestBody):
    """Start plan execution as background task. Returns immediately."""
    if req.plan_id in _running_tasks:
        raise HTTPException(status_code=409, detail=f"Plan {req.plan_id} already running")

    # Set up state tracking
    stop_event = asyncio.Event()
    _stop_events[req.plan_id] = stop_event
    _statuses[req.plan_id] = ExecuteStatus(plan_id=req.plan_id, status="running")
    _results.pop(req.plan_id, None)

    # Build context
    ctx = Context()
    if req.config:
        for k, v in req.config.items():
            if hasattr(ctx, k):
                setattr(ctx, k, v)

    # Extract snapshot_interval from config (default to context's value)
    snapshot_interval = req.config.get("snapshot_interval", ctx.snapshot_interval)

    # Create and track background task
    task = asyncio.create_task(
        _run_executor_task(
            plan_json=req.plan_json,
            plan_id=req.plan_id,
            ctx=ctx,
            callback_url=req.callback_url,
            snapshot_interval=snapshot_interval,
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
    """Graceful server shutdown."""
    logger.info("Shutdown requested")
    # Cancel all tasks
    for plan_id, task in _running_tasks.items():
        task.cancel()
    _running_tasks.clear()
    return {"status": "shutting_down"}
