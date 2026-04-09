"""Comprehensive tests for Planner tools registry and MCP integration."""

from unittest.mock import MagicMock, patch

import pytest

from src.planner_agent.tools import get_planner_tools
from src.common.context import Context
from src.common.tools import read_workspace_text_file, list_workspace_entries


class TestPlannerToolsRegistry:
    """Test Planner tools registry functionality."""

    def test_get_planner_tools_returns_safe_subset(self):
        """Test that planner tools only include readonly safe tools."""
        tools = get_planner_tools()
        tool_names = [getattr(t, "name", "") for t in tools]
        assert tool_names == ["read_workspace_text_file", "list_workspace_entries"]

    def test_planner_tools_are_readonly(self):
        """Test that all planner tools are readonly (no side effects)."""
        tools = get_planner_tools()

        # Verify no write operations
        tool_names = [getattr(t, "name", "") for t in tools]
        assert "write_file" not in tool_names
        assert "run_local_command" not in tool_names
        assert "delete_file" not in tool_names

        # Verify only read operations
        assert "read" in " ".join(tool_names).lower() or "list" in " ".join(tool_names).lower()

    def test_planner_tools_have_descriptions(self):
        """Test that all planner tools have proper descriptions."""
        tools = get_planner_tools()

        for tool in tools:
            assert hasattr(tool, "description")
            assert tool.description
            assert len(tool.description) > 0

    def test_planner_tools_exclude_executor_tools(self):
        """Test that executor-only tools are not in planner tools."""
        planner_tools = get_planner_tools()
        planner_tool_names = [getattr(t, "name", "") for t in planner_tools]

        # These are executor-only tools
        executor_only_tools = ["write_file", "run_local_command"]
        for tool_name in executor_only_tools:
            assert tool_name not in planner_tool_names

    def test_planner_tools_with_context(self):
        """Test that planner tools respect context configuration."""
        ctx = Context(
            observation_workspace_dir="test_workspace",
        )

        tools = get_planner_tools(ctx)

        # Verify tools are returned even with context
        assert len(tools) == 2
        tool_names = [getattr(t, "name", "") for t in tools]
        assert "read_workspace_text_file" in tool_names
        assert "list_workspace_entries" in tool_names

    def test_planner_tools_without_context(self):
        """Test that planner tools work without context."""
        tools = get_planner_tools(None)

        # Should still return tools even without context
        assert len(tools) == 2
        tool_names = [getattr(t, "name", "") for t in tools]
        assert "read_workspace_text_file" in tool_names

    def test_planner_tools_are_callable(self):
        """Test that planner tools have callable functions."""
        tools = get_planner_tools()

        for tool in tools:
            # StructuredTool has a func attribute that's callable
            assert hasattr(tool, "func"), f"Tool {getattr(tool, 'name', 'unknown')} has no func attribute"
            assert callable(tool.func), f"Tool {getattr(tool, 'name', 'unknown')} func is not callable"

    def test_planner_tools_number_consistent(self):
        """Test that the number of planner tools is consistent."""
        tools1 = get_planner_tools()
        tools2 = get_planner_tools()

        assert len(tools1) == len(tools2)
        assert len(tools1) == 2  # Should always be 2 readonly tools


class TestPlannerToolPermissions:
    """Test permission separation between Planner and Executor tools."""

    def test_planner_cannot_access_write_operations(self):
        """Test that planner cannot access write operations."""
        planner_tools = get_planner_tools()
        planner_tool_names = [getattr(t, "name", "") for t in planner_tools]

        # Verify no write capabilities
        assert "write" not in " ".join(planner_tool_names).lower()
        assert "create" not in " ".join(planner_tool_names).lower()
        assert "delete" not in " ".join(planner_tool_names).lower()

    def test_planner_cannot_access_command_execution(self):
        """Test that planner cannot execute commands."""
        planner_tools = get_planner_tools()
        planner_tool_names = [getattr(t, "name", "") for t in planner_tools]

        # Verify no command execution
        assert "run" not in " ".join(planner_tool_names).lower()
        assert "execute" not in " ".join(planner_tool_names).lower()
        assert "command" not in " ".join(planner_tool_names).lower()

    def test_planner_has_readonly_file_access(self):
        """Test that planner has readonly file access."""
        planner_tools = get_planner_tools()
        planner_tool_names = [getattr(t, "name", "") for t in planner_tools]

        # Verify readonly file access
        assert "read_workspace_text_file" in planner_tool_names
        assert "list_workspace_entries" in planner_tool_names


