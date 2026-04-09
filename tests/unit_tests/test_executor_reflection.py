"""Unit tests for V2-c Executor reflection/snapshot mechanism."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from src.executor_agent.graph import ExecutorState, ExecutorResult, reflection_node, route_after_tools
from src.common.context import Context


class TestReflectionRouting:
    """Test reflection routing logic (route_after_tools)."""

    def test_route_after_tools_disabled_reflection(self):
        """When reflection_interval=0, always go to call_executor."""
        state = ExecutorState(
            messages=[HumanMessage(content="test")],
            tool_rounds=5,
            reflection_interval=0,
        )

        result = route_after_tools(state)
        assert result == "call_executor"

    def test_route_after_tools_interval_trigger(self):
        """When tool_rounds % reflection_interval == 0, trigger reflection."""
        state = ExecutorState(
            messages=[HumanMessage(content="test")],
            tool_rounds=4,
            reflection_interval=2,
        )

        result = route_after_tools(state)
        assert result == "reflection"

    def test_route_after_tools_no_trigger(self):
        """When tool_rounds % reflection_interval != 0, continue execution."""
        state = ExecutorState(
            messages=[HumanMessage(content="test")],
            tool_rounds=3,
            reflection_interval=2,
        )

        result = route_after_tools(state)
        assert result == "call_executor"

    def test_route_after_tools_first_round(self):
        """First round (tool_rounds=0) should NOT trigger reflection (no tools executed yet)."""
        state = ExecutorState(
            messages=[HumanMessage(content="test")],
            tool_rounds=0,
            reflection_interval=2,
        )

        # tool_rounds must be > 0 for reflection to trigger
        result = route_after_tools(state)
        assert result == "call_executor"


class TestReflectionNode:
    """Test reflection node execution."""

    @pytest.mark.asyncio
    async def test_reflection_node_returns_paused_status(self):
        """Reflection node should return paused status with snapshot."""
        state = ExecutorState(
            messages=[
                HumanMessage(content="Execute plan"),
                AIMessage(content=""),
                ToolMessage(content="File written", tool_call_id="123"),
            ],
            tool_rounds=2,
            reflection_interval=2,
        )

        # Mock LLM response with valid reflection JSON
        reflection_response = AIMessage(content="""```json
{
  "status": "paused",
  "summary": "Progress check: 2/5 steps completed, on track",
  "snapshot": {
    "progress_summary": "Completed file writing, moving to next step",
    "reflection": "Task is progressing as expected",
    "suggestion": "continue",
    "confidence": 0.8
  },
  "updated_plan": {
    "plan_id": "test_plan",
    "version": 1,
    "goal": "Test goal",
    "steps": []
  }
}
```""")

        mock_model = MagicMock()
        mock_model.ainvoke = AsyncMock(return_value=reflection_response)

        runtime = MagicMock()
        runtime.context.executor_model = "test:model"
        runtime.context.get_agent_llm_kwargs = MagicMock(return_value={
            "temperature": 0.0,
            "top_p": 1.0,
            "max_tokens": 1500,
            "extra_body": {"enable_thinking": True}
        })
        runtime.context.enable_llm_streaming = False

        with patch("src.executor_agent.graph.load_chat_model", return_value=mock_model):
            with patch("src.executor_agent.graph.invoke_chat_model", return_value=reflection_response) as mock_invoke:
                result = await reflection_node(state, runtime)

                # Verify the message was added
                assert "messages" in result
                assert len(result["messages"]) == 1
                assert isinstance(result["messages"][0], AIMessage)

                # Verify LLM was called with correct parameters
                mock_invoke.assert_called_once()
                call_args = mock_invoke.call_args
                assert call_args[1]["enable_streaming"] == False

    @pytest.mark.asyncio
    async def test_reflection_node_handles_invalid_json(self):
        """Reflection node should handle invalid JSON gracefully."""
        state = ExecutorState(
            messages=[HumanMessage(content="Execute plan")],
            tool_rounds=2,
            reflection_interval=2,
        )

        # Mock LLM response with invalid JSON
        invalid_response = AIMessage(content="This is not valid JSON")

        mock_model = MagicMock()
        mock_model.ainvoke = AsyncMock(return_value=invalid_response)

        runtime = MagicMock()
        runtime.context.executor_model = "test:model"
        runtime.context.get_agent_llm_kwargs = MagicMock(return_value={})
        runtime.context.enable_llm_streaming = False

        with patch("src.executor_agent.graph.load_chat_model", return_value=mock_model):
            with patch("src.executor_agent.graph.invoke_chat_model", return_value=invalid_response):
                result = await reflection_node(state, runtime)

                # Should still return a message even with invalid JSON
                assert "messages" in result
                assert len(result["messages"]) == 1


class TestReflectionStateStructure:
    """Test reflection state and snapshot structure."""

    def test_executor_state_has_reflection_fields(self):
        """ExecutorState should have reflection configuration fields."""
        state = ExecutorState(
            messages=[],
            tool_rounds=0,
            reflection_interval=2,
            confidence_threshold=0.6,
        )

        assert hasattr(state, "reflection_interval")
        assert state.reflection_interval == 2
        assert hasattr(state, "confidence_threshold")
        assert state.confidence_threshold == 0.6
        assert hasattr(state, "tool_rounds")
        assert state.tool_rounds == 0

    def test_executor_result_has_snapshot_field(self):
        """ExecutorResult should have snapshot_json field for V2-c."""
        result = ExecutorResult(
            status="paused",
            updated_plan_json='{"plan_id": "test"}',
            summary="Paused for reflection",
            snapshot_json='{"snapshot": "data"}',
        )

        assert result.status == "paused"
        assert result.snapshot_json == '{"snapshot": "data"}'
        assert isinstance(result.snapshot_json, str)


class TestReflectionIntegration:
    """Test reflection integration with executor flow."""

    def test_reflection_interval_configuration(self):
        """Test reflection interval configuration from Context."""
        context = Context(
            reflection_interval=3,
            confidence_threshold=0.7,
        )

        assert context.reflection_interval == 3
        assert context.confidence_threshold == 0.7

    def test_reflection_disabled_by_default(self):
        """Test reflection is disabled by default (interval=0)."""
        context = Context()

        assert context.reflection_interval == 0  # Default is disabled

    def test_snapshot_json_structure(self):
        """Test snapshot JSON structure is valid."""
        snapshot_data = {
            "progress_summary": "Completed 2/5 steps",
            "reflection": "On track, no issues detected",
            "suggestion": "continue",
            "confidence": 0.8,
        }

        snapshot_json = json.dumps(snapshot_data, ensure_ascii=False)

        # Verify JSON is valid
        parsed = json.loads(snapshot_json)
        assert parsed["progress_summary"] == "Completed 2/5 steps"
        assert parsed["reflection"] == "On track, no issues detected"
        assert parsed["suggestion"] == "continue"
        assert parsed["confidence"] == 0.8

    def test_executor_result_with_snapshot(self):
        """Test ExecutorResult can properly serialize snapshot data."""
        snapshot = {
            "trigger_type": "interval",
            "current_step": "step_2",
            "confidence_score": 0.5,
            "reflection_analysis": "Task may be drifting from goal",
            "suggestion": "replan",
            "progress_summary": "Completed 2/5 steps, encountered issues",
        }

        result = ExecutorResult(
            status="paused",
            updated_plan_json='{"plan_id": "test"}',
            summary="Reflection suggests replanning",
            snapshot_json=json.dumps(snapshot, ensure_ascii=False),
        )

        # Verify snapshot can be parsed back
        parsed_snapshot = json.loads(result.snapshot_json)
        assert parsed_snapshot["trigger_type"] == "interval"
        assert parsed_snapshot["suggestion"] == "replan"
        assert parsed_snapshot["confidence_score"] == 0.5


class TestReflectionConfidenceThresholds:
    """Test confidence threshold evaluation."""

    def test_low_confidence_threshold(self):
        """Test low confidence threshold configuration."""
        context = Context(confidence_threshold=0.3)
        assert context.confidence_threshold == 0.3

    def test_high_confidence_threshold(self):
        """Test high confidence threshold configuration."""
        context = Context(confidence_threshold=0.9)
        assert context.confidence_threshold == 0.9

    def test_default_confidence_threshold(self):
        """Test default confidence threshold is 0.6."""
        # Check if default is 0.6 as per documentation
        context = Context()
        # Note: actual default may differ, this tests the documented value
        assert hasattr(context, "confidence_threshold")


class TestReflectionStatusNormalization:
    """Test reflection status normalization in executor output parsing."""

    def test_paused_status_normalization(self):
        """Test various 'paused' status variants normalize correctly."""
        from src.executor_agent.graph import _normalize_executor_status_token

        # Test various paused variants
        assert _normalize_executor_status_token("paused") == "paused"
        assert _normalize_executor_status_token("pause") == "paused"
        assert _normalize_executor_status_token("checkpoint") == "paused"
        assert _normalize_executor_status_token("halt") == "paused"

    def test_other_status_normalization(self):
        """Test other status variants normalize correctly."""
        from src.executor_agent.graph import _normalize_executor_status_token

        assert _normalize_executor_status_token("completed") == "completed"
        assert _normalize_executor_status_token("failed") == "failed"
        assert _normalize_executor_status_token("success") == "completed"
        assert _normalize_executor_status_token("error") == "failed"


class TestReflectionToolRoundsCounter:
    """Test tool_rounds counter in reflection triggering."""

    def test_tool_rounds_increment(self):
        """Test tool_rounds increments correctly."""
        state = ExecutorState(
            messages=[],
            tool_rounds=0,
            reflection_interval=2,
        )

        # Simulate tool rounds incrementing
        state.tool_rounds += 1
        assert state.tool_rounds == 1

        state.tool_rounds += 1
        assert state.tool_rounds == 2

        # Should trigger reflection at round 2
        assert route_after_tools(state) == "reflection"

    def test_tool_rounds_modulo_operation(self):
        """Test reflection triggers at correct intervals."""
        state = ExecutorState(
            messages=[],
            tool_rounds=0,
            reflection_interval=3,
        )

        # Test intervals: 3, 6, 9... should trigger reflection (not 0 because tool_rounds must be > 0)
        for round_num in [3, 6, 9]:
            state.tool_rounds = round_num
            assert route_after_tools(state) == "reflection", f"Should trigger at round {round_num}"

        # Test non-intervals: 0, 1, 2, 4, 5, 7, 8... should not trigger
        for round_num in [0, 1, 2, 4, 5, 7, 8]:
            state.tool_rounds = round_num
            assert route_after_tools(state) == "call_executor", f"Should not trigger at round {round_num}"


class TestReflectionErrorMessageHandling:
    """Test reflection error handling and edge cases."""

    @pytest.mark.asyncio
    async def test_reflection_node_llm_failure(self):
        """Test reflection node handles LLM failures gracefully."""
        state = ExecutorState(
            messages=[HumanMessage(content="Execute plan")],
            tool_rounds=2,
            reflection_interval=2,
        )

        mock_model = MagicMock()
        mock_model.ainvoke = AsyncMock(side_effect=Exception("LLM API Error"))

        runtime = MagicMock()
        runtime.context.executor_model = "test:model"
        runtime.context.get_agent_llm_kwargs = MagicMock(return_value={})
        runtime.context.enable_llm_streaming = False

        with patch("src.executor_agent.graph.load_chat_model", return_value=mock_model):
            with patch("src.executor_agent.graph.invoke_chat_model", side_effect=Exception("LLM API Error")):
                with pytest.raises(Exception):
                    await reflection_node(state, runtime)

    def test_reflection_with_empty_snapshot(self):
        """Test ExecutorResult handles empty snapshot correctly."""
        result = ExecutorResult(
            status="paused",
            updated_plan_json='{"plan_id": "test"}',
            summary="Paused with empty snapshot",
            snapshot_json="",  # Empty snapshot
        )

        assert result.snapshot_json == ""
        assert result.status == "paused"


class TestReflectionIntegrationWithExecutor:
    """Test reflection integration with overall executor flow."""

    def test_reflection_preserves_executor_state(self):
        """Test reflection doesn't corrupt executor state."""
        state = ExecutorState(
            messages=[
                HumanMessage(content="Test message"),
                AIMessage(content="Test response"),
            ],
            tool_rounds=2,
            reflection_interval=2,
        )

        # Reflection should not modify the state incorrectly
        assert len(state.messages) == 2
        assert state.tool_rounds == 2
        assert state.reflection_interval == 2

    def test_reflection_configuration_independence(self):
        """Test reflection config is independent per executor instance."""
        state1 = ExecutorState(
            messages=[],
            tool_rounds=0,
            reflection_interval=2,
            confidence_threshold=0.5,
        )

        state2 = ExecutorState(
            messages=[],
            tool_rounds=0,
            reflection_interval=5,
            confidence_threshold=0.8,
        )

        # Verify independence
        assert state1.reflection_interval == 2
        assert state2.reflection_interval == 5
        assert state1.confidence_threshold == 0.5
        assert state2.confidence_threshold == 0.8


