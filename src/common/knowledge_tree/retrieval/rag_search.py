"""RAG 向量相似度检索。

V4: RAG 是主检索路径（不再是 fallback）。
使用 content_embedding 进行纯语义检索（P1），
P2 将使用 stored_vector（含 structural 信息）。
"""

from __future__ import annotations

import logging

from src.common.knowledge_tree.dag.node import KnowledgeNode
from src.common.knowledge_tree.storage.markdown_store import MarkdownStore
from src.common.knowledge_tree.storage.vector_store import BaseVectorStore

logger = logging.getLogger(__name__)


def rag_search(
    query_vector: list[float],
    vector_store: BaseVectorStore,
    md_store: MarkdownStore,
    top_k: int = 5,
    threshold: float = 0.7,
) -> list[tuple[KnowledgeNode, float]]:
    """向量相似度检索。

    P1: 纯 content_embedding 检索。
    P2: stored_vector 检索（含 structural 信息）。

    Args:
        query_vector: 查询向量。
        vector_store: 向量索引。
        md_store: 文件系统存储（用于加载完整节点）。
        top_k: 返回最多 K 个结果。
        threshold: 相似度阈值。

    Returns:
        (node, similarity) 列表，按相似度降序。
    """
    raw_results = vector_store.similarity_search(
        query_vector, top_k=top_k, threshold=threshold
    )

    # 将 node_id 解析为完整节点
    results: list[tuple[KnowledgeNode, float]] = []
    for node_id, score in raw_results:
        node = md_store.read_node(node_id)
        if node is not None:
            # 附带 embedding 信息
            node.embedding = vector_store.get_embedding(node_id)
            results.append((node, score))
        else:
            logger.warning("Vector store references non-existent file: %s", node_id)

    return results
