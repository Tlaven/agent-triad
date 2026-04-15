"""V3 lifecycle integration tests for Pull-mode architecture.

Tests verify the V3 infrastructure lifecycle and tool integration
without requiring real LLM calls or subprocess spawning (mocked where needed).

Mailbox unit-level behaviour → tests/unit_tests/common/test_mailbox.py
Context fields → tests/unit_tests/common/test_context.py
ProcessManager recover logic → tests/unit_tests/common/test_process_manager.py
"""

import json
import re

import pytest

from src.common.context import Context
from src.common.mailbox import Mailbox, MailboxItem, set_mailbox, get_mailbox
from src.supervisor_agent.state import ActiveExecutorTask, PlannerSession, State


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
    return mb


@pytest.fixture
def v3_ctx():
    return Context(executor_host="localhost", executor_port=0, snapshot_interval=0)


# ==================== V3 State Contract ====================


def test_state_with_active_executor_tasks():
    """State can hold ActiveExecutorTask entries."""
    state = State()
    state.active_executor_tasks["plan_001"] = ActiveExecutorTask(
        plan_id="plan_001",
        status="running",
    )
    assert "plan_001" in state.active_executor_tasks
    assert state.active_executor_tasks["plan_001"].status == "running"


# ==================== V3 Tools Integration ====================


async def test_get_tools_returns_full_set(v3_ctx):
    """get_tools returns all expected tool names."""
    from src.supervisor_agent.tools import get_tools

    tools = await get_tools(v3_ctx)
    tool_names = [t.name for t in tools]
    for expected in ["call_planner", "call_executor", "stop_executor",
                     "get_executor_result", "get_executor_full_output",
                     "check_executor_progress", "list_executor_tasks"]:
        assert expected in tool_names


# ==================== Unified call_executor V3 Dispatch Format ====================


async def test_call_executor_v3_dispatch_format(mailbox):
    """call_executor in V3 mode returns [EXECUTOR_DISPATCH] format (fire-and-forget)."""
    from unittest.mock import AsyncMock, MagicMock, patch

    from src.supervisor_agent.tools import _build_call_executor_tool

    plan_id = "plan_format_test"
    plan_json = json.dumps({
        "plan_id": plan_id,
        "version": 1,
        "goal": "test task",
        "steps": [{"step_id": "step_1", "intent": "test", "expected_output": "result", "status": "pending"}],
    })

    ctx = Context()
    executor_tool = _build_call_executor_tool(ctx)

    state = State(
        planner_session=PlannerSession(session_id="s1", plan_json=plan_json),
        active_executor_tasks={},
    )

    mock_pm = MagicMock()
    mock_pm.base_url = "http://localhost:9999"
    mock_pm.is_running = True
    mock_handle = MagicMock()
    mock_handle.base_url = "http://localhost:9999"
    mock_pm.start_for_task = AsyncMock(return_value=mock_handle)

    mock_mailbox_server = MagicMock()
    mock_mailbox_server.base_url = "http://127.0.0.1:19999"

    mock_infra = MagicMock()
    mock_infra.process_manager = mock_pm
    mock_infra.mailbox_server = mock_mailbox_server

    mock_health_response = MagicMock()
    mock_health_response.status_code = 200

    mock_post_response = MagicMock()
    mock_post_response.status_code = 200
    mock_post_response.json.return_value = {"plan_id": plan_id, "status": "accepted"}

    mock_client_instance = AsyncMock()
    mock_client_instance.get = AsyncMock(return_value=mock_health_response)
    mock_client_instance.post = AsyncMock(return_value=mock_post_response)
    mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
    mock_client_instance.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("src.supervisor_agent.v3_lifecycle.v3_manager") as mock_v3_mgr,
        patch("httpx.AsyncClient", return_value=mock_client_instance),
    ):
        mock_v3_mgr.ensure_started = AsyncMock(return_value=mock_infra)
        result = await executor_tool.ainvoke({"state": state, "plan_id": plan_id})

    assert "[EXECUTOR_DISPATCH]" in result
    assert "[EXECUTOR_RESULT]" not in result
    assert plan_id in result

    match = re.search(r'\[EXECUTOR_DISPATCH\]\s*(\{.*?\})', result, re.DOTALL)
    assert match is not None
    meta = json.loads(match.group(1))
    assert meta["plan_id"] == plan_id
    assert meta["status"] == "accepted"


async def test_get_executor_result_returns_executor_result_format(mailbox):
    """get_executor_result returns [EXECUTOR_RESULT] after mailbox completion."""
    from unittest.mock import patch

    from src.supervisor_agent.tools import _build_get_executor_result_tool

    plan_id = "plan_result_test"
    plan_json = json.dumps({
        "plan_id": plan_id,
        "version": 1,
        "goal": "test task",
        "steps": [{"step_id": "step_1", "intent": "test", "expected_output": "result", "status": "pending"}],
    })

    await mailbox.post(plan_id, MailboxItem(
        item_type="completion",
        payload={
            "plan_id": plan_id,
            "status": "completed",
            "summary": "Test completed via V3",
            "updated_plan_json": plan_json,
            "snapshot_json": "",
        },
    ))

    ctx = Context()
    result_tool = _build_get_executor_result_tool(ctx)
    state = State(
        planner_session=PlannerSession(session_id="s1", plan_json=plan_json),
        active_executor_tasks={
            plan_id: ActiveExecutorTask(plan_id=plan_id, status="dispatched"),
        },
    )

    with patch("src.common.mailbox.get_mailbox", return_value=mailbox):
        result = await result_tool.ainvoke({"state": state, "plan_id": plan_id})

    assert "[EXECUTOR_RESULT]" in result
    assert "Test completed via V3" in result

    match = re.search(r'\[EXECUTOR_RESULT\]\s*(\{.*\})', result, re.DOTALL)
    assert match is not None
    meta = json.loads(match.group(1))
    assert meta["status"] == "completed"
    assert meta["plan_id"] == plan_id


