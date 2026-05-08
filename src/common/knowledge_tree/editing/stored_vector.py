"""P2: structural_vector 混合 — stored_vector = normalize(alpha*content + beta*structural)。

structural_vector = 所属目录的 anchor_vector。
stored_vector 用于检索，增强同目录文件的聚簇效果。
content_embedding 保持不变，用于去重和锚点计算。
"""

from __future__ import annotations

import logging
import math

from src.common.knowledge_tree.storage.markdown_store import MarkdownStore
from src.common.knowledge_tree.storage.vector_store import BaseVectorStore

logger = logging.getLogger(__name__)


def compute_stored_vector(
    content_embedding: list[float],
    structural_vector: list[float],
    content_weight: float = 0.8,
    structural_weight: float = 0.2,
) -> list[float]:
    """计算 stored_vector = normalize(alpha * content + beta * structural).

    Args:
        content_embedding: 纯内容语义向量（永不变）。
        structural_vector: 目录锚点向量。
        content_weight: alpha 权重（默认 0.8）。
        structural_weight: beta 权重（默认 0.2）。

    Returns:
        归一化的混合向量。
    """
    dim = len(content_embedding)
    mixed = [
        content_weight * content_embedding[i] + structural_weight * structural_vector[i]
        for i in range(dim)
    ]
    # 归一化
    norm = math.sqrt(sum(x * x for x in mixed))
    if norm == 0.0:
        return mixed
    return [x / norm for x in mixed]


def get_structural_vector_for_node(
    node_id: str,
    vector_store: BaseVectorStore,
) -> list[float] | None:
    """获取节点的 structural_vector = 其所属目录的 anchor_vector.

    Args:
        node_id: 文件相对路径。
        vector_store: 向量索引。

    Returns:
        目录锚点向量，或 None（根目录文件或无锚点时）。
    """
    directory = _extract_directory(node_id)
    if not directory:
        return None
    anchor = vector_store.get_anchor(directory)
    if anchor is None:
        return None
    return anchor.anchor_vector


def compute_and_store_stored_vector(
    node_id: str,
    vector_store: BaseVectorStore,
    content_weight: float = 0.8,
    structural_weight: float = 0.2,
) -> list[float] | None:
    """为单个节点计算并存储 stored_vector.

    存储在 vector_store 的 "stored:{node_id}" 键下。
    如果节点没有 content_embedding 或没有所属目录锚点，跳过。

    Returns:
        计算出的 stored_vector，或 None。
    """
    content_emb = vector_store.get_embedding(node_id)
    if content_emb is None:
        return None

    structural = get_structural_vector_for_node(node_id, vector_store)
    if structural is None:
        # 无锚点时不生成 stored_vector（保留 content_embedding 作为检索依据）
        return None

    stored = compute_stored_vector(content_emb, structural, content_weight, structural_weight)
    vector_store.upsert_embedding(f"stored:{node_id}", stored)
    return stored


def compute_stored_vectors_for_directory(
    directory: str,
    md_store: MarkdownStore,
    vector_store: BaseVectorStore,
    content_weight: float = 0.8,
    structural_weight: float = 0.2,
) -> int:
    """为目录内所有节点重新计算 stored_vector（锚点刷新后调用）.

    Args:
        directory: 目录相对路径。
        md_store: 文件系统存储。
        vector_store: 向量索引。
        content_weight: alpha 权重。
        structural_weight: beta 权重。

    Returns:
        更新的节点数。
    """
    file_ids = md_store.get_directory_files(directory)
    if not file_ids:
        return 0

    updated = 0
    for node_id in file_ids:
        result = compute_and_store_stored_vector(
            node_id, vector_store, content_weight, structural_weight
        )
        if result is not None:
            updated += 1

    if updated:
        logger.debug(
            "stored_vector: updated %d/%d nodes in %s", updated, len(file_ids), directory
        )
    return updated


def compute_all_stored_vectors(
    md_store: MarkdownStore,
    vector_store: BaseVectorStore,
    content_weight: float = 0.8,
    structural_weight: float = 0.2,
) -> int:
    """为所有节点计算 stored_vector（bootstrap / full_rebuild 后调用）.

    Returns:
        更新的节点数。
    """
    total = 0
    for directory in md_store.list_directories():
        total += compute_stored_vectors_for_directory(
            directory, md_store, vector_store, content_weight, structural_weight
        )
    return total


def _extract_directory(node_id: str) -> str:
    """从 node_id（相对路径）提取目录部分。"""
    parts = node_id.rsplit("/", 1)
    return parts[0] if len(parts) > 1 else ""
