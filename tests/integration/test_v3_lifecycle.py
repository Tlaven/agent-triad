"""E2E tests for V3 process-separated parallel execution.

These tests verify the V3 infrastructure lifecycle and tool integration
without requiring real LLM calls (mocked where needed).
"""

import asyncio
import json

import httpx
import pytest

from src.common.context import Context
from src.common.mailbox import Mailbox, MailboxItem
from src.supervisor_agent.callback_server import callback_app, set_mailbox
from src.supervisor_agent.state import ActiveExecutorTask, PlannerSession, State


@pytest.fixture
def mailbox():
    mb = Mailbox()
    set_mailbox(mb)
    return mb


@pytest.fixture
def v3_ctx():
    return Context(
        enable_v3_parallel=True,
        executor_host="localhost",
        executor_port=8100,
        supervisor_callback_port=8101,
        snapshot_interval=3,
    )


@pytest.fixture
async def callback_client(mailbox):
    """In-process callback server client."""
    transport = httpx.ASGITransport(app=callback_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# ==================== Callback + Mailbox E2E ====================

async def test_snapshot_then_completion_flow(callback_client, mailbox):
    """Full flow: Executor posts snapshots → posts completion → Supervisor reads via mailbox."""
    plan_id = "plan_e2e_001"

    # Step 1: Executor posts snapshots
    for rounds in (3, 6, 9):
        resp = await callback_client.post(
            "/callback/snapshot",
            json={"plan_id": plan_id, "tool_rounds": rounds},
        )
        assert resp.status_code == 200

    # Step 2: Verify snapshots in mailbox
    snaps = await mailbox.get_all_snapshots(plan_id)
    assert len(snaps) == 3
    assert snaps[-1].payload["tool_rounds"] == 9

    # Step 3: Executor posts completion
    resp = await callback_client.post(
        "/callback/completed",
        json={
            "plan_id": plan_id,
            "status": "completed",
            "summary": "All 5 steps completed successfully",
            "updated_plan_json": json.dumps({"plan_id": plan_id, "steps": []}),
        },
    )
    assert resp.status_code == 200

    # Step 4: Supervisor reads completion
    assert await mailbox.has_completion(plan_id)
    comp = await mailbox.get_completion(plan_id)
    assert comp is not None
    assert comp.payload["status"] == "completed"

    # Step 5: Read full mailbox
    resp = await callback_client.get(f"/mailbox/{plan_id}")
    data = resp.json()
    assert data["has_completion"] is True
    assert len(data["snapshots"]) == 3


async def test_wait_for_completion_with_post(callback_client, mailbox):
    """wait_for_completion receives result posted during wait."""

    async def delayed_completion():
        await asyncio.sleep(0.1)
        await callback_client.post(
            "/callback/completed",
            json={
                "plan_id": "plan_wait_test",
                "status": "completed",
                "summary": "done after delay",
            },
        )

    asyncio.get_event_loop().create_task(delayed_completion())
    result = await mailbox.wait_for_completion("plan_wait_test", timeout=2.0)
    assert result is not None
    assert result.payload["status"] == "completed"


# ==================== V3 Tools Integration ====================

async def test_v3_tools_in_get_tools(v3_ctx):
    """When enable_v3_parallel=True, get_tools returns call_executor + get_executor_result + stop_executor."""
    from src.supervisor_agent.tools import get_tools

    tools = await get_tools(v3_ctx)
    tool_names = [t.name for t in tools]
    assert "call_planner" in tool_names
    assert "call_executor" in tool_names
    assert "stop_executor" in tool_names
    assert "get_executor_result" in tool_names
    assert "get_executor_full_output" in tool_names
    # Old async tools should NOT be present
    assert "call_executor_async" not in tool_names
    assert "wait_for_executor" not in tool_names


async def test_v2_tools_when_v3_disabled():
    """When enable_v3_parallel=False, get_tools returns V2 tool set (no stop_executor or get_executor_result)."""
    from src.supervisor_agent.tools import get_tools

    ctx = Context(enable_v3_parallel=False)
    tools = await get_tools(ctx)
    tool_names = [t.name for t in tools]
    assert "call_planner" in tool_names
    assert "call_executor" in tool_names
    assert "get_executor_full_output" in tool_names
    # V3-only tools should NOT be present
    assert "call_executor_async" not in tool_names
    assert "wait_for_executor" not in tool_names
    assert "stop_executor" not in tool_names
    assert "get_executor_result" not in tool_names


# ==================== State with ActiveExecutorTask ====================

def test_state_with_active_executor_tasks():
    """State can hold ActiveExecutorTask entries."""
    state = State()
    state.active_executor_tasks["plan_001"] = ActiveExecutorTask(
        plan_id="plan_001",
        plan_json='{"steps":[]}',
        status="running",
    )
    assert "plan_001" in state.active_executor_tasks
    assert state.active_executor_tasks["plan_001"].status == "running"


# ==================== Unified call_executor V3 Format ====================

async def test_call_executor_v3_dispatch_format(mailbox):
    """Unified call_executor in V3 mode returns [EXECUTOR_DISPATCH] format (fire-and-forget)."""
    from unittest.mock import AsyncMock, MagicMock, patch

    from src.supervisor_agent.tools import _build_call_executor_tool

    plan_id = "plan_format_test"
    plan_json = json.dumps({
        "plan_id": plan_id,
        "version": 1,
        "goal": "test task",
        "steps": [{"step_id": "step_1", "intent": "test", "expected_output": "result", "status": "pending"}],
    })

    ctx = Context(enable_v3_parallel=True)
    executor_tool = _build_call_executor_tool(ctx)

    state = State(
        planner_session=PlannerSession(
            session_id="s1",
            plan_json=plan_json,
        ),
        active_executor_tasks={},
    )

    # Mock httpx.AsyncClient so POST to Executor server succeeds
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"plan_id": plan_id, "status": "accepted"}

    mock_client_instance = AsyncMock()
    mock_client_instance.post = AsyncMock(return_value=mock_response)
    mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
    mock_client_instance.__aexit__ = AsyncMock(return_value=False)

    with patch("httpx.AsyncClient", return_value=mock_client_instance):
        result = await executor_tool.ainvoke({
            "state": state,
            "plan_id": plan_id,
        })

    # V3 fire-and-forget: must contain [EXECUTOR_DISPATCH], NOT [EXECUTOR_RESULT]
    assert "[EXECUTOR_DISPATCH]" in result
    assert "[EXECUTOR_RESULT]" not in result
    assert plan_id in result

    # Extract and verify the dispatch metadata
    import re
    match = re.search(r'\[EXECUTOR_DISPATCH\]\s*(\{.*?\})', result, re.DOTALL)
    assert match is not None
    meta = json.loads(match.group(1))
    assert meta["plan_id"] == plan_id
    assert meta["status"] == "accepted"


