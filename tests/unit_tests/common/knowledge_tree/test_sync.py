"""文件系统→向量同步测试。"""

from pathlib import Path

import pytest

from src.common.knowledge_tree.dag.node import KnowledgeNode
from src.common.knowledge_tree.storage.markdown_store import MarkdownStore
from src.common.knowledge_tree.storage.sync import full_rebuild, sync_node
from src.common.knowledge_tree.storage.vector_store import InMemoryVectorStore


@pytest.fixture
def stores(tmp_path: Path):
    md = MarkdownStore(tmp_path / "md")
    vec = InMemoryVectorStore(dimension=4)
    return md, vec


def _make_embedder(dim: int = 4):
    """简单确定性 embedder。"""
    def embed(text: str) -> list[float]:
        base = sum(ord(c) for c in text) % 100 / 100.0
        return [base + i * 0.01 for i in range(dim)]
    return embed


class TestFullRebuild:
    def test_rebuild_with_embedder(self, stores, sample_nodes: list[KnowledgeNode]):
        md, vec = stores
        embedder = _make_embedder()

        # 写入 Markdown 文件
        for n in sample_nodes:
            md.write_node(n)

        # 全量重建
        report = full_rebuild(md, vec, embedder)
        assert report.nodes_synced == len(sample_nodes)
        assert report.embeddings_updated == len(sample_nodes) * 2  # content + title
        assert report.errors == []

        # 验证嵌入
        for n in sample_nodes:
            assert vec.get_embedding(n.node_id) is not None

    def test_rebuild_computes_anchors(self, stores, sample_nodes: list[KnowledgeNode]):
        md, vec = stores
        embedder = _make_embedder()

        for n in sample_nodes:
            md.write_node(n)

        report = full_rebuild(md, vec, embedder)
        assert report.anchors_computed >= 1
        anchors = vec.get_all_anchors()
        anchor_dirs = {a.directory for a in anchors}
        assert "development" in anchor_dirs
        assert "patterns" in anchor_dirs
        assert "fundamentals" in anchor_dirs


class TestSyncNode:
    def test_sync_single_node(self, stores, mock_embedder):
        md, vec = stores
        node = KnowledgeNode.create(
            node_id="test/new.md",
            title="Test",
            content="Test content",
        )
        md.write_node(node)
        sync_node("test/new.md", "Test content", md, vec, mock_embedder)
        assert vec.get_embedding("test/new.md") is not None
