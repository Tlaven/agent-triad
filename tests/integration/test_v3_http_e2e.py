"""True E2E tests for V3 HTTP infrastructure -- real Executor server, real HTTP, mock only LLM.

vs test_v3_lifecycle.py:
  - That file mocks httpx.AsyncClient -> skips ALL Executor server-side code
  - This file starts real uvicorn server -> tests actual state management

Catches bugs that mocked-transport tests miss:
  - Executor /result availability guarantee after dispatch
  - State cleanup timing (/_statuses cleared, /_results preserved)
  - Pull-mode polling: Supervisor GETs /result/{plan_id} for completion
  - Probe path correctness (/status 404 -> /result fallback)
  - Task crash does not lose result data
"""

import asyncio
import json
import socket

import httpx
import pytest
import uvicorn
from unittest.mock import AsyncMock, patch

from src.common.context import Context
from src.common.mailbox import Mailbox, MailboxItem, set_mailbox, get_mailbox
from src.executor_agent.graph import ExecutorResult
from src.executor_agent.server import app as executor_app
from src.supervisor_agent.state import ActiveExecutorTask, PlannerSession, State


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("", 0))
        return s.getsockname()[1]


def _simple_plan(plan_id: str = "plan_test") -> str:
    return json.dumps({
        "plan_id": plan_id,
        "version": 1,
        "goal": "test",
        "steps": [
            {
                "step_id": "step_1",
                "intent": "test step",
                "expected_output": "ok",
                "status": "pending",
            }
        ],
    })


# ==================== Fixtures ====================


@pytest.fixture(autouse=True)
def _reset_executor_state():
    """Reset Executor server module-level globals between tests."""
    import src.executor_agent.server as srv

    for pid, t in list(srv._running_tasks.items()):
        t.cancel()
    srv._running_tasks.clear()
    srv._stop_events.clear()
    srv._results.clear()
    srv._statuses.clear()
    yield
    for pid, t in list(srv._running_tasks.items()):
        t.cancel()
    srv._running_tasks.clear()
    srv._stop_events.clear()
    srv._results.clear()
    srv._statuses.clear()


@pytest.fixture(autouse=True)
def _reset_mailbox_singleton():
    """Reset module-level mailbox singleton between tests."""
    import src.common.mailbox as _mb_mod
    _mb_mod._mailbox = None
    yield
    _mb_mod._mailbox = None


@pytest.fixture
def mailbox():
    mb = Mailbox()
    set_mailbox(mb)
    yield mb


async def _wait_for_server(port: int, timeout: float = 5.0) -> None:
    """Poll /health until the server responds."""
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        try:
            async with httpx.AsyncClient(timeout=1.0) as c:
                r = await c.get(f"http://127.0.0.1:{port}/health")
                if r.status_code == 200:
                    return
        except (httpx.ConnectError, httpx.TimeoutException):
            await asyncio.sleep(0.05)
    raise RuntimeError(f"Server on port {port} failed to start")


@pytest.fixture
async def executor_server():
    """Real Executor FastAPI server on a random TCP port."""
    port = _free_port()
    config = uvicorn.Config(
        executor_app,
        host="127.0.0.1",
        port=port,
        log_level="critical",
    )
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    await _wait_for_server(port)

    yield port

    server.should_exit = True
    try:
        await asyncio.wait_for(task, timeout=5.0)
    except asyncio.TimeoutError:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


async def _wait_for_status(
    client: httpx.AsyncClient, plan_id: str, expected: str, timeout: float = 5.0
) -> dict:
    """Poll /result/{plan_id} until status matches expected, or timeout."""
    deadline = asyncio.get_event_loop().time() + timeout
    last_data: dict = {}
    while asyncio.get_event_loop().time() < deadline:
        resp = await client.get(f"/result/{plan_id}")
        assert resp.status_code == 200, f"/result returned {resp.status_code}"
        data = resp.json()
        last_data = data
        if data.get("status") == expected:
            return data
        await asyncio.sleep(0.1)
    raise AssertionError(
        f"Timed out waiting for status={expected}, last status={last_data.get('status')}"
    )


# ==================== Executor State Guarantee Tests ====================


