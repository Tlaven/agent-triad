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
        assert md_store.read_node("nonexistent.md") is None

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
        assert not nested.exists()
        store._ensure_root()
        assert nested.exists()

    def test_overwrite_existing(self, md_store: MarkdownStore, sample_node: KnowledgeNode):
        md_store.write_node(sample_node)
        sample_node.content = "updated content"
        md_store.write_node(sample_node)
        restored = md_store.read_node(sample_node.node_id)
        assert restored is not None
        assert restored.content == "updated content"

    def test_directory_structure(self, md_store: MarkdownStore):
        """目录层级正确创建和列举。"""
        node1 = KnowledgeNode.create(
            node_id="dev/python/debugging.md",
            title="Debugging",
            content="Python debugging tips",
        )
        node2 = KnowledgeNode.create(
            node_id="dev/rust/errors.md",
            title="Errors",
            content="Rust error handling",
        )
        md_store.write_node(node1)
        md_store.write_node(node2)

        ids = md_store.list_node_ids()
        assert len(ids) == 2
        assert "dev/python/debugging.md" in ids
        assert "dev/rust/errors.md" in ids

        dirs = md_store.list_directories()
        assert "dev" in dirs
        assert "dev/python" in dirs
        assert "dev/rust" in dirs

    def test_move_node(self, md_store: MarkdownStore):
        node = KnowledgeNode.create(
            node_id="old/location.md",
            title="Test",
            content="content",
        )
        md_store.write_node(node)
        assert md_store.move_node("old/location.md", "new/location.md")
        assert md_store.read_node("old/location.md") is None
        restored = md_store.read_node("new/location.md")
        assert restored is not None

    def test_get_directory_files(self, md_store: MarkdownStore):
        node1 = KnowledgeNode.create(node_id="dev/a.md", title="A", content="a")
        node2 = KnowledgeNode.create(node_id="dev/b.md", title="B", content="b")
        node3 = KnowledgeNode.create(node_id="test/c.md", title="C", content="c")
        md_store.write_node(node1)
        md_store.write_node(node2)
        md_store.write_node(node3)

        files = md_store.get_directory_files("dev")
        assert len(files) == 2
        assert "dev/a.md" in files
        assert "dev/b.md" in files
