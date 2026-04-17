"""跨层同步测试。"""

from pathlib import Path

import pytest

from src.common.knowledge_tree.dag.node import KnowledgeNode
from src.common.knowledge_tree.storage.graph_store import InMemoryGraphStore
from src.common.knowledge_tree.storage.markdown_store import MarkdownStore
from src.common.knowledge_tree.storage.sync import (
    full_rebuild,
    sync_markdown_to_graph,
    sync_node_to_stores,
)
from src.common.knowledge_tree.storage.vector_store import InMemoryVectorStore


@pytest.fixture
def stores(tmp_path: Path):
    md = MarkdownStore(tmp_path / "md")
    graph = InMemoryGraphStore()
    graph.initialize()
    vec = InMemoryVectorStore(dimension=4)
    return md, graph, vec


def _make_embedder(dim: int = 4):
    """简单确定性 embedder。"""
    def embed(text: str) -> list[float]:
        base = sum(ord(c) for c in text) % 100 / 100.0
        return [base + i * 0.01 for i in range(dim)]
    return embed


class TestSyncMarkdownToGraph:
    def test_sync_all_nodes(self, stores, sample_nodes: list[KnowledgeNode]):
        md, graph, _ = stores
        for n in sample_nodes:
            md.write_node(n)

        report = sync_markdown_to_graph(md, graph)
        assert report.nodes_synced == len(sample_nodes)
        assert report.errors == []

        # 验证图数据库中有对应节点
        for n in sample_nodes:
            assert graph.get_node(n.node_id) is not None


class TestSyncNodeToStores:
    def test_sync_with_embedding(self, stores, sample_node: KnowledgeNode, mock_embedder):
        md, graph, vec = stores
        sample_node.embedding = mock_embedder(sample_node.content, dim=4)

        sync_node_to_stores(sample_node, md, graph, vec)

        assert md.read_node(sample_node.node_id) is not None
        assert graph.get_node(sample_node.node_id) is not None
        assert vec.get_embedding(sample_node.node_id) is not None

    def test_sync_without_vector_store(self, stores, sample_node: KnowledgeNode):
        md, graph, _ = stores
        sync_node_to_stores(sample_node, md, graph, vector_store=None)
        assert md.read_node(sample_node.node_id) is not None
        assert graph.get_node(sample_node.node_id) is not None


class TestFullRebuild:
    def test_rebuild_with_embedder(self, stores, sample_nodes: list[KnowledgeNode]):
        md, graph, vec = stores
        embedder = _make_embedder()

        # 写入 Markdown
        for n in sample_nodes:
            md.write_node(n)

        # 全量重建
        report = full_rebuild(md, graph, vec, embedder)
        assert report.nodes_synced == len(sample_nodes)
        assert report.embeddings_updated == len(sample_nodes)
        assert report.errors == []

        # 验证嵌入
        for n in sample_nodes:
            assert vec.get_embedding(n.node_id) is not None