async def test_result_available_immediately_after_dispatch(executor_server):
    """POST /execute -> GET /result must return 200 immediately.

    This is the core guarantee: after a successful dispatch, the plan_id
    is ALWAYS queryable. "not found" must NEVER happen for a dispatched task.
    """
    port = executor_server
    plan_id = "plan_immediate_001"

    with patch("src.executor_agent.server.run_executor") as mock_run:
        # Make executor slow so we can test the "immediate" state
        async def slow_exec(*args, **kwargs):
            await asyncio.sleep(30)
            return ExecutorResult(
                status="completed",
                updated_plan_json=_simple_plan(plan_id),
                summary="slow done",
            )

        mock_run.side_effect = slow_exec

        base = f"http://127.0.0.1:{port}"
        async with httpx.AsyncClient(base_url=base, timeout=5.0) as client:
            resp = await client.post(
                "/execute",
                json={
                    "plan_json": _simple_plan(plan_id),
                    "plan_id": plan_id,
                },
            )
            assert resp.status_code == 200

            # CRITICAL: /result MUST be available right away
            resp = await client.get(f"/result/{plan_id}")
            assert resp.status_code == 200, (
                f"/result returned {resp.status_code} -- "
                "dispatched plan_id must always be queryable"
            )
            data = resp.json()
            assert data["status"] in ("accepted", "running"), (
                f"Expected 'accepted' or 'running', got {data['status']}"
            )


async def test_result_persists_after_task_completion(executor_server):
    """After task completes: /status cleaned up (404), /result persists (200).

    This tests the probe fallback: _probe_executor_task checks /status first,
    gets 404, then falls through to /result which must still return data.
    """
    port = executor_server
    plan_id = "plan_persist_001"

    with patch("src.executor_agent.server.run_executor") as mock_run:
        mock_run.return_value = ExecutorResult(
            status="completed",
            updated_plan_json=_simple_plan(plan_id),
            summary="All done",
        )

        base = f"http://127.0.0.1:{port}"
        async with httpx.AsyncClient(base_url=base, timeout=5.0) as client:
            resp = await client.post(
                "/execute",
                json={
                    "plan_json": _simple_plan(plan_id),
                    "plan_id": plan_id,
                },
            )
            assert resp.status_code == 200

            # Wait for background task to complete
            await _wait_for_status(client, plan_id, "completed")

            # /status should be 404 (cleaned up in finally block)
            resp = await client.get(f"/status/{plan_id}")
            assert resp.status_code == 404

            # /result should STILL be 200 -- this is the probe fallback path
            resp = await client.get(f"/result/{plan_id}")
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "completed"
            assert data["summary"] == "All done"


async def test_result_available_after_task_crash(executor_server):
    """If run_executor crashes, /result still has failure data.

    The guarantee holds even on exception: after dispatch, result is always
    queryable with a meaningful status.
    """
    port = executor_server
    plan_id = "plan_crash_001"

    with patch("src.executor_agent.server.run_executor") as mock_run:
        mock_run.side_effect = RuntimeError("LLM connection refused")

        base = f"http://127.0.0.1:{port}"
        async with httpx.AsyncClient(base_url=base, timeout=5.0) as client:
            resp = await client.post(
                "/execute",
                json={
                    "plan_json": _simple_plan(plan_id),
                    "plan_id": plan_id,
                },
            )
            assert resp.status_code == 200

            # Wait for crash to be recorded
            data = await _wait_for_status(client, plan_id, "failed")

            assert "crash" in data["summary"].lower() or "error" in data["summary"].lower()
            # /result still returns 200 with failure info
            resp = await client.get(f"/result/{plan_id}")
            assert resp.status_code == 200


async def test_duplicate_dispatch_returns_409(executor_server):
    """Dispatching same plan_id while running returns 409 conflict."""
    port = executor_server
    plan_id = "plan_dup_001"

    with patch("src.executor_agent.server.run_executor") as mock_run:
        async def slow(*args, **kwargs):
            await asyncio.sleep(60)
            return ExecutorResult(status="completed", updated_plan_json="", summary="")

        mock_run.side_effect = slow

        base = f"http://127.0.0.1:{port}"
        async with httpx.AsyncClient(base_url=base, timeout=5.0) as client:
            resp = await client.post(
                "/execute",
                json={
                    "plan_json": _simple_plan(plan_id),
                    "plan_id": plan_id,
                },
            )
            assert resp.status_code == 200

            # Second dispatch -> 409
            resp = await client.post(
                "/execute",
                json={
                    "plan_json": _simple_plan(plan_id),
                    "plan_id": plan_id,
                },
            )
            assert resp.status_code == 409


