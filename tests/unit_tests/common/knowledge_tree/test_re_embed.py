"""Re-embed 模块测试。"""

from pathlib import Path

import pytest

from src.common.knowledge_tree.dag.node import KnowledgeNode
from src.common.knowledge_tree.editing.re_embed import re_embed_nodes
from src.common.knowledge_tree.storage.markdown_store import MarkdownStore
from src.common.knowledge_tree.storage.vector_store import InMemoryVectorStore


@pytest.fixture
def stores(tmp_path: Path):
    md = MarkdownStore(tmp_path / "md")
    vec = InMemoryVectorStore(dimension=16)
    return md, vec


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


class TestReEmbedNodes:
    def test_re_embed_existing_nodes(self, stores):
        md, vec = stores
        embedder = _embedder()

        # 创建并写入节点
        node = KnowledgeNode.create(
            node_id="dev/test.md",
            title="Test",
            content="原始内容",
        )
        md.write_node(node)
        vec.upsert_embedding(node.node_id, embedder("原始内容"))

        # 修改内容（模拟编辑）
        edited = KnowledgeNode.create(
            node_id="dev/test.md",
            title="Test",
            content="修改后的内容",
        )
        md.write_node(edited)

        # 重嵌入
        updated = re_embed_nodes(["dev/test.md"], md, vec, embedder)
        assert updated == 1

        # 验证 embedding 已更新
        new_emb = vec.get_embedding("dev/test.md")
        assert new_emb is not None
        expected = embedder("修改后的内容")
        assert new_emb == expected

    def test_re_embed_skips_missing_nodes(self, stores):
        md, vec = stores
        updated = re_embed_nodes(["nonexistent.md"], md, vec, _embedder())
        assert updated == 0

    def test_re_embed_multiple_nodes(self, stores):
        md, vec = stores
        embedder = _embedder()

        for i in range(3):
            node = KnowledgeNode.create(
                node_id=f"dev/node{i}.md",
                title=f"Node {i}",
                content=f"Content {i}",
            )
            md.write_node(node)

        updated = re_embed_nodes(
            ["dev/node0.md", "dev/node1.md", "dev/node2.md"],
            md, vec, embedder,
        )
        assert updated == 3

    def test_re_embed_empty_list(self, stores):
        md, vec = stores
        updated = re_embed_nodes([], md, vec, _embedder())
        assert updated == 0

    def test_re_embed_refreshes_anchor(self, tmp_path: Path):
        """重嵌入后受影响目录的锚点应被刷新。"""
        md = MarkdownStore(tmp_path / "md")
        vec = InMemoryVectorStore(dimension=16)
        embedder = _embedder()
        from src.common.knowledge_tree.storage.sync import _refresh_anchor

        # 创建两个节点
        for name, content in [("a.md", "alpha content"), ("b.md", "beta content")]:
            node = KnowledgeNode.create(
                node_id=f"dev/{name}", title=name, content=content,
            )
            md.write_node(node)
            vec.upsert_embedding(node.node_id, embedder(content))

        # 初始锚点
        _refresh_anchor("dev", md, vec)
        anchor_before = vec.get_anchor("dev")
        assert anchor_before is not None
        vec_before = list(anchor_before.anchor_vector)

        # 修改 a.md 的内容
        edited = KnowledgeNode.create(
            node_id="dev/a.md", title="a", content="completely different gamma",
        )
        md.write_node(edited)

        # 重嵌入
        re_embed_nodes(["dev/a.md"], md, vec, embedder)

        # 锚点应该已改变
        anchor_after = vec.get_anchor("dev")
        assert anchor_after is not None
        assert anchor_after.anchor_vector != vec_before
