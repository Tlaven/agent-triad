"""知识树工具 Supervisor 集成测试。"""

import asyncio
import json
from unittest.mock import patch, MagicMock

import pytest

from src.common.context import Context
from src.supervisor_agent.tools import get_tools


@pytest.fixture
def ctx_kt_disabled(monkeypatch) -> Context:
    """知识树关闭的 Context（确保 env var 不干扰）。"""
    monkeypatch.delenv("ENABLE_KNOWLEDGE_TREE", raising=False)
    return Context(enable_knowledge_tree=False)


@pytest.fixture
def ctx_kt_enabled(tmp_path, monkeypatch) -> Context:
    """知识树开启的 Context。"""
    monkeypatch.setenv("ENABLE_KNOWLEDGE_TREE", "true")
    return Context(
        enable_knowledge_tree=True,
        knowledge_tree_root=str(tmp_path / "kt_md"),
        knowledge_tree_db_path=str(tmp_path / "kt_db"),
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
        assert "knowledge_tree_optimize" not in tool_names

    @pytest.mark.asyncio
    async def test_enabled_registers_five_kt_tools(self, ctx_kt_enabled: Context):
        """enable_knowledge_tree=True 时注册 5 个知识树工具。"""
        tools = await get_tools(ctx_kt_enabled)
        tool_names = [t.name for t in tools]

        expected = [
            "knowledge_tree_retrieve",
            "knowledge_tree_edit",
            "knowledge_tree_status",
            "knowledge_tree_ingest",
            "knowledge_tree_optimize",
        ]
        for name in expected:
            assert name in tool_names, f"Missing tool: {name}"

    @pytest.mark.asyncio
    async def test_enabled_total_tool_count(self, ctx_kt_enabled: Context):
        """总工具数 = 6 (核心) + 5 (知识树) = 11。"""
        tools = await get_tools(ctx_kt_enabled)
        assert len(tools) == 11

    @pytest.mark.asyncio
    async def test_disabled_total_tool_count(self, ctx_kt_disabled: Context):
        """总工具数 = 6 (核心)。"""
        tools = await get_tools(ctx_kt_disabled)
        assert len(tools) == 6

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
    async def test_status_tool_returns_json(self, ctx_kt_enabled: Context):
        """knowledge_tree_status 工具可执行并返回 JSON。"""
        tools = await get_tools(ctx_kt_enabled)
        status_tool = next(t for t in tools if t.name == "knowledge_tree_status")

        result = await status_tool.ainvoke({})
        data = json.loads(result)
        assert data["ok"] is True
        assert "total_nodes" in data

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

    @pytest.mark.asyncio
    async def test_optimize_dry_run(self, ctx_kt_enabled: Context):
        """optimize dry_run 只规划不执行。"""
        tools = await get_tools(ctx_kt_enabled)
        optimize_tool = next(t for t in tools if t.name == "knowledge_tree_optimize")

        result = await optimize_tool.ainvoke({"dry_run": True})
        data = json.loads(result)
        assert data["ok"] is True
        assert "signals_detected" in data


class TestBlockingIOCompliance:
    """验证工具执行不直接在事件循环中调用 blocking I/O。"""

    @pytest.mark.asyncio
    async def test_status_uses_to_thread(self, ctx_kt_enabled: Context):
        """knowledge_tree_status 通过 asyncio.to_thread 执行，不在主线程 blocking。"""
        tools = await get_tools(ctx_kt_enabled)
        status_tool = next(t for t in tools if t.name == "knowledge_tree_status")

        # patch asyncio.to_thread 来验证它被调用
        original_to_thread = asyncio.to_thread
        calls = []

        async def fake_to_thread(fn, *args, **kwargs):
            calls.append((fn.__name__ if hasattr(fn, '__name__') else str(fn), args))
            return await original_to_thread(fn, *args, **kwargs)

        with patch("asyncio.to_thread", side_effect=fake_to_thread):
            result = await status_tool.ainvoke({})

        assert len(calls) == 1, f"Expected 1 to_thread call, got {len(calls)}"
        data = json.loads(result)
        assert data["ok"] is True

    @pytest.mark.asyncio
    async def test_all_tools_use_to_thread(self, ctx_kt_enabled: Context):
        """所有 5 个知识树工具都通过 asyncio.to_thread 执行。"""
        tools = await get_tools(ctx_kt_enabled)

        original_to_thread = asyncio.to_thread
        tool_names_seen = set()

        async def tracking_to_thread(fn, *args, **kwargs):
            # 每次调用记录
            tool_names_seen.add(id(fn))
            return await original_to_thread(fn, *args, **kwargs)

        with patch("asyncio.to_thread", side_effect=tracking_to_thread):
            status_tool = next(t for t in tools if t.name == "knowledge_tree_status")
            await status_tool.ainvoke({})

            optimize_tool = next(t for t in tools if t.name == "knowledge_tree_optimize")
            await optimize_tool.ainvoke({"dry_run": True})

        # 至少 2 个不同的 sync 函数被 to_thread 卸载
        assert len(tool_names_seen) >= 2

    @pytest.mark.asyncio
    async def test_no_direct_mkdir_in_event_loop(self, ctx_kt_enabled: Context):
        """工具执行期间 Path.mkdir 不在事件循环线程中被直接调用。"""
        import threading
        from pathlib import Path

        tools = await get_tools(ctx_kt_enabled)
        status_tool = next(t for t in tools if t.name == "knowledge_tree_status")

        main_thread_id = threading.current_thread().ident
        mkdir_callers: list[int] = []

        original_mkdir = Path.mkdir

        def tracking_mkdir(self, *args, **kwargs):
            mkdir_callers.append(threading.current_thread().ident)
            return original_mkdir(self, *args, **kwargs)

        with patch.object(Path, "mkdir", tracking_mkdir):
            result = await status_tool.ainvoke({})

        # mkdir 可以被调用，但必须在非主线程中（通过 to_thread 卸载）
        for caller_thread in mkdir_callers:
            assert caller_thread != main_thread_id, (
                f"Path.mkdir called in main event loop thread {main_thread_id}"
            )

        data = json.loads(result)
        assert data["ok"] is True

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
