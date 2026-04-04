"""Unit tests for untested helper functions in supervisor_agent.graph and tools."""

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

@pytest.mark.parametrize("summary,expected", [
    ("需要计划层重构，当前路径无法推进", True),
    ("无法继续，请重新规划", True),
    ("无法完成当前任务", True),
    ("需要重新拆解意图", True),
    ("需要重构整体流程", True),
    ("replan needed", True),
    ("cannot proceed further", True),
    ("no reusable plan available", True),
])
def test_needs_mode3_upgrade_returns_true(summary: str, expected: bool) -> None:
    assert _needs_mode3_upgrade(summary, None) is True


@pytest.mark.parametrize("summary", [
    "工具调用超时，请重试",
    "文件写入失败，权限不足",
    "命令执行返回码非零",
    "运行中断，未知原因",
])
def test_needs_mode3_upgrade_returns_false_for_normal_failures(summary: str) -> None:
    assert _needs_mode3_upgrade(summary, None) is False


def test_needs_mode3_upgrade_both_none_returns_false() -> None:
    assert _needs_mode3_upgrade(None, None) is False


def test_needs_mode3_upgrade_checks_error_detail_too() -> None:
    assert _needs_mode3_upgrade(None, "需要重规划") is True


def test_needs_mode3_upgrade_case_insensitive() -> None:
    assert _needs_mode3_upgrade("REPLAN required", None) is True


# ---------------------------------------------------------------------------
# _build_executor_feedback_for_llm
# ---------------------------------------------------------------------------

def _make_full_content(summary: str) -> str:
    meta = {"status": "completed", "error_detail": None, "updated_plan_json": "{}"}
    return f"{summary}\n\n[EXECUTOR_RESULT] {json.dumps(meta)}"


def test_feedback_completed_returns_only_summary() -> None:
    content = _make_full_content("All tasks done successfully")
    feedback = _build_executor_feedback_for_llm(content, "completed", None)
    assert feedback == "All tasks done successfully"
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
# _parse_plan_meta
# ---------------------------------------------------------------------------

def test_parse_plan_meta_valid_json() -> None:
    plan = {"plan_id": "plan_abc", "version": 3, "goal": "g", "steps": []}
    plan_id, version = _parse_plan_meta(json.dumps(plan))
    assert plan_id == "plan_abc"
    assert version == 3


def test_parse_plan_meta_invalid_json_returns_none_tuple() -> None:
    plan_id, version = _parse_plan_meta("{not json}")
    assert plan_id is None
    assert version is None


def test_parse_plan_meta_missing_plan_id_returns_none_for_id() -> None:
    plan = {"version": 2, "goal": "g", "steps": []}
    plan_id, version = _parse_plan_meta(json.dumps(plan))
    assert plan_id is None
    assert version == 2


def test_parse_plan_meta_missing_version_returns_none_for_version() -> None:
    plan = {"plan_id": "plan_abc", "goal": "g", "steps": []}
    plan_id, version = _parse_plan_meta(json.dumps(plan))
    assert plan_id == "plan_abc"
    assert version is None


def test_parse_plan_meta_version_not_int_returns_none() -> None:
    plan = {"plan_id": "plan_abc", "version": "v1", "goal": "g"}
    plan_id, version = _parse_plan_meta(json.dumps(plan))
    assert plan_id == "plan_abc"
    assert version is None


def test_parse_plan_meta_non_dict_returns_none_tuple() -> None:
    plan_id, version = _parse_plan_meta(json.dumps([1, 2, 3]))
    assert plan_id is None
    assert version is None


# ---------------------------------------------------------------------------
# _build_id_to_name and _build_id_to_call
# ---------------------------------------------------------------------------

def test_build_id_to_name_empty_messages() -> None:
    state = State(messages=[])
    assert _build_id_to_name(state) == {}


def test_build_id_to_name_last_message_not_ai() -> None:
    state = State(messages=[HumanMessage(content="hi")])
    assert _build_id_to_name(state) == {}


def test_build_id_to_name_ai_without_tool_calls() -> None:
    state = State(messages=[AIMessage(content="direct answer")])
    assert _build_id_to_name(state) == {}


def test_build_id_to_name_with_tool_calls() -> None:
    msg = AIMessage(
        content="",
        tool_calls=[
            {"id": "call_abc", "name": "generate_plan", "args": {}, "type": "tool_call"},
            {"id": "call_def", "name": "execute_plan", "args": {}, "type": "tool_call"},
        ],
    )
    state = State(messages=[msg])
    mapping = _build_id_to_name(state)
    assert mapping == {"call_abc": "generate_plan", "call_def": "execute_plan"}


def test_build_id_to_call_with_tool_calls() -> None:
    tc = {"id": "call_xyz", "name": "execute_plan", "args": {"task_description": "run task"}, "type": "tool_call"}
    msg = AIMessage(content="", tool_calls=[tc])
    state = State(messages=[msg])
    mapping = _build_id_to_call(state)
    assert "call_xyz" in mapping
    assert mapping["call_xyz"]["name"] == "execute_plan"


# ---------------------------------------------------------------------------
# route_model_output
# ---------------------------------------------------------------------------

def test_route_model_output_no_tool_calls_returns_end() -> None:
    state = State(messages=[AIMessage(content="direct answer")])
    assert route_model_output(state) == "__end__"


def test_route_model_output_with_tool_calls_returns_tools() -> None:
    msg = AIMessage(
        content="",
        tool_calls=[{"name": "execute_plan", "args": {}, "id": "1", "type": "tool_call"}],
    )
    state = State(messages=[msg])
    assert route_model_output(state) == "tools"


def test_route_model_output_non_ai_message_raises_value_error() -> None:
    state = State(messages=[HumanMessage(content="hi")])
    with pytest.raises(ValueError):
        route_model_output(state)


# ---------------------------------------------------------------------------
# _normalize_plan_id_arg (from tools.py)
# ---------------------------------------------------------------------------

def test_normalize_plan_id_arg_none_returns_none() -> None:
    assert _normalize_plan_id_arg(None) is None


def test_normalize_plan_id_arg_empty_string_returns_none() -> None:
    assert _normalize_plan_id_arg("") is None


def test_normalize_plan_id_arg_whitespace_returns_none() -> None:
    assert _normalize_plan_id_arg("   ") is None


def test_normalize_plan_id_arg_valid_returns_stripped() -> None:
    assert _normalize_plan_id_arg("  plan_abc  ") == "plan_abc"
    assert _normalize_plan_id_arg("plan_xyz") == "plan_xyz"
