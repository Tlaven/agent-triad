"""Executor 子进程生命周期 — 半自动检查点测试。

Spawns a REAL Executor subprocess via asyncio.create_subprocess_exec.
Uses EXECUTOR_MOCK_MODE to skip LLM calls inside the subprocess.
Records state at each critical point for human/AI review.

Run:
    uv run pytest tests/e2e/test_v3_subprocess_checkpoint.py -v -s

Review:
    Read logs/checkpoints/v3_subprocess_lifecycle.md
"""

import asyncio
import json
import os
import sys
from pathlib import Path

import httpx
import pytest

from src.common.context import Context
from src.common.process_manager import ExecutorProcessManager
from tests.e2e.checkpoint_recorder import CheckpointRecorder

# ---------- helpers ----------


async def _read_available_stdout(
    process: asyncio.subprocess.Process, timeout: float = 0.5
) -> str:
    """Read whatever stdout is available without blocking indefinitely."""
    if process.stdout is None:
        return "(stdout not piped)"
    chunks: list[str] = []
    try:
        while True:
            chunk = await asyncio.wait_for(process.stdout.readline(), timeout=timeout)
            if not chunk:
                break
            chunks.append(chunk.decode("utf-8", errors="replace"))
    except asyncio.TimeoutError:
        pass
    return "".join(chunks) if chunks else "(no output yet)"


def _simple_plan(plan_id: str = "plan_checkpoint_test") -> str:
    return json.dumps(
        {
            "plan_id": plan_id,
            "version": 1,
            "goal": "checkpoint test goal",
            "steps": [
                {
                    "step_id": "step_1",
                    "intent": "test step",
                    "expected_output": "ok",
                    "status": "pending",
                }
            ],
        },
        ensure_ascii=False,
    )


# ---------- fixtures ----------


@pytest.fixture(autouse=True)
def _mock_mode_env():
    """Enable EXECUTOR_MOCK_MODE for subprocess (no real LLM)."""
    os.environ["EXECUTOR_MOCK_MODE"] = "completed"
    yield
    os.environ.pop("EXECUTOR_MOCK_MODE", None)


@pytest.fixture(autouse=True)
def _clean_port_files():
    """Ensure no stale per-task port files before/after test."""
    logs_dir = Path("logs")
    for pf in logs_dir.glob("executor_*.port"):
        pf.unlink(missing_ok=True)
    yield
    for pf in logs_dir.glob("executor_*.port"):
        pf.unlink(missing_ok=True)


@pytest.fixture
def ctx() -> Context:
    return Context(
        executor_host="127.0.0.1",
        executor_port=0,
        executor_startup_timeout=15.0,
    )


# ---------- the test ----------


