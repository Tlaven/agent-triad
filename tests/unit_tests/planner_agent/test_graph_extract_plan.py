from src.planner_agent.graph import _extract_plan_json_from_planner_content


def test_extract_plan_json_from_single_fence() -> None:
    content = """思路...

```json
{"goal":"x","steps":[]}
```
"""
    assert _extract_plan_json_from_planner_content(content) == '{"goal":"x","steps":[]}'


def test_extract_plan_json_without_fence_returns_raw() -> None:
    content = '{"goal":"x","steps":[]}'
    assert _extract_plan_json_from_planner_content(content) == content


def test_extract_plan_json_multiple_fences_returns_raw() -> None:
    content = """```json
{"a":1}
```
```json
{"b":2}
```"""
    assert _extract_plan_json_from_planner_content(content) == content
