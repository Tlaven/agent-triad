"""Unit tests for supervisor tools helper functions."""

import json
from datetime import datetime, timezone, timedelta

import pytest

from src.supervisor_agent.tools import (
    _relative_time_ago,
    _normalize_plan_json,
    _normalize_plan_id_arg,
    _resolve_planner_input_for_call_planner,
    _mark_plan_steps_failed,
    _format_completion_result,
)
from src.supervisor_agent.state import PlannerSession


# ---------------------------------------------------------------------------
# _format_relative_time
# ---------------------------------------------------------------------------

class TestFormatRelativeTime:
    def test_just_now(self) -> None:
        now = datetime.now(timezone.utc)
        assert _relative_time_ago(now) == "刚刚"

    def test_seconds_ago(self) -> None:
        dt = datetime.now(timezone.utc) - timedelta(seconds=30)
        assert "30秒前" in _relative_time_ago(dt)

    def test_minutes_ago(self) -> None:
        dt = datetime.now(timezone.utc) - timedelta(minutes=5)
        assert "5分钟前" in _relative_time_ago(dt)

    def test_hours_ago(self) -> None:
        dt = datetime.now(timezone.utc) - timedelta(hours=3)
        assert "3小时前" in _relative_time_ago(dt)

    def test_days_ago(self) -> None:
        dt = datetime.now(timezone.utc) - timedelta(days=2)
        assert "2天前" in _relative_time_ago(dt)

    def test_week_plus_shows_date(self) -> None:
        dt = datetime.now(timezone.utc) - timedelta(days=10)
        result = _relative_time_ago(dt)
        assert "天前" not in result  # Should show date instead

    def test_future_within_tolerance(self) -> None:
        dt = datetime.now(timezone.utc) + timedelta(seconds=2)
        assert _relative_time_ago(dt) == "刚刚"

    def test_future_far_shows_date(self) -> None:
        dt = datetime.now(timezone.utc) + timedelta(seconds=10)
        result = _relative_time_ago(dt)
        assert "天前" not in result


# ---------------------------------------------------------------------------
# _normalize_plan_id_arg
# ---------------------------------------------------------------------------

class TestNormalizePlanIdArg:
    def test_none_stays_none(self) -> None:
        assert _normalize_plan_id_arg(None) is None

    def test_empty_string_becomes_none(self) -> None:
        assert _normalize_plan_id_arg("") is None

    def test_whitespace_becomes_none(self) -> None:
        assert _normalize_plan_id_arg("  ") is None

    def test_valid_id_kept(self) -> None:
        assert _normalize_plan_id_arg("plan_abc123") == "plan_abc123"

    def test_whitespace_trimmed(self) -> None:
        assert _normalize_plan_id_arg("  plan_abc  ") == "plan_abc"


# ---------------------------------------------------------------------------
# _resolve_planner_input_for_call_planner
# ---------------------------------------------------------------------------

class TestResolvePlannerInput:
    def test_first_plan_with_task_core(self) -> None:
        err, plan_json = _resolve_planner_input_for_call_planner("build the app", None, None)
        assert err is None
        assert plan_json is None

    def test_first_plan_without_task_core(self) -> None:
        err, _ = _resolve_planner_input_for_call_planner("", None, None)
        assert err is not None
        assert "task_core" in err

    def test_replan_with_matching_plan_id(self) -> None:
        session = PlannerSession(
            session_id="s1",
            plan_json=json.dumps({"plan_id": "plan_abc", "version": 1, "steps": []}),
        )
        err, pj = _resolve_planner_input_for_call_planner("update step 1", "plan_abc", session)
        assert err is None
        assert pj is not None

    def test_replan_with_no_session(self) -> None:
        err, _ = _resolve_planner_input_for_call_planner("replan", "plan_abc", None)
        assert err is not None

    def test_replan_with_mismatched_id(self) -> None:
        session = PlannerSession(
            session_id="s1",
            plan_json=json.dumps({"plan_id": "plan_other", "version": 1, "steps": []}),
        )
        err, _ = _resolve_planner_input_for_call_planner("replan", "plan_abc", session)
        assert err is not None
        assert "不匹配" in err

    def test_replan_with_invalid_json(self) -> None:
        session = PlannerSession(
            session_id="s1",
            plan_json="not json",
        )
        err, _ = _resolve_planner_input_for_call_planner("replan", "plan_abc", session)
        assert err is not None

    def test_replan_with_non_dict_json(self) -> None:
        session = PlannerSession(
            session_id="s1",
            plan_json="[1,2,3]",
        )
        err, _ = _resolve_planner_input_for_call_planner("replan", "plan_abc", session)
        assert err is not None


# ---------------------------------------------------------------------------
# _normalize_plan_json
# ---------------------------------------------------------------------------

