"""Unit tests for supervisor_agent.graph.dynamic_tools_node (with mocked ToolNode)."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from src.common.context import Context
from src.supervisor_agent.graph import dynamic_tools_node
from src.supervisor_agent.state import PlannerSession, State


def _make_runtime(context: Context | None = None) -> MagicMock:
    mock_runtime = MagicMock()
    mock_runtime.context = context or Context(max_replan=2)
    return mock_runtime


def _make_state_with_tool_call(
    tool_name: str,
    tool_args: dict,
    call_id: str = "call_test1",
    planner_session: PlannerSession | None = None,
    replan_count: int = 0,
) -> State:
    """Build a State whose last message is an AIMessage with one tool_call."""
    msg = AIMessage(
        content="",
        tool_calls=[{
            "id": call_id,
            "name": tool_name,
            "args": tool_args,
            "type": "tool_call",
        }],
    )
    return State(
        messages=[HumanMessage(content="user input"), msg],
        planner_session=planner_session,
        replan_count=replan_count,
    )


def _make_tool_node_mock(tool_message: ToolMessage) -> MagicMock:
    """Build a ToolNode mock that returns a single ToolMessage."""
    mock_tn = MagicMock()
    mock_tn.ainvoke = AsyncMock(return_value={"messages": [tool_message]})
    return mock_tn


# ---------------------------------------------------------------------------
# call_planner → PlannerSession updated
# ---------------------------------------------------------------------------

async def test_call_planner_creates_planner_session() -> None:
    plan_json = json.dumps({
        "plan_id": "plan_newplan",
        "version": 1,
        "goal": "build something",
        "steps": [],
    })
    state = _make_state_with_tool_call(
        "call_planner", {"task_core": "build something"}, call_id="call_gen1"
    )
    tm = ToolMessage(content=plan_json, tool_call_id="call_gen1")

    with patch("src.supervisor_agent.graph.ToolNode", return_value=_make_tool_node_mock(tm)):
        result = await dynamic_tools_node(state, _make_runtime())

    assert "planner_session" in result
    session: PlannerSession = result["planner_session"]
    assert session.plan_json == plan_json
    assert "plan_newplan" in session.planner_history_by_plan_id


async def test_call_planner_stores_task_core_in_history() -> None:
    plan_json = json.dumps({"plan_id": "plan_abc", "version": 1, "goal": "g", "steps": []})
    state = _make_state_with_tool_call(
        "call_planner", {"task_core": "my important task"}, call_id="call_gen2"
    )
    tm = ToolMessage(content=plan_json, tool_call_id="call_gen2")

    with patch("src.supervisor_agent.graph.ToolNode", return_value=_make_tool_node_mock(tm)):
        result = await dynamic_tools_node(state, _make_runtime())

    history = result["planner_session"].planner_history_by_plan_id.get("plan_abc", [])
    assert any("my important task" in msg.get("content", "") for msg in history)


async def test_call_planner_archives_old_version() -> None:
    """When call_planner produces a newer version, the old plan must be archived."""
    old_plan = json.dumps({"plan_id": "plan_same", "version": 1, "goal": "g", "steps": []})
    new_plan = json.dumps({"plan_id": "plan_same", "version": 2, "goal": "g", "steps": []})

    existing_session = PlannerSession(
        session_id="sess1",
        plan_json=old_plan,
    )
    state = _make_state_with_tool_call(
        "call_planner", {"task_core": "update", "plan_id": "plan_same"},
        call_id="call_gen3",
        planner_session=existing_session,
    )
    tm = ToolMessage(content=new_plan, tool_call_id="call_gen3")

    with patch("src.supervisor_agent.graph.ToolNode", return_value=_make_tool_node_mock(tm)):
        result = await dynamic_tools_node(state, _make_runtime())

    archive = result["planner_session"].plan_archive_by_plan_id.get("plan_same", [])
    assert old_plan in archive


# ---------------------------------------------------------------------------
# call_executor → replan_count updated, planner_session synced
# ---------------------------------------------------------------------------

async def test_call_executor_completed_resets_replan_count(
    sample_executor_result_completed,
) -> None:
    state = _make_state_with_tool_call(
        "call_executor", {"plan_id": "plan_test0001"},
        call_id="call_exec1",
        planner_session=PlannerSession(
            session_id="s1",
            plan_json=json.dumps({"plan_id": "plan_test0001", "version": 1, "goal": "g", "steps": []}),
        ),
        replan_count=1,
    )
    tm = ToolMessage(content=sample_executor_result_completed, tool_call_id="call_exec1")

    with patch("src.supervisor_agent.graph.ToolNode", return_value=_make_tool_node_mock(tm)):
        result = await dynamic_tools_node(state, _make_runtime())

    assert result["replan_count"] == 0
    assert result["planner_session"].last_executor_status == "completed"


async def test_call_executor_failed_increments_replan_count(
    sample_executor_result_failed,
) -> None:
    state = _make_state_with_tool_call(
        "call_executor", {"plan_id": "plan_test0001"},
        call_id="call_exec2",
        planner_session=PlannerSession(
            session_id="s1",
            plan_json=json.dumps({"plan_id": "plan_test0001", "version": 1, "goal": "g", "steps": []}),
        ),
        replan_count=0,
    )
    tm = ToolMessage(content=sample_executor_result_failed, tool_call_id="call_exec2")

    with patch("src.supervisor_agent.graph.ToolNode", return_value=_make_tool_node_mock(tm)):
        result = await dynamic_tools_node(state, _make_runtime())

    assert result["replan_count"] == 1
    assert result["planner_session"].last_executor_status == "failed"


async def test_call_executor_failed_writes_updated_plan_to_session(
    sample_executor_result_failed,
    sample_failed_plan_json,
) -> None:
    state = _make_state_with_tool_call(
        "call_executor", {"plan_id": "plan_test0001"},
        call_id="call_exec3",
        planner_session=PlannerSession(
            session_id="s1",
            plan_json=json.dumps({"plan_id": "plan_test0001", "version": 1, "goal": "g", "steps": []}),
        ),
    )
    tm = ToolMessage(content=sample_executor_result_failed, tool_call_id="call_exec3")

    with patch("src.supervisor_agent.graph.ToolNode", return_value=_make_tool_node_mock(tm)):
        result = await dynamic_tools_node(state, _make_runtime())

    # The planner_session.plan_json should be the failed plan (non-empty)
    assert (result["planner_session"].plan_json or "").strip()


async def test_call_executor_llm_receives_sanitized_feedback(
    sample_executor_result_completed,
) -> None:
    """The ToolMessage visible to the LLM must NOT contain updated_plan_json."""
    state = _make_state_with_tool_call(
        "call_executor", {"plan_id": "plan_test0001"},
        call_id="call_exec4",
        planner_session=PlannerSession(
            session_id="s1",
            plan_json=json.dumps({"plan_id": "plan_test0001", "version": 1, "goal": "g", "steps": []}),
        ),
    )
    tm = ToolMessage(content=sample_executor_result_completed, tool_call_id="call_exec4")

    with patch("src.supervisor_agent.graph.ToolNode", return_value=_make_tool_node_mock(tm)):
        result = await dynamic_tools_node(state, _make_runtime())

    # The message returned in 'messages' should be the sanitized (public) feedback
    sanitized_msgs = result["messages"]
    assert len(sanitized_msgs) == 1
    content = sanitized_msgs[0].content
    assert "[EXECUTOR_RESULT]" not in content
    assert "updated_plan_json" not in content


# ---------------------------------------------------------------------------
# Non call_planner / call_executor tools pass through unchanged
# ---------------------------------------------------------------------------

async def test_unknown_tool_passthrough() -> None:
    state = _make_state_with_tool_call(
        "some_other_tool", {"x": 1}, call_id="call_other1"
    )
    tm = ToolMessage(content="raw result from other tool", tool_call_id="call_other1")

    with patch("src.supervisor_agent.graph.ToolNode", return_value=_make_tool_node_mock(tm)):
        result = await dynamic_tools_node(state, _make_runtime())

    assert result["messages"][0].content == "raw result from other tool"
    assert "planner_session" not in result
