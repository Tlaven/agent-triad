"""知识树工具 Supervisor 集成测试。"""

import asyncio
import json
from unittest.mock import patch

import pytest

from src.common.context import Context
from src.supervisor_agent.tools import get_tools


@pytest.fixture
def ctx_kt_disabled(monkeypatch) -> Context:
    """知识树关闭的 Context。"""
    monkeypatch.delenv("ENABLE_KNOWLEDGE_TREE", raising=False)
    return Context(enable_knowledge_tree=False)


@pytest.fixture
def ctx_kt_enabled(tmp_path, monkeypatch) -> Context:
    """知识树开启的 Context。"""
    monkeypatch.setenv("ENABLE_KNOWLEDGE_TREE", "true")
    return Context(
        enable_knowledge_tree=True,
        knowledge_tree_root=str(tmp_path / "kt_md"),
    )


class TestKnowledgeTreeToolRegistration:
    """验证知识树工具的条件注册。"""

    @pytest.mark.asyncio
    async def test_disabled_no_kt_tools(self, ctx_kt_disabled: Context):
        """enable_knowledge_tree=False 时不注册知识树工具。"""
        tools = await get_tools(ctx_kt_disabled)
        tool_names = [t.name for t in tools]
        assert "knowledge_tree_retrieve" not in tool_names
        assert "knowledge_tree_ingest" not in tool_names

    @pytest.mark.asyncio
    async def test_enabled_registers_two_kt_tools(self, ctx_kt_enabled: Context):
        """enable_knowledge_tree=True 时注册 retrieve + ingest 两个工具。"""
        tools = await get_tools(ctx_kt_enabled)
        tool_names = [t.name for t in tools]

        expected = [
            "knowledge_tree_retrieve",
            "knowledge_tree_ingest",
        ]
        for name in expected:
            assert name in tool_names, f"Missing tool: {name}"

        # bootstrap/status 已移除
        assert "knowledge_tree_bootstrap" not in tool_names
        assert "knowledge_tree_status" not in tool_names

    @pytest.mark.asyncio
    async def test_enabled_total_tool_count(self, ctx_kt_enabled: Context):
        """总工具数 = 3 (核心) + 2 (知识树) = 5。"""
        tools = await get_tools(ctx_kt_enabled)
        assert len(tools) == 5

    @pytest.mark.asyncio
    async def test_disabled_total_tool_count(self, ctx_kt_disabled: Context):
        """总工具数 = 3 (核心)。"""
        tools = await get_tools(ctx_kt_disabled)
        assert len(tools) == 3

    @pytest.mark.asyncio
    async def test_default_context_env_controls(self, monkeypatch):
        """默认 Context 工具注册由 ENABLE_KNOWLEDGE_TREE env var 决定。"""
        monkeypatch.delenv("ENABLE_KNOWLEDGE_TREE", raising=False)
        tools = await get_tools()
        tool_names = [t.name for t in tools]
        assert "knowledge_tree_retrieve" not in tool_names


class TestKnowledgeTreeToolExecution:
    """验证知识树工具在 Supervisor 上下文中可执行。"""

    @pytest.mark.asyncio
    async def test_ingest_and_retrieve(self, ctx_kt_enabled: Context):
        """ingest + retrieve 闭环在 Supervisor 工具中可用。"""
        tools = await get_tools(ctx_kt_enabled)
        ingest_tool = next(t for t in tools if t.name == "knowledge_tree_ingest")
        retrieve_tool = next(t for t in tools if t.name == "knowledge_tree_retrieve")

        ingest_result = await ingest_tool.ainvoke({
            "text": "架构原则：系统应遵循模块化设计，各组件通过明确接口通信。",
            "trigger": "user_explicit",
        })
        ingest_data = json.loads(ingest_result)
        assert "ok" in ingest_data


class TestBlockingIOCompliance:
    """验证工具执行不直接在事件循环中调用 blocking I/O。"""

    @pytest.mark.asyncio
    async def test_ingest_file_io_offloaded(self, ctx_kt_enabled: Context):
        """ingest 工具的文件写入也被卸载到线程。"""
        import threading
        from pathlib import Path

        tools = await get_tools(ctx_kt_enabled)
        ingest_tool = next(t for t in tools if t.name == "knowledge_tree_ingest")

        main_thread_id = threading.current_thread().ident
        write_callers: list[int] = []

        original_write_text = Path.write_text

        def tracking_write_text(self, *args, **kwargs):
            write_callers.append(threading.current_thread().ident)
            return original_write_text(self, *args, **kwargs)

        with patch.object(Path, "write_text", tracking_write_text):
            result = await ingest_tool.ainvoke({
                "text": "测试知识摄入",
                "trigger": "user_explicit",
            })

        for caller_thread in write_callers:
            assert caller_thread != main_thread_id, (
                "Path.write_text called in main event loop thread"
            )
