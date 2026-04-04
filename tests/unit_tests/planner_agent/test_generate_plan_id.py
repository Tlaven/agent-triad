"""Unit tests for planner_agent._generate_plan_id."""

import re

from src.planner_agent.graph import _generate_plan_id


def test_generate_plan_id_format() -> None:
    pid = _generate_plan_id()
    # Expected format: plan_vYYYYMMDD_xxxx (e.g. plan_v20260403_a1b2)
    assert re.match(r"^plan_v\d{8}_[0-9a-f]{4}$", pid), f"Unexpected format: {pid!r}"


def test_generate_plan_id_is_string() -> None:
    assert isinstance(_generate_plan_id(), str)


def test_generate_plan_id_is_unique() -> None:
    ids = {_generate_plan_id() for _ in range(50)}
    # With 50 generations, collisions should be essentially impossible
    assert len(ids) > 40
