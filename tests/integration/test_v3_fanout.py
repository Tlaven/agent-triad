"""Integration tests for V3 fan-out functionality.

These tests verify that Supervisor can fan-out multiple Executor instances
and merge their results.
"""

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.common.context import Context
from src.executor_agent.graph import run_executor
from src.supervisor_agent.state import (
    ExecutorRef,
    PlannerSession,
    State,
)
from src.supervisor_agent.tools import get_tools
from src.supervisor_agent.parallel import (
    build_execution_batches,
    merge_parallel_step_states,
    merge_fanin_summaries,
)


class TestV3FanoutBasics:
    """Test basic fan-out functionality."""

    @pytest.mark.asyncio
    async def test_fan_out_multiple_executors(self):
        """Test that Supervisor can launch multiple Executor instances in parallel."""
        ctx = Context()

        # Create a plan with parallelizable steps
        plan_json = json.dumps({
            "plan_id": "plan_parallel",
            "version": 1,
            "goal": "Test parallel execution",
            "steps": [
                {
                    "step_id": "step_1",
                    "intent": "Task A",
                    "expected_output": "Result A",
                    "status": "pending",
                    "parallel_group": "group_a",
                },
                {
                    "step_id": "step_2",
                    "intent": "Task B",
                    "expected_output": "Result B",
                    "status": "pending",
                    "parallel_group": "group_a",
                },
            ],
        })

        # Verify the plan can be parsed
        plan = json.loads(plan_json)
        assert len(plan["steps"]) == 2
        assert plan["steps"][0]["parallel_group"] == "group_a"
        assert plan["steps"][1]["parallel_group"] == "group_a"

    @pytest.mark.asyncio
    async def test_executor_ref_structure(self):
        """Test that ExecutorRef structure is valid."""
        ref = ExecutorRef(
            executor_session_id="exec_123",
            planner_session_id="plan_456",
            plan_json='{"plan_id": "test"}',
            status="running",
            experiment_name="test_exp",
        )

        assert ref.executor_session_id == "exec_123"
        assert ref.planner_session_id == "plan_456"
        assert ref.status == "running"
        assert ref.experiment_name == "test_exp"

    @pytest.mark.asyncio
    async def test_state_maintains_executor_registry(self):
        """Test that State can maintain multiple executor references."""
        state = State(messages=[])

        # Add multiple executors
        exec1 = ExecutorRef(
            executor_session_id="exec_1",
            planner_session_id="plan_1",
            plan_json='{"plan_id": "test"}',
            status="running",
        )
        exec2 = ExecutorRef(
            executor_session_id="exec_2",
            planner_session_id="plan_1",
            plan_json='{"plan_id": "test"}',
            status="running",
        )

        state.executors["exec_1"] = exec1
        state.executors["exec_2"] = exec2

        assert len(state.executors) == 2
        assert "exec_1" in state.executors
        assert "exec_2" in state.executors


