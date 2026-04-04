"""Unit tests for planner_agent._normalize_planner_output_plan_json."""

import json

from src.planner_agent.graph import _normalize_planner_output_plan_json


def _plan_without_meta(goal: str = "test goal") -> str:
    """Return a minimal plan JSON string without plan_id/version."""
    return json.dumps({
        "goal": goal,
        "steps": [
            {
                "step_id": "step_1",
                "intent": "do something",
                "expected_output": "something done",
                "status": "pending",
                "result_summary": None,
                "failure_reason": None,
            }
        ],
    })


# ---------------------------------------------------------------------------
# First-time plan (no plan_id arg, no previous_plan_json)
# ---------------------------------------------------------------------------

def test_first_plan_generates_plan_id() -> None:
    result = _normalize_planner_output_plan_json(
        _plan_without_meta(), plan_id=None, previous_plan_json=None
    )
    parsed = json.loads(result)
    assert isinstance(parsed["plan_id"], str)
    assert parsed["plan_id"].startswith("plan_v")


def test_first_plan_sets_version_to_one() -> None:
    result = _normalize_planner_output_plan_json(
        _plan_without_meta(), plan_id=None, previous_plan_json=None
    )
    assert json.loads(result)["version"] == 1


def test_first_plan_uses_explicit_plan_id_arg() -> None:
    result = _normalize_planner_output_plan_json(
        _plan_without_meta(), plan_id="plan_explicit", previous_plan_json=None
    )
    parsed = json.loads(result)
    assert parsed["plan_id"] == "plan_explicit"
    assert parsed["version"] == 1


def test_first_plan_ignores_llm_plan_id_in_output() -> None:
    plan_with_llm_id = json.dumps({"plan_id": "llm_generated_id", "goal": "g", "steps": []})
    result = _normalize_planner_output_plan_json(
        plan_with_llm_id, plan_id=None, previous_plan_json=None
    )
    parsed = json.loads(result)
    # LLM-provided plan_id should be replaced by a system-generated one
    assert parsed["plan_id"] != "llm_generated_id"
    assert parsed["plan_id"].startswith("plan_v")


# ---------------------------------------------------------------------------
# Replan (with previous_plan_json)
# ---------------------------------------------------------------------------

def test_replan_preserves_plan_id_from_previous() -> None:
    previous = json.dumps({"plan_id": "plan_keep_me", "version": 2, "goal": "g", "steps": []})
    result = _normalize_planner_output_plan_json(
        _plan_without_meta(), plan_id=None, previous_plan_json=previous
    )
    parsed = json.loads(result)
    assert parsed["plan_id"] == "plan_keep_me"


def test_replan_increments_version() -> None:
    previous = json.dumps({"plan_id": "plan_keep_me", "version": 2, "goal": "g", "steps": []})
    result = _normalize_planner_output_plan_json(
        _plan_without_meta(), plan_id=None, previous_plan_json=previous
    )
    assert json.loads(result)["version"] == 3


def test_replan_plan_id_arg_takes_precedence_over_previous() -> None:
    previous = json.dumps({"plan_id": "plan_old", "version": 1, "goal": "g", "steps": []})
    result = _normalize_planner_output_plan_json(
        _plan_without_meta(), plan_id="plan_explicit", previous_plan_json=previous
    )
    parsed = json.loads(result)
    assert parsed["plan_id"] == "plan_explicit"


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

def test_invalid_json_returns_original_string() -> None:
    raw = "{not valid json}"
    result = _normalize_planner_output_plan_json(raw, plan_id=None, previous_plan_json=None)
    assert result == raw


def test_non_dict_json_returns_original_string() -> None:
    raw = json.dumps([1, 2, 3])
    result = _normalize_planner_output_plan_json(raw, plan_id=None, previous_plan_json=None)
    assert result == raw


def test_empty_string_returns_empty_string() -> None:
    result = _normalize_planner_output_plan_json("", plan_id=None, previous_plan_json=None)
    assert result == ""


def test_invalid_previous_plan_json_falls_back_gracefully() -> None:
    result = _normalize_planner_output_plan_json(
        _plan_without_meta(), plan_id=None, previous_plan_json="{not valid}"
    )
    parsed = json.loads(result)
    # Should still produce a valid plan with generated plan_id
    assert isinstance(parsed["plan_id"], str)
    assert parsed["version"] == 1