async def test_v3_subprocess_lifecycle_checkpoints(ctx: Context):
    """Full subprocess lifecycle with checkpoints. No assertions — review the report."""
    recorder = CheckpointRecorder("v3_subprocess_lifecycle")
    pm = ExecutorProcessManager(ctx)
    plan_id = "plan_checkpoint_test"

    # ================================================================
    # CP1: Per-task Subprocess Spawn — start_for_task()
    # ================================================================
    cp1 = recorder.checkpoint("subprocess_spawn")
    try:
        handle = await pm.start_for_task(plan_id, ctx, mailbox_url=None)
        cp1.record("success", True)
    except Exception as e:
        cp1.record("success", f"FAILED: {e}")
        report = recorder.write_report()
        pytest.fail(f"CP1 spawn failed. Partial report: {report}")
        return

    port_file = pm._port_file_for_task(plan_id)
    cp1.record("port_file_path", str(port_file))
    cp1.record("port_file_exists", port_file.exists())
    cp1.record("port_file_content", port_file.read_text() if port_file.exists() else "(missing)")
    cp1.record("base_url", handle.base_url)
    cp1.record("is_running", pm.is_running)
    cp1.record("subprocess_pid", handle.process.pid)
    cp1.record("subprocess_returncode", handle.process.returncode)
    stdout = await _read_available_stdout(handle.process, timeout=1.0)
    cp1.record_raw("subprocess_stdout", stdout)

    # ================================================================
    # CP2: Health Check — GET /health
    # ================================================================
    cp2 = recorder.checkpoint("health_check")
    try:
        async with httpx.AsyncClient(base_url=handle.base_url, timeout=5.0) as client:
            resp = await client.get("/health")
            cp2.record("http_status_code", resp.status_code)
            cp2.record("response_body", resp.json())
            cp2.record("response_headers", dict(resp.headers))
    except Exception as e:
        cp2.record("error", f"{type(e).__name__}: {e}")

    # ================================================================
    # CP3: Task Dispatch — POST /execute
    # ================================================================
    cp3 = recorder.checkpoint("task_dispatch")
    try:
        async with httpx.AsyncClient(base_url=handle.base_url, timeout=10.0) as client:
            resp = await client.post(
                "/execute",
                json={
                    "plan_json": _simple_plan(plan_id),
                    "plan_id": plan_id,
                },
            )
            cp3.record("http_status_code", resp.status_code)
            cp3.record("response_body", resp.json())
    except Exception as e:
        cp3.record("error", f"{type(e).__name__}: {e}")

    # ================================================================
    # CP4: Immediate Result Availability — GET /result right after dispatch
    # ================================================================
    cp4 = recorder.checkpoint("immediate_result_after_dispatch")
    try:
        async with httpx.AsyncClient(base_url=handle.base_url, timeout=5.0) as client:
            resp = await client.get(f"/result/{plan_id}")
            cp4.record("http_status_code", resp.status_code)
            cp4.record("response_body", resp.json())
            data = resp.json()
            cp4.record("status_field", data.get("status"))
            cp4.record("summary_field", data.get("summary"))
    except Exception as e:
        cp4.record("error", f"{type(e).__name__}: {e}")

    # ================================================================
    # CP5: Completion Poll — wait for mock executor to finish
    # ================================================================
    cp5 = recorder.checkpoint("completion_poll")
    try:
        deadline = asyncio.get_event_loop().time() + 10.0
        final_data = None
        while asyncio.get_event_loop().time() < deadline:
            async with httpx.AsyncClient(base_url=handle.base_url, timeout=3.0) as client:
                resp = await client.get(f"/result/{plan_id}")
                if resp.status_code == 200:
                    data = resp.json()
                    final_data = data
                    if data.get("status") in ("completed", "failed", "stopped"):
                        break
            await asyncio.sleep(0.3)

        if final_data:
            cp5.record("final_status", final_data.get("status"))
            cp5.record("final_summary", final_data.get("summary"))
            cp5.record("updated_plan_json_present", bool(final_data.get("updated_plan_json")))
            cp5.record("full_response_body", final_data)
        else:
            cp5.record("result", "TIMEOUT — no result data received")
    except Exception as e:
        cp5.record("error", f"{type(e).__name__}: {e}")

    # ================================================================
    # CP6: Status Cleanup — /status 404, /result persists
    # ================================================================
    cp6 = recorder.checkpoint("status_cleanup_after_completion")
    try:
        async with httpx.AsyncClient(base_url=handle.base_url, timeout=5.0) as client:
            # /status should be 404 (cleaned up in finally block)
            resp_status = await client.get(f"/status/{plan_id}")
            cp6.record("GET /status code", resp_status.status_code)
            cp6.record("GET /status body", resp_status.text)

            # /result should still be 200
            resp_result = await client.get(f"/result/{plan_id}")
            cp6.record("GET /result code", resp_result.status_code)
            cp6.record("GET /result body", resp_result.json() if resp_result.status_code == 200 else resp_result.text)
    except Exception as e:
        cp6.record("error", f"{type(e).__name__}: {e}")

    # ================================================================
    # CP7: Process Stop — ProcessManager.stop_task()
    # ================================================================
    cp7 = recorder.checkpoint("process_stop")
    try:
        await pm.stop_task(plan_id)
        cp7.record("stop_success", True)
        cp7.record("is_running_after_stop", pm.is_running)
        cp7.record("port_file_exists_after_stop", port_file.exists())
        cp7.record("returncode", handle.process.returncode)
    except Exception as e:
        cp7.record("stop_error", f"{type(e).__name__}: {e}")

    # ================================================================
    # CP8: Per-task spawn — start_for_task creates isolated process
    # ================================================================
    cp8 = recorder.checkpoint("per_task_spawn")
    pm2 = ExecutorProcessManager(ctx)
    try:
        plan_id_8 = "plan_recovery_test"
        handle8 = await pm2.start_for_task(plan_id_8, ctx, mailbox_url=None)
        cp8.record("spawn_base_url", handle8.base_url)
        cp8.record("spawn_is_running", pm2.is_running)
        cp8.record("get_task_base_url", pm2.get_task_base_url(plan_id_8))

        # Cleanup
        await pm2.stop()
    except Exception as e:
        cp8.record("error", f"{type(e).__name__}: {e}")
        try:
            await pm2.stop()
        except Exception:
            pass

    # ================================================================
    # CP9: Duplicate Dispatch — should get 409 conflict
    # ================================================================
    cp9 = recorder.checkpoint("duplicate_dispatch_409")
    pm3 = ExecutorProcessManager(ctx)
    try:
        dup_plan_id = "plan_dup_test"
        handle3 = await pm3.start_for_task(dup_plan_id, ctx, mailbox_url=None)
        async with httpx.AsyncClient(base_url=handle3.base_url, timeout=10.0) as client:
            # First dispatch
            resp1 = await client.post(
                "/execute",
                json={"plan_json": _simple_plan(dup_plan_id), "plan_id": dup_plan_id},
            )
            cp9.record("first_dispatch_status", resp1.status_code)
            cp9.record("first_dispatch_body", resp1.json())

            # Second dispatch (duplicate) — should be 409
            resp2 = await client.post(
                "/execute",
                json={"plan_json": _simple_plan(dup_plan_id), "plan_id": dup_plan_id},
            )
            cp9.record("duplicate_dispatch_status", resp2.status_code)
            cp9.record("duplicate_dispatch_body", resp2.json())

        await pm3.stop()
    except Exception as e:
        cp9.record("error", f"{type(e).__name__}: {e}")
        try:
            await pm3.stop()
        except Exception:
            pass

    # ================================================================
    # Write report
    # ================================================================
    report_path = recorder.write_report()
    print(f"\n{'='*60}")
    print(f"Checkpoint report written to: {report_path}")
    print(f"{'='*60}\n")