# ==================== Pull-mode Result Retrieval Tests ====================


async def test_supervisor_polls_result_after_completion(executor_server, mailbox):
    """Executor stores result in _results -> Supervisor polls /result/{plan_id} -> gets completion.

    Tests the core Pull-mode flow: no callback needed.
    """
    port = executor_server
    plan_id = "plan_pull_001"

    with patch("src.executor_agent.server.run_executor") as mock_run:
        mock_run.return_value = ExecutorResult(
            status="completed",
            updated_plan_json=_simple_plan(plan_id),
            summary="Pull mode completed",
        )

        base = f"http://127.0.0.1:{port}"
        async with httpx.AsyncClient(base_url=base, timeout=5.0) as client:
            # 1) Dispatch task
            resp = await client.post(
                "/execute",
                json={
                    "plan_json": _simple_plan(plan_id),
                    "plan_id": plan_id,
                },
            )
            assert resp.status_code == 200

            # 2) Poll /result until completion (simulating Supervisor pull)
            data = await _wait_for_status(client, plan_id, "completed")
            assert data["summary"] == "Pull mode completed"

        # 3) Write result to mailbox (as Supervisor tools do)
        await mailbox.post(plan_id, MailboxItem(
            item_type="completion",
            payload=data,
        ))

        # 4) Verify mailbox has the completion
        assert await mailbox.has_completion(plan_id)
        comp = await mailbox.get_completion(plan_id)
        assert comp.payload["status"] == "completed"
        assert comp.payload["summary"] == "Pull mode completed"


async def test_supervisor_polls_failed_task(executor_server, mailbox):
    """Executor crashes -> Supervisor polls /result -> gets failure data in mailbox."""
    port = executor_server
    plan_id = "plan_pull_fail_001"

    with patch("src.executor_agent.server.run_executor") as mock_run:
        mock_run.side_effect = ValueError("Simulated crash")

        base = f"http://127.0.0.1:{port}"
        async with httpx.AsyncClient(base_url=base, timeout=5.0) as client:
            resp = await client.post(
                "/execute",
                json={
                    "plan_json": _simple_plan(plan_id),
                    "plan_id": plan_id,
                },
            )
            assert resp.status_code == 200

            # Poll until failure recorded
            data = await _wait_for_status(client, plan_id, "failed")

        # Write to mailbox and verify
        await mailbox.post(plan_id, MailboxItem(
            item_type="completion",
            payload=data,
        ))
        comp = await mailbox.get_completion(plan_id)
        assert comp.payload["status"] == "failed"


# ==================== Supervisor Tool E2E (full chain) ====================


