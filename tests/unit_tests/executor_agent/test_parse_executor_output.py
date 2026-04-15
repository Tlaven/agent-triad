"""Unit tests for executor_agent._parse_executor_output and route_executor_output."""

import json

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from src.executor_agent.graph import (
    ExecutorState,
    _executor_final_text_from_messages,
    _parse_executor_output,
    route_executor_output,
)


def _make_executor_content(status: str, summary: str, updated_plan: dict | None = None) -> str:
    """Build a well-formed executor output string with a single JSON fence."""
    data: dict = {
        "status": status,
        "summary": summary,
        "updated_plan": updated_plan or {},
    }
    return f"```json\n{json.dumps(data, ensure_ascii=False)}\n```"


# ---------------------------------------------------------------------------
# Happy-path status variants
# ---------------------------------------------------------------------------

def test_completed_with_non_empty_plan() -> None:
    plan = {
        "plan_id": "plan_abc",
        "version": 1,
        "goal": "do something",
        "steps": [
            {
                "step_id": "step_1",
                "intent": "act",
                "expected_output": "done",
                "status": "completed",
                "result_summary": "wrote file",
                "failure_reason": None,
            }
        ],
    }
    content = _make_executor_content("completed", "All done", plan)
    result = _parse_executor_output(content)

    assert result.status == "completed"
    assert result.summary == "All done"
    parsed = json.loads(result.updated_plan_json)
    assert parsed["plan_id"] == "plan_abc"
    assert parsed["steps"][0]["status"] == "completed"


def test_paused_with_snapshot() -> None:
    data = {
        "status": "paused",
        "summary": "checkpoint reached",
        "snapshot": {
            "progress_summary": "halfway",
            "reflection": "on track",
            "suggestion": "continue",
            "confidence": 0.8,
        },
        "updated_plan": {"plan_id": "plan_abc", "version": 1, "goal": "g", "steps": []},
    }
    content = f"```json\n{json.dumps(data, ensure_ascii=False)}\n```"
    result = _parse_executor_output(content)
    assert result.status == "paused"
    assert "halfway" in result.snapshot_json


def test_failed_with_failure_reason_in_plan() -> None:
    plan = {
        "plan_id": "plan_abc",
        "version": 1,
        "goal": "do something",
        "steps": [
            {
                "step_id": "step_1",
                "intent": "act",
                "expected_output": "done",
                "status": "failed",
                "result_summary": None,
                "failure_reason": "tool timeout",
            }
        ],
    }
    content = _make_executor_content("failed", "Step 1 timed out", plan)
    result = _parse_executor_output(content)
    assert result.status == "failed"
    assert "Step 1" in result.summary or "timed out" in result.summary


# ---------------------------------------------------------------------------
# Degradation paths
# ---------------------------------------------------------------------------

def test_no_json_fence_degrades_to_failed() -> None:
    content = "Executor ran but forgot to output JSON block"
    result = _parse_executor_output(content)
    assert result.status == "failed"
    assert result.updated_plan_json == ""
    assert result.summary == content


def test_no_json_fence_with_explicit_status_completed() -> None:
    content = "执行完成\nstatus: completed\nsummary: hello.txt created"
    result = _parse_executor_output(content)
    assert result.status == "completed"
    assert result.updated_plan_json == ""
    assert "hello.txt" in result.summary


def test_invalid_json_in_fence_degrades() -> None:
    content = "```json\n{not valid json at all}\n```"
    result = _parse_executor_output(content)
    assert result.status == "failed"
    assert result.updated_plan_json == ""


def test_invalid_status_value_corrected_to_failed() -> None:
    data = {"status": "running", "summary": "still going", "updated_plan": {}}
    result = _parse_executor_output(f"```json\n{json.dumps(data)}\n```")
    assert result.status == "failed"


# ---------------------------------------------------------------------------
# Multiple fences
# ---------------------------------------------------------------------------

def test_multiple_json_fences_no_valid_payload_degrades() -> None:
    content = "```json\n{\"a\": 1}\n```\n```json\n{\"b\": 2}\n```"
    assert _parse_executor_output(content).status == "failed"


def test_multiple_fences_with_valid_executor_payload_uses_valid_one() -> None:
    valid = {
        "status": "completed",
        "summary": "ok",
        "updated_plan": {"plan_id": "p1", "version": 1, "goal": "g", "steps": []},
    }
    content = (
        "```json\n{\"a\": 1}\n```\n"
        f"```json\n{json.dumps(valid, ensure_ascii=False)}\n```"
    )
    result = _parse_executor_output(content)
    assert result.status == "completed"
    assert json.loads(result.updated_plan_json)["plan_id"] == "p1"


def test_multiple_fences_prefers_completed_over_trailing_failed_stub() -> None:
    full = {
        "status": "completed",
        "summary": "ok",
        "updated_plan": {"plan_id": "p1", "version": 1, "goal": "g", "steps": []},
    }
    content = (
        f"```json\n{json.dumps(full, ensure_ascii=False)}\n```\n"
        f"```json\n{json.dumps({'status': 'failed', 'summary': 'noise'})}\n```"
    )
    result = _parse_executor_output(content)
    assert result.status == "completed"