async def test_get_executor_result_returns_executor_result_format(mailbox):
    """get_executor_result returns [EXECUTOR_RESULT] format after mailbox completion."""
    from unittest.mock import patch

    from src.supervisor_agent.state import ActiveExecutorTask
    from src.supervisor_agent.tools import _build_get_executor_result_tool

    plan_id = "plan_result_test"
    plan_json = json.dumps({
        "plan_id": plan_id,
        "version": 1,
        "goal": "test task",
        "steps": [{"step_id": "step_1", "intent": "test", "expected_output": "result", "status": "pending"}],
    })

    # Pre-populate mailbox with a completion result
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

    ctx = Context(enable_v3_parallel=True)
    result_tool = _build_get_executor_result_tool(ctx)

    state = State(
        planner_session=PlannerSession(
            session_id="s1",
            plan_json=plan_json,
        ),
        active_executor_tasks={
            plan_id: ActiveExecutorTask(
                plan_id=plan_id,
                plan_json=plan_json,
                status="dispatched",
            ),
        },
    )

    with patch("src.supervisor_agent.callback_server.get_mailbox", return_value=mailbox):
        result = await result_tool.ainvoke({
            "state": state,
            "plan_id": plan_id,
        })

    # Must contain [EXECUTOR_RESULT] marker
    assert "[EXECUTOR_RESULT]" in result
    assert "Test completed via V3" in result

    # Extract and verify the JSON
    import re
    match = re.search(r'\[EXECUTOR_RESULT\]\s*(\{.*\})', result, re.DOTALL)
    assert match is not None
    meta = json.loads(match.group(1))
    assert meta["status"] == "completed"
    assert meta["plan_id"] == plan_id


# ==================== Context V3 Fields ====================

def test_context_v3_fields_defaults():
    """V3 config fields have correct defaults."""
    ctx = Context(enable_v3_parallel=False)
    assert ctx.enable_v3_parallel is False
    assert ctx.executor_host == "localhost"
    assert ctx.executor_port == 8100
    assert ctx.supervisor_callback_port == 8101
    assert ctx.snapshot_interval == 0
    assert ctx.executor_startup_timeout == 30.0


def test_context_v3_fields_from_env():
    """V3 config fields can be set via environment variables."""
    import os
    os.environ["ENABLE_V3_PARALLEL"] = "true"
    os.environ["EXECUTOR_PORT"] = "9999"
    try:
        ctx = Context()
        assert ctx.enable_v3_parallel is True
        assert ctx.executor_port == 9999
    finally:
        del os.environ["ENABLE_V3_PARALLEL"]
        del os.environ["EXECUTOR_PORT"]
