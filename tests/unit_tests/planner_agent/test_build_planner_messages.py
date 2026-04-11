from langchain_core.messages import HumanMessage

from src.planner_agent.graph import build_planner_messages


def test_fresh_plan_only_task_core() -> None:
    msgs = build_planner_messages("详细任务：目标、约束与验收标准", None)
    assert len(msgs) == 1
    assert isinstance(msgs[0], HumanMessage)
    assert msgs[0].content == "详细任务：目标、约束与验收标准"


def test_replan_two_messages() -> None:
    plan = '{"plan_id": "p1", "steps": []}'
    msgs = build_planner_messages("根据失败原因调整 step_2", plan)
    assert len(msgs) == 2
    assert isinstance(msgs[0], HumanMessage)
    assert "调整 step_2" in msgs[0].content
    assert isinstance(msgs[1], HumanMessage)
    assert msgs[1].content == plan


def test_replan_without_task_core_uses_placeholder_first_message() -> None:
    plan = '{"plan_id": "p1"}'
    msgs = build_planner_messages("", plan)
    assert len(msgs) == 2
    assert isinstance(msgs[0], HumanMessage)
    assert "未单独补充 task_core" in msgs[0].content
    assert msgs[1].content == plan
