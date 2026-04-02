import json

from src.supervisor_agent.tools import _mark_plan_steps_failed, _normalize_plan_json


def test_mark_plan_steps_failed_marks_only_pending_running() -> None:
    plan = {
        "steps": [
            {"step_id": "step_1", "status": "pending"},
            {"step_id": "step_2", "status": "running"},
            {"step_id": "step_3", "status": "completed"},
        ]
    }
    out = _mark_plan_steps_failed(json.dumps(plan), "boom")
    parsed = json.loads(out)

    assert parsed["steps"][0]["status"] == "failed"
    assert "boom" in parsed["steps"][0]["failure_reason"]
    assert parsed["steps"][1]["status"] == "failed"
    assert "boom" in parsed["steps"][1]["failure_reason"]
    assert parsed["steps"][2]["status"] == "completed"


def test_mark_plan_steps_failed_invalid_json_passthrough() -> None:
    raw = "{not json}"
    assert _mark_plan_steps_failed(raw, "boom") == raw


def test_normalize_plan_json_adds_plan_id_and_version_for_new_plan() -> None:
    raw = json.dumps(
        {
            "goal": "x",
            "steps": [{"intent": "do something", "expected_output": "done"}],
        }
    )
    normalized = json.loads(_normalize_plan_json(raw))
    assert isinstance(normalized.get("plan_id"), str)
    assert normalized["version"] == 1
    assert normalized["steps"][0]["status"] == "pending"
    assert normalized["steps"][0]["result_summary"] is None
    assert normalized["steps"][0]["failure_reason"] is None


def test_normalize_plan_json_keeps_plan_id_and_bumps_version_on_replan() -> None:
    previous = json.dumps(
        {
            "plan_id": "plan_abc",
            "version": 2,
            "goal": "x",
            "steps": [{"step_id": "step_1", "status": "completed"}],
        }
    )
    replanned = json.dumps(
        {
            "goal": "x2",
            "steps": [{"intent": "redo", "expected_output": "ok"}],
        }
    )
    normalized = json.loads(_normalize_plan_json(replanned, previous_plan_json=previous))
    assert normalized["plan_id"] == "plan_abc"
    assert normalized["version"] == 3
