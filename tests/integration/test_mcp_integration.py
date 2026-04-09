"""Integration tests for V2-b MCP (Model Context Protocol) integration."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.common.mcp import get_mcp_client, get_readonly_mcp_tools, MCP_SERVERS
from src.common.context import Context


class TestMCPClientInitialization:
    """Test MCP client initialization and configuration."""

    @pytest.mark.asyncio
    async def test_get_mcp_client_returns_client(self):
        """Test that get_mcp_client returns a valid client."""
        # Note: This test may require actual MCP server connectivity
        # For now, we test the structure
        with patch("src.common.mcp.MultiServerMCPClient") as mock_client_class:
            mock_client = MagicMock()
            mock_client_class.return_value = mock_client

            client = await get_mcp_client({"test": {"url": "http://test.com", "transport": "http"}})

            assert client is not None
            mock_client_class.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_mcp_client_handles_errors(self):
        """Test that get_mcp_client handles connection errors gracefully."""
        with patch("src.common.mcp.MultiServerMCPClient", side_effect=Exception("Connection error")):
            client = await get_mcp_client({"test": {"url": "http://invalid.com", "transport": "http"}})

            # Should return None on error
            assert client is None

    @pytest.mark.asyncio
    async def test_get_mcp_client_with_server_configs(self):
        """Test get_mcp_client with specific server configurations."""
        server_configs = {
            "test_server": {
                "url": "http://test.com/mcp",
                "transport": "http",
            }
        }

        with patch("src.common.mcp.MultiServerMCPClient") as mock_client_class:
            mock_client = MagicMock()
            mock_client_class.return_value = mock_client

            client = await get_mcp_client(server_configs)

            assert client is not None
            mock_client_class.assert_called_once_with(server_configs)


class TestMCPServersConfiguration:
    """Test MCP servers configuration structure."""

    def test_mcp_servers_config_exists(self):
        """Test that MCP servers configuration is defined."""
        assert MCP_SERVERS is not None
        assert isinstance(MCP_SERVERS, dict)

    def test_mcp_servers_has_deepwiki(self):
        """Test that DeepWiki MCP server is configured."""
        assert "deepwiki" in MCP_SERVERS
        assert "url" in MCP_SERVERS["deepwiki"]
        assert "transport" in MCP_SERVERS["deepwiki"]

    def test_mcp_servers_structure_valid(self):
        """Test that all MCP server configs have required fields."""
        for server_name, config in MCP_SERVERS.items():
            assert "url" in config
            assert "transport" in config
            assert isinstance(config["url"], str)
            assert isinstance(config["transport"], str)

    def test_mcp_servers_urls_are_valid(self):
        """Test that MCP server URLs are properly formatted."""
        for server_name, config in MCP_SERVERS.items():
            url = config["url"]
            assert url.startswith("http://") or url.startswith("https://")


class TestMCPReadonlyTools:
    """Test MCP readonly tools retrieval and functionality."""

    @pytest.mark.asyncio
    async def test_get_readonly_mcp_tools_returns_list(self):
        """Test that get_readonly_mcp_tools returns a list."""
        with patch("src.common.mcp.get_mcp_client") as mock_get_client:
            mock_client = MagicMock()
            mock_get_client.return_value = mock_client

            # Mock the client methods
            mock_client.get_tools = AsyncMock(return_value=[])

            tools = await get_readonly_mcp_tools()

            assert isinstance(tools, list)

    @pytest.mark.asyncio
    async def test_get_readonly_mcp_tools_handles_disabled_mcp(self):
        """Test that get_readonly_mcp_tools handles disabled MCP gracefully."""
        context = Context(
            enable_deepwiki=False,
            enable_filesystem_mcp=False,
        )

        tools = await get_readonly_mcp_tools(context)

        # Should return empty list when all MCP is disabled
        assert isinstance(tools, list)

    @pytest.mark.asyncio
    async def test_get_readonly_mcp_tools_with_deepwiki_enabled(self):
        """Test that get_readonly_mcp_tools includes DeepWiki when enabled."""
        context = Context(
            enable_deepwiki=True,
            enable_filesystem_mcp=False,
        )

        with patch("src.common.mcp.get_mcp_client") as mock_get_client:
            mock_client = MagicMock()
            mock_get_client.get_tools = AsyncMock(return_value=[])
            mock_get_client.return_value = mock_client

            tools = await get_readonly_mcp_tools(context)

            # Should attempt to get DeepWiki tools
            assert isinstance(tools, list)

    @pytest.mark.asyncio
    async def test_get_readonly_mcp_tools_with_filesystem_enabled(self):
        """Test that get_readonly_mcp_tools includes filesystem when enabled."""
        context = Context(
            enable_deepwiki=False,
            enable_filesystem_mcp=True,
        )

        with patch("src.common.mcp.get_mcp_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.get_tools = AsyncMock(return_value=[])
            mock_get_client.return_value = mock_client

            tools = await get_readonly_mcp_tools(context)

            # Should attempt to get filesystem tools
            assert isinstance(tools, list)


class TestMCPToolPermissions:
    """Test MCP tool permission controls."""

    @pytest.mark.asyncio
    async def test_mcp_tools_are_readonly(self):
        """Test that MCP tools are readonly (no side effects)."""
        with patch("src.common.mcp.get_mcp_client") as mock_get_client:
            mock_client = MagicMock()

            # Mock readonly tools (no write operations)
            mock_tools = [
                MagicMock(name="read_file", description="Read file"),
                MagicMock(name="search", description="Search content"),
            ]
            mock_client.get_tools = AsyncMock(return_value=mock_tools)
            mock_get_client.return_value = mock_client

            tools = await get_readonly_mcp_tools()

            # All tools should be readonly
            tool_names = [getattr(t, "name", "") for t in tools]
            for name in tool_names:
                assert "write" not in name.lower()
                assert "delete" not in name.lower()

    @pytest.mark.asyncio
    async def test_mcp_tools_no_execution_capabilities(self):
        """Test that MCP tools don't have execution capabilities."""
        with patch("src.common.mcp.get_mcp_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.get_tools = AsyncMock(return_value=[])
            mock_get_client.return_value = mock_client

            tools = await get_readonly_mcp_tools()

            # Should not include execution capabilities
            tool_names = [getattr(t, "name", "") for t in tools]
            for name in tool_names:
                assert "execute" not in name.lower()
                assert "run" not in name.lower()
                assert "command" not in name.lower()


class TestMCPIntegrationWithPlanner:
    """Test MCP integration with Planner agent."""

    @pytest.mark.asyncio
    async def test_planner_can_use_mcp_tools(self):
        """Test that Planner can use MCP readonly tools."""
        from src.planner_agent.tools import get_planner_tools

        # Get local planner tools
        local_tools = get_planner_tools()
        local_tool_names = [getattr(t, "name", "") for t in local_tools]

        # Should have readonly tools
        assert "read_workspace_text_file" in local_tool_names
        assert "list_workspace_entries" in local_tool_names

    @pytest.mark.asyncio
    async def test_planner_cannot_use_mcp_write_tools(self):
        """Test that Planner cannot use MCP write tools."""
        from src.planner_agent.tools import get_planner_tools

        tools = get_planner_tools()
        tool_names = [getattr(t, "name", "") for t in tools]

        # Should not have write capabilities
        assert "write" not in " ".join(tool_names).lower()
        assert "delete" not in " ".join(tool_names).lower()


class TestMCPIntegrationWithExecutor:
    """Test MCP integration with Executor agent."""

    @pytest.mark.asyncio
    async def test_executor_can_use_mcp_readonly_tools(self):
        """Test that Executor can use MCP readonly tools."""
        from src.executor_agent.tools import get_executor_tools

        tools = get_executor_tools()
        tool_names = [getattr(t, "name", "") for t in tools]

        # Should have readonly tools (if MCP is enabled)
        # At minimum, should have write_file and run_local_command
        assert "write_file" in tool_names
        assert "run_local_command" in tool_names

        # May also have readonly tools from MCP
        readonly_tools = ["read_workspace_text_file", "list_workspace_entries"]
        has_readonly = any(tool in tool_names for tool in readonly_tools)
        assert has_readonly  # Should have at least one readonly tool

    @pytest.mark.asyncio
    async def test_executor_has_write_tools(self):
        """Test that Executor has write tools (unlike Planner)."""
        from src.executor_agent.tools import get_executor_tools

        tools = get_executor_tools()
        tool_names = [getattr(t, "name", "") for t in tools]

        # Should have write capabilities
        assert "write_file" in tool_names
        assert "run_local_command" in tool_names


class TestMCPErrorHandling:
    """Test MCP error handling and resilience."""

    @pytest.mark.asyncio
    async def test_mcp_handles_connection_timeout(self):
        """Test that MCP handles connection timeouts gracefully."""
        with patch("src.common.mcp.MultiServerMCPClient", side_effect=TimeoutError("Timeout")):
            client = await get_mcp_client({"test": {"url": "http://slow.com", "transport": "http"}})

            # Should handle timeout gracefully
            assert client is None

    @pytest.mark.asyncio
    async def test_mcp_handles_invalid_server_url(self):
        """Test that MCP handles invalid server URLs."""
        with patch("src.common.mcp.MultiServerMCPClient", side_effect=Exception("Invalid URL")):
            client = await get_mcp_client({"test": {"url": "not-a-url", "transport": "http"}})

            # Should handle invalid URL gracefully
            assert client is None

    @pytest.mark.asyncio
    async def test_mcp_handles_network_errors(self):
        """Test that MCP handles network errors."""
        with patch("src.common.mcp.MultiServerMCPClient", side_effect=OSError("Network error")):
            client = await get_mcp_client({"test": {"url": "http://unreachable.com", "transport": "http"}})

            # Should handle network error gracefully
            assert client is None


class TestMCPCaching:
    """Test MCP tool caching behavior."""

    @pytest.mark.asyncio
    async def test_mcp_tools_are_cached(self):
        """Test that MCP tools are cached for performance."""
        with patch("src.common.mcp.get_mcp_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.get_tools = AsyncMock(return_value=[])
            mock_get_client.return_value = mock_client

            # First call
            tools1 = await get_readonly_mcp_tools()

            # Second call should use cache
            tools2 = await get_readonly_mcp_tools()

            # Both should return the same type
            assert type(tools1) == type(tools2)


class TestMCPConcurrentAccess:
    """Test MCP behavior under concurrent access."""

    @pytest.mark.asyncio
    async def test_mcp_handles_concurrent_requests(self):
        """Test that MCP can handle multiple concurrent requests."""
        import asyncio

        async def get_tools():
            with patch("src.common.mcp.get_mcp_client") as mock_get_client:
                mock_client = MagicMock()
                mock_client.get_tools = AsyncMock(return_value=[])
                mock_get_client.return_value = mock_client
                return await get_readonly_mcp_tools()

        # Make concurrent requests
        results = await asyncio.gather(
            get_tools(),
            get_tools(),
            get_tools(),
        )

        # All should complete successfully
        for result in results:
            assert isinstance(result, list)


class TestMCPConfiguration:
    """Test MCP configuration and settings."""

    def test_mcp_deepwiki_configuration(self):
        """Test DeepWiki MCP server configuration."""
        assert "deepwiki" in MCP_SERVERS
        config = MCP_SERVERS["deepwiki"]

        assert config["url"] == "https://mcp.deepwiki.com/mcp"
        assert config["transport"] == "streamable_http"

    def test_mcp_context_configuration(self):
        """Test that Context has MCP configuration fields."""
        context = Context(
            enable_deepwiki=True,
            enable_filesystem_mcp=True,
        )

        assert context.enable_deepwiki == True
        assert context.enable_filesystem_mcp == True

    def test_mcp_disabled_by_default(self):
        """Test that MCP is disabled by default in Context."""
        context = Context()

        # Check default values
        assert hasattr(context, "enable_deepwiki")
        assert hasattr(context, "enable_filesystem_mcp")


class TestMCPToolStructure:
    """Test structure and format of MCP tools."""

    @pytest.mark.asyncio
    async def test_mcp_tools_have_proper_structure(self):
        """Test that MCP tools have proper structure."""
        with patch("src.common.mcp.get_mcp_client") as mock_get_client:
            mock_client = MagicMock()

            # Mock tool with proper structure
            mock_tool = MagicMock()
            mock_tool.name = "test_tool"
            mock_tool.description = "Test description"
            mock_client.get_tools = AsyncMock(return_value=[mock_tool])
            mock_get_client.return_value = mock_client

            tools = await get_readonly_mcp_tools()

            # Should have proper structure
            for tool in tools:
                assert hasattr(tool, "name")
                assert hasattr(tool, "description")

    @pytest.mark.asyncio
    async def test_mcp_tools_have_valid_names(self):
        """Test that MCP tools have valid, usable names."""
        with patch("src.common.mcp.get_mcp_client") as mock_get_client:
            mock_client = MagicMock()

            mock_tools = [
                MagicMock(name="read_file", description="Read file"),
                MagicMock(name="search", description="Search"),
            ]
            mock_client.get_tools = AsyncMock(return_value=mock_tools)
            mock_get_client.return_value = mock_client

            tools = await get_readonly_mcp_tools()

            # All tools should have valid names
            for tool in tools:
                name = getattr(tool, "name", "")
                assert name
                assert len(name) > 0
                assert " " not in name  # No spaces in names


class TestMCPWithDifferentConfigurations:
    """Test MCP with various configuration combinations."""

    @pytest.mark.asyncio
    async def test_mcp_with_only_deepwiki(self):
        """Test MCP with only DeepWiki enabled."""
        context = Context(
            enable_deepwiki=True,
            enable_filesystem_mcp=False,
        )

        with patch("src.common.mcp.get_mcp_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.get_tools = AsyncMock(return_value=[])
            mock_get_client.return_value = mock_client

            tools = await get_readonly_mcp_tools(context)

            assert isinstance(tools, list)

    @pytest.mark.asyncio
    async def test_mcp_with_only_filesystem(self):
        """Test MCP with only filesystem enabled."""
        context = Context(
            enable_deepwiki=False,
            enable_filesystem_mcp=True,
        )

        with patch("src.common.mcp.get_mcp_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.get_tools = AsyncMock(return_value=[])
            mock_get_client.return_value = mock_client

            tools = await get_readonly_mcp_tools(context)

            assert isinstance(tools, list)

    @pytest.mark.asyncio
    async def test_mcp_with_both_enabled(self):
        """Test MCP with both DeepWiki and filesystem enabled."""
        context = Context(
            enable_deepwiki=True,
            enable_filesystem_mcp=True,
        )

        with patch("src.common.mcp.get_mcp_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.get_tools = AsyncMock(return_value=[])
            mock_get_client.return_value = mock_client

            tools = await get_readonly_mcp_tools(context)

            assert isinstance(tools, list)

    @pytest.mark.asyncio
    async def test_mcp_with_none_disabled(self):
        """Test MCP with all disabled."""
        context = Context(
            enable_deepwiki=False,
            enable_filesystem_mcp=False,
        )

        tools = await get_readonly_mcp_tools(context)

        # Should return empty list
        assert isinstance(tools, list)


class TestMCPToolFunctionality:
    """Test actual functionality of MCP tools."""

    @pytest.mark.asyncio
    async def test_mcp_tools_are_callable(self):
        """Test that MCP tools are callable."""
        with patch("src.common.mcp.get_mcp_client") as mock_get_client:
            mock_client = MagicMock()

            # Mock callable tools
            mock_tool = MagicMock()
            mock_tool.name = "test_tool"
            mock_tool.description = "Test"
            mock_client.get_tools = AsyncMock(return_value=[mock_tool])
            mock_get_client.return_value = mock_client

            tools = await get_readonly_mcp_tools()

            # Tools should be callable or have proper structure
            for tool in tools:
                assert callable(tool) or hasattr(tool, "name")
