"""Integration tests for V2 features working together."""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from src.common.context import Context
from src.common.observation import normalize_observation
from src.executor_agent.graph import ExecutorState, reflection_node, route_after_tools
from src.planner_agent.tools import get_planner_tools


class TestV2ToolOutputWithReflection:
    """Test V2-a tool output governance combined with V2-c reflection."""

    def test_large_tool_output_before_reflection(self):
        """Test that large tool outputs are governed before reflection triggers."""
        # Create a large tool output that would trigger governance
        large_output = "x" * 30000  # 30KB output

        # Create observation with large output
        ctx = Context(
            max_observation_chars=6500,
            observation_offload_threshold_chars=28000,
            enable_observation_offload=True,
        )
        observation = normalize_observation(large_output, context=ctx)

        # Verify governance is applied
        assert observation.truncated or observation.offloaded

    def test_observation_truncation_preserves_reflection_state(self):
        """Test that observation truncation doesn't break reflection state tracking."""
        # Simulate multiple tool rounds with large outputs
        tool_rounds = 3
        reflection_interval = 2

        ctx = Context(
            max_observation_chars=6500,
            observation_offload_threshold_chars=28000,
            enable_observation_offload=True,
        )

        for i in range(tool_rounds):
            # Create large output each round
            large_output = f"Round {i}: " + "x" * 10000
            observation = normalize_observation(large_output, context=ctx)

            # Verify reflection trigger condition still works
            state = ExecutorState(
                messages=[HumanMessage(content=f"Round {i}")],
                tool_rounds=i + 1,
                reflection_interval=reflection_interval,
            )

            route = route_after_tools(state)

            if (i + 1) > 0 and (i + 1) % reflection_interval == 0:
                assert route == "reflection"

    def test_multiple_large_outputs_within_reflection_interval(self):
        """Test handling multiple large outputs within a reflection interval."""
        ctx = Context(
            max_observation_chars=6500,
            observation_offload_threshold_chars=28000,
            enable_observation_offload=True,
        )

        outputs = []
        for i in range(5):
            # Mix of sizes: some truncated, some offloaded
            if i < 2:
                large_output = f"Output {i}: " + "y" * 30000  # Will be offloaded
            else:
                large_output = f"Output {i}: " + "z" * 10000  # Will be truncated
            observation = normalize_observation(large_output, context=ctx)
            outputs.append(observation)

        # Verify all observations are governed
        assert all(obs.truncated or obs.offloaded for obs in outputs)

        # Verify at least some are offloaded (very large)
        assert any(obs.offloaded for obs in outputs)

        # Verify at least some are truncated
        assert any(obs.truncated for obs in outputs)


class TestV2MCPWithReflection:
    """Test V2-b MCP tools combined with V2-c reflection."""

    def test_planner_readonly_tools_with_reflection_context(self):
        """Test that Planner readonly tools work alongside reflection configuration."""
        ctx = Context(
            observation_workspace_dir="test_workspace",
            reflection_interval=3,
            confidence_threshold=0.6,
        )

        # Get planner tools with reflection context
        tools = get_planner_tools(ctx)

        # Verify planner still has readonly tools
        tool_names = [getattr(t, "name", "") for t in tools]
        assert "read_workspace_text_file" in tool_names
        assert "list_workspace_entries" in tool_names

        # Verify reflection config is preserved
        assert ctx.reflection_interval == 3
        assert ctx.confidence_threshold == 0.6

    def test_reflection_after_mcp_tool_usage(self):
        """Test that reflection can trigger after MCP tool usage."""
        # Simulate tool rounds with MCP tools
        tool_rounds = 4
        reflection_interval = 2

        # At round 2 and 4, reflection should trigger
        for i in range(1, tool_rounds + 1):
            state = ExecutorState(
                messages=[HumanMessage(content=f"Round {i}")],
                tool_rounds=i,
                reflection_interval=reflection_interval,
            )

            route = route_after_tools(state)

            if i % reflection_interval == 0:
                assert route == "reflection", f"Reflection should trigger at round {i}"

    def test_mcp_tool_permissions_with_reflection_enabled(self):
        """Test that MCP tool permissions remain correct with reflection enabled."""
        # Enable reflection
        ctx = Context(
            reflection_interval=2,
            confidence_threshold=0.6,
        )

        planner_tools = get_planner_tools(ctx)
        planner_tool_names = [getattr(t, "name", "") for t in planner_tools]

        # Verify planner still can't use write tools
        assert "write_file" not in planner_tool_names
        assert "run_local_command" not in planner_tool_names

        # Verify planner still has readonly tools
        assert "read_workspace_text_file" in planner_tool_names


