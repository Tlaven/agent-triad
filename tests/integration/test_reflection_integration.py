"""Integration tests for Executor reflection/snapshot mechanism.

Focus: cross-module contracts — reflection_node output format, snapshot
roundtrip through ExecutorResult, and Supervisor-facing paused status.
Pure routing logic (route_after_tools) is covered in unit tests.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from src.executor_agent.graph import ExecutorState, ExecutorResult, reflection_node
from src.common.context import Context


# ---------------------------------------------------------------------------
# reflection_node output contracts (replan / abort suggestions)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("suggestion,confidence", [
    ("replan", 0.4),
    ("abort", 0.1),
])
@pytest.mark.asyncio
async def test_reflection_node_non_continue_suggestion(suggestion, confidence):
    """reflection_node output contains the LLM's suggestion keyword."""
    response = AIMessage(content=f"""```json
{{
  "status": "paused",
  "summary": "Reflection: {suggestion} suggested",
  "snapshot": {{
    "progress_summary": "Encountered issues",
    "reflection": "Current approach not working",
    "suggestion": "{suggestion}",
    "confidence": {confidence}
  }},
  "updated_plan": {{"plan_id": "test_plan", "version": 1, "goal": "g", "steps": []}}
}}
```""")

    state = ExecutorState(
        messages=[HumanMessage(content="Execute task")],
        tool_rounds=2,
        reflection_interval=2,
    )
    runtime = MagicMock()
    runtime.context.executor_model = "test:model"
    runtime.context.get_agent_llm_kwargs = MagicMock(return_value={})
    runtime.context.enable_llm_streaming = False
    runtime.context.executor_call_model_timeout = 0

    mock_model = MagicMock()
    with patch("src.executor_agent.graph.load_chat_model", return_value=mock_model):
        with patch("src.executor_agent.graph.invoke_chat_model", return_value=response):
            result = await reflection_node(state, runtime)

    content = result["messages"][0].content
    assert "paused" in content
    assert suggestion in content


# ---------------------------------------------------------------------------
# reflection_node error resilience
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bad_content", [
    "```json\n{invalid json}\n```",
    '```json\n{"status": "paused", "summary": "incomplete"}\n```',
])
@pytest.mark.asyncio
async def test_reflection_node_bad_llm_output_returns_message(bad_content):
    """reflection_node returns an AIMessage even for malformed LLM output."""
    state = ExecutorState(
        messages=[HumanMessage(content="Execute plan")],
        tool_rounds=2,
        reflection_interval=2,
    )
    runtime = MagicMock()
    runtime.context.executor_model = "test:model"
    runtime.context.get_agent_llm_kwargs = MagicMock(return_value={})
    runtime.context.enable_llm_streaming = False
    runtime.context.executor_call_model_timeout = 0

    mock_model = MagicMock()
    with patch("src.executor_agent.graph.load_chat_model", return_value=mock_model):
        with patch("src.executor_agent.graph.invoke_chat_model", return_value=AIMessage(content=bad_content)):
            result = await reflection_node(state, runtime)

    assert len(result["messages"]) == 1
    assert isinstance(result["messages"][0], AIMessage)


# ---------------------------------------------------------------------------
# Snapshot roundtrip through ExecutorResult (Supervisor-facing contract)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("suggestion,confidence,expect_replan", [
    ("continue", 0.8, False),
    ("replan", 0.4, True),
    ("abort", 0.1, True),
])
def test_executor_result_snapshot_supervisor_contract(suggestion, confidence, expect_replan):
    """Supervisor can parse snapshot from ExecutorResult.snapshot_json."""
    snapshot = {
        "trigger_type": "interval",
        "current_step": "step_2",
        "confidence_score": confidence,
        "suggestion": suggestion,
        "progress_summary": "Some steps done",
    }
    result = ExecutorResult(
        status="paused",
        updated_plan_json='{"plan_id": "test"}',
        summary=f"Reflection: {suggestion}",
        snapshot_json=json.dumps(snapshot, ensure_ascii=False),
    )

    assert result.status == "paused"
    parsed = json.loads(result.snapshot_json)
    assert parsed["suggestion"] == suggestion
    if expect_replan:
        assert parsed["confidence_score"] < 0.6
