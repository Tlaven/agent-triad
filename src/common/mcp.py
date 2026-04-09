"""MCP 客户端设置和管理模块，用于 LangGraph ReAct Agent。"""

import logging
from typing import Any, Callable, Dict, List, Optional, cast

from langchain_mcp_adapters.client import (  # type: ignore[import-untyped]
    MultiServerMCPClient,
)

logger = logging.getLogger(__name__)

# 全局 MCP 客户端和工具缓存
_mcp_client: Optional[MultiServerMCPClient] = None
_mcp_tools_cache: Dict[str, List[Callable[..., Any]]] = {}

# MCP 服务器配置
MCP_SERVERS = {
    "deepwiki": {
        "url": "https://mcp.deepwiki.com/mcp",
        "transport": "streamable_http",
    },
    # 如需添加更多 MCP 服务器，请在此处添加
    # "context7": {
    #     "url": "https://mcp.context7.com/sse",
    #     "transport": "sse",
    # },
}


async def get_mcp_client(
    server_configs: Optional[Dict[str, Any]] = None,
) -> Optional[MultiServerMCPClient]:
    """获取或初始化 MCP 客户端。

    如果传入了 server_configs，则为指定的服务器创建一个新的客户端。
    如果未传入 server_configs，则使用全局客户端（包含所有已配置的服务器）。
    """
    global _mcp_client

    # 如果传入了特定的服务器配置，则创建专用客户端
    if server_configs is not None:
        try:
            client = MultiServerMCPClient(server_configs)  # pyright: ignore[reportArgumentType]
            logger.info(f"已创建 MCP 客户端，包含服务器: {list(server_configs.keys())}")
            return client
        except Exception as e:
            logger.error(f"创建 MCP 客户端失败: {e}")
            return None

    # 否则使用全局客户端（向后兼容）
    if _mcp_client is None:
        try:
            _mcp_client = MultiServerMCPClient(MCP_SERVERS)  # pyright: ignore[reportArgumentType]
            logger.info(f"已初始化全局 MCP 客户端，包含服务器: {list(MCP_SERVERS.keys())}")
        except Exception as e:
            logger.error(f"初始化全局 MCP 客户端失败: {e}")
            return None
    return _mcp_client


async def get_mcp_tools(server_name: str) -> List[Callable[..., Any]]:
    """获取指定 MCP 服务器的工具，会在必要时初始化客户端。"""
    global _mcp_tools_cache

    # 如果缓存中已有，直接返回
    if server_name in _mcp_tools_cache:
        return _mcp_tools_cache[server_name]

    # 检查服务器是否在配置中存在
    if server_name not in MCP_SERVERS:
        logger.warning(f"MCP 服务器 '{server_name}' 未在配置中找到")
        _mcp_tools_cache[server_name] = []
        return []

    try:
        # 为该服务器创建独立的客户端（避免使用全局单例）
        server_config = {server_name: MCP_SERVERS[server_name]}
        client = await get_mcp_client(server_config)
        if client is None:
            _mcp_tools_cache[server_name] = []
            return []

        # 获取该服务器的所有工具
        all_tools = await client.get_tools()
        tools = cast(List[Callable[..., Any]], all_tools)

        _mcp_tools_cache[server_name] = tools
        logger.info(f"已从 MCP 服务器 '{server_name}' 加载 {len(tools)} 个工具")
        return tools
    except Exception as e:
        logger.warning(f"从 MCP 服务器 '{server_name}' 加载工具失败: {e}")
        _mcp_tools_cache[server_name] = []
        return []


async def get_deepwiki_tools() -> List[Callable[..., Any]]:
    """获取 DeepWiki MCP 工具。"""
    return await get_mcp_tools("deepwiki")


async def get_all_mcp_tools() -> List[Callable[..., Any]]:
    """获取所有已配置 MCP 服务器的全部工具。"""
    all_tools = []
    for server_name in MCP_SERVERS.keys():
        tools = await get_mcp_tools(server_name)
        all_tools.extend(tools)
    return all_tools


async def get_readonly_mcp_tools(context: Any = None) -> List[Callable[..., Any]]:
    """获取共享的只读 MCP 工具（Planner 和 Executor 均可绑定这些工具）。

    当前仅聚合外部 MCP（例如 DeepWiki）。
    带有副作用的工具应保持在 Executor 本地，不在此处暴露。
    """
    if context is None:
        return await get_all_mcp_tools()

    tools: List[Callable[..., Any]] = []
    if bool(getattr(context, "enable_deepwiki", False)):
        tools.extend(await get_deepwiki_tools())
    return tools


def add_mcp_server(name: str, config: Dict[str, Any]) -> None:
    """添加一个新的 MCP 服务器配置。"""
    MCP_SERVERS[name] = config
    # 清空客户端缓存，强制使用新配置重新初始化
    clear_mcp_cache()


def remove_mcp_server(name: str) -> None:
    """移除一个 MCP 服务器配置。"""
    if name in MCP_SERVERS:
        del MCP_SERVERS[name]
        # 清空客户端缓存，强制使用新配置重新初始化
        clear_mcp_cache()


def clear_mcp_cache() -> None:
    """清空 MCP 客户端和工具缓存（主要用于测试）。"""
    global _mcp_client, _mcp_tools_cache
    _mcp_client = None
    _mcp_tools_cache = {}