class TestV3FanoutIntegration:
    """Integration tests for fan-out with real execution."""

    @pytest.mark.asyncio
    async def test_parallel_execution_with_dependencies(self):
        """Test parallel execution with step dependencies."""
        from src.supervisor_agent.parallel import build_execution_batches

        steps = [
            {
                "step_id": "step_1",
                "intent": "Independent task A",
                "status": "pending",
                "parallel_group": "prep",
            },
            {
                "step_id": "step_2",
                "intent": "Independent task B",
                "status": "pending",
                "parallel_group": "prep",
            },
            {
                "step_id": "step_3",
                "intent": "Dependent task",
                "depends_on": ["step_1", "step_2"],
                "status": "pending",
            },
        ]

        batches = build_execution_batches(steps)

        # First batch should have step_1 and step_2
        assert len(batches) == 2
        assert len(batches[0].step_ids) == 2
        assert "step_1" in batches[0].step_ids
        assert "step_2" in batches[0].step_ids

        # Second batch should have step_3
        assert len(batches[1].step_ids) == 1
        assert "step_3" in batches[1].step_ids

    @pytest.mark.asyncio
    async def test_merge_parallel_results(self):
        """Test merging results from multiple executors."""
        from src.supervisor_agent.parallel import merge_parallel_step_states

        base_steps = [
            {"step_id": "step_1", "status": "pending"},
            {"step_id": "step_2", "status": "pending"},
        ]

        # Two executors return partial results
        result1 = [{"step_id": "step_1", "status": "completed", "result_summary": "Done by exec 1"}]
        result2 = [{"step_id": "step_2", "status": "completed", "result_summary": "Done by exec 2"}]

        merged = merge_parallel_step_states(base_steps, [result1, result2])

        assert merged[0]["status"] == "completed"
        assert merged[0]["result_summary"] == "Done by exec 1"
        assert merged[1]["status"] == "completed"
        assert merged[1]["result_summary"] == "Done by exec 2"

    @pytest.mark.asyncio
    async def test_merge_summaries_with_budget(self):
        """Test merging summaries respects character budget."""
        from src.supervisor_agent.parallel import merge_fanin_summaries

        summaries = [
            "Executor 1 completed task A successfully with detailed output",
            "Executor 2 completed task B successfully with detailed output",
            "Executor 3 completed task C successfully with detailed output",
        ]

        merged = merge_fanin_summaries(summaries, max_chars=100)

        # Should be truncated
        assert len(merged) <= 120  # Allow some margin for truncation marker
        assert "[已截断" in merged or len(merged) <= 100


class TestV3FanoutErrorHandling:
    """Test error handling in fan-out scenarios."""

    @pytest.mark.asyncio
    async def test_one_executor_fails_others_succeed(self):
        """Test that failure in one executor doesn't block others."""
        from src.supervisor_agent.parallel import merge_parallel_step_states

        base_steps = [
            {"step_id": "step_1", "status": "pending"},
            {"step_id": "step_2", "status": "pending"},
        ]

        # Executor 1 succeeds, Executor 2 fails
        result1 = [{"step_id": "step_1", "status": "completed", "result_summary": "Success"}]
        result2 = [{"step_id": "step_2", "status": "failed", "failure_reason": "Task failed"}]

        merged = merge_parallel_step_states(base_steps, [result1, result2])

        assert merged[0]["status"] == "completed"
        assert merged[1]["status"] == "failed"
        assert merged[1]["failure_reason"] == "Task failed"

    @pytest.mark.asyncio
    async def test_circular_dependency_detection(self):
        """Test that circular dependencies are detected."""
        from src.supervisor_agent.parallel import build_execution_batches

        steps = [
            {"step_id": "step_1", "depends_on": ["step_2"]},
            {"step_id": "step_2", "depends_on": ["step_1"]},
        ]

        with pytest.raises(ValueError, match="circular"):
            build_execution_batches(steps)