# ---------------------------------------------------------------------------
# Placeholder status strings copied from prompt template
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw_status", ["completed | failed", "completed / failed"])
def test_status_placeholder_from_prompt_resolves_to_completed(raw_status) -> None:
    data = {
        "status": raw_status,
        "summary": "文件已写入",
        "updated_plan": {"plan_id": "p_pipe", "version": 1, "goal": "g", "steps": []},
    }
    result = _parse_executor_output(f"```json\n{json.dumps(data, ensure_ascii=False)}\n```")
    assert result.status == "completed"


# ---------------------------------------------------------------------------
# Alias keys for summary and plan
# ---------------------------------------------------------------------------

def test_status_with_message_key_instead_of_summary() -> None:
    data = {"status": "completed", "message": "文件已写入指定路径", "extra": 1}
    result = _parse_executor_output(f"```json\n{json.dumps(data, ensure_ascii=False)}\n```")
    assert result.status == "completed"
    assert "路径" in result.summary or "写入" in result.summary


def test_status_with_plan_key_as_updated_plan() -> None:
    plan = {"plan_id": "p1", "version": 1, "goal": "g", "steps": []}
    data = {"status": "completed", "plan": plan}
    result = _parse_executor_output(f"```json\n{json.dumps(data, ensure_ascii=False)}\n```")
    assert result.status == "completed"
    assert json.loads(result.updated_plan_json)["plan_id"] == "p1"


# ---------------------------------------------------------------------------
# Missing / empty plan keys
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("data", [
    {"status": "completed", "summary": "done", "updated_plan": {}},
    {"status": "completed", "summary": "done"},
])
def test_no_valid_plan_gives_empty_updated_plan_json(data) -> None:
    result = _parse_executor_output(f"```json\n{json.dumps(data)}\n```")
    assert result.updated_plan_json == ""


# ---------------------------------------------------------------------------
# Misc
# ---------------------------------------------------------------------------

def test_json_fence_with_trailing_nonjson_text_uses_raw_decode() -> None:
    inner = '{"status": "completed", "summary": "ok", "updated_plan": {}}\n（执行完毕）'
    result = _parse_executor_output(f"```json\n{inner}\n```")
    assert result.status == "completed"


def test_chinese_status_completed() -> None:
    data = {
        "status": "已完成",
        "summary": "文件已创建",
        "updated_plan": {"plan_id": "p_cn", "version": 1, "goal": "g", "steps": []},
    }
    result = _parse_executor_output(f"```json\n{json.dumps(data, ensure_ascii=False)}\n```")
    assert result.status == "completed"
    assert result.summary == "文件已创建"


def test_embedded_json_after_prose() -> None:
    payload = {
        "status": "completed",
        "summary": "文件已写入",
        "updated_plan": {"plan_id": "p3", "version": 1, "goal": "g", "steps": []},
    }
    content = f"执行完成。\n{json.dumps(payload, ensure_ascii=False)}\n（以上为结构化结果）"
    result = _parse_executor_output(content)
    assert result.status == "completed"
    assert result.summary == "文件已写入"


def test_summary_null_uses_full_content_as_fallback() -> None:
    data = {"status": "completed", "summary": None, "updated_plan": {}}
    content = f"说明文字\n```json\n{json.dumps(data, ensure_ascii=False)}\n```\n尾部"
    result = _parse_executor_output(content)
    assert result.status == "completed"
    assert "说明文字" in result.summary


def test_executor_final_text_prefers_ai_without_tool_calls() -> None:
    messages = [
        AIMessage(
            content="先调用工具写入。",
            tool_calls=[{"name": "write_file", "args": {}, "id": "call_1", "type": "tool_call"}],
        ),
        AIMessage(content='```json\n{"status": "completed", "summary": "ok", "updated_plan": {}}\n```'),
    ]
    text = _executor_final_text_from_messages(messages)
    assert text is not None
    result = _parse_executor_output(text)
    assert result.status == "completed"


# ---------------------------------------------------------------------------
# route_executor_output
# ---------------------------------------------------------------------------

def test_route_executor_output_no_tool_calls_returns_end() -> None:
    state = ExecutorState(messages=[AIMessage(content="Final answer")])
    assert route_executor_output(state) == "__end__"


def test_route_executor_output_with_tool_calls_returns_tools() -> None:
    msg = AIMessage(
        content="",
        tool_calls=[{"name": "write_file", "args": {}, "id": "1", "type": "tool_call"}],
    )
    state = ExecutorState(messages=[msg])
    assert route_executor_output(state) == "tools"


def test_route_executor_output_non_ai_message_raises() -> None:
    state = ExecutorState(messages=[HumanMessage(content="human")])
    with pytest.raises(ValueError):
        route_executor_output(state)
