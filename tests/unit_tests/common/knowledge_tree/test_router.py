"""Tree Navigation Router 测试。"""

import json
from unittest.mock import MagicMock

import pytest

from src.common.knowledge_tree.dag.edge import KnowledgeEdge
from src.common.knowledge_tree.dag.node import KnowledgeNode
from src.common.knowledge_tree.retrieval.router import NavigationResult, navigate_tree
from src.common.knowledge_tree.storage.graph_store import InMemoryGraphStore


def _build_tree():
    """构建测试树：root → [a, b], a → [c]"""
    graph = InMemoryGraphStore()
    graph.initialize()

    root = KnowledgeNode.create(title="Root", content="Root node", summary="Root summary")
    a = KnowledgeNode.create(title="Category A", content="A content", summary="A summary")
    b = KnowledgeNode.create(title="Category B", content="B content", summary="B summary")
    c = KnowledgeNode.create(title="Leaf C", content="C content", summary="C summary")

    for n in [root, a, b, c]:
        graph.upsert_node(n)

    graph.upsert_edge(KnowledgeEdge.create(parent_id=root.node_id, child_id=a.node_id, is_primary=True))
    graph.upsert_edge(KnowledgeEdge.create(parent_id=root.node_id, child_id=b.node_id, is_primary=True))
    graph.upsert_edge(KnowledgeEdge.create(parent_id=a.node_id, child_id=c.node_id, is_primary=True))

    return graph, root, a, b, c


def _mock_llm(select_index: int, confidence: float):
    """创建返回固定路由决策的 mock LLM。"""
    llm = MagicMock()
    response = json.dumps({"selected_index": select_index, "confidence": confidence})
    llm.invoke.return_value = response
    return llm


class TestNavigateTree:
    def test_navigate_to_leaf(self):
        """导航到叶子节点。"""
        graph, root, a, b, c = _build_tree()
        # 第一次选择 A（index 0），第二次选择 C（index 0）
        llm = MagicMock()
        llm.invoke.side_effect = [
            json.dumps({"selected_index": 0, "confidence": 0.9}),
            json.dumps({"selected_index": 0, "confidence": 0.95}),
        ]

        result = navigate_tree("查询C", graph, llm, confidence_threshold=0.7)
        assert result.success is True
        assert result.final_node.node_id == c.node_id
        assert len(result.path) == 3  # root → a → c

    def test_low_confidence_stops(self):
        """低置信度停止导航。"""
        graph, root, a, b, c = _build_tree()
        llm = _mock_llm(select_index=0, confidence=0.3)  # 低于阈值

        result = navigate_tree("查询", graph, llm, confidence_threshold=0.7)
        assert result.success is False
        assert result.final_node.node_id == root.node_id  # 停在根节点

    def test_no_root(self):
        """无根节点。"""
        graph = InMemoryGraphStore()
        graph.initialize()
        llm = _mock_llm(0, 0.9)

        result = navigate_tree("查询", graph, llm)
        assert result.success is False
        assert result.final_node is None

    def test_max_depth(self):
        """达到最大深度。"""
        graph, root, a, b, c = _build_tree()
        # 总是选择 A，无限循环但被 max_depth 截断
        llm = MagicMock()
        llm.invoke.return_value = json.dumps({"selected_index": 0, "confidence": 0.9})

        result = navigate_tree("查询", graph, llm, max_depth=2)
        assert len(result.path) <= 3  # root + 最多 2 层