async def test_tool_dispatch_then_get_result(
    executor_server, mailbox
):
    """call_executor(wait=false) -> get_executor_result -> [EXECUTOR_RESULT].

    Full chain: Supervisor tool -> HTTP -> Executor server ->
    Supervisor polls /result -> mailbox -> get_executor_result reads result.
    """
    from unittest.mock import MagicMock

    from src.supervisor_agent.tools import (
        _build_call_executor_tool,
        _build_get_executor_result_tool,
    )

    port = executor_server
    plan_id = "plan_tool_e2e_001"

    ctx = Context(
        
        executor_host="127.0.0.1",
        executor_port=port,
    )

    with patch("src.executor_agent.server.run_executor") as mock_run:
        mock_run.return_value = ExecutorResult(
            status="completed",
            updated_plan_json=_simple_plan(plan_id),
            summary="Tool e2e completed",
        )

        # Mock v3_manager to return our executor's base_url
        mock_pm = MagicMock()
        mock_pm.base_url = f"http://127.0.0.1:{port}"
        mock_pm.is_running = True
        # Mock start_for_task to return a handle with the existing executor's base_url
        mock_handle = MagicMock()
        mock_handle.base_url = f"http://127.0.0.1:{port}"
        mock_pm.start_for_task = AsyncMock(return_value=mock_handle)
        mock_pm.get_task_base_url = MagicMock(return_value=f"http://127.0.0.1:{port}")

        mock_mailbox_server = MagicMock()
        mock_mailbox_server.base_url = "http://127.0.0.1:19999"

        mock_infra = MagicMock()
        mock_infra.process_manager = mock_pm
        mock_infra.mailbox_server = mock_mailbox_server

        # 1) call_executor (fire-and-forget)
        call_exec = _build_call_executor_tool(ctx)
        state = State(
            planner_session=PlannerSession(
                session_id="s1",
                plan_json=_simple_plan(plan_id),
            ),
            active_executor_tasks={},
        )

        with patch("src.supervisor_agent.v3_lifecycle.v3_manager") as mock_v3:
            mock_v3.ensure_started = AsyncMock(return_value=mock_infra)
            dispatch_result = await call_exec.ainvoke(
                {"state": state, "plan_id": plan_id}
            )

        assert "[EXECUTOR_DISPATCH]" in dispatch_result, (
            f"Expected [EXECUTOR_DISPATCH], got: {dispatch_result[:200]}"
        )
        assert plan_id in dispatch_result

        # 2) Wait for task to complete on Executor server
        base = f"http://127.0.0.1:{port}"
        async with httpx.AsyncClient(base_url=base, timeout=5.0) as client:
            await _wait_for_status(client, plan_id, "completed")

        # 3) Simulate Supervisor polling: fetch result from Executor and write to mailbox
        async with httpx.AsyncClient(base_url=base, timeout=5.0) as client:
            resp = await client.get(f"/result/{plan_id}")
            assert resp.status_code == 200
            result_data = resp.json()
            await mailbox.post(plan_id, MailboxItem(
                item_type="completion", payload=result_data,
            ))

        # 4) get_executor_result reads from mailbox
        state2 = State(
            planner_session=PlannerSession(
                session_id="s1",
                plan_json=_simple_plan(plan_id),
            ),
            active_executor_tasks={
                plan_id: ActiveExecutorTask(
                    plan_id=plan_id,
                    status="dispatched",
                ),
            },
        )
        get_result = _build_get_executor_result_tool(ctx)
        with patch("src.common.mailbox.get_mailbox", return_value=mailbox):
            final_result = await get_result.ainvoke(
                {"state": state2, "plan_id": plan_id}
            )

        assert "[EXECUTOR_RESULT]" in final_result, (
            f"Expected [EXECUTOR_RESULT], got: {final_result[:200]}"
        )
        assert "completed" in final_result

        # Verify the result JSON is well-formed
        import re

        match = re.search(r"\[EXECUTOR_RESULT\]\s*(\{.*\})", final_result, re.DOTALL)
        assert match is not None
        meta = json.loads(match.group(1))
        assert meta["status"] == "completed"
        assert meta["plan_id"] == plan_id


