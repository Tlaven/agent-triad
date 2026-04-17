"""RAG 向量兜底检索（决策 21 第三节）。"""

from __future__ import annotations

import logging

from src.common.knowledge_tree.dag.node import KnowledgeNode
from src.common.knowledge_tree.storage.graph_store import BaseGraphStore
from src.common.knowledge_tree.storage.vector_store import BaseVectorStore

logger = logging.getLogger(__name__)


def rag_search(
    query_embedding: list[float],
    graph_store: BaseGraphStore,
    vector_store: BaseVectorStore,
    top_k: int = 5,
    threshold: float = 0.85,
) -> list[tuple[KnowledgeNode, float]]:
    """向量相似度检索。

    Returns:
        (node, similarity_score) 列表，按相似度降序。
    """
    raw_results = vector_store.similarity_search(
        query_embedding, top_k=top_k, threshold=threshold
    )

    # 将 node_id 解析为完整节点
    results: list[tuple[KnowledgeNode, float]] = []
    for node_id, score in raw_results:
        node = graph_store.get_node(node_id)
        if node is not None:
            results.append((node, score))
        else:
            logger.warning("Vector store references non-existent node: %s", node_id)

    return results
