"""目录锚点边界条件与集成测试。"""

from pathlib import Path

import pytest

from src.common.knowledge_tree.dag.node import KnowledgeNode
from src.common.knowledge_tree.storage.markdown_store import MarkdownStore
from src.common.knowledge_tree.storage.vector_store import (
    DirectoryAnchor,
    InMemoryVectorStore,
    compute_anchor_vector,
)


def _embedder(dim: int = 16):
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


class TestComputeAnchorVector:
    def test_single_embedding(self):
        emb = [[1.0, 0.0, 0.0, 0.0]]
        anchor = compute_anchor_vector(emb)
        assert len(anchor) == 4
        mag = sum(x * x for x in anchor) ** 0.5
        assert abs(mag - 1.0) < 1e-6

    def test_multiple_embeddings(self):
        emb = [[1.0, 0.0], [0.0, 1.0]]
        anchor = compute_anchor_vector(emb)
        assert len(anchor) == 2
        # 均值为 [0.5, 0.5]，归一化后各分量相等
        assert abs(anchor[0] - anchor[1]) < 1e-6

    def test_empty_embeddings(self):
        assert compute_anchor_vector([]) == []

    def test_zero_vector_embeddings(self):
        emb = [[0.0, 0.0], [0.0, 0.0]]
        anchor = compute_anchor_vector(emb)
        assert all(x == 0.0 for x in anchor)


class TestDirectoryAnchor:
    def test_to_dict_roundtrip(self):
        a = DirectoryAnchor(
            directory="development",
            anchor_vector=[0.5, 0.5],
            file_count=3,
            last_updated="2026-01-01T00:00:00",
        )
        d = a.to_dict()
        assert d["directory"] == "development"
        assert d["file_count"] == 3


class TestAnchorIntegration:
    def test_find_nearest_anchor_returns_best(self):
        vec_store = InMemoryVectorStore(dimension=16)
        embedder = _embedder(16)

        # 创建两个锚点——用足够不同的文本
        dev_emb = embedder("LangGraph 状态管理 TypedDict 开发")
        vec_store.upsert_anchor(DirectoryAnchor("dev", dev_emb, 1))
        pat_emb = embedder("设计模式 嵌入向量 语义检索")
        vec_store.upsert_anchor(DirectoryAnchor("patterns", pat_emb, 1))

        # 查询接近 dev 的内容
        query = embedder("LangGraph 开发测试")
        best = vec_store.find_nearest_anchor(query, threshold=0.0)
        assert best is not None
        assert best.directory == "dev"

    def test_find_nearest_anchor_no_match_above_threshold(self):
        vec_store = InMemoryVectorStore(dimension=4)
        embedder = _embedder(4)

        vec_store.upsert_anchor(DirectoryAnchor("dev", embedder("aaa"), 1))

        query = embedder("zzz")
        best = vec_store.find_nearest_anchor(query, threshold=0.99)
        assert best is None

    def test_find_nearest_anchor_empty_store(self):
        vec_store = InMemoryVectorStore(dimension=4)
        best = vec_store.find_nearest_anchor([1.0, 0.0, 0.0, 0.0])
        assert best is None

    def test_anchor_upsert_updates_existing(self):
        vec_store = InMemoryVectorStore(dimension=4)
        vec_store.upsert_anchor(DirectoryAnchor("dev", [1.0, 0.0, 0.0, 0.0], 1))
        vec_store.upsert_anchor(DirectoryAnchor("dev", [0.0, 1.0, 0.0, 0.0], 2))

        anchor = vec_store.get_anchor("dev")
        assert anchor is not None
        assert anchor.file_count == 2
        assert anchor.anchor_vector == [0.0, 1.0, 0.0, 0.0]

    def test_delete_anchor(self):
        vec_store = InMemoryVectorStore(dimension=4)
        vec_store.upsert_anchor(DirectoryAnchor("dev", [1.0, 0.0], 1))
        assert vec_store.delete_anchor("dev") is True
        assert vec_store.get_anchor("dev") is None
        assert vec_store.delete_anchor("dev") is False

    def test_get_embeddings_in_directory(self, tmp_path: Path):
        vec_store = InMemoryVectorStore(dimension=4)
        vec_store.upsert_embedding("dev/a.md", [1.0, 0.0, 0.0, 0.0])
        vec_store.upsert_embedding("dev/b.md", [0.0, 1.0, 0.0, 0.0])
        vec_store.upsert_embedding("patterns/c.md", [0.0, 0.0, 1.0, 0.0])

        dev_embs = vec_store.get_embeddings_in_directory("dev")
        assert len(dev_embs) == 2
        assert "dev/a.md" in dev_embs
        assert "dev/b.md" in dev_embs

        pat_embs = vec_store.get_embeddings_in_directory("patterns")
        assert len(pat_embs) == 1

    def test_anchor_refresh_after_file_delete(self, tmp_path: Path):
        """删除文件后锚点应更新。"""
        md = MarkdownStore(tmp_path / "md")
        vec = InMemoryVectorStore(dimension=4)
        embedder = _embedder(4)

        # 写入两个节点
        for name in ["a.md", "b.md"]:
            node = KnowledgeNode.create(
                node_id=f"dev/{name}",
                title=name,
                content=f"Content for {name}",
            )
            md.write_node(node)
            vec.upsert_embedding(node.node_id, embedder(node.content))

        # 初始锚点
        from src.common.knowledge_tree.storage.sync import _refresh_anchor
        _refresh_anchor("dev", md, vec)
        anchor1 = vec.get_anchor("dev")
        assert anchor1 is not None
        assert anchor1.file_count == 2

        # 删除一个文件
        md.delete_node("dev/b.md")
        vec.delete_embedding("dev/b.md")

        # 刷新锚点
        _refresh_anchor("dev", md, vec)
        anchor2 = vec.get_anchor("dev")
        assert anchor2 is not None
        assert anchor2.file_count == 1

    def test_anchor_deleted_when_directory_empty(self, tmp_path: Path):
        """目录清空后锚点应被删除。"""
        md = MarkdownStore(tmp_path / "md")
        vec = InMemoryVectorStore(dimension=4)
        embedder = _embedder(4)

        node = KnowledgeNode.create(
            node_id="dev/only.md",
            title="Only",
            content="Only file",
        )
        md.write_node(node)
        vec.upsert_embedding(node.node_id, embedder(node.content))

        from src.common.knowledge_tree.storage.sync import _refresh_anchor
        _refresh_anchor("dev", md, vec)
        assert vec.get_anchor("dev") is not None

        md.delete_node("dev/only.md")
        vec.delete_embedding("dev/only.md")
        _refresh_anchor("dev", md, vec)
        assert vec.get_anchor("dev") is None
