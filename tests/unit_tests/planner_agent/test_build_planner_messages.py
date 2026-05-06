from langchain_core.messages import AIMessage, HumanMessage

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


# ---------------------------------------------------------------------------
# Edge cases for coverage
# ---------------------------------------------------------------------------


def test_fresh_plan_empty_task_core_uses_placeholder() -> None:
    """Empty task_core with no replan → placeholder message."""
    from langchain_core.messages import AIMessage
    msgs = build_planner_messages("", None)
    assert len(msgs) == 1
    assert "未提供任务描述" in msgs[0].content


def test_planner_history_with_assistant_role() -> None:
    """History messages with assistant role become AIMessage."""
    history = [
        {"role": "assistant", "content": "Previous analysis"},
    ]
    msgs = build_planner_messages("new task", None, planner_history_messages=history)
    assert len(msgs) == 2
    assert isinstance(msgs[0], AIMessage)
    assert "Previous analysis" in msgs[0].content


def test_planner_history_skips_empty_content() -> None:
    """History entries with empty content are skipped."""
    history = [
        {"role": "user", "content": ""},
        {"role": "assistant", "content": "  "},
        {"role": "user", "content": "valid"},
    ]
    msgs = build_planner_messages("task", None, planner_history_messages=history)
    # Only "valid" from history + "task" = 2 messages
    assert len(msgs) == 2
