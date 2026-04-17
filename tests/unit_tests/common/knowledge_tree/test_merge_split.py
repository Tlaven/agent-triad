"""Merge/Split 测试。"""

import pytest

from src.common.knowledge_tree.dag.edge import KnowledgeEdge
from src.common.knowledge_tree.dag.node import KnowledgeNode
from src.common.knowledge_tree.editing.merge_split import merge_nodes, split_node
from src.common.knowledge_tree.storage.graph_store import InMemoryGraphStore


@pytest.fixture
def tree():
    """返回 (graph, root_id, a_id, b_id, c_id)"""
    g = InMemoryGraphStore()
    g.initialize()
    root = KnowledgeNode.create(title="Root", content="Root")
    a = KnowledgeNode.create(title="A", content="Content A")
    b = KnowledgeNode.create(title="B", content="Content B")
    c = KnowledgeNode.create(title="C", content="Content C")
    for n in [root, a, b, c]:
        g.upsert_node(n)
    g.upsert_edge(KnowledgeEdge.create(parent_id=root.node_id, child_id=a.node_id, is_primary=True))
    g.upsert_edge(KnowledgeEdge.create(parent_id=root.node_id, child_id=b.node_id, is_primary=True))
    g.upsert_edge(KnowledgeEdge.create(parent_id=a.node_id, child_id=c.node_id, is_primary=True))
    return g, root.node_id, a.node_id, b.node_id, c.node_id


class TestMergeNodes:
    def test_merge_two_nodes(self, tree):
        g, root_id, a_id, b_id, c_id = tree
        merged = merge_nodes([a_id, b_id], g)

        assert "A" in merged.title
        assert "B" in merged.title
        assert "Content A" in merged.content
        assert "Content B" in merged.content
        # 源节点已删除
        assert g.get_node(a_id) is None
        assert g.get_node(b_id) is None
        # 合并节点存在
        assert g.get_node(merged.node_id) is not None

    def test_merge_inherits_edges(self, tree):
        g, root_id, a_id, b_id, c_id = tree
        # A 有子节点 C
        assert len(g.get_children(a_id)) == 1

        merged = merge_nodes([a_id, b_id], g)

        # 合并节点应继承 C 作为子节点
        merged_children = g.get_children(merged.node_id)
        assert len(merged_children) == 1

        # 合并节点应仍是 root 的子节点
        root_children_after = g.get_children(root_id)
        merged_ids = [n.node_id for n in root_children_after]
        assert merged.node_id in merged_ids

    def test_merge_custom_content(self, tree):
        g, root_id, a_id, b_id, c_id = tree
        merged = merge_nodes(
            [a_id, b_id],
            g,
            title="Merged",
            content="Custom content",
            summary="Custom summary",
        )
        assert merged.title == "Merged"
        assert merged.content == "Custom content"

    def test_merge_single_node_raises(self, tree):
        g = tree[0]
        with pytest.raises(ValueError, match="at least 2"):
            merge_nodes(["single_id"], g)

    def test_merge_nonexistent_raises(self, tree):
        g = tree[0]
        with pytest.raises(ValueError, match="not found"):
            merge_nodes(["nonexistent1", "nonexistent2"], g)


class TestSplitNode:
    def test_split_into_two(self, tree):
        g, root_id, a_id, b_id, c_id = tree
        children = split_node(a_id, [
            {"title": "Part 1", "content": "First part", "summary": "P1"},
            {"title": "Part 2", "content": "Second part", "summary": "P2"},
        ], g)

        assert len(children) == 2
        assert children[0].title == "Part 1"
        assert children[1].title == "Part 2"

        # 子节点应该是原节点的 children（原有 C + 新增 2 个 = 3）
        a_children = g.get_children(a_id)
        assert len(a_children) == 3  # C + Part 1 + Part 2

    def test_split_empty_raises(self, tree):
        g, root_id, a_id, b_id, c_id = tree
        with pytest.raises(ValueError, match="cannot be empty"):
            split_node(a_id, [], g)

    def test_split_nonexistent_raises(self, tree):
        g = tree[0]
        with pytest.raises(ValueError, match="not found"):
            split_node("nonexistent", [{"title": "T"}], g)
