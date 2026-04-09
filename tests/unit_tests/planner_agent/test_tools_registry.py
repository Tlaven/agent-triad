from src.planner_agent.tools import get_planner_tools


def test_get_planner_tools_returns_safe_subset():
    tools = get_planner_tools()
    tool_names = [getattr(t, "name", "") for t in tools]
    assert tool_names == ["read_workspace_text_file", "list_workspace_entries"]