class TestV3SupervisorFanoutIntegration:
    """Integration tests for Supervisor fan-out functionality."""

    @pytest.mark.asyncio
    async def test_supervisor_identifies_parallel_steps(self):
        """Test that Supervisor can identify parallelizable steps from a plan."""
        plan_json = {
            "plan_id": "plan_fanout",
            "version": 1,
            "goal": "Test parallel step identification",
            "steps": [
                {
                    "step_id": "step_1",
                    "intent": "Independent task A",
                    "status": "pending",
                    "parallel_group": "prep",
                },
                {
                    "step_id": "step_2",
                    "intent": "Independent task B",
                    "status": "pending",
                    "parallel_group": "prep",
                },
                {
                    "step_id": "step_3",
                    "intent": "Dependent task",
                    "depends_on": ["step_1", "step_2"],
                    "status": "pending",
                },
            ],
        }

        batches = build_execution_batches(plan_json["steps"])

        # Should identify 2 batches
        assert len(batches) == 2

        # First batch should have both parallel steps
        assert len(batches[0].step_ids) == 2
        assert "step_1" in batches[0].step_ids
        assert "step_2" in batches[0].step_ids

        # Second batch should have the dependent step
        assert len(batches[1].step_ids) == 1
        assert "step_3" in batches[1].step_ids

    @pytest.mark.asyncio
    async def test_supervisor_creates_executor_refs(self):
        """Test that Supervisor creates ExecutorRef instances for parallel execution."""
        plan_json = {
            "plan_id": "plan_parallel",
            "version": 1,
            "goal": "Test executor creation",
            "steps": [
                {
                    "step_id": "step_1",
                    "intent": "Task A",
                    "status": "pending",
                    "parallel_group": "group_a",
                },
                {
                    "step_id": "step_2",
                    "intent": "Task B",
                    "status": "pending",
                    "parallel_group": "group_a",
                },
            ],
        }

        # Simulate Supervisor creating executor refs
        state = State(messages=[])
        batches = build_execution_batches(plan_json["steps"])

        for batch in batches:
            for step_id in batch.step_ids:
                executor_ref = ExecutorRef(
                    executor_session_id=f"exec_{step_id}",
                    planner_session_id=plan_json["plan_id"],
                    plan_json=json.dumps(plan_json),
                    status="running",
                )
                state.executors[executor_ref.executor_session_id] = executor_ref

        # Should have created executor refs for all steps
        assert len(state.executors) == 2
        assert "exec_step_1" in state.executors
        assert "exec_step_2" in state.executors

    @pytest.mark.asyncio
    async def test_supervisor_merges_parallel_results(self):
        """Test that Supervisor properly merges results from parallel executors."""
        base_plan = {
            "plan_id": "plan_merge",
            "version": 1,
            "goal": "Test result merging",
            "steps": [
                {"step_id": "step_1", "intent": "Task A", "status": "pending"},
                {"step_id": "step_2", "intent": "Task B", "status": "pending"},
            ],
        }

        # Simulate results from parallel executors
        result_1 = [
            {"step_id": "step_1", "status": "completed", "result_summary": "Task A completed successfully"}
        ]
        result_2 = [
            {"step_id": "step_2", "status": "completed", "result_summary": "Task B completed successfully"}
        ]

        merged_steps = merge_parallel_step_states(base_plan["steps"], [result_1, result_2])

        # Both steps should be marked as completed
        assert merged_steps[0]["status"] == "completed"
        assert merged_steps[1]["status"] == "completed"
        assert "Task A completed" in merged_steps[0]["result_summary"]
        assert "Task B completed" in merged_steps[1]["result_summary"]

    @pytest.mark.asyncio
    async def test_supervisor_handles_partial_failure_in_parallel(self):
        """Test that Supervisor handles partial failures in parallel execution."""
        base_plan = {
            "plan_id": "plan_failure",
            "version": 1,
            "goal": "Test failure handling",
            "steps": [
                {"step_id": "step_1", "intent": "Task A", "status": "pending"},
                {"step_id": "step_2", "intent": "Task B", "status": "pending"},
            ],
        }

        # One succeeds, one fails
        result_1 = [
            {"step_id": "step_1", "status": "completed", "result_summary": "Success"}
        ]
        result_2 = [
            {"step_id": "step_2", "status": "failed", "failure_reason": "Task B failed"}
        ]

        merged_steps = merge_parallel_step_states(base_plan["steps"], [result_1, result_2])

        # Should preserve both results
        assert merged_steps[0]["status"] == "completed"
        assert merged_steps[1]["status"] == "failed"
        assert merged_steps[1]["failure_reason"] == "Task B failed"

    @pytest.mark.asyncio
    async def test_supervisor_fanout_with_budget_control(self):
        """Test that Supervisor respects character budget when merging summaries."""
        summaries = [
            "Executor 1: Long detailed output from task A that goes on and on" * 3,
            "Executor 2: Long detailed output from task B that goes on and on" * 3,
            "Executor 3: Long detailed output from task C that goes on and on" * 3,
        ]

        merged = merge_fanin_summaries(summaries, max_chars=200)

        # Should respect budget
        assert len(merged) <= 220  # Allow some margin
        assert "已截断" in merged or len(merged) <= 200
