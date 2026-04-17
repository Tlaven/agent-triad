"""结果融合测试。"""

import pytest

from src.common.knowledge_tree.dag.node import KnowledgeNode
from src.common.knowledge_tree.retrieval.fusion import RetrievalResult, fuse_results
from src.common.knowledge_tree.retrieval.router import NavigationResult


@pytest.fixture
def tree_node() -> KnowledgeNode:
    return KnowledgeNode.create(title="T", content="C", summary="S")


@pytest.fixture
def rag_node() -> KnowledgeNode:
    return KnowledgeNode.create(title="R", content="RC", summary="RS")


def _nav_result(success: bool, node: KnowledgeNode | None, confidence: float = 0.9) -> NavigationResult:
    return NavigationResult(
        path=["root", node.node_id] if node else [],
        confidence=confidence,
        final_node=node,
        success=success,
    )


class TestFuseResults:
    def test_tree_only(self, tree_node: KnowledgeNode):
        """树导航成功，无 RAG → fusion_mode="tree"。"""
        nav = _nav_result(True, tree_node)
        result = fuse_results(nav, [])
        assert result.fusion_mode == "tree"
        assert len(result.nodes) == 1
        assert result.nodes[0].node_id == tree_node.node_id

    def test_tree_plus_rag(self, tree_node: KnowledgeNode, rag_node: KnowledgeNode):
        """树导航成功 + RAG 有结果 → fusion_mode="tree+rag"。"""
        nav = _nav_result(True, tree_node)
        rag = [(rag_node, 0.9)]
        result = fuse_results(nav, rag)
        assert result.fusion_mode == "tree+rag"
        assert len(result.nodes) == 2

    def test_rag_only(self, rag_node: KnowledgeNode):
        """树导航失败，RAG 有结果 → fusion_mode="rag"。"""
        nav = _nav_result(False, None)
        rag = [(rag_node, 0.9)]
        result = fuse_results(nav, rag)
        assert result.fusion_mode == "rag"
        assert len(result.nodes) == 1
        assert result.confidence == 0.9

    def test_none(self):
        """两者均无结果 → fusion_mode="none"。"""
        nav = _nav_result(False, None)
        result = fuse_results(nav, [])
        assert result.fusion_mode == "none"
        assert result.nodes == []

    def test_tree_success_none_input(self, tree_node: KnowledgeNode):
        """tree_result=None, 无 RAG → none。"""
        result = fuse_results(None, [])
        assert result.fusion_mode == "none"

    def test_tree_plus_rag_no_duplicate(self, tree_node: KnowledgeNode):
        """tree+rag 中 RAG 结果与树结果相同节点时不重复。"""
        nav = _nav_result(True, tree_node)
        rag = [(tree_node, 0.95)]  # 同一节点
        result = fuse_results(nav, rag)
        assert result.fusion_mode == "tree+rag"
        assert len(result.nodes) == 1  # 不重复