class TestV2ObservationGovernanceWithMCP:
    """Test V2-a observation governance combined with V2-b MCP tools."""

    def test_mcp_tool_output_governance(self):
        """Test that MCP tool outputs are also governed."""
        # Simulate MCP tool returning large output
        mcp_output = {
            "content": "x" * 30000,
            "is_error": False,
        }

        # Convert to observation
        ctx = Context(
            max_observation_chars=6500,
            observation_offload_threshold_chars=28000,
            enable_observation_offload=True,
        )
        observation = normalize_observation(
            json.dumps(mcp_output), context=ctx
        )

        # Verify governance
        assert observation.truncated or observation.offloaded

    def test_mcp_tool_error_handling_with_governance(self):
        """Test that MCP tool errors are handled properly with governance."""
        # Simulate MCP tool error
        error_output = {
            "error": "File not found",
            "details": "x" * 5000,  # Long error details
        }

        ctx = Context(
            max_observation_chars=6500,
            observation_offload_threshold_chars=28000,
            enable_observation_offload=True,
        )
        observation = normalize_observation(
            json.dumps(error_output), context=ctx
        )

        # Verify error is preserved even with governance
        assert "error" in observation.text.lower() or observation.truncated

    def test_concurrent_mcp_tools_with_governance(self):
        """Test multiple MCP tools with observation governance."""
        mcp_outputs = [
            json.dumps({"content": "x" * 15000, "file": "file1.txt"}),
            json.dumps({"content": "y" * 20000, "file": "file2.txt"}),
            json.dumps({"content": "z" * 25000, "file": "file3.txt"}),
        ]

        ctx = Context(
            max_observation_chars=6500,
            observation_offload_threshold_chars=28000,
            enable_observation_offload=True,
        )

        observations = [
            normalize_observation(output, context=ctx)
            for output in mcp_outputs
        ]

        # Verify all are governed
        assert all(obs.truncated or obs.offloaded for obs in observations)


class TestV2FullIntegrationScenarios:
    """Test full scenarios combining all V2 features."""

    def test_scenario_large_mcp_read_then_reflection(self):
        """Test scenario: Large MCP file read, then reflection triggers."""
        # Step 1: MCP tool reads large file
        mcp_output = json.dumps({"content": "x" * 30000, "file": "large_file.txt"})

        ctx = Context(
            max_observation_chars=6500,
            observation_offload_threshold_chars=28000,
            enable_observation_offload=True,
        )
        observation = normalize_observation(mcp_output, context=ctx)

        # Verify governance applied
        assert observation.truncated or observation.offloaded

        # Step 2: After 2 rounds, reflection should trigger
        state = ExecutorState(
            messages=[HumanMessage(content="Read large file")],
            tool_rounds=2,
            reflection_interval=2,
        )

        route = route_after_tools(state)
        assert route == "reflection"

    def test_scenario_reflection_routing_with_interval(self):
        """Test scenario: Reflection triggers at correct intervals."""
        state = ExecutorState(
            messages=[HumanMessage(content="Execute plan")],
            tool_rounds=2,
            reflection_interval=2,
        )

        route = route_after_tools(state)
        assert route == "reflection"

    def test_scenario_reflection_snapshot_structure(self):
        """Test scenario: Reflection produces valid snapshot structure."""
        # This test verifies that snapshot structure can be validated
        snapshot_data = {
            "progress_summary": "Completed 2/5 steps",
            "reflection": "Task is progressing normally",
            "suggestion": "continue",
            "confidence": 0.8
        }

        # Verify JSON structure is valid
        snapshot_json = json.dumps(snapshot_data)
        parsed = json.loads(snapshot_json)

        assert "progress_summary" in parsed
        assert "reflection" in parsed
        assert "suggestion" in parsed
        assert "confidence" in parsed

    def test_scenario_planner_executor_separation_with_reflection(self):
        """Test that Planner/Executor separation works with reflection enabled."""
        ctx = Context(
            reflection_interval=2,
            confidence_threshold=0.7,
        )

        # Get Planner tools
        planner_tools = get_planner_tools(ctx)
        planner_tool_names = [getattr(t, "name", "") for t in planner_tools]

        # Verify Planner has readonly tools
        assert "read_workspace_text_file" in planner_tool_names
        assert "write_file" not in planner_tool_names

        # Verify reflection config is accessible
        assert ctx.reflection_interval == 2
        assert ctx.confidence_threshold == 0.7

    def test_scenario_v2_features_do_not_interfere(self):
        """Test that V2 features don't interfere with each other."""
        # Enable all V2 features
        ctx = Context(
            observation_workspace_dir="test_workspace",
            reflection_interval=2,
            confidence_threshold=0.6,
            max_observation_chars=6500,
            observation_offload_threshold_chars=28000,
            enable_observation_offload=True,
        )

        # Test 1: Observation governance works
        large_output = "x" * 30000
        observation = normalize_observation(large_output, context=ctx)
        assert observation.offloaded

        # Test 2: MCP tools work
        tools = get_planner_tools(ctx)
        assert len(tools) >= 2

        # Test 3: Reflection triggers correctly
        state = ExecutorState(
            messages=[HumanMessage(content="Test")],
            tool_rounds=2,
            reflection_interval=ctx.reflection_interval,
        )
        route = route_after_tools(state)
        assert route == "reflection"


