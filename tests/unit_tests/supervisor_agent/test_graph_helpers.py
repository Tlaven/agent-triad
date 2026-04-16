"""Unit tests for helper functions in supervisor_agent.graph and tools."""

import json

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from src.supervisor_agent.graph import (
    _build_executor_feedback_for_llm,
    _build_id_to_call,
    _build_id_to_name,
    _extract_executor_summary,
    _needs_mode3_upgrade,
    _parse_plan_meta,
    route_model_output,
)
from src.supervisor_agent.state import State
from src.supervisor_agent.tools import _normalize_plan_id_arg

# ---------------------------------------------------------------------------
# _needs_mode3_upgrade
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("summary", [
    "需要计划层重构，当前路径无法推进",
    "无法继续，请重新规划",
    "需要重新拆解意图",
    "replan needed",
    "cannot proceed further",
    "REPLAN required",  # case-insensitive
])
def test_needs_mode3_upgrade_returns_true(summary: str) -> None:
    assert _needs_mode3_upgrade(summary, None) is True


@pytest.mark.parametrize("summary", [
    "工具调用超时，请重试",
    "文件写入失败，权限不足",
    "命令执行返回码非零",
])
def test_needs_mode3_upgrade_returns_false_for_normal_failures(summary: str) -> None:
    assert _needs_mode3_upgrade(summary, None) is False


def test_needs_mode3_upgrade_both_none_returns_false() -> None:
    assert _needs_mode3_upgrade(None, None) is False


def test_needs_mode3_upgrade_checks_error_detail_too() -> None:
    assert _needs_mode3_upgrade(None, "需要重规划") is True


# ---------------------------------------------------------------------------
# _build_executor_feedback_for_llm
# ---------------------------------------------------------------------------

def _make_full_content(summary: str) -> str:
    meta = {"status": "completed", "error_detail": None, "updated_plan_json": "{}"}
    return f"{summary}\n\n[EXECUTOR_RESULT] {json.dumps(meta)}"


def test_feedback_completed_returns_summary_with_hint() -> None:
    content = _make_full_content("All tasks done successfully")
    feedback = _build_executor_feedback_for_llm(content, "completed", None)
    assert feedback.startswith("All tasks done successfully")
    assert "get_executor_result" in feedback
    assert "detail" in feedback
    assert "[EXECUTOR_RESULT]" not in feedback


def test_feedback_failed_includes_error_detail() -> None:
    meta = {"status": "failed", "error_detail": "tool timeout", "updated_plan_json": "{}"}
    content = f"Something failed\n\n[EXECUTOR_RESULT] {json.dumps(meta)}"
    feedback = _build_executor_feedback_for_llm(content, "failed", "tool timeout")
    assert "failed" in feedback.lower()
    assert "tool timeout" in feedback


def test_feedback_failed_no_error_detail_uses_default() -> None:
    meta = {"status": "failed", "error_detail": None, "updated_plan_json": "{}"}
    content = f"summary\n\n[EXECUTOR_RESULT] {json.dumps(meta)}"
    feedback = _build_executor_feedback_for_llm(content, "failed", None)
    assert "failed" in feedback.lower()
    assert "未知错误" in feedback


def test_feedback_none_status_returns_summary() -> None:
    content = _make_full_content("some output")
    feedback = _build_executor_feedback_for_llm(content, None, None)
    assert "some output" in feedback


# ---------------------------------------------------------------------------
# _extract_executor_summary
# ---------------------------------------------------------------------------

def test_extract_summary_with_marker_returns_preamble() -> None:
    content = "This is the summary text.\n\n[EXECUTOR_RESULT] {}"
    assert _extract_executor_summary(content) == "This is the summary text."


def test_extract_summary_without_marker_returns_full_stripped() -> None:
    content = "  Just a plain string.  "
    assert _extract_executor_summary(content) == "Just a plain string."


def test_extract_summary_empty_before_marker() -> None:
    content = "[EXECUTOR_RESULT] {}"
    assert _extract_executor_summary(content) == ""


# ---------------------------------------------------------------------------
# _parse_plan_meta — parametrized
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw,exp_id,exp_version", [
    (json.dumps({"plan_id": "plan_abc", "version": 3, "goal": "g", "steps": []}), "plan_abc", 3),
    ("{not json}", None, None),
    (json.dumps({"version": 2, "goal": "g", "steps": []}), None, 2),
    (json.dumps({"plan_id": "plan_abc", "goal": "g", "steps": []}), "plan_abc", None),
    (json.dumps({"plan_id": "plan_abc", "version": "v1", "goal": "g"}), "plan_abc", None),
    (json.dumps([1, 2, 3]), None, None),
])
def test_parse_plan_meta(raw, exp_id, exp_version) -> None:
    plan_id, version = _parse_plan_meta(raw)
    assert plan_id == exp_id
    assert version == exp_version


# ---------------------------------------------------------------------------
# _build_id_to_name and _build_id_to_call
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("messages", [
    [],
    [HumanMessage(content="hi")],
    [AIMessage(content="direct answer")],
])
def test_build_id_to_name_returns_empty_dict(messages) -> None:
    state = State(messages=messages)
    assert _build_id_to_name(state) == {}


def test_build_id_to_name_with_tool_calls() -> None:
    msg = AIMessage(
        content="",
        tool_calls=[
            {"id": "call_abc", "name": "call_planner", "args": {}, "type": "tool_call"},
            {"id": "call_def", "name": "call_executor", "args": {}, "type": "tool_call"},
        ],
    )
    state = State(messages=[msg])
    assert _build_id_to_name(state) == {"call_abc": "call_planner", "call_def": "call_executor"}


def test_build_id_to_call_with_tool_calls() -> None:
    tc = {"id": "call_xyz", "name": "call_executor", "args": {"task_description": "run task"}, "type": "tool_call"}
    msg = AIMessage(content="", tool_calls=[tc])
    state = State(messages=[msg])
    mapping = _build_id_to_call(state)
    assert "call_xyz" in mapping
    assert mapping["call_xyz"]["name"] == "call_executor"


# ---------------------------------------------------------------------------
# route_model_output
# ---------------------------------------------------------------------------

def test_route_model_output_no_tool_calls_returns_end() -> None:
    state = State(messages=[AIMessage(content="direct answer")])
    assert route_model_output(state) == "__end__"


def test_route_model_output_with_tool_calls_returns_tools() -> None:
    msg = AIMessage(
        content="",
        tool_calls=[{"name": "call_executor", "args": {}, "id": "1", "type": "tool_call"}],
    )
    state = State(messages=[msg])
    assert route_model_output(state) == "tools"


def test_route_model_output_non_ai_message_raises_value_error() -> None:
    state = State(messages=[HumanMessage(content="hi")])
    with pytest.raises(ValueError):
        route_model_output(state)


# ---------------------------------------------------------------------------
# _normalize_plan_id_arg
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("arg", [None, "", "   "])
def test_normalize_plan_id_arg_empty_inputs_return_none(arg) -> None:
    assert _normalize_plan_id_arg(arg) is None


def test_normalize_plan_id_arg_valid_returns_stripped() -> None:
    assert _normalize_plan_id_arg("  plan_abc  ") == "plan_abc"
    assert _normalize_plan_id_arg("plan_xyz") == "plan_xyz"
