"""向量索引操作（Layer 2）+ 目录锚点管理。

V4: 向量是文件系统的派生物。
content_embedding — 纯内容语义，永不变。
stored_vector — P2: α·content + β·structural（目录锚点）。
目录锚点 = 目录内所有文件 content_embedding 的质心。
"""

from __future__ import annotations

import logging
import math
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class DirectoryAnchor:
    """目录锚点——目录内所有文件 content_embedding 的质心。"""

    directory: str  # 目录相对路径
    anchor_vector: list[float]
    file_count: int
    last_updated: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "directory": self.directory,
            "anchor_vector": self.anchor_vector,
            "file_count": self.file_count,
            "last_updated": self.last_updated,
        }


class BaseVectorStore(ABC):
    """向量索引抽象接口。"""

    @abstractmethod
    def upsert_embedding(self, node_id: str, embedding: list[float]) -> None:
        """插入或更新节点的 content_embedding。"""

    @abstractmethod
    def get_embedding(self, node_id: str) -> list[float] | None:
        """获取节点的 content_embedding。"""

    @abstractmethod
    def similarity_search(
        self,
        query_embedding: list[float],
        top_k: int = 5,
        threshold: float = 0.7,
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

    # -- 目录锚点 --

    @abstractmethod
    def upsert_anchor(self, anchor: DirectoryAnchor) -> None:
        """插入或更新目录锚点。"""

    @abstractmethod
    def get_anchor(self, directory: str) -> DirectoryAnchor | None:
        """获取目录锚点。"""

    @abstractmethod
    def get_all_anchors(self) -> list[DirectoryAnchor]:
        """获取所有目录锚点。"""

    @abstractmethod
    def delete_anchor(self, directory: str) -> bool:
        """删除目录锚点。"""

    @abstractmethod
    def similarity_search_with_prefix(
        self,
        prefix: str,
        query_embedding: list[float],
        top_k: int = 5,
        threshold: float = 0.15,
    ) -> list[tuple[str, float]]:
        """在具有指定前缀的 embedding 中搜索。

        Args:
            prefix: node_id 前缀（如 "title:"）。
            query_embedding: 查询向量。
            top_k: 返回最多 K 个结果。
            threshold: 相似度阈值。

        Returns:
            (node_id, similarity_score) 列表。
        """

    @abstractmethod
    def find_nearest_anchor(
        self,
        query_embedding: list[float],
        threshold: float = 0.5,
    ) -> DirectoryAnchor | None:
        """找到与查询向量最相似的目录锚点。"""

    @abstractmethod
    def close(self) -> None:
        """清理资源。"""


class InMemoryVectorStore(BaseVectorStore):
    """内存向量存储（测试和回退用）。

    使用余弦相似度。不持久化。
    """

    def __init__(self, dimension: int = 512) -> None:
        self._embeddings: dict[str, list[float]] = {}
        self._anchors: dict[str, DirectoryAnchor] = {}
        self._dimension = dimension

    # -- 节点向量 --

    def upsert_embedding(self, node_id: str, embedding: list[float]) -> None:
        self._embeddings[node_id] = embedding

    def get_embedding(self, node_id: str) -> list[float] | None:
        return self._embeddings.get(node_id)

    def similarity_search(
        self,
        query_embedding: list[float],
        top_k: int = 5,
        threshold: float = 0.7,
    ) -> list[tuple[str, float]]:
        if not self._embeddings:
            return []

        results: list[tuple[str, float]] = []
        for node_id, emb in self._embeddings.items():
            # 跳过 title: 前缀的条目（它们是辅助索引）
            if node_id.startswith("title:"):
                continue
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

    # -- 目录锚点 --

    def upsert_anchor(self, anchor: DirectoryAnchor) -> None:
        self._anchors[anchor.directory] = anchor

    def get_anchor(self, directory: str) -> DirectoryAnchor | None:
        return self._anchors.get(directory)

    def get_all_anchors(self) -> list[DirectoryAnchor]:
        return list(self._anchors.values())

    def delete_anchor(self, directory: str) -> bool:
        if directory in self._anchors:
            del self._anchors[directory]
            return True
        return False

    def find_nearest_anchor(
        self,
        query_embedding: list[float],
        threshold: float = 0.5,
    ) -> DirectoryAnchor | None:
        """找到与查询向量最相似的目录锚点。"""
        best: tuple[DirectoryAnchor, float] | None = None
        for anchor in self._anchors.values():
            score = _cosine_similarity(query_embedding, anchor.anchor_vector)
            if score >= threshold and (best is None or score > best[1]):
                best = (anchor, score)
        return best[0] if best is not None else None

    def similarity_search_with_prefix(
        self,
        prefix: str,
        query_embedding: list[float],
        top_k: int = 5,
        threshold: float = 0.15,
    ) -> list[tuple[str, float]]:
        """在具有指定前缀的 embedding 中搜索。"""
        results: list[tuple[str, float]] = []
        for node_id, emb in self._embeddings.items():
            if not node_id.startswith(prefix):
                continue
            score = _cosine_similarity(query_embedding, emb)
            if score >= threshold:
                results.append((node_id, score))
        results.sort(key=lambda x: x[1], reverse=True)
        return results[:top_k]

    def close(self) -> None:
        self._embeddings.clear()
        self._anchors.clear()

    # -- 辅助 --

    def get_embeddings_in_directory(self, directory: str) -> dict[str, list[float]]:
        """获取指定目录下所有文件的 embedding。"""
        prefix = directory.rstrip("/") + "/"
        return {
            nid: emb
            for nid, emb in self._embeddings.items()
            if nid.startswith(prefix)
        }


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """计算余弦相似度。"""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def compute_anchor_vector(embeddings: list[list[float]]) -> list[float]:
    """计算目录锚点向量 = 归一化的 embedding 均值。"""
    if not embeddings:
        return []
    dim = len(embeddings[0])
    mean = [0.0] * dim
    for emb in embeddings:
        for i in range(dim):
            mean[i] += emb[i]
    for i in range(dim):
        mean[i] /= len(embeddings)
    # 归一化
    norm = math.sqrt(sum(x * x for x in mean))
    if norm == 0.0:
        return mean
    return [x / norm for x in mean]
