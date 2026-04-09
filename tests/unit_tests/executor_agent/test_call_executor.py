"""Unit tests for executor_agent.graph.call_executor node (with mocked LLM)."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

from langchain_core.messages import AIMessage, HumanMessage

from src.common.context import Context
from src.executor_agent.graph import ExecutorState, call_executor


def _make_runtime(context: Context | None = None) -> MagicMock:
    mock_runtime = MagicMock()
    mock_runtime.context = context or Context(max_replan=2, max_executor_iterations=5)
    return mock_runtime


def _make_mock_llm(response: AIMessage) -> MagicMock:
    mock = MagicMock()
    mock.bind_tools = MagicMock(return_value=mock)
    mock.ainvoke = AsyncMock(return_value=response)
    return mock


def _make_completed_content() -> str:
    plan = {
        "plan_id": "plan_exec_test",
        "version": 1,
        "goal": "write a file",
        "steps": [
            {
                "step_id": "step_1",
                "intent": "write hello world",
                "expected_output": "file written",
                "status": "completed",
                "result_summary": "wrote hello.txt",
                "failure_reason": None,
            }
        ],
    }
    result_data = {
        "status": "completed",
        "summary": "File written successfully",
        "updated_plan": plan,
    }
    return f"```json\n{json.dumps(result_data, ensure_ascii=False)}\n```"


# ---------------------------------------------------------------------------
# Normal response: LLM returns final JSON output
# ---------------------------------------------------------------------------

async def test_call_executor_normal_response_is_stored() -> None:
    state = ExecutorState(
        messages=[HumanMessage(content="請按照以下計劃執行：\n\n{plan}")]
    )
    runtime = _make_runtime()
    mock_llm = _make_mock_llm(AIMessage(content=_make_completed_content()))

    with patch("src.executor_agent.graph.load_chat_model", return_value=mock_llm):
        result = await call_executor(state, runtime)

    messages = result["messages"]
    assert len(messages) == 1
    assert isinstance(messages[0], AIMessage)
    assert "completed" in messages[0].content


# ---------------------------------------------------------------------------
# is_last_step=True with tool_calls forces termination
# ---------------------------------------------------------------------------

async def test_call_executor_last_step_with_tool_calls_forces_end() -> None:
    state = ExecutorState(
        messages=[HumanMessage(content="plan")],
        is_last_step=True,
    )
    runtime = _make_runtime()
    # LLM wants to call a tool but is_last_step prevents it
    tool_response = AIMessage(
        content="",
        tool_calls=[{"name": "write_file", "args": {}, "id": "c1", "type": "tool_call"}],
    )
    mock_llm = _make_mock_llm(tool_response)

    with patch("src.executor_agent.graph.load_chat_model", return_value=mock_llm):
        result = await call_executor(state, runtime)

    response_msg = result["messages"][0]
    assert not response_msg.tool_calls
    assert "步数限制" in response_msg.content or "最大" in response_msg.content


# ---------------------------------------------------------------------------
# LLM response is passed through without modification when no tool_calls
# ---------------------------------------------------------------------------

async def test_call_executor_not_last_step_preserves_tool_calls() -> None:
    state = ExecutorState(
        messages=[HumanMessage(content="plan")],
        is_last_step=False,
    )
    runtime = _make_runtime()
    tool_response = AIMessage(
        content="",
        tool_calls=[{"name": "write_file", "args": {"path": "x.txt", "content": "hi"}, "id": "c2", "type": "tool_call"}],
    )
    mock_llm = _make_mock_llm(tool_response)

    with patch("src.executor_agent.graph.load_chat_model", return_value=mock_llm):
        result = await call_executor(state, runtime)

    # Tool calls should be preserved
    assert result["messages"][0].tool_calls


async def test_call_executor_passes_executor_llm_kwargs() -> None:
    state = ExecutorState(messages=[HumanMessage(content="plan")])
    runtime = _make_runtime(
        Context(
            executor_temperature=0.0,
            executor_top_p=1.0,
            executor_max_tokens=1500,
            executor_seed=33,
        )
    )
    mock_llm = _make_mock_llm(AIMessage(content=_make_completed_content()))

    with patch("src.executor_agent.graph.load_chat_model", return_value=mock_llm) as mock_loader:
        await call_executor(state, runtime)

    mock_loader.assert_called_once_with(
        runtime.context.executor_model,
        temperature=0.0,
        top_p=1.0,
        max_tokens=1500,
        seed=33,
    )
