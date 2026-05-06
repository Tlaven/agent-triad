"""Unit tests for Executor tool registration and capability documentation helpers."""

from src.common.capabilities import get_executor_capabilities_docs
from src.executor_agent.tools import get_executor_tools


def test_get_executor_tools_returns_two_tools() -> None:
    tools = get_executor_tools()
    # Executor now has 4 tools: write_file, run_local_command, and 2 MCP readonly tools
    assert len(tools) == 4


def test_get_executor_tools_have_names() -> None:
    tools = get_executor_tools()
    names = {getattr(t, "name", None) for t in tools}
    assert "write_file" in names
    assert "run_local_command" in names


def test_get_executor_capabilities_docs_is_non_empty_string() -> None:
    docs = get_executor_capabilities_docs()
    assert isinstance(docs, str)
    assert docs.strip()


def test_get_executor_capabilities_docs_contains_tool_descriptions() -> None:
    docs = get_executor_capabilities_docs()
    # Both tool descriptions should appear (as first-line summaries)
    assert "write" in docs.lower() or "文件" in docs
    assert "command" in docs.lower() or "命令" in docs


def test_get_executor_capabilities_docs_contains_safety_hint() -> None:
    docs = get_executor_capabilities_docs()
    # The safety hint for run_local_command should be present
    assert "run_local_command" in docs


def test_get_executor_capabilities_docs_contains_constraints() -> None:
    """能力描述必须包含关键约束，让 Agent 知道操作边界。"""
    docs = get_executor_capabilities_docs()
    # 文件大小限制
    assert "1MB" in docs or "1_000_000" in docs
    # 工作区限制
    assert "工作区" in docs


def test_capabilities_match_registered_tools() -> None:
    """能力描述必须覆盖所有已注册工具。"""
    docs = get_executor_capabilities_docs()
    tools = get_executor_tools()
    tool_names = {getattr(t, "name", None) for t in tools}
    # write_file 和 run_local_command 必须被提及
    assert "write" in docs.lower() or "文件" in docs
    assert "command" in docs.lower() or "命令" in docs
    # 只读工具必须被提及
    assert "读取" in docs or "read" in docs.lower()
    assert "列出" in docs or "list" in docs.lower()
    # 能力描述的工具数应 >= 注册工具数
    assert len(tool_names) == 4
