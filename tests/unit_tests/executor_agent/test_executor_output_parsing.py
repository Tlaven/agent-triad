"""Unit tests for executor_agent.graph helper functions (pure functions)."""

import json

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from src.executor_agent.graph import (
    _executor_final_text_from_messages,
    _extract_executor_payload,
    _flatten_ai_message_content,
    _iter_json_objects,
    _normalize_executor_status,
    _parse_executor_output,
    _validate_executor_payload,
)


# ---------------------------------------------------------------------------
# _normalize_executor_status
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("raw,expected", [
    ("completed", "completed"),
    ("Completed", "completed"),
    ("COMPLETED", "completed"),
    ("success", "completed"),
    ("done", "completed"),
    ("ok", "completed"),
    (True, "completed"),
    ("failed", "failed"),
    ("error", "failed"),
    (False, "failed"),
    ("paused", "paused"),
    ("checkpoint", "paused"),
    (None, None),
    ("", None),
    ("unknown_status", None),
    ("成功", "completed"),
    ("完成", "completed"),
    ("失败", "failed"),
    ("暂停", "paused"),
])
def test_normalize_executor_status(raw, expected) -> None:
    assert _normalize_executor_status(raw) == expected


def test_normalize_executor_status_pipe_separated() -> None:
    assert _normalize_executor_status("completed | failed") == "completed"
    assert _normalize_executor_status("failed | completed") == "failed"


def test_normalize_executor_status_slash_separated() -> None:
    assert _normalize_executor_status("completed / failed") == "completed"


# ---------------------------------------------------------------------------
# _validate_executor_payload
# ---------------------------------------------------------------------------


def test_validate_executor_payload_valid() -> None:
    data = {"status": "completed", "summary": "done", "updated_plan": {}}
    result = _validate_executor_payload(data)
    assert result is not None
    assert result["status"] == "completed"


def test_validate_executor_payload_non_dict_returns_none() -> None:
    assert _validate_executor_payload("string") is None
    assert _validate_executor_payload([1, 2]) is None


def test_validate_executor_payload_no_status_returns_none() -> None:
    assert _validate_executor_payload({"summary": "text"}) is None


def test_validate_executor_payload_status_only() -> None:
    result = _validate_executor_payload({"status": "completed"})
    assert result == {"status": "completed"}


def test_validate_executor_payload_plan_alias() -> None:
    data = {"status": "completed", "plan": {"steps": []}}
    result = _validate_executor_payload(data)
    assert result is not None
    assert "updated_plan" in result


def test_validate_executor_payload_alt_summary_fields() -> None:
    data = {"status": "completed", "message": "task done"}
    result = _validate_executor_payload(data)
    assert result is not None
    assert result["summary"] == "task done"


# ---------------------------------------------------------------------------
# _flatten_ai_message_content
# ---------------------------------------------------------------------------


def test_flatten_string_content() -> None:
    assert _flatten_ai_message_content("hello") == "hello"


def test_flatten_list_of_strings() -> None:
    result = _flatten_ai_message_content(["line1", "line2"])
    assert "line1" in result
    assert "line2" in result


def test_flatten_list_of_dicts() -> None:
    result = _flatten_ai_message_content([{"type": "text", "text": "hello"}])
    assert "hello" in result


def test_flatten_non_text_dict() -> None:
    result = _flatten_ai_message_content([{"type": "image", "url": "http://x"}])
    assert "image" in result or "http://x" in result


def test_flatten_non_string_non_list() -> None:
    assert _flatten_ai_message_content(42) == "42"


def test_flatten_list_with_non_dict_non_string_block() -> None:
    """Blocks that aren't str or dict get str()'d."""
    result = _flatten_ai_message_content([123, {"type": "text", "text": "hello"}])
    assert "123" in result
    assert "hello" in result


# ---------------------------------------------------------------------------
# _executor_final_text_from_messages
# ---------------------------------------------------------------------------


def test_final_text_from_ai_no_tool_calls() -> None:
    msgs = [AIMessage(content="final result")]
    assert _executor_final_text_from_messages(msgs) == "final result"


