"""Tests for _split_reasoning_and_json — separating Planner reasoning from Plan JSON."""

from src.planner_agent.graph import _split_reasoning_and_json


def test_split_with_reasoning_and_json() -> None:
    content = """让我分析一下任务...\n需要先了解项目结构。\n\n```json\n{"goal":"x","steps":[]}\n```"""
    reasoning, json_text = _split_reasoning_and_json(content)
    assert "分析" in reasoning
    assert json_text == '{"goal":"x","steps":[]}'


def test_split_json_only_no_reasoning() -> None:
    content = """```json\n{"goal":"x","steps":[]}\n```"""
    reasoning, json_text = _split_reasoning_and_json(content)
    assert reasoning == ""
    assert json_text == '{"goal":"x","steps":[]}'


def test_split_no_json_block() -> None:
    content = "这是一段没有 JSON 的纯文本"
    reasoning, json_text = _split_reasoning_and_json(content)
    assert reasoning == content
    assert json_text == content


def test_split_multiple_json_blocks_returns_raw() -> None:
    content = """```json\n{"a":1}\n```\n```json\n{"b":2}\n```"""
    reasoning, json_text = _split_reasoning_and_json(content)
    assert reasoning == content
    assert json_text == content


def test_split_with_multiline_reasoning() -> None:
    content = """第一步：分析需求
第二步：评估依赖
第三步：制定计划

```json
{"goal": "build feature", "steps": [{"step_id": "step_1"}]}
```"""
    reasoning, json_text = _split_reasoning_and_json(content)
    assert "分析需求" in reasoning
    assert "评估依赖" in reasoning
    assert "step_1" in json_text