class TestNormalizePlanJson:
    def test_empty_string_passthrough(self) -> None:
        assert _normalize_plan_json("") == ""

    def test_whitespace_passthrough(self) -> None:
        assert _normalize_plan_json("  ") == "  "

    def test_invalid_json_passthrough(self) -> None:
        assert _normalize_plan_json("not json") == "not json"

    def test_non_dict_passthrough(self) -> None:
        assert _normalize_plan_json("[1,2,3]") == "[1,2,3]"

    def test_generates_plan_id_and_version(self) -> None:
        result = json.loads(_normalize_plan_json('{"goal": "test", "steps": []}'))
        assert result["plan_id"].startswith("plan_")
        assert result["version"] == 1

    def test_increments_version_on_replan(self) -> None:
        prev = json.dumps({"plan_id": "plan_abc", "version": 3, "steps": []})
        result = json.loads(_normalize_plan_json('{"goal": "test", "steps": []}', previous_plan_json=prev))
        assert result["plan_id"] == "plan_abc"
        assert result["version"] == 4

    def test_normalizes_steps(self) -> None:
        plan = json.dumps({"goal": "g", "steps": [{"intent": "do stuff"}]})
        result = json.loads(_normalize_plan_json(plan))
        step = result["steps"][0]
        assert step["step_id"] == "step_1"
        assert step["status"] == "pending"
        assert step["result_summary"] is None
        assert step["failure_reason"] is None
        assert step["parallel_group"] is None

    def test_preserves_existing_step_id(self) -> None:
        plan = json.dumps({"goal": "g", "steps": [{"step_id": "custom_1", "intent": "x"}]})
        result = json.loads(_normalize_plan_json(plan))
        assert result["steps"][0]["step_id"] == "custom_1"

    def test_skips_non_dict_steps(self) -> None:
        plan = json.dumps({"goal": "g", "steps": ["not a dict", {"intent": "valid"}]})
        result = json.loads(_normalize_plan_json(plan))
        assert len(result["steps"]) == 2
        assert result["steps"][0] == "not a dict"
        assert result["steps"][1]["step_id"] == "step_2"

    def test_parallel_group_kept_if_present(self) -> None:
        plan = json.dumps({"goal": "g", "steps": [{"intent": "x", "parallel_group": "A"}]})
        result = json.loads(_normalize_plan_json(plan))
        assert result["steps"][0]["parallel_group"] == "A"


# ---------------------------------------------------------------------------
# _mark_plan_steps_failed
# ---------------------------------------------------------------------------

class TestMarkPlanStepsFailed:
    def test_empty_passthrough(self) -> None:
        assert _mark_plan_steps_failed("", "err") == ""

    def test_invalid_json_passthrough(self) -> None:
        assert _mark_plan_steps_failed("not json", "err") == "not json"

    def test_marks_pending_steps_as_failed(self) -> None:
        plan = json.dumps({"plan_id": "p1", "steps": [
            {"step_id": "s1", "status": "pending", "intent": "x"},
            {"step_id": "s2", "status": "completed", "intent": "y"},
        ]})
        result = json.loads(_mark_plan_steps_failed(plan, "timeout"))
        assert result["steps"][0]["status"] == "failed"
        assert "timeout" in result["steps"][0]["failure_reason"]
        assert result["steps"][1]["status"] == "completed"

    def test_marks_running_steps(self) -> None:
        plan = json.dumps({"steps": [{"status": "running"}]})
        result = json.loads(_mark_plan_steps_failed(plan, "crash"))
        assert result["steps"][0]["status"] == "failed"

    def test_marks_steps_with_no_status(self) -> None:
        plan = json.dumps({"steps": [{"intent": "orphan"}]})
        result = json.loads(_mark_plan_steps_failed(plan, "err"))
        assert result["steps"][0]["status"] == "failed"

    def test_handles_list_plan(self) -> None:
        plan = json.dumps([{"status": "pending"}])
        result = json.loads(_mark_plan_steps_failed(plan, "err"))
        assert isinstance(result, list)
        assert result[0]["status"] == "failed"


# ---------------------------------------------------------------------------
# _format_completion_result
# ---------------------------------------------------------------------------

class TestFormatCompletionResult:
    def test_completed_payload(self) -> None:
        payload = {"status": "completed", "summary": "done", "updated_plan_json": "", "snapshot_json": ""}
        result = _format_completion_result(payload, "plan_1", '{"steps":[]}')
        assert "done" in result
        assert "[EXECUTOR_RESULT]" in result
        meta = json.loads(result.split("[EXECUTOR_RESULT] ")[1])
        assert meta["status"] == "completed"

    def test_failed_with_plan_json(self) -> None:
        plan = json.dumps({"steps": [{"status": "pending"}]})
        payload = {"status": "failed", "summary": "error", "updated_plan_json": plan, "snapshot_json": ""}
        result = _format_completion_result(payload, "plan_1", plan)
        meta = json.loads(result.split("[EXECUTOR_RESULT] ")[1])
        assert meta["status"] == "failed"
        assert meta["error_detail"] is None

    def test_failed_without_plan_json_triggers_mark_failed(self) -> None:
        plan = json.dumps({"steps": [{"status": "pending"}]})
        payload = {"status": "failed", "summary": "crash", "updated_plan_json": "", "snapshot_json": ""}
        result = _format_completion_result(payload, "plan_1", plan)
        meta = json.loads(result.split("[EXECUTOR_RESULT] ")[1])
        assert meta["status"] == "failed"
        assert meta["error_detail"] is not None
        assert "兜底" in meta["error_detail"]
