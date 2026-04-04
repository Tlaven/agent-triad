import json

from src.supervisor_agent.state import PlannerSession
from src.supervisor_agent.tools import _resolve_planner_input_for_call_planner


def test_fresh_plan_requires_task_core() -> None:
    err, replan = _resolve_planner_input_for_call_planner("", None, None)
    assert err is not None
    assert replan is None


def test_fresh_plan_ok() -> None:
    err, replan = _resolve_planner_input_for_call_planner("训练一个分类模型", None, None)
    assert err is None
    assert replan is None


def test_replan_requires_session() -> None:
    err, replan = _resolve_planner_input_for_call_planner("", "p1", None)
    assert err is not None
    assert replan is None


def test_replan_plan_id_mismatch() -> None:
    session = PlannerSession(
        session_id="s",
        plan_json=json.dumps({"plan_id": "p1", "steps": []}, ensure_ascii=False),
    )
    err, replan = _resolve_planner_input_for_call_planner("", "p2", session)
    assert err is not None
    assert replan is None


def test_replan_ok() -> None:
    plan = {"plan_id": "p1", "steps": [{"step_id": "step_1"}]}
    raw = json.dumps(plan, ensure_ascii=False)
    session = PlannerSession(session_id="s", plan_json=raw)
    err, replan = _resolve_planner_input_for_call_planner("重点修复 step_1", "p1", session)
    assert err is None
    assert replan == raw


def test_empty_plan_id_means_fresh() -> None:
    err, replan = _resolve_planner_input_for_call_planner("仅 task_core", "", None)
    assert err is None
    assert replan is None


def test_whitespace_plan_id_means_fresh() -> None:
    err, replan = _resolve_planner_input_for_call_planner("任务", "   ", None)
    assert err is None
    assert replan is None