async def test_tool_get_result_via_direct_fetch(executor_server, mailbox):
    """get_executor_result fetches via direct /result when not yet in mailbox.

    Tests the probe fallback path: mailbox empty -> probe Executor ->
    task completed -> _fetch_executor_result_directly.
    """
    from unittest.mock import MagicMock

    from src.supervisor_agent.tools import _build_get_executor_result_tool

    port = executor_server
    plan_id = "plan_probe_fb_001"

    ctx = Context(
        
        executor_host="127.0.0.1",
        executor_port=port,
    )

    with patch("src.executor_agent.server.run_executor") as mock_run:
        mock_run.return_value = ExecutorResult(
            status="completed",
            updated_plan_json=_simple_plan(plan_id),
            summary="Completed via direct fetch",
        )

        # Dispatch directly to Executor
        base = f"http://127.0.0.1:{port}"
        async with httpx.AsyncClient(base_url=base, timeout=5.0) as client:
            resp = await client.post(
                "/execute",
                json={
                    "plan_json": _simple_plan(plan_id),
                    "plan_id": plan_id,
                },
            )
            assert resp.status_code == 200

        # Wait for task to complete
        async with httpx.AsyncClient(base_url=base, timeout=5.0) as client:
            await _wait_for_status(client, plan_id, "completed")

        # get_executor_result: mailbox is empty -> probe finds completed task ->
        # direct fetch returns the result
        state = State(
            planner_session=PlannerSession(
                session_id="s1",
                plan_json=_simple_plan(plan_id),
            ),
            active_executor_tasks={
                plan_id: ActiveExecutorTask(
                    plan_id=plan_id,
                    status="dispatched",
                ),
            },
        )

        get_result = _build_get_executor_result_tool(ctx)
        # Mock v3_manager so _probe_executor_task can get base_url
        mock_pm = MagicMock()
        mock_pm.base_url = f"http://127.0.0.1:{port}"
        mock_pm.is_running = True
        mock_infra = MagicMock()
        mock_infra.process_manager = mock_pm
        with patch("src.supervisor_agent.v3_lifecycle.v3_manager") as mock_v3, \
             patch("src.common.mailbox.get_mailbox", return_value=mailbox):
            mock_v3.ensure_started = AsyncMock(return_value=mock_infra)
            result = await get_result.ainvoke(
                {"state": state, "plan_id": plan_id}
            )

        # Must get result via direct /result fetch, not "not_found"
        assert "[EXECUTOR_RESULT]" in result, (
            f"Expected [EXECUTOR_RESULT] via probe fallback, got: {result[:300]}"
        )
        assert "completed" in result


async def test_tool_dispatch_and_get_result_separate(executor_server, mailbox):
    """call_executor dispatches (fire-and-forget), then get_executor_result retrieves from mailbox."""
    from unittest.mock import MagicMock

    from src.supervisor_agent.tools import (
        _build_call_executor_tool,
        _build_get_executor_result_tool,
    )

    port = executor_server
    plan_id = "plan_separate_001"

    ctx = Context(
        executor_host="127.0.0.1",
        executor_port=port,
    )

    with patch("src.executor_agent.server.run_executor") as mock_run:
        async def delayed_exec(*args, **kwargs):
            await asyncio.sleep(0.3)
            return ExecutorResult(
                status="completed",
                updated_plan_json=_simple_plan(plan_id),
                summary="Separate test done",
            )

        mock_run.side_effect = delayed_exec

        # Mock v3_manager
        mock_pm = MagicMock()
        mock_pm.base_url = f"http://127.0.0.1:{port}"
        mock_pm.is_running = True
        mock_handle = MagicMock()
        mock_handle.base_url = f"http://127.0.0.1:{port}"
        mock_pm.start_for_task = AsyncMock(return_value=mock_handle)
        mock_pm.get_task_base_url = MagicMock(return_value=f"http://127.0.0.1:{port}")

        mock_mailbox_server = MagicMock()
        mock_mailbox_server.base_url = "http://127.0.0.1:19999"

        mock_infra = MagicMock()
        mock_infra.process_manager = mock_pm
        mock_infra.mailbox_server = mock_mailbox_server

        call_exec = _build_call_executor_tool(ctx)
        state = State(
            planner_session=PlannerSession(
                session_id="s1",
                plan_json=_simple_plan(plan_id),
            ),
            active_executor_tasks={},
        )

        with patch("src.supervisor_agent.v3_lifecycle.v3_manager") as mock_v3:
            mock_v3.ensure_started = AsyncMock(return_value=mock_infra)
            result = await call_exec.ainvoke(
                {"state": state, "plan_id": plan_id}
            )

        assert "[EXECUTOR_DISPATCH]" in result, (
            f"Expected [EXECUTOR_DISPATCH], got: {result[:200]}"
        )

        # Wait for Executor to complete
        base = f"http://127.0.0.1:{port}"
        async with httpx.AsyncClient(base_url=base, timeout=5.0) as client:
            await _wait_for_status(client, plan_id, "completed")

        # Simulate mailbox population (as Executor would push)
        async with httpx.AsyncClient(base_url=base, timeout=5.0) as client:
            resp = await client.get(f"/result/{plan_id}")
            result_data = resp.json()
            await mailbox.post(plan_id, MailboxItem(
                item_type="completion", payload=result_data,
            ))

        # Get result from mailbox
        state2 = State(
            planner_session=PlannerSession(
                session_id="s1",
                plan_json=_simple_plan(plan_id),
            ),
            active_executor_tasks={
                plan_id: ActiveExecutorTask(
                    plan_id=plan_id,
                    status="dispatched",
                ),
            },
        )
        get_result = _build_get_executor_result_tool(ctx)
        with patch("src.common.mailbox.get_mailbox", return_value=mailbox):
            final_result = await get_result.ainvoke(
                {"state": state2, "plan_id": plan_id}
            )

        assert "[EXECUTOR_RESULT]" in final_result, (
            f"Expected [EXECUTOR_RESULT], got: {final_result[:200]}"
        )
        assert "completed" in final_result


