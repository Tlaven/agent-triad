"""Tests for Planner tools registry — readonly contract and separation from Executor."""

import pytest
from unittest.mock import patch

from src.planner_agent.tools import get_planner_tools
from src.common.context import Context


EXPECTED_TOOL_NAMES = ["read_workspace_text_file", "list_workspace_entries"]
DANGEROUS_KEYWORDS = ["write", "delete", "remove", "create", "execute", "run", "command"]


def test_get_planner_tools_returns_exact_readonly_set():
    """Planner tools are exactly the two workspace read-only tools, in order."""
    tools = get_planner_tools()
    assert [getattr(t, "name", "") for t in tools] == EXPECTED_TOOL_NAMES


@pytest.mark.parametrize("kw", DANGEROUS_KEYWORDS)
def test_planner_tools_no_dangerous_names(kw):
    """No planner tool name contains a dangerous keyword."""
    names_joined = " ".join(getattr(t, "name", "") for t in get_planner_tools())
    assert kw not in names_joined.lower()


def test_planner_executor_separation():
    """Executor has write tools; planner does not."""
    from src.executor_agent.tools import get_executor_tools

    planner_names = {getattr(t, "name", "") for t in get_planner_tools()}
    executor_names = {getattr(t, "name", "") for t in get_executor_tools()}

    assert "write_file" in executor_names
    assert "run_local_command" in executor_names
    assert "write_file" not in planner_names
    assert "run_local_command" not in planner_names


def test_planner_tools_idempotent_and_no_duplicates():
    """Repeated calls return tools in identical order with no duplicates."""
    names1 = [getattr(t, "name", "") for t in get_planner_tools()]
    names2 = [getattr(t, "name", "") for t in get_planner_tools()]
    assert names1 == names2
    assert len(names1) == len(set(names1))


@pytest.mark.parametrize("ctx", [
    None,
    Context(),
    Context(observation_workspace_dir="workspace1"),
    Context(observation_workspace_dir=""),
])
def test_planner_tools_consistent_across_contexts(ctx):
    """Tool list is always the same two tools regardless of context."""
    tools = get_planner_tools(ctx)
    assert [getattr(t, "name", "") for t in tools] == EXPECTED_TOOL_NAMES


def test_planner_tools_apply_context_workspace_root():
    """When context is provided, apply_context_workspace_root is called once."""
    ctx = Context(observation_workspace_dir="/custom/workspace")
    with patch("src.planner_agent.tools.apply_context_workspace_root") as mock_apply:
        get_planner_tools(ctx)
        mock_apply.assert_called_once_with(ctx)
