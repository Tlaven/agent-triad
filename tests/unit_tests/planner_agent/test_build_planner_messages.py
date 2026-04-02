from langchain_core.messages import HumanMessage, SystemMessage

from src.planner_agent.graph import build_planner_messages


def test_fresh_plan_system_then_task_core() -> None:
    msgs = build_planner_messages("详细任务：目标、约束与验收标准", None)
    assert len(msgs) == 2
    assert isinstance(msgs[0], SystemMessage)
    assert "你是 Planner Agent" in msgs[0].content
    assert isinstance(msgs[1], HumanMessage)
    assert msgs[1].content == "详细任务：目标、约束与验收标准"


def test_replan_three_messages() -> None:
    plan = '{"plan_id": "p1", "steps": []}'
    msgs = build_planner_messages("根据失败原因调整 step_2", plan)
    assert len(msgs) == 3
    assert isinstance(msgs[0], SystemMessage)
    assert isinstance(msgs[1], HumanMessage)
    assert "调整 step_2" in msgs[1].content
    assert isinstance(msgs[2], HumanMessage)
    assert msgs[2].content == plan


def test_replan_without_task_core_uses_placeholder_second_message() -> None:
    plan = '{"plan_id": "p1"}'
    msgs = build_planner_messages("", plan)
    assert len(msgs) == 3
    assert isinstance(msgs[1], HumanMessage)
    assert "未单独补充 task_core" in msgs[1].content
    assert msgs[2].content == plan