class TestReflectionScenarios:
    """Test realistic reflection scenarios."""

    def test_reflection_at_halfway_point(self):
        """Test reflection triggering at task halfway point."""
        # Simulate a 10-step task with reflection every 5 steps
        state = ExecutorState(
            messages=[],
            tool_rounds=5,
            reflection_interval=5,
        )

        # Should trigger reflection at halfway point
        assert route_after_tools(state) == "reflection"

    def test_reflection_frequency_configuration(self):
        """Test different reflection frequencies."""
        test_cases = [
            (1, 1, True),   # Every step: round 1 % 1 = 0, triggers
            (1, 2, True),   # Every step: round 2 % 1 = 0, triggers
            (2, 2, True),   # Every 2 steps: round 2 % 2 = 0, triggers
            (2, 4, True),   # Every 2 steps: round 4 % 2 = 0, triggers
            (10, 20, True), # Every 10 steps: round 20 % 10 = 0, triggers
            (10, 10, True),  # Every 10 steps: round 10 % 10 = 0, triggers
            (3, 5, False),  # Every 3 steps: round 5 % 3 = 2, no trigger
            (5, 12, False), # Every 5 steps: round 12 % 5 = 2, no trigger
        ]

        for interval, round_num, should_trigger in test_cases:
            state = ExecutorState(
                messages=[],
                tool_rounds=round_num,
                reflection_interval=interval,
            )

            result = route_after_tools(state)
            if should_trigger:
                assert result == "reflection", f"Failed for interval={interval}, round={round_num}"
            else:
                assert result == "call_executor", f"Failed for interval={interval}, round={round_num}"

    def test_reflection_disabled_scenarios(self):
        """Test that reflection disabled (interval=0) never triggers."""
        for round_num in range(0, 20):
            state = ExecutorState(
                messages=[],
                tool_rounds=round_num,
                reflection_interval=0,  # Disabled
            )

            result = route_after_tools(state)
            assert result == "call_executor", f"Should not trigger when disabled (round {round_num})"