# ==================== V3 Lifecycle Manager ====================


async def test_v3_lifecycle_starts_process_manager(mailbox):
    """V3LifecycleManager.ensure_started initializes mailbox and process manager."""
    from unittest.mock import AsyncMock, MagicMock, patch

    from src.supervisor_agent.v3_lifecycle import V3LifecycleManager

    mgr = V3LifecycleManager()
    mock_pm = MagicMock()
    mock_pm.base_url = "http://localhost:8080"
    mock_pm.is_running = True
    mock_pm.recover_or_start = AsyncMock()
    mock_pm.stop = AsyncMock()

    ctx = Context(executor_port=0)

    with patch("src.common.process_manager.ExecutorProcessManager", return_value=mock_pm):
        infra = await mgr.ensure_started(ctx)

    assert infra.started is True
    assert infra.process_manager is mock_pm
    assert infra.mailbox is not None
    assert get_mailbox() is infra.mailbox

    await mgr.stop()


async def test_v3_lifecycle_ensure_started_idempotent(mailbox):
    """ensure_started returns same infrastructure on repeated calls."""
    from unittest.mock import AsyncMock, MagicMock, patch

    from src.supervisor_agent.v3_lifecycle import V3LifecycleManager

    mgr = V3LifecycleManager()
    mock_pm = MagicMock()
    mock_pm.stop = AsyncMock()
    ctx = Context(executor_port=0)

    with patch("src.common.process_manager.ExecutorProcessManager", return_value=mock_pm):
        infra1 = await mgr.ensure_started(ctx)
        infra2 = await mgr.ensure_started(ctx)

    assert infra1 is infra2
    await mgr.stop()


# ==================== Fire-and-forget Dispatch ====================


async def test_call_executor_dispatches_then_get_result(mailbox):
    """call_executor dispatches; get_executor_result reads the mailbox result."""
    from unittest.mock import AsyncMock, MagicMock, patch

    from src.supervisor_agent.tools import (
        _build_call_executor_tool,
        _build_get_executor_result_tool,
    )

    plan_id = "plan_dispatch_test"
    plan_json = json.dumps({
        "plan_id": plan_id,
        "version": 1,
        "goal": "test dispatch + get",
        "steps": [{"step_id": "step_1", "intent": "test", "expected_output": "result", "status": "pending"}],
    })

    ctx = Context()
    executor_tool = _build_call_executor_tool(ctx)
    state = State(
        planner_session=PlannerSession(session_id="s1", plan_json=plan_json),
        active_executor_tasks={},
    )

    mock_pm = MagicMock()
    mock_pm.base_url = "http://localhost:9999"
    mock_pm.is_running = True
    mock_handle = MagicMock()
    mock_handle.base_url = "http://localhost:9999"
    mock_pm.start_for_task = AsyncMock(return_value=mock_handle)

    mock_mailbox_server = MagicMock()
    mock_mailbox_server.base_url = "http://127.0.0.1:19999"

    mock_infra = MagicMock()
    mock_infra.process_manager = mock_pm
    mock_infra.mailbox_server = mock_mailbox_server

    mock_post_resp = MagicMock()
    mock_post_resp.status_code = 200
    mock_post_resp.json.return_value = {"plan_id": plan_id, "status": "accepted"}

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_post_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("src.supervisor_agent.v3_lifecycle.v3_manager") as mock_v3_mgr,
        patch("httpx.AsyncClient", return_value=mock_client),
    ):
        mock_v3_mgr.ensure_started = AsyncMock(return_value=mock_infra)
        dispatch_result = await executor_tool.ainvoke({"state": state, "plan_id": plan_id})

    assert "[EXECUTOR_DISPATCH]" in dispatch_result

    # Simulate Executor pushing result to mailbox
    await mailbox.post(plan_id, MailboxItem(
        item_type="completion",
        payload={
            "plan_id": plan_id,
            "status": "completed",
            "updated_plan_json": plan_json,
            "summary": "Dispatch test completed via mailbox push",
            "snapshot_json": "",
        },
    ))

    state2 = State(
        planner_session=PlannerSession(session_id="s1", plan_json=plan_json),
        active_executor_tasks={
            plan_id: ActiveExecutorTask(plan_id=plan_id, status="dispatched"),
        },
    )

    result_tool = _build_get_executor_result_tool(ctx)
    with patch("src.common.mailbox.get_mailbox", return_value=mailbox):
        final_result = await result_tool.ainvoke({"state": state2, "plan_id": plan_id})

    assert "[EXECUTOR_RESULT]" in final_result
    assert "completed" in final_result
    assert "Dispatch test completed via mailbox push" in final_result
