"""Unit tests for planner_agent._final_planner_text_from_messages and route_planner_output."""

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from src.planner_agent.graph import _final_planner_text_from_messages, route_planner_output
from src.planner_agent.state import PlannerState


# ---------------------------------------------------------------------------
# _final_planner_text_from_messages — string content
# ---------------------------------------------------------------------------


def test_final_text_from_string_content() -> None:
    msg = AIMessage(content="plain plan text")
    assert _final_planner_text_from_messages([msg]) == "plain plan text"


def test_final_text_skips_tool_call_messages() -> None:
    tc_msg = AIMessage(
        content="",
        tool_calls=[{"id": "1", "name": "read_file", "args": {}, "type": "tool_call"}],
    )
    final_msg = AIMessage(content="the plan")
    assert _final_planner_text_from_messages([tc_msg, final_msg]) == "the plan"


def test_final_text_skips_empty_content() -> None:
    empty = AIMessage(content="")
    final = AIMessage(content="real plan")
    assert _final_planner_text_from_messages([empty, final]) == "real plan"


def test_final_text_raises_when_no_valid_message() -> None:
    with pytest.raises(RuntimeError, match="未产生最终文本输出"):
        _final_planner_text_from_messages([])


def test_final_text_raises_when_only_tool_calls() -> None:
    tc_msg = AIMessage(
        content="",
        tool_calls=[{"id": "1", "name": "read_file", "args": {}, "type": "tool_call"}],
    )
    with pytest.raises(RuntimeError):
        _final_planner_text_from_messages([tc_msg])


# ---------------------------------------------------------------------------
# _final_planner_text_from_messages — list content
# ---------------------------------------------------------------------------


def test_final_text_from_list_of_strings() -> None:
    msg = AIMessage(content=["line1", "line2"])
    result = _final_planner_text_from_messages([msg])
    assert "line1" in result
    assert "line2" in result


def test_final_text_from_list_of_dicts() -> None:
    msg = AIMessage(content=[{"type": "text", "text": "block1"}, {"type": "text", "text": " block2"}])
    result = _final_planner_text_from_messages([msg])
    assert "block1" in result
    assert "block2" in result


def test_final_text_from_mixed_list() -> None:
    msg = AIMessage(content=["plain text ", {"type": "text", "text": "dict part"}])
    result = _final_planner_text_from_messages([msg])
    assert "plain text" in result
    assert "dict part" in result


def test_final_text_list_skips_non_text_dicts() -> None:
    msg = AIMessage(content=[{"type": "image", "url": "http://example.com"}, {"type": "text", "text": "only this"}])
    result = _final_planner_text_from_messages([msg])
    assert result == "only this"


# ---------------------------------------------------------------------------
# route_planner_output
# ---------------------------------------------------------------------------


def test_route_planner_output_with_tool_calls() -> None:
    state = PlannerState(messages=[
        AIMessage(content="", tool_calls=[{"id": "1", "name": "t", "args": {}, "type": "tool_call"}])
    ])
    assert route_planner_output(state) == "tools"


def test_route_planner_output_without_tool_calls() -> None:
    state = PlannerState(messages=[AIMessage(content="final plan")])
    assert route_planner_output(state) == "__end__"
