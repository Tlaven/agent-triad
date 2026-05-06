"""知识树工具 Supervisor 集成测试。"""

import asyncio
import json
from unittest.mock import MagicMock, patch

import pytest

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from src.common.context import Context
from src.supervisor_agent.state import State
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
        kt_embedding_model="hash",
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

        retrieve_result = await retrieve_tool.ainvoke({
            "query": "模块化设计 明确接口通信",
        })
        retrieve_data = json.loads(retrieve_result)
        assert retrieve_data["ok"] is True
        assert retrieve_data["source"] == "rag"
        assert retrieve_data["node_id"]
        assert "模块化设计" in retrieve_data["content"]


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


class TestAutoIngestFromExecutorResult:
    """验证 Entry A: Executor 完成后自动知识提取。"""

    def test_extract_knowledge_from_completed_result(self):
        """completed executor 结果应触发知识提取。"""
        from src.common.knowledge_tree.ingestion.extractor import (
            extract_knowledge_from_executor_result,
        )

        summary = "完成了知识提取器的实现，创建了 extractor.py 模块。"
        plan_json = json.dumps({
            "plan_id": "plan_test",
            "goal": "实现 Entry A",
            "steps": [
                {
                    "step_id": "step_1",
                    "intent": "创建提取器模块",
                    "status": "completed",
                    "result_summary": "创建了 extract_knowledge_from_executor_result 函数。",
                    "failure_reason": "",
                },
            ],
        })

        chunks = extract_knowledge_from_executor_result(summary, plan_json, "completed")
        assert len(chunks) >= 2  # summary + step result + goal
        all_text = " ".join(chunks)
        assert "extractor" in all_text

    def test_failed_status_still_extracts(self):
        """failed 状态也应提取 failure_reason 作为负面知识。"""
        from src.common.knowledge_tree.ingestion.extractor import (
            extract_knowledge_from_executor_result,
        )

        summary = "执行失败：Executor 进程超时。"
        plan_json = json.dumps({
            "plan_id": "plan_test",
            "goal": "测试任务",
            "steps": [
                {
                    "step_id": "step_1",
                    "intent": "执行任务",
                    "status": "failed",
                    "result_summary": "",
                    "failure_reason": "Executor 超时，exit code 137 (SIGKILL)。",
                },
            ],
        })

        chunks = extract_knowledge_from_executor_result(summary, plan_json, "failed")
        assert len(chunks) >= 1
        all_text = " ".join(chunks)
        assert "失败原因" in all_text

    def test_auto_ingest_helper_does_not_crash(self, tmp_path):
        """_try_auto_ingest_executor_result 在各种输入下不应崩溃。"""
        from src.supervisor_agent.graph import _try_auto_ingest_executor_result

        ctx = Context(
            enable_knowledge_tree=True,
            knowledge_tree_root=str(tmp_path / "kt_md"),
            kt_embedding_model="hash",
        )

        # 正常输入
        _try_auto_ingest_executor_result(
            "完成了模块创建。\n[EXECUTOR_RESULT] {\"status\":\"completed\",\"summary\":\"ok\"}",
            ctx,
        )

        # 空输入
        _try_auto_ingest_executor_result("", ctx)

        # 非法 JSON
        _try_auto_ingest_executor_result("summary\n[EXECUTOR_RESULT] {bad json}", ctx)
