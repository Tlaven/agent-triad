"""Tests for _split_planner_output — extracting reasoning from call_planner return."""

from src.supervisor_agent.graph import _split_planner_output


def test_split_with_reasoning_marker() -> None:
    content = "[PLANNER_REASONING]\n分析推理内容\n[/PLANNER_REASONING]\n\n{\"plan_id\":\"p1\",\"version\":1}"
    reasoning, plan_json = _split_planner_output(content)
    assert reasoning == "分析推理内容"
    assert '"plan_id"' in plan_json


def test_split_no_marker() -> None:
    content = '{"plan_id":"p1","version":1}'
    reasoning, plan_json = _split_planner_output(content)
    assert reasoning == ""
    assert plan_json == '{"plan_id":"p1","version":1}'


def test_split_empty_reasoning() -> None:
    content = "[PLANNER_REASONING]\n[/PLANNER_REASONING]\n\n{\"plan_id\":\"p1\"}"
    reasoning, plan_json = _split_planner_output(content)
    assert reasoning == ""
    assert '"plan_id"' in plan_json


def test_split_preserves_multiline_reasoning() -> None:
    content = "[PLANNER_REASONING]\n第一行分析\n第二行分析\n第三行分析\n[/PLANNER_REASONING]\n\n{\"plan_id\":\"p1\"}"
    reasoning, plan_json = _split_planner_output(content)
    assert "第一行" in reasoning
    assert "第三行" in reasoning
