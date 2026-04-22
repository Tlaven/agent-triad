"""Ingest 测试（V4: 目录锚点定位）。"""

from pathlib import Path

import pytest

from src.common.knowledge_tree.dag.node import KnowledgeNode
from src.common.knowledge_tree.ingestion.ingest import ingest_nodes
from src.common.knowledge_tree.storage.markdown_store import MarkdownStore
from src.common.knowledge_tree.storage.overlay import OverlayStore
from src.common.knowledge_tree.storage.vector_store import (
    DirectoryAnchor,
    InMemoryVectorStore,
)


def _diverse_embedder(dim: int = 16):
    """多样性 embedder。"""
    def embed(text: str) -> list[float]:
        vec = [0.0] * dim
        for i, c in enumerate(text):
            idx = (ord(c) + i) % dim
            vec[idx] += 1.0
        mag = sum(x * x for x in vec) ** 0.5
        if mag > 0:
            vec = [x / mag for x in vec]
        return vec
    return embed


@pytest.fixture
def stores(tmp_path: Path):
    """初始化两层存储 + 已有锚点的知识树。"""
    md_store = MarkdownStore(tmp_path / "md")
    vector_store = InMemoryVectorStore(dimension=16)
    overlay_store = OverlayStore(tmp_path / "md" / ".overlay.json")
    embedder = _diverse_embedder(16)

    # 手动创建目录锚点
    dev_emb = embedder("LangGraph 开发相关")
    vector_store.upsert_anchor(DirectoryAnchor(
        directory="development",
        anchor_vector=dev_emb,
        file_count=2,
    ))
    pattern_emb = embedder("设计模式和原理")
    vector_store.upsert_anchor(DirectoryAnchor(
        directory="patterns",
        anchor_vector=pattern_emb,
        file_count=1,
    ))

    return md_store, vector_store, overlay_store, embedder


class TestIngestNodes:
    def test_ingest_attaches_to_existing_directory(self, stores):
        md_store, vector_store, overlay_store, embedder = stores

        candidate = KnowledgeNode.create(
            node_id="",
            title="LangGraph 状态管理",
            content="LangGraph 使用 TypedDict 定义状态。",
            source="agent:supervisor",
        )

        report = ingest_nodes(
            [candidate], vector_store, md_store, overlay_store, embedder,
            dedup_threshold=0.95, attach_threshold=0.3,
        )

        assert report.nodes_ingested == 1
        assert report.errors == []
        # 验证：新节点被放在了某个目录下
        assert "/" in candidate.node_id

    def test_ingest_creates_new_directory(self, stores):
        md_store, vector_store, overlay_store, embedder = stores

        # 用与现有锚点都不相似的内容
        candidate = KnowledgeNode.create(
            node_id="",
            title="完全不同的主题",
            content="这是一个全新的知识领域。",
            source="agent:supervisor",
        )

        # 设置很高的 attach_threshold 使得无法匹配现有锚点
        report = ingest_nodes(
            [candidate], vector_store, md_store, overlay_store, embedder,
            dedup_threshold=0.99, attach_threshold=0.99,
        )

        assert report.nodes_ingested == 1
        assert report.errors == []
        # 验证：新目录被创建
        assert "/" in candidate.node_id
        assert candidate.directory != ""

    def test_ingest_deduplicates(self, stores):
        md_store, vector_store, overlay_store, embedder = stores

        # 先 ingest 一个节点
        node1 = KnowledgeNode.create(
            node_id="",
            title="知识A",
            content="这是知识A的完整内容，包含了独特的信息。",
            source="test",
        )
        report1 = ingest_nodes(
            [node1], vector_store, md_store, overlay_store, embedder,
            dedup_threshold=0.90, attach_threshold=0.3,
        )
        assert report1.nodes_ingested == 1

        # 完全相同 content → 去重
        node2 = KnowledgeNode.create(
            node_id="",
            title="知识A副本",
            content="这是知识A的完整内容，包含了独特的信息。",
            source="test",
        )
        report2 = ingest_nodes(
            [node2], vector_store, md_store, overlay_store, embedder,
            dedup_threshold=0.90, attach_threshold=0.3,
        )
        assert report2.nodes_deduplicated == 1
        assert report2.nodes_ingested == 0

    def test_ingest_empty_candidates(self, stores):
        md_store, vector_store, overlay_store, embedder = stores
        report = ingest_nodes([], vector_store, md_store, overlay_store, embedder)
        assert report.nodes_ingested == 0

    def test_ingest_multiple_candidates(self, stores):
        md_store, vector_store, overlay_store, embedder = stores

        # 使用差异明显的主题内容，避免 mock embedder 产生相似向量
        topics = [
            ("Python 装饰器", "Python 装饰器是修改函数行为的高级特性，使用 @ 语法。"),
            ("Rust 所有权", "Rust 通过所有权系统在编译期保证内存安全，无需垃圾回收。"),
            ("React Hooks", "useState 和 useEffect 让函数组件管理状态和副作用。"),
            ("TCP 协议", "TCP 提供可靠的字节流传输，通过三次握手建立连接。"),
            ("Docker 容器", "Docker 通过容器化技术实现应用的隔离和可重复部署。"),
        ]

        candidates = [
            KnowledgeNode.create(
                node_id="",
                title=title,
                content=content,
                source="test",
            )
            for title, content in topics
        ]

        report = ingest_nodes(
            candidates, vector_store, md_store, overlay_store, embedder,
            dedup_threshold=0.99, attach_threshold=0.3,
        )

        assert report.nodes_ingested == 5
        assert report.errors == []