async def test_call_executor_starts_real_subprocess_lifecycle(monkeypatch):
    """call_executor survives asyncio subprocess NotImplementedError via fallback."""
    from unittest.mock import patch

    from src.supervisor_agent.tools import (
        _build_call_executor_tool,
        _build_get_executor_result_tool,
    )
    from src.supervisor_agent.v3_lifecycle import V3LifecycleManager

    plan_id = "plan_real_lifecycle_001"
    plan_json = _simple_plan(plan_id)

    # Ensure subprocess Executor returns quickly without real LLM calls.
    monkeypatch.setenv("EXECUTOR_MOCK_MODE", "completed")

    ctx = Context(
        executor_host="127.0.0.1",
        executor_port=0,
        executor_startup_timeout=20.0,
    )
    call_exec = _build_call_executor_tool(ctx)
    get_result = _build_get_executor_result_tool(ctx)
    state = State(
        planner_session=PlannerSession(session_id="s1", plan_json=plan_json),
        active_executor_tasks={},
    )

    # Use an isolated lifecycle manager so this test won't pollute global singleton state.
    local_v3_manager = V3LifecycleManager()

    try:
        with (
            patch("src.supervisor_agent.v3_lifecycle.v3_manager", local_v3_manager),
            patch(
                "src.common.process_manager.asyncio.create_subprocess_exec",
                side_effect=NotImplementedError,
            ) as mock_async_spawn,
        ):
            dispatch_result = await call_exec.ainvoke(
                {"state": state, "plan_id": plan_id}
            )

            assert "[EXECUTOR_DISPATCH]" in dispatch_result
            assert '"status": "accepted"' in dispatch_result
            assert plan_id in dispatch_result
            assert mock_async_spawn.call_count >= 1

            infra = await local_v3_manager.ensure_started(ctx)
            handle = infra.process_manager.get_task_handle(plan_id)
            assert handle is not None, "Executor subprocess should be started"
            assert handle.process.returncode is None

            state_after_dispatch = State(
                planner_session=PlannerSession(session_id="s1", plan_json=plan_json),
                active_executor_tasks={
                    plan_id: ActiveExecutorTask(plan_id=plan_id, status="dispatched"),
                },
            )
            final_result = await get_result.ainvoke(
                {"state": state_after_dispatch, "plan_id": plan_id}
            )

            assert "[EXECUTOR_RESULT]" in final_result
            assert '"status": "completed"' in final_result
            assert "Mock executor completed successfully" in final_result
    finally:
        await local_v3_manager.stop()


# ==================== Executor Server Edge Cases ====================


async def test_health_endpoint(executor_server):
    """Executor /health returns 200."""
    port = executor_server
    async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", timeout=5.0) as client:
        resp = await client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"


async def test_list_tasks_endpoint(executor_server):
    """Executor /tasks returns empty task list initially."""
    port = executor_server
    async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", timeout=5.0) as client:
        resp = await client.get("/tasks")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 0
        assert data["tasks"] == {}


async def test_result_not_found_for_unknown_plan(executor_server):
    """GET /result for unknown plan_id returns 404."""
    port = executor_server
    async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", timeout=5.0) as client:
        resp = await client.get("/result/nonexistent_plan")
        assert resp.status_code == 404


async def test_stop_nonexistent_plan(executor_server):
    """POST /stop for unknown plan_id returns 404."""
    port = executor_server
    async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", timeout=5.0) as client:
        resp = await client.post("/stop/nonexistent_plan", json={"reason": "test"})
        assert resp.status_code == 404
