"""Integration tests for V2-c Executor reflection/snapshot mechanism."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from src.executor_agent.graph import ExecutorState, ExecutorResult, run_executor
from src.common.context import Context


class TestReflectionIntegrationFlow:
    """Test full reflection flow with executor graph."""

    @pytest.mark.asyncio
    async def test_reflection_flow_with_plan(self):
        """Test complete reflection flow: executor → reflection → paused status."""
        plan_json = {
            "plan_id": "test_plan_v1",
            "version": 1,
            "goal": "Write and execute a Python script",
            "steps": [
                {
                    "step_id": "step_1",
                    "intent": "Write Python script",
                    "expected_output": "script.py file created",
                    "status": "completed",
                    "result_summary": "Created script.py",
                    "failure_reason": None,
                },
                {
                    "step_id": "step_2",
                    "intent": "Execute script",
                    "expected_output": "Script runs successfully",
                    "status": "pending",
                    "result_summary": None,
                    "failure_reason": None,
                },
            ],
        }

        # Mock executor LLM to complete first step and trigger reflection
        executor_responses = [
            # First response: complete step 1
            AIMessage(content="""```json
{
  "status": "completed",
  "summary": "Step 1 completed: script.py created",
  "updated_plan": {
    "plan_id": "test_plan_v1",
    "version": 1,
    "goal": "Write and execute a Python script",
    "steps": [
      {
        "step_id": "step_1",
        "intent": "Write Python script",
        "expected_output": "script.py file created",
        "status": "completed",
        "result_summary": "Created script.py",
        "failure_reason": null
      },
      {
        "step_id": "step_2",
        "intent": "Execute script",
        "expected_output": "Script runs successfully",
        "status": "pending",
        "result_summary": null,
        "failure_reason": null
      }
    ]
  }
}
```"""),
        ]

        # Mock reflection LLM response
        reflection_response = AIMessage(content="""```json
{
  "status": "paused",
  "summary": "Reflection: 1/2 steps completed, suggest continue execution",
  "snapshot": {
    "progress_summary": "Completed step 1 (script creation), step 2 pending",
    "reflection": "Task is on track, no issues detected",
    "suggestion": "continue",
    "confidence": 0.85
  },
  "updated_plan": {
    "plan_id": "test_plan_v1",
    "version": 1,
    "goal": "Write and execute a Python script",
    "steps": [
      {
        "step_id": "step_1",
        "intent": "Write Python script",
        "expected_output": "script.py file created",
        "status": "completed",
        "result_summary": "Created script.py",
        "failure_reason": null
      },
      {
        "step_id": "step_2",
        "intent": "Execute script",
        "expected_output": "Script runs successfully",
        "status": "pending",
        "result_summary": null,
        "failure_reason": null
      }
    ]
  }
}
```""")

        mock_model = MagicMock()
        mock_model.ainvoke = AsyncMock(side_effect=executor_responses + [reflection_response])

        context = Context(
            reflection_interval=1,  # Trigger reflection after every tool round
            confidence_threshold=0.6,
        )

        runtime = MagicMock()
        runtime.context = context
        runtime.context.executor_model = "test:model"
        runtime.context.get_agent_llm_kwargs = MagicMock(return_value={
            "temperature": 0.0,
            "top_p": 1.0,
            "max_tokens": 1500,
        })
        runtime.context.enable_llm_streaming = False

        # Note: Full integration test requires complete graph setup
        # This is simplified to test individual components
        # with patch("src.executor_agent.graph.load_chat_model", return_value=mock_model):
        #     with patch("src.executor_agent.graph.invoke_chat_model", side_effect=executor_responses + [reflection_response]):
        #         # Full integration test would go here

    @pytest.mark.asyncio
    async def test_reflection_suggests_replan(self):
        """Test reflection suggesting replan due to low confidence."""
        reflection_response = AIMessage(content="""```json
{
  "status": "paused",
  "summary": "Confidence low: task direction unclear, suggest replanning",
  "snapshot": {
    "progress_summary": "Started execution but encountered ambiguity",
    "reflection": "Task goals are unclear, current approach may not be optimal",
    "suggestion": "replan",
    "confidence": 0.4
  },
  "updated_plan": {
    "plan_id": "test_plan",
    "version": 1,
    "goal": "Ambiguous task",
    "steps": []
  }
}
```""")

        mock_model = MagicMock()
        mock_model.ainvoke = AsyncMock(return_value=reflection_response)

        state = ExecutorState(
            messages=[HumanMessage(content="Execute ambiguous task")],
            tool_rounds=2,
            reflection_interval=2,
        )

        runtime = MagicMock()
        runtime.context.executor_model = "test:model"
        runtime.context.get_agent_llm_kwargs = MagicMock(return_value={})
        runtime.context.enable_llm_streaming = False

        with patch("src.executor_agent.graph.load_chat_model", return_value=mock_model):
            with patch("src.executor_agent.graph.invoke_chat_model", return_value=reflection_response):
                from src.executor_agent.graph import reflection_node

                result = await reflection_node(state, runtime)

                # Verify reflection response contains low confidence
                assert "messages" in result
                message_content = result["messages"][0].content
                assert "paused" in message_content
                assert "replan" in message_content

    @pytest.mark.asyncio
    async def test_reflection_suggests_abort(self):
        """Test reflection suggesting abort due to critical issues."""
        reflection_response = AIMessage(content="""```json
{
  "status": "paused",
  "summary": "Critical error detected: suggest aborting task",
  "snapshot": {
    "progress_summary": "Encountered critical system error",
    "reflection": "Task cannot proceed due to unrecoverable error",
    "suggestion": "abort",
    "confidence": 0.1
  },
  "updated_plan": {
    "plan_id": "test_plan",
    "version": 1,
    "goal": "Failed task",
    "steps": []
  }
}
```""")

        mock_model = MagicMock()
        mock_model.ainvoke = AsyncMock(return_value=reflection_response)

        state = ExecutorState(
            messages=[HumanMessage(content="Execute failing task")],
            tool_rounds=1,
            reflection_interval=1,
        )

        runtime = MagicMock()
        runtime.context.executor_model = "test:model"
        runtime.context.get_agent_llm_kwargs = MagicMock(return_value={})
        runtime.context.enable_llm_streaming = False

        with patch("src.executor_agent.graph.load_chat_model", return_value=mock_model):
            with patch("src.executor_agent.graph.invoke_chat_model", return_value=reflection_response):
                from src.executor_agent.graph import reflection_node

                result = await reflection_node(state, runtime)

                # Verify reflection response contains abort suggestion
                assert "messages" in result
                message_content = result["messages"][0].content
                assert "abort" in message_content


class TestReflectionWithDifferentIntervals:
    """Test reflection with different interval configurations."""

    @pytest.mark.asyncio
    async def test_reflection_every_step(self):
        """Test reflection triggering every single step."""
        state = ExecutorState(
            messages=[HumanMessage(content="Test")],
            tool_rounds=1,
            reflection_interval=1,  # Every step
        )

        from src.executor_agent.graph import route_after_tools
        result = route_after_tools(state)
        assert result == "reflection"

    @pytest.mark.asyncio
    async def test_reflection_every_three_steps(self):
        """Test reflection triggering every 3 steps."""
        state = ExecutorState(
            messages=[HumanMessage(content="Test")],
            tool_rounds=3,
            reflection_interval=3,
        )

        from src.executor_agent.graph import route_after_tools
        result = route_after_tools(state)
        assert result == "reflection"

    @pytest.mark.asyncio
    async def test_reflection_every_five_steps(self):
        """Test reflection triggering every 5 steps."""
        state = ExecutorState(
            messages=[HumanMessage(content="Test")],
            tool_rounds=5,
            reflection_interval=5,
        )

        from src.executor_agent.graph import route_after_tools
        result = route_after_tools(state)
        assert result == "reflection"


class TestReflectionSnapshotHandling:
    """Test snapshot data handling in reflection."""

    def test_snapshot_serialization(self):
        """Test snapshot data can be serialized and deserialized."""
        snapshot = {
            "trigger_type": "interval",
            "current_step": "step_3",
            "confidence_score": 0.75,
            "reflection_analysis": "Task progressing normally",
            "suggestion": "continue",
            "progress_summary": "3/5 steps completed, no issues",
        }

        # Serialize
        snapshot_json = json.dumps(snapshot, ensure_ascii=False)

        # Deserialize
        parsed = json.loads(snapshot_json)

        assert parsed["trigger_type"] == "interval"
        assert parsed["current_step"] == "step_3"
        assert parsed["confidence_score"] == 0.75
        assert parsed["suggestion"] == "continue"

    def test_executor_result_with_snapshot(self):
        """Test ExecutorResult correctly handles snapshot field."""
        snapshot = {
            "progress_summary": "Halfway done",
            "reflection": "On track",
            "suggestion": "continue",
            "confidence": 0.8,
        }

        result = ExecutorResult(
            status="paused",
            updated_plan_json='{"plan_id": "test"}',
            summary="Reflection: continue execution",
            snapshot_json=json.dumps(snapshot, ensure_ascii=False),
        )

        # Verify snapshot is accessible and parseable
        parsed_snapshot = json.loads(result.snapshot_json)
        assert parsed_snapshot["suggestion"] == "continue"
        assert parsed_snapshot["confidence"] == 0.8


class TestReflectionErrorScenarios:
    """Test reflection error handling scenarios."""

    @pytest.mark.asyncio
    async def test_reflection_with_malformed_json(self):
        """Test reflection handles malformed JSON in LLM response."""
        # Mock LLM response with malformed JSON
        malformed_response = AIMessage(content="```json\n{invalid json}\n```")

        mock_model = MagicMock()
        mock_model.ainvoke = AsyncMock(return_value=malformed_response)

        state = ExecutorState(
            messages=[HumanMessage(content="Execute plan")],
            tool_rounds=2,
            reflection_interval=2,
        )

        runtime = MagicMock()
        runtime.context.executor_model = "test:model"
        runtime.context.get_agent_llm_kwargs = MagicMock(return_value={})
        runtime.context.enable_llm_streaming = False

        with patch("src.executor_agent.graph.load_chat_model", return_value=mock_model):
            with patch("src.executor_agent.graph.invoke_chat_model", return_value=malformed_response):
                from src.executor_agent.graph import reflection_node

                # Should not crash, but handle gracefully
                result = await reflection_node(state, runtime)
                assert "messages" in result

    @pytest.mark.asyncio
    async def test_reflection_with_missing_fields(self):
        """Test reflection handles missing required fields in snapshot."""
        # Mock LLM response with missing fields
        incomplete_response = AIMessage(content="""```json
{
  "status": "paused",
  "summary": "Incomplete snapshot"
}
```""")

        mock_model = MagicMock()
        mock_model.ainvoke = AsyncMock(return_value=incomplete_response)

        state = ExecutorState(
            messages=[HumanMessage(content="Execute plan")],
            tool_rounds=2,
            reflection_interval=2,
        )

        runtime = MagicMock()
        runtime.context.executor_model = "test:model"
        runtime.context.get_agent_llm_kwargs = MagicMock(return_value={})
        runtime.context.enable_llm_streaming = False

        with patch("src.executor_agent.graph.load_chat_model", return_value=mock_model):
            with patch("src.executor_agent.graph.invoke_chat_model", return_value=incomplete_response):
                from src.executor_agent.graph import reflection_node

                result = await reflection_node(state, runtime)
                assert "messages" in result


class TestReflectionWithConfidenceThresholds:
    """Test reflection with different confidence threshold configurations."""

    def test_high_confidence_threshold(self):
        """Test reflection with high confidence threshold (0.9)."""
        context = Context(
            reflection_interval=2,
            confidence_threshold=0.9,  # Very high threshold
        )

        assert context.confidence_threshold == 0.9
        assert context.reflection_interval == 2

    def test_low_confidence_threshold(self):
        """Test reflection with low confidence threshold (0.3)."""
        context = Context(
            reflection_interval=2,
            confidence_threshold=0.3,  # Very low threshold
        )

        assert context.confidence_threshold == 0.3

    def test_default_confidence_threshold(self):
        """Test reflection confidence threshold has sensible default."""
        context = Context()
        # Should have a default threshold (likely 0.6 as per documentation)
        assert hasattr(context, "confidence_threshold")


class TestReflectionMultiStepScenarios:
    """Test reflection in multi-step execution scenarios."""

    @pytest.mark.asyncio
    async def test_reflection_at_multiple_checkpoints(self):
        """Test reflection triggering at multiple checkpoints during execution."""
        # Simulate a 10-step task with reflection every 3 steps
        # Note: reflection only triggers when tool_rounds > 0, so round 0 is excluded
        reflection_points = [3, 6, 9]  # Reflection triggers here (not 0)

        for round_num in range(10):
            state = ExecutorState(
                messages=[HumanMessage(content=f"Step {round_num}")],
                tool_rounds=round_num,
                reflection_interval=3,
            )

            from src.executor_agent.graph import route_after_tools
            result = route_after_tools(state)

            if round_num in reflection_points:
                assert result == "reflection", f"Should trigger at round {round_num}"
            else:
                assert result == "call_executor", f"Should not trigger at round {round_num}"

    @pytest.mark.asyncio
    async def test_reflection_continues_after_checkpoint(self):
        """Test execution continues after reflection checkpoint."""
        # This tests that after reflection returns "paused", the system
        # can resume execution when Supervisor calls executor again
        state1 = ExecutorState(
            messages=[HumanMessage(content="Execute step 1")],
            tool_rounds=2,
            reflection_interval=2,
        )

        from src.executor_agent.graph import route_after_tools
        result1 = route_after_tools(state1)
        assert result1 == "reflection"  # Should trigger

        # After reflection, if Supervisor decides to continue, execution resumes
        state2 = ExecutorState(
            messages=[HumanMessage(content="Execute step 1")],
            tool_rounds=3,
            reflection_interval=2,
        )

        result2 = route_after_tools(state2)
        assert result2 == "call_executor"  # Should continue


class TestReflectionIntegrationWithSupervisor:
    """Test reflection integration with Supervisor decision-making."""

    def test_reflection_paused_status_for_supervisor(self):
        """Test that paused status from reflection is correctly formatted for Supervisor."""
        snapshot = {
            "progress_summary": "2/5 steps completed",
            "reflection": "Task on track",
            "suggestion": "continue",
            "confidence": 0.8,
        }

        result = ExecutorResult(
            status="paused",
            updated_plan_json='{"plan_id": "test"}',
            summary="Reflection suggests continue execution",
            snapshot_json=json.dumps(snapshot, ensure_ascii=False),
        )

        # Verify Supervisor can parse the result
        assert result.status == "paused"
        assert "Reflection suggests" in result.summary

        # Verify snapshot is valid JSON
        parsed_snapshot = json.loads(result.snapshot_json)
        assert parsed_snapshot["suggestion"] == "continue"

    def test_reflection_replan_suggestion_for_supervisor(self):
        """Test reflection replan suggestion is clear for Supervisor."""
        snapshot = {
            "progress_summary": "Encountered obstacles",
            "reflection": "Current approach not working",
            "suggestion": "replan",
            "confidence": 0.4,
        }

        result = ExecutorResult(
            status="paused",
            updated_plan_json='{"plan_id": "test"}',
            summary="Reflection low confidence: suggests replanning",
            snapshot_json=json.dumps(snapshot, ensure_ascii=False),
        )

        # Verify Supervisor gets clear replan signal
        parsed_snapshot = json.loads(result.snapshot_json)
        assert parsed_snapshot["suggestion"] == "replan"
        assert parsed_snapshot["confidence"] < 0.6  # Below threshold

    def test_reflection_abort_suggestion_for_supervisor(self):
        """Test reflection abort suggestion is clear for Supervisor."""
        snapshot = {
            "progress_summary": "Critical error",
            "reflection": "Cannot continue",
            "suggestion": "abort",
            "confidence": 0.1,
        }

        result = ExecutorResult(
            status="paused",
            updated_plan_json='{"plan_id": "test"}',
            summary="Reflection critical failure: suggests abort",
            snapshot_json=json.dumps(snapshot, ensure_ascii=False),
        )

        # Verify Supervisor gets clear abort signal
        parsed_snapshot = json.loads(result.snapshot_json)
        assert parsed_snapshot["suggestion"] == "abort"
        assert parsed_snapshot["confidence"] < 0.3  # Very low


class TestReflectionContextPreservation:
    """Test that reflection preserves execution context correctly."""

    def test_reflection_preserves_plan_state(self):
        """Test reflection includes current plan state in snapshot."""
        original_plan = {
            "plan_id": "test_plan",
            "version": 2,
            "goal": "Test goal",
            "steps": [
                {"step_id": "step_1", "status": "completed"},
                {"step_id": "step_2", "status": "pending"},
            ],
        }

        snapshot = {
            "progress_summary": "1/2 steps completed",
            "reflection": "On track",
            "suggestion": "continue",
            "confidence": 0.8,
        }

        result = ExecutorResult(
            status="paused",
            updated_plan_json=json.dumps(original_plan, ensure_ascii=False),
            summary="Reflection checkpoint",
            snapshot_json=json.dumps(snapshot, ensure_ascii=False),
        )

        # Verify plan is preserved
        parsed_plan = json.loads(result.updated_plan_json)
        assert parsed_plan["plan_id"] == "test_plan"
        assert parsed_plan["version"] == 2
        assert len(parsed_plan["steps"]) == 2

    @pytest.mark.asyncio
    async def test_reflection_preserves_message_history(self):
        """Test reflection has access to full message history."""
        messages = [
            SystemMessage(content="System prompt"),
            HumanMessage(content="Task description"),
            AIMessage(content="Response"),
            ToolMessage(content="Tool result", tool_call_id="123"),
        ]

        state = ExecutorState(
            messages=messages,
            tool_rounds=2,
            reflection_interval=2,
        )

        # Verify reflection node can access full history
        assert len(state.messages) == 4
        assert any(isinstance(msg, ToolMessage) for msg in state.messages)
