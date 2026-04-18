"""Ingest 测试。"""

from pathlib import Path

import pytest

from src.common.knowledge_tree.config import KnowledgeTreeConfig
from src.common.knowledge_tree.dag.node import KnowledgeNode
from src.common.knowledge_tree.ingestion.ingest import IngestReport, ingest_nodes
from src.common.knowledge_tree.storage.graph_store import InMemoryGraphStore
from src.common.knowledge_tree.storage.markdown_store import MarkdownStore
from src.common.knowledge_tree.storage.vector_store import InMemoryVectorStore


def _diverse_embedder(dim: int = 16):
    """多样性 embedder：不同文本产生明显不同的向量。

    使用字符位置加权的哈希，确保不同文本有正交的向量。
    """
    def embed(text: str) -> list[float]:
        vec = [0.0] * dim
        for i, c in enumerate(text):
            idx = (ord(c) + i) % dim
            vec[idx] += 1.0
        # 归一化
        mag = sum(x * x for x in vec) ** 0.5
        if mag > 0:
            vec = [x / mag for x in vec]
        return vec
    return embed


@pytest.fixture
def stores(tmp_path: Path):
    """初始化三层存储 + 已 bootstrap 的知识树。"""
    config = KnowledgeTreeConfig(
        markdown_root=tmp_path / "md",
        db_path=tmp_path / "db",
    )
    md_store = MarkdownStore(config.markdown_root)
    graph_store = InMemoryGraphStore()
    graph_store.initialize()
    vector_store = InMemoryVectorStore(dimension=16)
    embedder = _diverse_embedder(16)

    # 创建 root 节点
    root = KnowledgeNode.create(title="Root", content="Root node", source="system")
    root.embedding = embedder(root.content)

    # 手动初始化：写入 root
    from src.common.knowledge_tree.storage.sync import sync_node_to_stores
    sync_node_to_stores(root, md_store, graph_store, vector_store)

    return md_store, graph_store, vector_store, embedder, root, config


class TestIngestNodes:
    def test_ingest_attaches_to_existing_group(self, stores):
        md_store, graph_store, vector_store, embedder, root, config = stores

        # 创建一个现有 group
        from src.common.knowledge_tree.dag.edge import KnowledgeEdge
        from src.common.knowledge_tree.storage.sync import sync_node_to_stores

        group = KnowledgeNode.create(title="LangGraph", content="关于LangGraph的知识", source="seed")
        group.embedding = embedder("关于LangGraph的知识")
        graph_store.upsert_edge(KnowledgeEdge.create(parent_id=root.node_id, child_id=group.node_id))
        sync_node_to_stores(group, md_store, graph_store, vector_store)

        # Ingest 一个相似节点
        candidate = KnowledgeNode.create(
            title="LangGraph 状态管理",
            content="LangGraph 使用 TypedDict 定义状态。",
            source="agent:supervisor",
        )

        report = ingest_nodes(
            [candidate], graph_store, vector_store, md_store, embedder,
            dedup_threshold=0.95, cluster_attach_threshold=0.3,
        )

        assert report.nodes_ingested == 1
        assert report.errors == []

        # 验证：新节点挂在了 group 下
        children = graph_store.get_children(group.node_id)
        child_ids = [c.node_id for c in children]
        assert candidate.node_id in child_ids

    def test_ingest_creates_new_group(self, stores):
        md_store, graph_store, vector_store, embedder, root, config = stores

        # 不创建任何现有 group → 直接在 root 下创建新 group
        candidate = KnowledgeNode.create(
            title="完全不同的主题",
            content="这是一个全新的知识领域，与现有内容完全无关。",
            source="agent:supervisor",
        )

        report = ingest_nodes(
            [candidate], graph_store, vector_store, md_store, embedder,
            dedup_threshold=0.99, cluster_attach_threshold=0.99,
        )

        assert report.nodes_ingested == 1
        assert report.errors == []

        # 验证：新 group 挂在 root 下
        root_children = graph_store.get_children(root.node_id)
        assert len(root_children) == 1  # 新 group

        # 新 group 下有 candidate
        group_children = graph_store.get_children(root_children[0].node_id)
        assert len(group_children) == 1
        assert group_children[0].node_id == candidate.node_id

    def test_ingest_deduplicates(self, stores):
        md_store, graph_store, vector_store, embedder, root, config = stores

        # 先 ingest 一个节点
        node1 = KnowledgeNode.create(
            title="知识A", content="这是知识A的完整内容，包含了独特的信息。",
            source="test",
        )
        report1 = ingest_nodes(
            [node1], graph_store, vector_store, md_store, embedder,
            dedup_threshold=0.99, cluster_attach_threshold=0.3,
        )
        assert report1.nodes_ingested == 1

        # 用完全相同的 content 再 ingest → 去重（hash 相同 → 向量相同 → sim=1.0）
        node2 = KnowledgeNode.create(
            title="知识A副本", content="这是知识A的完整内容，包含了独特的信息。",
            source="test",
        )
        report2 = ingest_nodes(
            [node2], graph_store, vector_store, md_store, embedder,
            dedup_threshold=0.99, cluster_attach_threshold=0.3,
        )
        assert report2.nodes_deduplicated == 1
        assert report2.nodes_ingested == 0

    def test_ingest_empty_candidates(self, stores):
        md_store, graph_store, vector_store, embedder, root, config = stores
        report = ingest_nodes([], graph_store, vector_store, md_store, embedder)
        assert report.nodes_ingested == 0

    def test_ingest_no_root_fails(self, tmp_path: Path):
        """没有 root 时 ingest 应报错。"""
        graph_store = InMemoryGraphStore()
        graph_store.initialize()
        vector_store = InMemoryVectorStore(dimension=16)
        md_store = MarkdownStore(tmp_path / "md")
        embedder = _diverse_embedder(16)

        node = KnowledgeNode.create(title="test", content="test", source="test")
        report = ingest_nodes([node], graph_store, vector_store, md_store, embedder)
        assert len(report.errors) > 0
        assert "No root" in report.errors[0]

    def test_ingest_multiple_candidates(self, stores):
        md_store, graph_store, vector_store, embedder, root, config = stores

        candidates = [
            KnowledgeNode.create(title=f"知识{i}", content=f"这是第{i}个知识点。", source="test")
            for i in range(5)
        ]

        report = ingest_nodes(
            candidates, graph_store, vector_store, md_store, embedder,
            dedup_threshold=0.95, cluster_attach_threshold=0.3,
        )

        assert report.nodes_ingested == 5
        assert report.errors == []

        # 验证 root 下有新 group
        root_children = graph_store.get_children(root.node_id)
        assert len(root_children) >= 1
