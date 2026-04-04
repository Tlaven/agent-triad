"""Unit tests for supervisor_agent.graph.call_model node (with mocked LLM)."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

from langchain_core.messages import AIMessage, HumanMessage

from src.common.context import Context
from src.supervisor_agent.graph import call_model
from src.supervisor_agent.state import PlannerSession, State


def _make_runtime(context: Context | None = None) -> MagicMock:
    """Build a mock Runtime with a real Context attached."""
    mock_runtime = MagicMock()
    mock_runtime.context = context or Context(max_replan=2, max_executor_iterations=5)
    return mock_runtime


def _make_mock_llm(response: AIMessage) -> MagicMock:
    """Build a mock LLM that returns a single preset response."""
    mock = MagicMock()
    mock.bind_tools = MagicMock(return_value=mock)
    mock.ainvoke = AsyncMock(return_value=response)
    return mock


# ---------------------------------------------------------------------------
# Max-replan hit → forced mode-1 termination (no LLM call needed)
# ---------------------------------------------------------------------------

async def test_call_model_max_replan_reached_returns_mode1() -> None:
    state = State(
        messages=[HumanMessage(content="do something complex")],
        planner_session=PlannerSession(
            session_id="s1",
            last_executor_status="failed",
        ),
        replan_count=2,
    )
    runtime = _make_runtime(Context(max_replan=2))

    result = await call_model(state, runtime)

    assert result["supervisor_decision"].mode == 1
    assert len(result["messages"]) == 1
    assert isinstance(result["messages"][0], AIMessage)


async def test_call_model_below_max_replan_does_not_force_terminate() -> None:
    """When replan_count < max_replan, it should NOT short-circuit to mode-1."""
    state = State(
        messages=[HumanMessage(content="do something")],
        planner_session=PlannerSession(
            session_id="s1",
            last_executor_status="failed",
            plan_json=json.dumps({"plan_id": "p1", "version": 1, "goal": "g", "steps": []}),
        ),
        replan_count=1,
    )
    runtime = _make_runtime(Context(max_replan=3))
    mock_llm = _make_mock_llm(AIMessage(content="Trying again"))

    with patch("src.supervisor_agent.graph.load_chat_model", return_value=mock_llm):
        result = await call_model(state, runtime)

    # LLM was called (mode-1 forced exit was NOT triggered)
    mock_llm.ainvoke.assert_called_once()
    assert result["messages"][0].content == "Trying again"


# ---------------------------------------------------------------------------
# Mode 2 → Mode 3 automatic upgrade (no LLM call)
# ---------------------------------------------------------------------------

async def test_call_model_mode2_to_mode3_upgrade_triggered() -> None:
    """When Mode2 executor fails with an upgrade-signal summary and plan_json is empty,
    call_model should auto-generate a call_planner tool_call without consulting the LLM."""
    state = State(
        messages=[HumanMessage(content="original task")],
        planner_session=PlannerSession(
            session_id="s1",
            last_executor_status="failed",
            plan_json="",  # Mode2: no plan JSON
            last_executor_summary="需要重新规划，无法继续当前路径",
        ),
        replan_count=0,
    )
    runtime = _make_runtime(Context(max_replan=3))

    result = await call_model(state, runtime)

    assert result["supervisor_decision"].mode == 3
    tool_calls = result["messages"][0].tool_calls
    assert any(tc.get("name") == "call_planner" for tc in tool_calls)


async def test_call_model_mode2_to_mode3_not_triggered_if_plan_json_present() -> None:
    """If plan_json is non-empty, Mode2→3 upgrade is suppressed; LLM decides."""
    state = State(
        messages=[HumanMessage(content="task")],
        planner_session=PlannerSession(
            session_id="s1",
            last_executor_status="failed",
            plan_json=json.dumps({"plan_id": "p1", "version": 1, "goal": "g", "steps": []}),
            last_executor_summary="需要重新规划",
        ),
        replan_count=0,
    )
    runtime = _make_runtime(Context(max_replan=3))
    mock_llm = _make_mock_llm(AIMessage(content="I'll try again"))

    with patch("src.supervisor_agent.graph.load_chat_model", return_value=mock_llm):
        await call_model(state, runtime)

    # LLM should have been consulted (no forced upgrade happened)
    mock_llm.ainvoke.assert_called_once()


# ---------------------------------------------------------------------------
# is_last_step forces termination
# ---------------------------------------------------------------------------

async def test_call_model_is_last_step_with_tool_calls_forces_end() -> None:
    state = State(
        messages=[HumanMessage(content="task")],
        is_last_step=True,
    )
    runtime = _make_runtime()
    # LLM wants to call a tool, but is_last_step prevents it
    tool_response = AIMessage(
        content="",
        tool_calls=[{"name": "call_executor", "args": {}, "id": "c1", "type": "tool_call"}],
    )
    mock_llm = _make_mock_llm(tool_response)

    with patch("src.supervisor_agent.graph.load_chat_model", return_value=mock_llm):
        result = await call_model(state, runtime)

    assert result["supervisor_decision"].mode == 1
    assert not result["messages"][0].tool_calls


# ---------------------------------------------------------------------------
# Normal direct response (mode 1)
# ---------------------------------------------------------------------------

async def test_call_model_direct_response_sets_mode1() -> None:
    state = State(messages=[HumanMessage(content="什么是 Python？")])
    runtime = _make_runtime()
    mock_llm = _make_mock_llm(AIMessage(content="Python 是一种编程语言。"))

    with patch("src.supervisor_agent.graph.load_chat_model", return_value=mock_llm):
        result = await call_model(state, runtime)

    assert result["supervisor_decision"].mode == 1
    assert result["messages"][0].content == "Python 是一种编程语言。"
