"""Integration tests for planner_agent.graph.run_planner (mock LLM, real graph)."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

from langchain_core.messages import AIMessage

from src.planner_agent.graph import run_planner


def _make_mock_llm(content: str) -> MagicMock:
    mock = MagicMock()
    mock.bind_tools = MagicMock(return_value=mock)
    mock.ainvoke = AsyncMock(return_value=AIMessage(content=content, name="planner"))
    return mock


def _plan_content(goal: str = "train a classifier", n_steps: int = 2) -> str:
    steps = [
        {
            "step_id": f"step_{i}",
            "intent": f"step {i} intent",
            "expected_output": f"step {i} done",
            "status": "pending",
            "result_summary": None,
            "failure_reason": None,
        }
        for i in range(1, n_steps + 1)
    ]
    plan = {"goal": goal, "steps": steps}
    return f"```json\n{json.dumps(plan, ensure_ascii=False)}\n```"


# ---------------------------------------------------------------------------
# First-time planning
# ---------------------------------------------------------------------------

async def test_run_planner_fresh_returns_valid_plan(mock_context) -> None:
    mock_llm = _make_mock_llm(_plan_content("train a classifier"))

    with patch("src.planner_agent.graph.load_chat_model", return_value=mock_llm):
        result = await run_planner("train a classifier", context=mock_context)

    parsed = json.loads(result)
    assert parsed["goal"] == "train a classifier"
    assert len(parsed["steps"]) >= 1
    assert isinstance(parsed["plan_id"], str)
    assert parsed["version"] == 1


async def test_run_planner_assigns_plan_id_starting_with_plan_v(mock_context) -> None:
    mock_llm = _make_mock_llm(_plan_content("any task"))

    with patch("src.planner_agent.graph.load_chat_model", return_value=mock_llm):
        result = await run_planner("any task", context=mock_context)

    assert json.loads(result)["plan_id"].startswith("plan_v")


# ---------------------------------------------------------------------------
# Replan: version incremented, plan_id preserved
# ---------------------------------------------------------------------------

async def test_run_planner_replan_increments_version(mock_context) -> None:
    previous = json.dumps({
        "plan_id": "plan_existing_abc",
        "version": 1,
        "goal": "original goal",
        "steps": [
            {
                "step_id": "step_1",
                "intent": "do x",
                "expected_output": "x done",
                "status": "failed",
                "result_summary": None,
                "failure_reason": "timeout",
            }
        ],
    })
    mock_llm = _make_mock_llm(_plan_content("updated goal"))

    with patch("src.planner_agent.graph.load_chat_model", return_value=mock_llm):
        result = await run_planner(
            "根据失败原因重新规划",
            plan_id="plan_existing_abc",
            replan_plan_json=previous,
            context=mock_context,
        )

    parsed = json.loads(result)
    assert parsed["plan_id"] == "plan_existing_abc"
    assert parsed["version"] == 2


# ---------------------------------------------------------------------------
# Planner history messages are passed through build_planner_messages
# ---------------------------------------------------------------------------

async def test_run_planner_with_history_passes_more_messages(mock_context) -> None:
    mock_llm = _make_mock_llm(_plan_content("task"))
    history = [
        {"role": "user", "content": "original task"},
        {"role": "assistant", "content": '{"goal": "task", "steps": []}'},
    ]

    received: list = []

    async def capture_ainvoke(messages):
        received.extend(messages)
        return AIMessage(content=_plan_content("task"), name="planner")

    mock_llm.ainvoke = AsyncMock(side_effect=capture_ainvoke)

    with patch("src.planner_agent.graph.load_chat_model", return_value=mock_llm):
        await run_planner("updated task", planner_history_messages=history, context=mock_context)

    # History messages should have been injected → more messages than just system+human
    assert len(received) > 2
