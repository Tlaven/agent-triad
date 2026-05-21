"""P2 Overlay 主动管理工具测试。

验证：
- overlay_add: 创建关联边 + 验证 source/target 存在 + 拒绝自链接
- overlay_remove: 删除边 + 不存在边返回 False
- overlay_list: 列出全部 + 按路径过滤
- 工具注册在 build_knowledge_tree_tools 输出中
"""

import json

import pytest

from src.common.knowledge_tree import build_knowledge_tree_tools
from src.common.knowledge_tree.dag.node import KnowledgeNode
from src.common.knowledge_tree.storage.markdown_store import MarkdownStore
from src.common.knowledge_tree.storage.overlay import OverlayStore
from src.common.knowledge_tree.storage.vector_store import InMemoryVectorStore


@pytest.fixture
def md_store(tmp_path):
    store = MarkdownStore(tmp_path / "kt_md")
    # 创建测试节点
    for dir_name, filename, title in [
        ("dev", "debugging.md", "Debugging"),
        ("dev", "testing.md", "Testing"),
        ("ops", "deploy.md", "Deploy"),
    ]:
        node = KnowledgeNode.create(
            node_id=f"{dir_name}/{filename}",
            title=title,
            content=f"Content of {title}",
            source="test",
        )
        store.write_node(node)
    return store


@pytest.fixture
def overlay_store(tmp_path):
    return OverlayStore(tmp_path / "kt_md" / ".overlay.json")


@pytest.fixture
def vector_store():
    return InMemoryVectorStore(dimension=16)


# -- overlay_add --


class TestOverlayAdd:
    def test_creates_edge(self, md_store, overlay_store):
        kt = _make_kt(md_store, overlay_store)
        result = kt.overlay_add("dev/debugging.md", "dev/testing.md")
        assert result["ok"] is True
        edges = overlay_store.get_all_edges()
        assert len(edges) == 1
        assert edges[0].source_path == "dev/debugging.md"
        assert edges[0].target_path == "dev/testing.md"

    def test_with_relation_and_note(self, md_store, overlay_store):
        kt = _make_kt(md_store, overlay_store)
        result = kt.overlay_add(
            "dev/debugging.md",
            "ops/deploy.md",
            relation="prerequisite",
            note="Debugging skills needed before deploy",
        )
        assert result["ok"] is True
        edge = overlay_store.get_all_edges()[0]
        assert edge.relation == "prerequisite"
        assert edge.note == "Debugging skills needed before deploy"
        assert edge.created_by == "agent"

    def test_validates_source_exists(self, md_store, overlay_store):
        kt = _make_kt(md_store, overlay_store)
        result = kt.overlay_add("nonexistent.md", "dev/debugging.md")
        assert result["ok"] is False
        assert "source not found" in result["error"]

    def test_validates_target_exists(self, md_store, overlay_store):
        kt = _make_kt(md_store, overlay_store)
        result = kt.overlay_add("dev/debugging.md", "nonexistent.md")
        assert result["ok"] is False
        assert "target not found" in result["error"]

    def test_rejects_self_link(self, md_store, overlay_store):
        kt = _make_kt(md_store, overlay_store)
        result = kt.overlay_add("dev/debugging.md", "dev/debugging.md")
        assert result["ok"] is False
        assert "must be different" in result["error"]

    def test_deduplicates_same_edge(self, md_store, overlay_store):
        kt = _make_kt(md_store, overlay_store)
        kt.overlay_add("dev/debugging.md", "dev/testing.md")
        kt.overlay_add("dev/debugging.md", "dev/testing.md", note="updated")
        edges = overlay_store.get_all_edges()
        assert len(edges) == 1
        assert edges[0].note == "updated"


# -- overlay_remove --


class TestOverlayRemove:
    def test_removes_edge(self, md_store, overlay_store):
        kt = _make_kt(md_store, overlay_store)
        kt.overlay_add("dev/debugging.md", "dev/testing.md")
        result = kt.overlay_remove("dev/debugging.md", "dev/testing.md")
        assert result["ok"] is True
        assert len(overlay_store.get_all_edges()) == 0

    def test_nonexistent_edge(self, md_store, overlay_store):
        kt = _make_kt(md_store, overlay_store)
        result = kt.overlay_remove("dev/debugging.md", "dev/testing.md")
        assert result["ok"] is False


# -- overlay_list --


class TestOverlayList:
    def test_lists_all(self, md_store, overlay_store):
        kt = _make_kt(md_store, overlay_store)
        kt.overlay_add("dev/debugging.md", "dev/testing.md")
        kt.overlay_add("dev/debugging.md", "ops/deploy.md")
        result = kt.overlay_list()
        assert result["ok"] is True
        assert result["total"] == 2
        assert len(result["edges"]) == 2

    def test_lists_empty(self, md_store, overlay_store):
        kt = _make_kt(md_store, overlay_store)
        result = kt.overlay_list()
        assert result["ok"] is True
        assert result["total"] == 0

    def test_filters_by_path(self, md_store, overlay_store):
        kt = _make_kt(md_store, overlay_store)
        kt.overlay_add("dev/debugging.md", "dev/testing.md")
        kt.overlay_add("dev/debugging.md", "ops/deploy.md")
        result = kt.overlay_list(path="ops/deploy.md")
        assert result["ok"] is True
        assert result["total"] == 1
        assert result["edges"][0]["source"] == "dev/debugging.md"


# -- Tool registration --


class TestOverlayToolRegistration:
    def test_tool_in_build_output(self):
        tools = build_knowledge_tree_tools(_FakeContext())
        names = [t.name for t in tools]
        assert "knowledge_tree_overlay" in names

    def test_tool_add_action(self):
        tools = build_knowledge_tree_tools(_FakeContext())
        overlay_tool = next(t for t in tools if t.name == "knowledge_tree_overlay")
        # Tool should be callable
        assert overlay_tool is not None

    def test_unknown_action(self):
        tools = build_knowledge_tree_tools(_FakeContext())
        overlay_tool = next(t for t in tools if t.name == "knowledge_tree_overlay")
        result = _run_tool(overlay_tool, action="unknown")
        data = json.loads(result)
        assert data["ok"] is False
        assert "Unknown action" in data["error"]


# -- Helpers --


def _make_kt(md_store, overlay_store):
    """创建测试用 KnowledgeTree（绕过 bootstrap）。"""
    from src.common.knowledge_tree import KnowledgeTree
    from src.common.knowledge_tree.config import KnowledgeTreeConfig

    config = KnowledgeTreeConfig(
        markdown_root=md_store.root,
        embedding_model="hash",
    )
    kt = KnowledgeTree.__new__(KnowledgeTree)
    kt.config = config
    kt.md_store = md_store
    kt.vector_store = InMemoryVectorStore(dimension=16)
    kt.overlay_store = overlay_store
    kt.embedder = lambda text: [0.0] * 16
    kt._retrieval_logs = []
    kt._max_retrieval_logs = 1000
    return kt


def _run_tool(tool, **kwargs):
    """运行 async 工具并返回结果。"""
    import asyncio

    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(tool.coroutine(**kwargs))
    finally:
        loop.close()


@staticmethod
def _FakeContext():
    """创建测试用 Context 替身。"""
    from dataclasses import dataclass, field

    @dataclass
    class FakeContext:
        knowledge_tree_root: str = "workspace/knowledge_tree"
        kt_embedder_type: str = "hash"
        kt_embedding_model: str = "hash"
        kt_embedding_dimension: int = 1024
        kt_rag_similarity_threshold: float = 0.15
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

    return FakeContext()