def test_final_text_skips_tool_calls() -> None:
    msgs = [
        AIMessage(content="", tool_calls=[{"id": "1", "name": "t", "args": {}, "type": "tool_call"}]),
        AIMessage(content="actual final"),
    ]
    assert _executor_final_text_from_messages(msgs) == "actual final"


def test_final_text_skips_non_ai() -> None:
    msgs = [HumanMessage(content="skip"), AIMessage(content="use this")]
    assert _executor_final_text_from_messages(msgs) == "use this"


def test_final_text_fallback_to_any_ai_content() -> None:
    """When all AIMessages have tool_calls, falls back to last AI with content."""
    msgs = [
        AIMessage(content="I'll use a tool", tool_calls=[{"id": "1", "name": "t", "args": {}, "type": "tool_call"}]),
    ]
    assert _executor_final_text_from_messages(msgs) == "I'll use a tool"


def test_final_text_skips_tool_calls_even_with_content() -> None:
    """AIMessage with both content and tool_calls is skipped in first pass."""
    msgs = [
        AIMessage(content="thinking about using tool", tool_calls=[{"id": "1", "name": "t", "args": {}, "type": "tool_call"}]),
        ToolMessage(content="tool result", tool_call_id="1"),
        AIMessage(content="final answer here"),
    ]
    assert _executor_final_text_from_messages(msgs) == "final answer here"


def test_final_text_returns_none_for_empty() -> None:
    assert _executor_final_text_from_messages([]) is None


# ---------------------------------------------------------------------------
# _iter_json_objects
# ---------------------------------------------------------------------------


def test_iter_json_objects_finds_objects() -> None:
    text = 'prefix {"a": 1} middle {"b": 2} suffix'
    objects = list(_iter_json_objects(text))
    assert len(objects) == 2
    assert objects[0] == {"a": 1}
    assert objects[1] == {"b": 2}


def test_iter_json_objects_no_objects() -> None:
    text = "no json here"
    assert list(_iter_json_objects(text)) == []


# ---------------------------------------------------------------------------
# _extract_executor_payload
# ---------------------------------------------------------------------------


def test_extract_payload_from_fenced_json() -> None:
    content = 'Result:\n```json\n{"status": "completed", "summary": "done", "updated_plan": {}}\n```'
    result = _extract_executor_payload(content)
    assert result is not None
    assert result["status"] == "completed"


def test_extract_payload_from_raw_json() -> None:
    content = '{"status": "completed", "summary": "done"}'
    result = _extract_executor_payload(content)
    assert result is not None
    assert result["status"] == "completed"


def test_extract_payload_from_embedded_json() -> None:
    content = 'The result is {"status": "completed", "summary": "done"} end.'
    result = _extract_executor_payload(content)
    assert result is not None


def test_extract_payload_no_valid_json() -> None:
    assert _extract_executor_payload("no json at all") is None


def test_extract_payload_prefers_completed_over_failed() -> None:
    """When multiple payloads found, prefer completed."""
    content = (
        '```json\n{"status": "failed", "summary": "error"}\n```\n'
        '```json\n{"status": "completed", "summary": "done", "updated_plan": {}}\n```'
    )
    result = _extract_executor_payload(content)
    assert result is not None
    assert result["status"] == "completed"


def test_extract_payload_status_line_fallback() -> None:
    content = "status: completed"
    result = _extract_executor_payload(content)
    assert result is not None
    assert result["status"] == "completed"


# ---------------------------------------------------------------------------
# _parse_executor_output
# ---------------------------------------------------------------------------


def test_parse_executor_output_success() -> None:
    content = '```json\n{"status": "completed", "summary": "all done", "updated_plan": {"steps": []}}\n```'
    result = _parse_executor_output(content)
    assert result.status == "completed"
    assert result.summary == "all done"


def test_parse_executor_output_fallback_on_invalid() -> None:
    result = _parse_executor_output("just plain text, no json")
    assert result.status == "failed"
    assert "plain text" in result.summary


def test_parse_executor_output_with_snapshot() -> None:
    content = json.dumps({
        "status": "paused",
        "summary": "checkpoint",
        "snapshot": {"step": 1, "confidence": 0.8},
    })
    result = _parse_executor_output(content)
    assert result.status == "paused"
    assert result.snapshot_json  # non-empty
    assert "confidence" in result.snapshot_json
