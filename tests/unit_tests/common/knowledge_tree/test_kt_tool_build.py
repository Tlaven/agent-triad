"""测试 build_knowledge_tree_tools() 生成的 LangChain 工具。

验证 4 个 KT 工具能正确创建并执行，
覆盖工具注册 → 调用 → 返回 JSON 的完整路径。
"""

from __future__ import annotations

import asyncio
import json
import shutil
from dataclasses import dataclass
from pathlib import Path

import pytest

from src.common.knowledge_tree import KnowledgeTree, build_knowledge_tree_tools
from src.common.knowledge_tree.config import KnowledgeTreeConfig

SEED_DIR = Path("workspace/knowledge_tree")


@dataclass
class _FakeContext:
    """模拟 Context 对象，只包含 KT 相关字段。"""
    knowledge_tree_root: str = ""
    kt_rag_similarity_threshold: float = 0.15
    kt_embedder_type: str = "hash"
    kt_embedding_model: str = "hash"
    kt_embedding_dimension: int = 1024
    kt_max_tree_depth: int = 5
    kt_ingest_enabled: bool = True
    kt_ingest_chunk_max_tokens: int = 512
    kt_dedup_threshold: float = 0.95
    kt_ingest_attach_threshold: float = 0.7
    kt_structural_weight: float = 0.2
    kt_content_weight: float = 0.8
    kt_optimization_window: int = 3600
    kt_max_optimizations_per_window: int = 10
    kt_total_failure_threshold: int = 3
    kt_rag_false_positive_threshold: int = 3
    kt_content_insufficient_threshold: int = 5


@pytest.fixture
def fake_ctx(tmp_path: Path) -> _FakeContext:
    """创建使用 hash embedder 的测试 Context。"""
    seed_copy = tmp_path / "kt_md"
    shutil.copytree(SEED_DIR, seed_copy)
    return _FakeContext(
        knowledge_tree_root=str(seed_copy),
        kt_embedding_model="hash",
        kt_rag_similarity_threshold=0.15,
    )


@pytest.fixture
def tools(fake_ctx: _FakeContext):
    """创建 KT 工具列表。"""
    # 先 bootstrap KT
    config = KnowledgeTreeConfig.from_context(fake_ctx)
    kt = KnowledgeTree(config)
    kt.bootstrap()

    return build_knowledge_tree_tools(fake_ctx)


def _run(coro):
    """同步运行异步工具。"""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class TestBuildToolsCreatesCorrectTools:
    """build_knowledge_tree_tools 应返回正确的工具集。"""

    def test_returns_five_tools(self, tools):
        assert len(tools) == 11

    def test_tool_names(self, tools):
        names = {t.name for t in tools}
        assert names == {
            "knowledge_tree_retrieve",
            "knowledge_tree_ingest",
            "knowledge_tree_status",
            "knowledge_tree_list",
            "knowledge_tree_overlay",
            "knowledge_tree_tree",
            "knowledge_tree_reorganize",
            "knowledge_tree_add_meta_rule",
            "knowledge_tree_delete_meta_rule",
            "knowledge_tree_list_meta_rules",
            "knowledge_tree_record_feedback",
        }

    def test_tools_are_callable(self, tools):
        for tool in tools:
            assert callable(tool.func) or callable(tool.coroutine)


class TestRetrieveTool:
    """knowledge_tree_retrieve 工具测试。"""

    def test_retrieve_returns_json(self, tools):
        tool = next(t for t in tools if t.name == "knowledge_tree_retrieve")
        result = _run(tool.coroutine("AgentTriad 架构"))
        parsed = json.loads(result)
        assert "ok" in parsed
        assert "query_id" in parsed

    def test_retrieve_finds_architecture(self, tools):
        tool = next(t for t in tools if t.name == "knowledge_tree_retrieve")
        result = _run(tool.coroutine("AgentTriad 三层架构 Supervisor"))
        parsed = json.loads(result)
        assert parsed["ok"] is True
        assert "architecture" in parsed.get("node_id", "").lower() or \
               "架构" in parsed.get("title", "")


class TestIngestTool:
    """knowledge_tree_ingest 工具测试。"""

    def test_ingest_returns_json(self, tools):
        tool = next(t for t in tools if t.name == "knowledge_tree_ingest")
        result = _run(tool.coroutine(
            "测试知识：Executor 使用 uvicorn 启动 FastAPI 服务，端口动态分配。",
            trigger="user_explicit",
            source="test",
        ))
        parsed = json.loads(result)
        assert parsed["ok"] is True
        assert "nodes_ingested" in parsed


class TestStatusTool:
    """knowledge_tree_status 工具测试。"""

    def test_status_returns_json(self, tools):
        tool = next(t for t in tools if t.name == "knowledge_tree_status")
        result = _run(tool.coroutine())
        parsed = json.loads(result)
        assert "total_nodes" in parsed
        assert parsed["total_nodes"] >= 15  # 15 seed documents


class TestListTool:
    """knowledge_tree_list 工具测试。"""

    def test_list_returns_json(self, tools):
        tool = next(t for t in tools if t.name == "knowledge_tree_list")
        result = _run(tool.coroutine())
        parsed = json.loads(result)
        assert parsed["ok"] is True
        assert "total" in parsed
        assert "items" in parsed

    def test_list_filter_by_directory(self, tools):
        tool = next(t for t in tools if t.name == "knowledge_tree_list")
        result = _run(tool.coroutine(directory="architecture"))
        parsed = json.loads(result)
        assert parsed["ok"] is True
        for item in parsed["items"]:
            assert item["directory"] == "architecture"
