"""Unit tests for Executor reflection/snapshot mechanism."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from src.executor_agent.graph import (
    ExecutorState,
    ExecutorResult,
    _normalize_executor_status_token,
    reflection_node,
    route_after_tools,
)
from src.common.context import Context


# ---------------------------------------------------------------------------
# route_after_tools — parametrized routing table
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("tool_rounds,interval,expected", [
    (5, 0, "call_executor"),    # reflection disabled
    (0, 2, "call_executor"),    # first round, no tools yet
    (3, 2, "call_executor"),    # 3 % 2 != 0
    (4, 2, "reflection"),       # 4 % 2 == 0
    (3, 3, "reflection"),       # 3 % 3 == 0
    (5, 5, "reflection"),       # halfway (divisible)
    (9, 3, "reflection"),       # 9 % 3 == 0
    (5, 3, "call_executor"),    # 5 % 3 != 0
    (1, 1, "reflection"),       # every step
    (20, 10, "reflection"),     # 20 % 10 == 0
])
def test_route_after_tools(tool_rounds, interval, expected) -> None:
    state = ExecutorState(messages=[], tool_rounds=tool_rounds, reflection_interval=interval)
    assert route_after_tools(state) == expected


# ---------------------------------------------------------------------------
# _normalize_executor_status_token — parametrized
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("paused", "paused"),
    ("pause", "paused"),
    ("checkpoint", "paused"),
    ("halt", "paused"),
    ("completed", "completed"),
    ("success", "completed"),
    ("failed", "failed"),
    ("error", "failed"),
])
def test_normalize_executor_status_token(raw, expected) -> None:
    assert _normalize_executor_status_token(raw) == expected


# ---------------------------------------------------------------------------
# ExecutorResult snapshot contract
# ---------------------------------------------------------------------------

def test_executor_result_snapshot_roundtrip() -> None:
    """paused result carries a parseable snapshot_json."""
    snapshot = {
        "trigger_type": "interval",
        "current_step": "step_2",
        "confidence_score": 0.5,
        "suggestion": "replan",
        "progress_summary": "Completed 2/5 steps",
    }
    result = ExecutorResult(
        status="paused",
        updated_plan_json='{"plan_id": "test"}',
        summary="Reflection suggests replanning",
        snapshot_json=json.dumps(snapshot, ensure_ascii=False),
    )
    parsed = json.loads(result.snapshot_json)
    assert parsed["trigger_type"] == "interval"
    assert parsed["suggestion"] == "replan"


def test_executor_result_empty_snapshot_allowed() -> None:
    result = ExecutorResult(
        status="paused",
        updated_plan_json='{"plan_id": "test"}',
        summary="Paused",
        snapshot_json="",
    )
    assert result.snapshot_json == ""
    assert result.status == "paused"


# ---------------------------------------------------------------------------
# Context reflection configuration
# ---------------------------------------------------------------------------

def test_reflection_disabled_by_default() -> None:
    assert Context().reflection_interval == 0


@pytest.mark.parametrize("interval,threshold", [
    (3, 0.7),
    (5, 0.3),
    (0, 0.9),
])
def test_reflection_context_config(interval, threshold) -> None:
    ctx = Context(reflection_interval=interval, confidence_threshold=threshold)
    assert ctx.reflection_interval == interval
    assert ctx.confidence_threshold == threshold


# ---------------------------------------------------------------------------
# reflection_node async behaviour
# ---------------------------------------------------------------------------

def _make_runtime(streaming: bool = False):
    runtime = MagicMock()
    runtime.context.executor_model = "test:model"
    runtime.context.get_agent_llm_kwargs = MagicMock(return_value={
        "temperature": 0.0,
        "extra_body": {"enable_thinking": True},
    })
    runtime.context.enable_llm_streaming = streaming
    runtime.context.executor_call_model_timeout = 0  # disabled in tests
    return runtime


def _patch_reflection_llm(response):
    """Context manager that patches both load_chat_model and invoke_chat_model."""
    from contextlib import contextmanager
    from unittest.mock import patch as _patch, MagicMock

    @contextmanager
    def _cm():
        mock_model = MagicMock()
        with _patch("src.executor_agent.graph.load_chat_model", return_value=mock_model):
            with _patch("src.executor_agent.graph.invoke_chat_model", return_value=response) as p:
                yield p

    return _cm()


@pytest.mark.asyncio
async def test_reflection_node_returns_ai_message() -> None:
    """reflection_node appends exactly one AIMessage to state."""
    state = ExecutorState(
        messages=[
            HumanMessage(content="Execute plan"),
            AIMessage(content=""),
            ToolMessage(content="File written", tool_call_id="123"),
        ],
        tool_rounds=2,
        reflection_interval=2,
    )
    reflection_response = AIMessage(content="""```json
{
  "status": "paused",
  "summary": "Progress check",
  "snapshot": {"suggestion": "continue", "confidence": 0.8}
}
```""")

    with _patch_reflection_llm(reflection_response):
        result = await reflection_node(state, _make_runtime())

    assert "messages" in result
    assert len(result["messages"]) == 1
    assert isinstance(result["messages"][0], AIMessage)


@pytest.mark.asyncio
async def test_reflection_node_handles_invalid_json() -> None:
    """reflection_node still returns a message even when LLM outputs non-JSON."""
    state = ExecutorState(
        messages=[HumanMessage(content="Execute plan")],
        tool_rounds=2,
        reflection_interval=2,
    )
    with _patch_reflection_llm(AIMessage(content="not json")):
        result = await reflection_node(state, _make_runtime())
    assert len(result["messages"]) == 1


@pytest.mark.asyncio
async def test_reflection_node_llm_failure_propagates() -> None:
    """LLM exceptions bubble out of reflection_node."""
    state = ExecutorState(
        messages=[HumanMessage(content="Execute plan")],
        tool_rounds=2,
        reflection_interval=2,
    )
    mock_model = MagicMock()
    with patch("src.executor_agent.graph.load_chat_model", return_value=mock_model):
        with patch("src.executor_agent.graph.invoke_chat_model", side_effect=Exception("API error")):
            with pytest.raises(Exception, match="API error"):
                await reflection_node(state, _make_runtime())
