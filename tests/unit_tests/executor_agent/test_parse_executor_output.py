"""Unit tests for executor_agent._parse_executor_output and route_executor_output."""

import json

from langchain_core.messages import AIMessage, HumanMessage

from src.executor_agent.graph import (
    ExecutorState,
    _executor_final_text_from_messages,
    _parse_executor_output,
    route_executor_output,
)


def _make_executor_content(status: str, summary: str, updated_plan: dict | None = None) -> str:
    """Helper: build a well-formed executor output string with a single JSON fence."""
    data: dict = {
        "status": status,
        "summary": summary,
        "updated_plan": updated_plan or {},
    }
    return f"```json\n{json.dumps(data, ensure_ascii=False)}\n```"


# ---------------------------------------------------------------------------
# _parse_executor_output
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
        "updated_plan": {
            "plan_id": "plan_abc",
            "version": 1,
            "goal": "g",
            "steps": [],
        },
    }
    content = f"```json\n{json.dumps(data, ensure_ascii=False)}\n```"
    result = _parse_executor_output(content)
    assert result.status == "paused"
    assert result.summary == "checkpoint reached"
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


def test_no_json_fence_degrades_to_failed() -> None:
    content = "Executor ran but forgot to output JSON block"
    result = _parse_executor_output(content)

    assert result.status == "failed"
    assert result.updated_plan_json == ""
    assert result.summary == content


def test_multiple_json_fences_degrades_to_failed() -> None:
    content = (
        "```json\n{\"a\": 1}\n```\n"
        "```json\n{\"b\": 2}\n```"
    )
    result = _parse_executor_output(content)

    assert result.status == "failed"


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
    assert result.summary == "ok"
    assert json.loads(result.updated_plan_json)["plan_id"] == "p1"


def test_json_fence_with_trailing_nonjson_text_uses_raw_decode() -> None:
    """围栏内 JSON 后若紧跟说明文字，json.loads 会失败，应用 raw_decode 取首个对象。"""
    inner = '{"status": "completed", "summary": "ok", "updated_plan": {}}\n（执行完毕）'
    content = f"```json\n{inner}\n```"
    result = _parse_executor_output(content)

    assert result.status == "completed"
    assert result.summary == "ok"


def test_invalid_json_in_fence_degrades() -> None:
    content = "```json\n{not valid json at all}\n```"
    result = _parse_executor_output(content)

    assert result.status == "failed"
    assert result.updated_plan_json == ""


def test_invalid_status_value_corrected_to_failed() -> None:
    data = {"status": "running", "summary": "still going", "updated_plan": {}}
    content = f"```json\n{json.dumps(data)}\n```"
    result = _parse_executor_output(content)

    assert result.status == "failed"


def test_empty_updated_plan_dict_gives_empty_string() -> None:
    data = {"status": "completed", "summary": "done", "updated_plan": {}}
    content = f"```json\n{json.dumps(data)}\n```"
    result = _parse_executor_output(content)

    assert result.updated_plan_json == ""


def test_missing_updated_plan_key_gives_empty_string() -> None:
    data = {"status": "completed", "summary": "done"}
    content = f"```json\n{json.dumps(data)}\n```"
    result = _parse_executor_output(content)

    assert result.updated_plan_json == ""


def test_missing_summary_falls_back_to_raw_content() -> None:
    data = {"status": "completed", "updated_plan": {}}
    content = f"```json\n{json.dumps(data)}\n```"
    result = _parse_executor_output(content)

    assert result.summary  # not empty


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


def test_raw_json_without_fence_can_be_parsed() -> None:
    content = json.dumps(
        {
            "status": "completed",
            "summary": "direct json",
            "updated_plan": {"plan_id": "p2", "version": 1, "goal": "g", "steps": []},
        },
        ensure_ascii=False,
    )
    result = _parse_executor_output(content)

    assert result.status == "completed"
    assert result.summary == "direct json"


def test_status_placeholder_completed_pipe_failed_from_prompt() -> None:
    """Models often copy 'completed | failed' from the template; must still parse."""
    data = {
        "status": "completed | failed",
        "summary": "文件已写入",
        "updated_plan": {"plan_id": "p_pipe", "version": 1, "goal": "g", "steps": []},
    }
    content = f"```json\n{json.dumps(data, ensure_ascii=False)}\n```"
    result = _parse_executor_output(content)

    assert result.status == "completed"
    assert result.summary == "文件已写入"


def test_status_with_message_key_instead_of_summary() -> None:
    data = {
        "status": "completed",
        "message": "文件已写入指定路径",
        "extra": 1,
    }
    content = f"```json\n{json.dumps(data, ensure_ascii=False)}\n```"
    result = _parse_executor_output(content)

    assert result.status == "completed"
    assert "路径" in result.summary or "写入" in result.summary


def test_status_with_plan_key_as_updated_plan() -> None:
    plan = {
        "plan_id": "p1",
        "version": 1,
        "goal": "g",
        "steps": [],
    }
    data = {"status": "completed", "plan": plan}
    content = f"```json\n{json.dumps(data, ensure_ascii=False)}\n```"
    result = _parse_executor_output(content)

    assert result.status == "completed"
    assert json.loads(result.updated_plan_json)["plan_id"] == "p1"


def test_executor_final_text_prefers_ai_without_tool_calls() -> None:
    """中间轮带 tool_calls 有短文，末轮为 JSON 时应解析末轮。"""
    messages = [
        AIMessage(
            content="先调用工具写入。",
            tool_calls=[{"name": "write_file", "args": {}, "id": "call_1", "type": "tool_call"}],
        ),
        AIMessage(
            content='```json\n{"status": "completed", "summary": "ok", "updated_plan": {}}\n```',
        ),
    ]
    text = _executor_final_text_from_messages(messages)
    assert text is not None
    result = _parse_executor_output(text)
    assert result.status == "completed"
    assert result.summary == "ok"


def test_status_placeholder_completed_slash_failed_from_prompt() -> None:
    data = {
        "status": "completed / failed",
        "summary": "ok",
        "updated_plan": {"plan_id": "p_slash", "version": 1, "goal": "g", "steps": []},
    }
    content = f"```json\n{json.dumps(data, ensure_ascii=False)}\n```"
    result = _parse_executor_output(content)

    assert result.status == "completed"


def test_chinese_status_completed() -> None:
    data = {
        "status": "已完成",
        "summary": "文件已创建",
        "updated_plan": {"plan_id": "p_cn", "version": 1, "goal": "g", "steps": []},
    }
    content = f"```json\n{json.dumps(data, ensure_ascii=False)}\n```"
    result = _parse_executor_output(content)

    assert result.status == "completed"
    assert result.summary == "文件已创建"


def test_multiple_fences_prefers_completed_over_trailing_failed_stub() -> None:
    full = {
        "status": "completed",
        "summary": "ok",
        "updated_plan": {
            "plan_id": "p1",
            "version": 1,
            "goal": "g",
            "steps": [{"step_id": "1", "status": "completed"}],
        },
    }
    stub_failed = {"status": "failed", "summary": "noise"}
    content = (
        f"```json\n{json.dumps(full, ensure_ascii=False)}\n```\n"
        f"```json\n{json.dumps(stub_failed)}\n```"
    )
    result = _parse_executor_output(content)

    assert result.status == "completed"
    assert result.summary == "ok"


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
    import pytest

    state = ExecutorState(messages=[HumanMessage(content="human")])
    with pytest.raises(ValueError):
        route_executor_output(state)
