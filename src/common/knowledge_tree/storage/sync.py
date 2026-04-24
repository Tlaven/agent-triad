"""文件系统 → 向量索引单向派生同步。

V4: 文件系统是 Source of Truth，向量是派生物。
sync 从 markdown 文件重新生成向量索引和目录锚点。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable

from src.common.knowledge_tree.storage.markdown_store import MarkdownStore
from src.common.knowledge_tree.storage.vector_store import (
    BaseVectorStore,
    DirectoryAnchor,
    compute_anchor_vector,
)

logger = logging.getLogger(__name__)


@dataclass
class SyncReport:
    """同步结果报告。"""

    nodes_synced: int = 0
    embeddings_updated: int = 0
    anchors_computed: int = 0
    errors: list[str] = field(default_factory=list)


def full_rebuild(
    md_store: MarkdownStore,
    vector_store: BaseVectorStore,
    embedder: Callable[[str], list[float]],
) -> SyncReport:
    """全量重建：从 Markdown 文件重建向量索引 + 目录锚点。

    Args:
        md_store: 文件系统存储。
        vector_store: 向量索引。
        embedder: text → embedding 向量化函数。
    """
    report = SyncReport()

    nodes = md_store.list_nodes()
    # directory → list[content_embedding]
    dir_embeddings: dict[str, list[list[float]]] = {}

    for node in nodes:
        try:
            # 生成 content_embedding
            embedding = embedder(node.content)
            node.embedding = embedding
            vector_store.upsert_embedding(node.node_id, embedding)
            report.embeddings_updated += 1

            # 同时索引 title embedding
            if node.title:
                title_embedding = embedder(node.title)
                vector_store.upsert_embedding(f"title:{node.node_id}", title_embedding)
                report.embeddings_updated += 1

            # 收集目录信息用于锚点计算
            parts = node.node_id.rsplit("/", 1)
            directory = parts[0] if len(parts) > 1 else ""
            dir_embeddings.setdefault(directory, []).append(embedding)

            report.nodes_synced += 1
        except Exception as e:
            err = f"Failed to rebuild node {node.node_id}: {e}"
            report.errors.append(err)
            logger.error(err)

    # 计算目录锚点
    for directory, embeddings in dir_embeddings.items():
        try:
            anchor_vec = compute_anchor_vector(embeddings)
            if anchor_vec:
                anchor = DirectoryAnchor(
                    directory=directory,
                    anchor_vector=anchor_vec,
                    file_count=len(embeddings),
                )
                vector_store.upsert_anchor(anchor)
                report.anchors_computed += 1
        except Exception as e:
            err = f"Failed to compute anchor for {directory}: {e}"
            report.errors.append(err)
            logger.error(err)

    return report


def sync_node(
    node_id: str,
    content: str,
    md_store: MarkdownStore,
    vector_store: BaseVectorStore,
    embedder: Callable[[str], list[float]],
) -> None:
    """同步单个节点：写入文件 + 更新向量 + 刷新目录锚点。"""
    embedding = embedder(content)
    vector_store.upsert_embedding(node_id, embedding)

    # 刷新目录锚点
    parts = node_id.rsplit("/", 1)
    directory = parts[0] if len(parts) > 1 else ""
    if directory:
        _refresh_anchor(directory, md_store, vector_store)


def _refresh_anchor(
    directory: str,
    md_store: MarkdownStore,
    vector_store: BaseVectorStore,
) -> None:
    """重新计算目录锚点。"""
    embeddings: list[list[float]] = []
    for nid in md_store.get_directory_files(directory):
        emb = vector_store.get_embedding(nid)
        if emb is not None:
            embeddings.append(emb)

    if embeddings:
        anchor_vec = compute_anchor_vector(embeddings)
        if anchor_vec:
            anchor = DirectoryAnchor(
                directory=directory,
                anchor_vector=anchor_vec,
                file_count=len(embeddings),
            )
            vector_store.upsert_anchor(anchor)
    else:
        # 目录空了，删除锚点
        vector_store.delete_anchor(directory)
