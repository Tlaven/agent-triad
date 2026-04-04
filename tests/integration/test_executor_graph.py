"""Integration tests for executor_agent.graph.run_executor (mock LLM, real graph)."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage

from src.executor_agent.graph import run_executor


def _make_mock_llm(response: AIMessage) -> MagicMock:
    mock = MagicMock()
    mock.bind_tools = MagicMock(return_value=mock)
    mock.ainvoke = AsyncMock(return_value=response)
    return mock


def _completed_output(plan_id: str = "plan_integ_exec") -> str:
    result_data = {
        "status": "completed",
        "summary": "File written successfully",
        "updated_plan": {
            "plan_id": plan_id,
            "version": 1,
            "goal": "write hello world",
            "steps": [
                {
                    "step_id": "step_1",
                    "intent": "write a file",
                    "expected_output": "file written",
                    "status": "completed",
                    "result_summary": "hello.txt written",
                    "failure_reason": None,
                }
            ],
        },
    }
    return f"```json\n{json.dumps(result_data, ensure_ascii=False)}\n```"


def _failed_output(plan_id: str = "plan_integ_exec") -> str:
    result_data = {
        "status": "failed",
        "summary": "Command timed out",
        "updated_plan": {
            "plan_id": plan_id,
            "version": 1,
            "goal": "write hello world",
            "steps": [
                {
                    "step_id": "step_1",
                    "intent": "write a file",
                    "expected_output": "file written",
                    "status": "failed",
                    "result_summary": None,
                    "failure_reason": "timeout after 60s",
                }
            ],
        },
    }
    return f"```json\n{json.dumps(result_data, ensure_ascii=False)}\n```"


def _make_plan_json(plan_id: str = "plan_integ_exec") -> str:
    return json.dumps({
        "plan_id": plan_id,
        "version": 1,
        "goal": "write hello world",
        "steps": [
            {
                "step_id": "step_1",
                "intent": "write a file with 'hello world'",
                "expected_output": "file hello.txt created",
                "status": "pending",
                "result_summary": None,
                "failure_reason": None,
            }
        ],
    }, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Basic sanity: empty plan raises ValueError
# ---------------------------------------------------------------------------

async def test_run_executor_empty_plan_raises_value_error(mock_context) -> None:
    with pytest.raises(ValueError, match="不能为空"):
        await run_executor("", context=mock_context)


async def test_run_executor_whitespace_plan_raises_value_error(mock_context) -> None:
    with pytest.raises(ValueError, match="不能为空"):
        await run_executor("   ", context=mock_context)


# ---------------------------------------------------------------------------
# Full graph run: completed scenario
# ---------------------------------------------------------------------------

async def test_run_executor_completed_returns_completed_result(mock_context) -> None:
    plan_json = _make_plan_json()
    mock_llm = _make_mock_llm(AIMessage(content=_completed_output()))

    with patch("src.executor_agent.graph.load_chat_model", return_value=mock_llm):
        result = await run_executor(plan_json, context=mock_context)

    assert result.status == "completed"
    assert result.summary == "File written successfully"
    assert result.updated_plan_json


# ---------------------------------------------------------------------------
# Full graph run: failed scenario
# ---------------------------------------------------------------------------

async def test_run_executor_failed_returns_failed_result(mock_context) -> None:
    plan_json = _make_plan_json()
    mock_llm = _make_mock_llm(AIMessage(content=_failed_output()))

    with patch("src.executor_agent.graph.load_chat_model", return_value=mock_llm):
        result = await run_executor(plan_json, context=mock_context)

    assert result.status == "failed"
    assert "timeout" in result.summary.lower() or "timed" in result.summary.lower()


# ---------------------------------------------------------------------------
# LLM produces no valid JSON block → degraded failed result
# ---------------------------------------------------------------------------

async def test_run_executor_no_json_block_degrades_gracefully(mock_context) -> None:
    plan_json = _make_plan_json()
    mock_llm = _make_mock_llm(AIMessage(content="I could not find the right tool."))

    with patch("src.executor_agent.graph.load_chat_model", return_value=mock_llm):
        result = await run_executor(plan_json, context=mock_context)

    assert result.status == "failed"
