"""Markdown Store 测试。"""

from pathlib import Path

import pytest

from src.common.knowledge_tree.dag.node import KnowledgeNode
from src.common.knowledge_tree.storage.markdown_store import MarkdownStore


@pytest.fixture
def md_store(tmp_path: Path) -> MarkdownStore:
    return MarkdownStore(tmp_path / "kt_md")


class TestMarkdownStore:
    def test_write_and_read(self, md_store: MarkdownStore, sample_node: KnowledgeNode):
        path = md_store.write_node(sample_node)
        assert path.exists()

        restored = md_store.read_node(sample_node.node_id)
        assert restored is not None
        assert restored.node_id == sample_node.node_id
        assert restored.title == sample_node.title
        assert restored.content == sample_node.content

    def test_read_nonexistent(self, md_store: MarkdownStore):
        assert md_store.read_node("nonexistent") is None

    def test_delete(self, md_store: MarkdownStore, sample_node: KnowledgeNode):
        md_store.write_node(sample_node)
        assert md_store.delete_node(sample_node.node_id) is True
        assert md_store.read_node(sample_node.node_id) is None
        assert md_store.delete_node(sample_node.node_id) is False

    def test_list_node_ids(self, md_store: MarkdownStore, sample_nodes: list[KnowledgeNode]):
        for n in sample_nodes:
            md_store.write_node(n)
        ids = md_store.list_node_ids()
        assert len(ids) == len(sample_nodes)
        assert all(n.node_id in ids for n in sample_nodes)

    def test_list_nodes(self, md_store: MarkdownStore, sample_nodes: list[KnowledgeNode]):
        for n in sample_nodes:
            md_store.write_node(n)
        nodes = md_store.list_nodes()
        assert len(nodes) == len(sample_nodes)

    def test_node_exists(self, md_store: MarkdownStore, sample_node: KnowledgeNode):
        assert md_store.node_exists(sample_node.node_id) is False
        md_store.write_node(sample_node)
        assert md_store.node_exists(sample_node.node_id) is True

    def test_create_root_directory(self, tmp_path: Path):
        nested = tmp_path / "a" / "b" / "c"
        store = MarkdownStore(nested)
        assert nested.exists()

    def test_overwrite_existing(self, md_store: MarkdownStore, sample_node: KnowledgeNode):
        md_store.write_node(sample_node)
        sample_node.content = "updated content"
        md_store.write_node(sample_node)
        restored = md_store.read_node(sample_node.node_id)
        assert restored is not None
        assert restored.content == "updated content"
