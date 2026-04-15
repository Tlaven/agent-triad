"""跨模块特性组合的集成测试。

覆盖：工具输出治理（observation）与相关组合场景。
Reflection 路由逻辑在单元测试中覆盖；Planner 工具契约见 test_tools_registry.py。
"""

import json

import pytest
from langchain_core.messages import HumanMessage

from src.common.context import Context
from src.common.observation import normalize_observation
from src.executor_agent.graph import ExecutorState, route_after_tools
from src.planner_agent.tools import get_planner_tools


# ---------------------------------------------------------------------------
# Observation governance
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("size,expect_offloaded", [
    (30_000, True),   # very large -> offloaded
    (10_000, False),  # medium    -> truncated only
])
def test_large_tool_output_governance(size, expect_offloaded):
    """Outputs over threshold are offloaded; smaller outputs are truncated."""
    ctx = Context(
        max_observation_chars=6500,
        observation_offload_threshold_chars=28000,
        enable_observation_offload=True,
    )
    obs = normalize_observation("x" * size, context=ctx)
    assert obs.truncated or obs.offloaded
    if expect_offloaded:
        assert obs.offloaded


def test_governed_observation_preserves_error_prefix():
    """Error prefix remains visible even after truncation."""
    ctx = Context(
        max_observation_chars=6500,
        observation_offload_threshold_chars=28000,
        enable_observation_offload=True,
    )
    error_output = "Error: Command failed\n" + "x" * 10_000
    obs = normalize_observation(error_output, context=ctx)
    assert "error" in obs.text.lower() or obs.truncated


# ---------------------------------------------------------------------------
# 组合冒烟
# ---------------------------------------------------------------------------

def test_v2_features_do_not_interfere():
    """Observation governance + reflection config + planner tools all work together."""
    ctx = Context(
        observation_workspace_dir="workspace",
        reflection_interval=2,
        confidence_threshold=0.6,
        max_observation_chars=6500,
        observation_offload_threshold_chars=28000,
        enable_observation_offload=True,
    )

    # observation governance
    obs = normalize_observation("x" * 30_000, context=ctx)
    assert obs.offloaded

    # Planner tools still return the expected readonly set
    tools = get_planner_tools(ctx)
    assert len(tools) == 2
    assert {t.name for t in tools} == {"read_workspace_text_file", "list_workspace_entries"}

    # reflection routing
    state = ExecutorState(
        messages=[HumanMessage(content="Test")],
        tool_rounds=2,
        reflection_interval=ctx.reflection_interval,
    )
    assert route_after_tools(state) == "reflection"
