"""Layer 3: 向量索引操作（抽象接口）。

P1 使用 Kùzu 内置向量索引或内存回退。
实际向量计算由调用方负责，此模块仅管理存储和检索。
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod

from src.common.knowledge_tree.dag.node import KnowledgeNode

logger = logging.getLogger(__name__)


class BaseVectorStore(ABC):
    """向量索引抽象接口。"""

    @abstractmethod
    def upsert_embedding(self, node_id: str, embedding: list[float]) -> None:
        """插入或更新节点的向量嵌入。"""

    @abstractmethod
    def get_embedding(self, node_id: str) -> list[float] | None:
        """获取节点的向量嵌入。"""

    @abstractmethod
    def similarity_search(
        self,
        query_embedding: list[float],
        top_k: int = 5,
        threshold: float = 0.85,
    ) -> list[tuple[str, float]]:
        """向量相似度检索。

        Args:
            query_embedding: 查询向量。
            top_k: 返回最多 K 个结果。
            threshold: 相似度阈值。

        Returns:
            (node_id, similarity_score) 列表，按相似度降序。
        """

    @abstractmethod
    def delete_embedding(self, node_id: str) -> bool:
        """删除节点的向量嵌入。"""

    @abstractmethod
    def close(self) -> None:
        """清理资源。"""


class InMemoryVectorStore(BaseVectorStore):
    """内存向量存储（测试和回退用）。

    使用余弦相似度计算。向量存储在内存中，不持久化。
    """

    def __init__(self, dimension: int = 512) -> None:
        self._embeddings: dict[str, list[float]] = {}
        self._dimension = dimension

    def upsert_embedding(self, node_id: str, embedding: list[float]) -> None:
        self._embeddings[node_id] = embedding

    def get_embedding(self, node_id: str) -> list[float] | None:
        return self._embeddings.get(node_id)

    def similarity_search(
        self,
        query_embedding: list[float],
        top_k: int = 5,
        threshold: float = 0.85,
    ) -> list[tuple[str, float]]:
        if not self._embeddings:
            return []

        results: list[tuple[str, float]] = []
        for node_id, emb in self._embeddings.items():
            score = _cosine_similarity(query_embedding, emb)
            if score >= threshold:
                results.append((node_id, score))

        results.sort(key=lambda x: x[1], reverse=True)
        return results[:top_k]

    def delete_embedding(self, node_id: str) -> bool:
        if node_id in self._embeddings:
            del self._embeddings[node_id]
            return True
        return False

    def close(self) -> None:
        self._embeddings.clear()


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """计算余弦相似度。"""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(x * x for x in b) ** 0.5
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)