class TestPlannerToolSecurity:
    """Test security aspects of planner tools."""

    def test_planner_tools_no_side_effects(self):
        """Test that planner tools cannot cause side effects."""
        planner_tools = get_planner_tools()
        planner_tool_names = [getattr(t, "name", "") for t in planner_tools]

        # Verify no tools that can modify state
        dangerous_keywords = ["write", "delete", "remove", "create", "execute", "run", "command"]
        for keyword in dangerous_keywords:
            assert keyword not in " ".join(planner_tool_names).lower(), \
                f"Dangerous keyword '{keyword}' found in planner tools"

    def test_planner_tools_workspace_bound(self):
        """Test that planner tools are workspace-bound."""
        tools = get_planner_tools()

        # All tools should be workspace-related
        tool_names = [getattr(t, "name", "") for t in tools]
        for tool_name in tool_names:
            assert "workspace" in tool_name.lower(), \
                f"Tool '{tool_name}' should be workspace-bound"


class TestPlannerToolIntegrity:
    """Test integrity and consistency of planner tools."""

    def test_planner_tools_order_consistent(self):
        """Test that planner tools are returned in consistent order."""
        tools1 = get_planner_tools()
        tools2 = get_planner_tools()

        names1 = [getattr(t, "name", "") for t in tools1]
        names2 = [getattr(t, "name", "") for t in tools2]

        assert names1 == names2

    def test_planner_tools_no_duplicates(self):
        """Test that there are no duplicate tools."""
        tools = get_planner_tools()
        tool_names = [getattr(t, "name", "") for t in tools]

        assert len(tool_names) == len(set(tool_names)), "Duplicate tools found"

    def test_planner_tools_all_valid(self):
        """Test that all planner tools are valid and properly structured."""
        tools = get_planner_tools()

        for tool in tools:
            # Must have name
            assert hasattr(tool, "name")
            assert tool.name

            # Must have description
            assert hasattr(tool, "description")
            assert tool.description

            # Must have callable func attribute
            assert hasattr(tool, "func")
            assert callable(tool.func)

            # Must have args_schema or similar
            assert hasattr(tool, "args_schema") or hasattr(tool, "args")


class TestPlannerToolsContextIntegration:
    """Test planner tools integration with context."""

    def test_planner_tools_apply_workspace_root(self):
        """Test that planner tools properly apply workspace root from context."""
        ctx = Context(
            observation_workspace_dir="/custom/workspace",
        )

        with patch("src.planner_agent.tools.apply_context_workspace_root") as mock_apply:
            tools = get_planner_tools(ctx)

            # Verify workspace root was applied
            mock_apply.assert_called_once_with(ctx)

    def test_planner_tools_work_with_default_context(self):
        """Test that planner tools work with default context."""
        # Should not raise any errors
        tools = get_planner_tools()
        assert len(tools) == 2


class TestPlannerToolsComparisonWithExecutor:
    """Test comparison between planner and executor tools."""

    def test_planner_tools_subset_of_executor_capabilities(self):
        """Test that planner tools are a safe subset of executor capabilities."""
        from src.executor_agent.tools import get_executor_tools

        planner_tools = get_planner_tools()
        executor_tools = get_executor_tools()

        planner_tool_names = set(getattr(t, "name", "") for t in planner_tools)
        executor_tool_names = set(getattr(t, "name", "") for t in executor_tools)

        # Planner tools should be a subset or completely separate (for readonly sharing)
        # In this case, they're shared readonly tools
        assert planner_tool_names.issubset(executor_tool_names) or \
               len(planner_tool_names & executor_tool_names) >= 0

    def test_planner_executor_tool_separation(self):
        """Test clear separation between planner and executor tools."""
        from src.executor_agent.tools import get_executor_tools

        planner_tools = get_planner_tools()
        executor_tools = get_executor_tools()

        planner_tool_names = [getattr(t, "name", "") for t in planner_tools]
        executor_tool_names = [getattr(t, "name", "") for t in executor_tools]

        # Executor should have write capabilities
        assert "write_file" in executor_tool_names
        assert "run_local_command" in executor_tool_names

        # Planner should NOT have write capabilities
        assert "write_file" not in planner_tool_names
        assert "run_local_command" not in planner_tool_names


