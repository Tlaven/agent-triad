"""Unit tests for executor_agent.tools capability documentation helpers."""

from src.executor_agent.tools import get_executor_capabilities_docs, get_executor_tools


def test_get_executor_tools_returns_two_tools() -> None:
    tools = get_executor_tools()
    assert len(tools) == 2


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
