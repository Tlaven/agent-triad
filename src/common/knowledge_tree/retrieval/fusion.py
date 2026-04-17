"""结果融合（决策 21 第四节）。"""

from __future__ import annotations

from dataclasses import dataclass

from src.common.knowledge_tree.dag.node import KnowledgeNode
from src.common.knowledge_tree.retrieval.router import NavigationResult


@dataclass
class RetrievalResult:
    """融合后的检索结果。"""

    fusion_mode: str  # "tree" | "tree+rag" | "rag" | "none"
    nodes: list[KnowledgeNode]
    confidence: float  # 树导航置信度或 RAG 最高相似度
    tree_path: list[str]  # 树导航路径（node IDs）


def fuse_results(
    tree_result: NavigationResult | None,
    rag_results: list[tuple[KnowledgeNode, float]],
) -> RetrievalResult:
    """融合树导航和 RAG 结果。

    四种场景（决策 21 第四节）：
    1. tree: 树导航成功且内容充分
    2. tree+rag: 树导航成功但需 RAG 补充
    3. rag: 树导航失败，RAG 有结果
    4. none: 两者均无结果
    """
    tree_ok = tree_result is not None and tree_result.success and tree_result.final_node is not None
    rag_ok = len(rag_results) > 0

    if tree_ok and rag_ok:
        # 场景 2: 合并结果
        tree_node = tree_result.final_node
        assert tree_node is not None  # 已在 tree_ok 中检查
        rag_nodes = [n for n, _ in rag_results if n.node_id != tree_node.node_id]
        all_nodes = [tree_node] + rag_nodes
        return RetrievalResult(
            fusion_mode="tree+rag",
            nodes=all_nodes,
            confidence=tree_result.confidence,
            tree_path=tree_result.path,
        )

    if tree_ok:
        # 场景 1: 仅树结果
        assert tree_result.final_node is not None
        return RetrievalResult(
            fusion_mode="tree",
            nodes=[tree_result.final_node],
            confidence=tree_result.confidence,
            tree_path=tree_result.path,
        )

    if rag_ok:
        # 场景 3: 仅 RAG 结果
        return RetrievalResult(
            fusion_mode="rag",
            nodes=[n for n, _ in rag_results],
            confidence=rag_results[0][1],
            tree_path=tree_result.path if tree_result else [],
        )

    # 场景 4: 无结果
    return RetrievalResult(
        fusion_mode="none",
        nodes=[],
        confidence=0.0,
        tree_path=tree_result.path if tree_result else [],
    )
