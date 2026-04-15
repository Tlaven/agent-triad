"""Integration tests for V2-b MCP (Model Context Protocol) integration."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.common.mcp import get_mcp_client, get_readonly_mcp_tools, MCP_SERVERS
from src.common.context import Context


# ---------------------------------------------------------------------------
# get_mcp_client — creation and error handling
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_mcp_client_with_server_configs():
    """get_mcp_client forwards server config dict to MultiServerMCPClient."""
    server_configs = {"test_server": {"url": "http://test.com/mcp", "transport": "http"}}

    with patch("src.common.mcp.MultiServerMCPClient") as mock_cls:
        mock_cls.return_value = MagicMock()
        client = await get_mcp_client(server_configs)

    assert client is not None
    mock_cls.assert_called_once_with(server_configs)


@pytest.mark.parametrize("exc", [TimeoutError("timeout"), Exception("Invalid URL"), OSError("Network error")])
@pytest.mark.asyncio
async def test_get_mcp_client_returns_none_on_error(exc):
    """get_mcp_client returns None for any connection error."""
    with patch("src.common.mcp.MultiServerMCPClient", side_effect=exc):
        client = await get_mcp_client({"test": {"url": "http://example.com", "transport": "http"}})
    assert client is None


# ---------------------------------------------------------------------------
# get_readonly_mcp_tools — switch combinations
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_readonly_mcp_tools_all_disabled_returns_empty():
    """With both MCP switches off, get_readonly_mcp_tools returns empty list."""
    ctx = Context(enable_deepwiki=False, enable_filesystem_mcp=False)
    tools = await get_readonly_mcp_tools(ctx)
    assert tools == []


@pytest.mark.parametrize("deepwiki,filesystem", [
    (True, False),
    (False, True),
    (True, True),
])
@pytest.mark.asyncio
async def test_get_readonly_mcp_tools_enabled_combo(deepwiki, filesystem):
    """When MCP is enabled, get_readonly_mcp_tools returns a list (even if empty from mock)."""
    ctx = Context(enable_deepwiki=deepwiki, enable_filesystem_mcp=filesystem)

    with patch("src.common.mcp.get_mcp_client") as mock_get_client:
        mock_client = MagicMock()
        mock_client.get_tools = AsyncMock(return_value=[])
        mock_get_client.return_value = mock_client

        tools = await get_readonly_mcp_tools(ctx)
    assert isinstance(tools, list)


# ---------------------------------------------------------------------------
# MCP_SERVERS configuration contract
# ---------------------------------------------------------------------------

def test_mcp_deepwiki_config_url_and_transport():
    """DeepWiki MCP entry has correct URL and transport type."""
    assert "deepwiki" in MCP_SERVERS
    config = MCP_SERVERS["deepwiki"]
    assert config["url"] == "https://mcp.deepwiki.com/mcp"
    assert config["transport"] == "streamable_http"


def test_mcp_all_servers_have_required_fields():
    """Every MCP server entry has 'url' and 'transport' string fields."""
    for name, cfg in MCP_SERVERS.items():
        assert "url" in cfg and isinstance(cfg["url"], str), f"Missing url in {name}"
        assert "transport" in cfg and isinstance(cfg["transport"], str), f"Missing transport in {name}"
        assert cfg["url"].startswith("http"), f"URL not http in {name}"


# ---------------------------------------------------------------------------
# Context MCP configuration contract
# ---------------------------------------------------------------------------

def test_context_mcp_fields_default_to_disabled():
    """Context MCP fields are False by default."""
    ctx = Context()
    assert ctx.enable_deepwiki is False
    assert ctx.enable_filesystem_mcp is False


def test_context_mcp_fields_can_be_enabled():
    """Context MCP fields can be explicitly enabled."""
    ctx = Context(enable_deepwiki=True, enable_filesystem_mcp=True)
    assert ctx.enable_deepwiki is True
    assert ctx.enable_filesystem_mcp is True
