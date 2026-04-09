import pytest

from src.supervisor_agent.parallel import (
    build_execution_batches,
    merge_fanin_summaries,
    merge_parallel_step_states,
)


def test_build_execution_batches_with_dependencies_and_groups():
    steps = [
        {"step_id": "step_1", "depends_on": [], "parallel_group": "prep"},
        {"step_id": "step_2", "depends_on": [], "parallel_group": "prep"},
        {"step_id": "step_3", "depends_on": ["step_1", "step_2"]},
    ]
    batches = build_execution_batches(steps)
    assert batches[0].step_ids == ["step_1", "step_2"]
    assert batches[1].step_ids == ["step_3"]


def test_build_execution_batches_detects_cycle():
    steps = [
        {"step_id": "step_1", "depends_on": ["step_2"]},
        {"step_id": "step_2", "depends_on": ["step_1"]},
    ]
    with pytest.raises(ValueError):
        _ = build_execution_batches(steps)


def test_merge_parallel_step_states_uses_status_priority():
    base = [
        {"step_id": "step_1", "status": "pending", "result_summary": None, "failure_reason": None},
        {"step_id": "step_2", "status": "pending", "result_summary": None, "failure_reason": None},
    ]
    merged = merge_parallel_step_states(
        base,
        [
            [{"step_id": "step_1", "status": "completed", "result_summary": "ok"}],
            [{"step_id": "step_1", "status": "failed", "failure_reason": "conflict"}],
        ],
    )
    assert merged[0]["status"] == "failed"
    assert merged[0]["failure_reason"] == "conflict"


def test_merge_fanin_summaries_respects_budget():
    merged = merge_fanin_summaries(["a" * 20, "b" * 20], max_chars=25)
    assert "[已截断" in merged
