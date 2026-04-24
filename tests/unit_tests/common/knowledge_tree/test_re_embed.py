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