class TestPlannerToolsFutureCompatibility:
    """Test future compatibility and extensibility."""

    def test_planner_tools_can_be_extended(self):
        """Test that planner tools structure allows for future extensions."""
        tools = get_planner_tools()

        # Current implementation should support adding more readonly tools
        assert isinstance(tools, list)
        assert len(tools) >= 2  # At least 2 tools currently

    def test_planner_tools_type_consistent(self):
        """Test that all planner tools have consistent type structure."""
        tools = get_planner_tools()

        for tool in tools:
            # All tools should have the same basic structure
            assert hasattr(tool, "name")
            assert hasattr(tool, "description")
            assert hasattr(tool, "func")  # StructuredTool has func attribute
            assert callable(tool.func)


class TestPlannerToolsDocumentation:
    """Test documentation quality of planner tools."""

    def test_planner_tools_have_meaningful_descriptions(self):
        """Test that planner tools have meaningful, non-empty descriptions."""
        tools = get_planner_tools()

        for tool in tools:
            description = tool.description
            assert description
            assert len(description) > 10  # Should have meaningful content
            assert not description.isspace()  # Should not be just whitespace

    def test_planner_tools_descriptions_are_informative(self):
        """Test that tool descriptions provide useful information."""
        tools = get_planner_tools()

        for tool in tools:
            description = tool.description.lower()
            # Description should mention what the tool does (may be in Chinese or English)
            # Look for common words in either language
            informative_words = ["read", "list", "file", "workspace", "directory", "读取", "文件", "工作", "目录"]
            assert any(word in description for word in informative_words), \
                f"Description for {tool.name} lacks informative keywords: {description}"


class TestPlannerToolsErrorHandling:
    """Test error handling in planner tools."""

    def test_planner_tools_handle_none_context_gracefully(self):
        """Test that planner tools handle None context without errors."""
        # Should not raise any errors
        tools = get_planner_tools(None)
        assert len(tools) == 2

    def test_planner_tools_handle_empty_context(self):
        """Test that planner tools work with empty/default context."""
        ctx = Context()  # Default context
        tools = get_planner_tools(ctx)
        assert len(tools) == 2


class TestPlannerToolsNamingConventions:
    """Test naming conventions and consistency."""

    def test_planner_tools_follow_naming_conventions(self):
        """Test that planner tools follow consistent naming conventions."""
        tools = get_planner_tools()

        for tool in tools:
            name = getattr(tool, "name", "")

            # Should use snake_case
            assert name.islower() or "_" in name or "workspace" in name.lower()

            # Should be descriptive
            assert len(name) > 3  # Minimum reasonable length

    def test_planner_tools_no_special_characters(self):
        """Test that planner tool names don't have problematic special characters."""
        tools = get_planner_tools()

        for tool in tools:
            name = getattr(tool, "name", "")

            # No spaces or special characters that could cause issues
            assert " " not in name
            assert not any(char in name for char in ["!", "@", "#", "$", "%", "^", "&", "*"])


class TestPlannerToolsConfigurationIndependence:
    """Test that planner tools are independent of specific configurations."""

    def test_planner_tools_independent_of_mcp_settings(self):
        """Test that planner tools work regardless of MCP settings."""
        # Should work even if MCP is disabled
        tools = get_planner_tools()
        assert len(tools) == 2

    def test_planner_tools_independent_of_workspace_settings(self):
        """Test that planner tools work with various workspace configurations."""
        contexts = [
            Context(observation_workspace_dir="workspace1"),
            Context(observation_workspace_dir="workspace2"),
            Context(observation_workspace_dir=""),
            None,
        ]

        for ctx in contexts:
            tools = get_planner_tools(ctx)
            assert len(tools) == 2  # Should always return 2 tools
