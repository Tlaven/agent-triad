"""Unit tests for planner_agent.graph.call_planner node (with mocked LLM)."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from src.common.context import Context
from src.planner_agent.graph import call_planner
from src.planner_agent.state import PlannerState


def _make_runtime(context: Context | None = None) -> MagicMock:
    mock_runtime = MagicMock()
    mock_runtime.context = context or Context()
    return mock_runtime


def _make_plan_content(goal: str = "train a model") -> str:
    plan = {
        "goal": goal,
        "steps": [
            {
                "step_id": "step_1",
                "intent": "do the thing",
                "expected_output": "thing done",
                "status": "pending",
                "result_summary": None,
                "failure_reason": None,
            }
        ],
    }
    return f"```json\n{json.dumps(plan, ensure_ascii=False)}\n```"


# ---------------------------------------------------------------------------
# Normal response: LLM returns plan JSON in code block
# ---------------------------------------------------------------------------

async def test_call_planner_returns_ai_message_with_plan() -> None:
    state = PlannerState(messages=[
        SystemMessage(content="you are planner"),
        HumanMessage(content="train a classifier"),
    ])
    runtime = _make_runtime()

    mock_llm = MagicMock()
    mock_llm.bind_tools = MagicMock(return_value=mock_llm)
    mock_llm.ainvoke = AsyncMock(return_value=AIMessage(
        content=_make_plan_content("train a classifier"), name="planner"
    ))

    with patch("src.planner_agent.graph.load_chat_model", return_value=mock_llm):
        result = await call_planner(state, runtime)

    messages = result["messages"]
    assert len(messages) == 1
    assert isinstance(messages[0], AIMessage)
    assert "train a classifier" in messages[0].content


# ---------------------------------------------------------------------------
# Empty content raises RuntimeError
# ---------------------------------------------------------------------------

async def test_call_planner_empty_content_raises_runtime_error() -> None:
    state = PlannerState(messages=[
        SystemMessage(content="system"),
        HumanMessage(content="task"),
    ])
    runtime = _make_runtime()

    mock_llm = MagicMock()
    mock_llm.bind_tools = MagicMock(return_value=mock_llm)
    mock_llm.ainvoke = AsyncMock(return_value=AIMessage(content=""))

    with patch("src.planner_agent.graph.load_chat_model", return_value=mock_llm):
        with pytest.raises(RuntimeError, match="未返回文本内容且无工具调用"):
            await call_planner(state, runtime)


# ---------------------------------------------------------------------------
# ReAct：完整消息历史（含带 tool_calls 的 AIMessage）会传入模型
# ---------------------------------------------------------------------------

async def test_call_planner_passes_full_history_including_tool_calls() -> None:
    """V2-b：Planner ReAct 需要保留 tool_calls 轮次，供下一轮模型理解 Observation。"""
    state = PlannerState(messages=[
        SystemMessage(content="system"),
        HumanMessage(content="human task"),
        AIMessage(content="assistant text"),
        AIMessage(
            content="",
            tool_calls=[{"name": "some_tool", "args": {}, "id": "x", "type": "tool_call"}],
        ),
    ])
    runtime = _make_runtime()

    received_messages: list = []

    async def capture_ainvoke(messages):
        received_messages.extend(messages)
        return AIMessage(content=_make_plan_content())

    mock_llm = MagicMock()
    mock_llm.bind_tools = MagicMock(return_value=mock_llm)
    mock_llm.ainvoke = AsyncMock(side_effect=capture_ainvoke)

    with patch("src.planner_agent.graph.load_chat_model", return_value=mock_llm):
        await call_planner(state, runtime)

    assert len(received_messages) == 4
    assert isinstance(received_messages[-1], AIMessage)
    assert received_messages[-1].tool_calls


async def test_call_planner_passes_planner_llm_kwargs() -> None:
    state = PlannerState(messages=[
        SystemMessage(content="system"),
        HumanMessage(content="task"),
    ])
    runtime = _make_runtime(
        Context(
            planner_temperature=0.0,
            planner_top_p=1.0,
            planner_max_tokens=1200,
            planner_seed=22,
        )
    )
    mock_llm = MagicMock()
    mock_llm.bind_tools = MagicMock(return_value=mock_llm)
    mock_llm.ainvoke = AsyncMock(return_value=AIMessage(content=_make_plan_content()))

    with patch("src.planner_agent.graph.load_chat_model", return_value=mock_llm) as mock_loader:
        await call_planner(state, runtime)

    mock_loader.assert_called_once_with(
        runtime.context.planner_model,
        temperature=0.0,
        top_p=1.0,
        max_tokens=1200,
        seed=22,
    )