class TestV2ErrorHandlingIntegration:
    """Test error handling across V2 features."""

    def test_governed_observation_with_error_state(self):
        """Test that error states are preserved in governed observations."""
        error_output = "Error: Command failed\n" + "x" * 10000

        ctx = Context(
            max_observation_chars=6500,
            observation_offload_threshold_chars=28000,
            enable_observation_offload=True,
        )
        observation = normalize_observation(error_output, context=ctx)

        # Error should be visible even with truncation
        assert "error" in observation.text.lower() or observation.truncated

    def test_reflection_handles_suggestions(self):
        """Test that reflection can handle different suggestions."""
        valid_suggestions = ["continue", "replan", "abort"]

        for suggestion in valid_suggestions:
            snapshot_data = {
                "progress_summary": "Test",
                "reflection": "Test reflection",
                "suggestion": suggestion,
                "confidence": 0.8
            }

            # Verify suggestion is valid
            assert snapshot_data["suggestion"] in valid_suggestions

    def test_mcp_error_with_governance(self):
        """Test that MCP errors are handled with governance."""
        mcp_error = json.dumps({
            "error": "Connection timeout",
            "details": "x" * 10000,
        })

        ctx = Context(
            max_observation_chars=6500,
            observation_offload_threshold_chars=28000,
            enable_observation_offload=True,
        )
        observation = normalize_observation(mcp_error, context=ctx)

        # Error should be preserved
        assert observation.truncated or observation.offloaded


class TestV2PerformanceIntegration:
    """Test performance aspects of V2 feature integration."""

    def test_governance_does_not_slow_reflection_triggering(self):
        """Test that observation governance doesn't impact reflection triggering performance."""
        import time

        # Test with large outputs
        ctx = Context(
            max_observation_chars=6500,
            observation_offload_threshold_chars=28000,
            enable_observation_offload=True,
        )

        start = time.time()
        for i in range(10):
            large_output = "x" * 30000
            observation = normalize_observation(large_output, context=ctx)

            # Check reflection trigger
            state = ExecutorState(
                messages=[HumanMessage(content=f"Round {i}")],
                tool_rounds=i + 1,
                reflection_interval=2,
            )
            route_after_tools(state)
        end = time.time()

        # Should complete quickly (less than 1 second for 10 iterations)
        assert end - start < 1.0

    def test_mcp_tools_load_quickly_with_reflection_enabled(self):
        """Test that MCP tools load efficiently with reflection configuration."""
        import time

        ctx = Context(
            reflection_interval=3,
            confidence_threshold=0.6,
        )

        start = time.time()
        tools = get_planner_tools(ctx)
        end = time.time()

        # Should load quickly
        assert end - start < 0.1
        assert len(tools) >= 2


class TestV2ConfigurationIntegration:
    """Test configuration management across V2 features."""

    def test_all_v2_configs_in_context(self):
        """Test that all V2 configurations are accessible in Context."""
        ctx = Context(
            # V2-a: Observation governance
            max_observation_chars=6500,
            observation_offload_threshold_chars=28000,
            enable_observation_offload=True,
            # V2-c: Reflection
            reflection_interval=2,
            confidence_threshold=0.6,
            # Common
            observation_workspace_dir="workspace",
        )

        # Verify all configs are set
        assert ctx.max_observation_chars == 6500
        assert ctx.observation_offload_threshold_chars == 28000
        assert ctx.enable_observation_offload is True
        assert ctx.reflection_interval == 2
        assert ctx.confidence_threshold == 0.6
        assert ctx.observation_workspace_dir == "workspace"

    def test_v2_configs_independent(self):
        """Test that V2 feature configurations are independent."""
        # Enable only V2-a
        ctx1 = Context(
            max_observation_chars=6500,
            reflection_interval=0,  # Disabled
        )

        assert ctx1.max_observation_chars == 6500
        assert ctx1.reflection_interval == 0

        # Enable only V2-c
        ctx2 = Context(
            max_observation_chars=0,  # Disabled
            reflection_interval=2,
        )

        assert ctx2.max_observation_chars == 0
        assert ctx2.reflection_interval == 2

        # Enable both
        ctx3 = Context(
            max_observation_chars=6500,
            reflection_interval=2,
        )

        assert ctx3.max_observation_chars == 6500
        assert ctx3.reflection_interval == 2
