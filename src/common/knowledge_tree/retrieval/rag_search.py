"""RAG 向量相似度检索。

V4: RAG 是主检索路径（不再是 fallback）。
P1: content_embedding + title_embedding 双路检索 + 倒数秩融合。
P2: stored_vector 检索（含 structural 信息）。
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
    embedder: object = None,
    top_k: int = 5,
    threshold: float = 0.15,
) -> list[tuple[KnowledgeNode, float]]:
    """向量相似度检索（content + title 双路融合）。

    检索策略：
    1. content embedding 路径：query_vector vs content embeddings
    2. title embedding 路径：query_vector vs title embeddings（key 前缀 "title:"）
    3. 两路结果用倒数秩融合（RRF）合并

    Args:
        query_vector: 查询向量。
        vector_store: 向量索引。
        md_store: 文件系统存储（用于加载完整节点）。
        embedder: 可选的文本向量化函数（用于动态生成 title embedding）。
        top_k: 返回最多 K 个结果。
        threshold: 相似度阈值。

    Returns:
        (node, similarity) 列表，按相似度降序。
    """
    # 路径 1: content embedding
    content_results = vector_store.similarity_search(
        query_vector, top_k=top_k * 2, threshold=threshold
    )

    # 路径 2: title embedding
    title_results: list[tuple[str, float]] = []
    if embedder is not None:
        # 检查向量存储中是否有 title 前缀的 embedding
        title_query = query_vector  # 用同一个 query vector
        title_results = vector_store.similarity_search_with_prefix(
            "title:", title_query, top_k=top_k * 2, threshold=threshold
        )

    # 倒数秩融合（RRF）
    rrf_scores: dict[str, float] = {}
    k_rrf = 60  # RRF 平滑常数
    # 同时跟踪每条路径的最佳余弦相似度（用于返回给调用者）
    best_similarities: dict[str, float] = {}

    for rank, (node_id, score) in enumerate(content_results):
        rrf_scores[node_id] = rrf_scores.get(node_id, 0.0) + 1.0 / (k_rrf + rank + 1)
        best_similarities[node_id] = max(best_similarities.get(node_id, 0.0), score)

    for rank, (title_key, score) in enumerate(title_results):
        # title:node_id → node_id
        node_id = title_key[6:] if title_key.startswith("title:") else title_key
        rrf_scores[node_id] = rrf_scores.get(node_id, 0.0) + 1.0 / (k_rrf + rank + 1)
        best_similarities[node_id] = max(best_similarities.get(node_id, 0.0), score)

    # RRF 选出候选集（取 top_k）
    sorted_ids = sorted(rrf_scores.keys(), key=lambda x: rrf_scores[x], reverse=True)[:top_k]

    # 加载完整节点
    results: list[tuple[KnowledgeNode, float]] = []
    for node_id in sorted_ids:
        node = md_store.read_node(node_id)
        if node is not None:
            node.embedding = vector_store.get_embedding(node_id)
            results.append((node, best_similarities[node_id]))
        else:
            logger.warning("Vector store references non-existent file: %s", node_id)

    # 按实际相似度降序排列，确保返回分数单调递减
    results.sort(key=lambda x: x[1], reverse=True)

    return results
