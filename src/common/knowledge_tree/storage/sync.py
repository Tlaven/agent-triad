"""跨层同步：Markdown ↔ 图数据库 ↔ 向量索引。

Markdown 是 Source of Truth：
- 写入顺序：Markdown 先写 → 图数据库 → 向量索引
- 全量同步：从 Markdown 重建图数据库和向量索引
- 单节点同步：编辑后同步单个节点
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from src.common.knowledge_tree.dag.edge import KnowledgeEdge
from src.common.knowledge_tree.dag.node import KnowledgeNode
from src.common.knowledge_tree.storage.graph_store import BaseGraphStore
from src.common.knowledge_tree.storage.markdown_store import MarkdownStore
from src.common.knowledge_tree.storage.vector_store import BaseVectorStore

logger = logging.getLogger(__name__)


@dataclass
class SyncReport:
    """同步结果报告。"""

    nodes_synced: int = 0
    edges_synced: int = 0
    embeddings_updated: int = 0
    errors: list[str] = field(default_factory=list)


def sync_markdown_to_graph(
    md_store: MarkdownStore,
    graph_store: BaseGraphStore,
) -> SyncReport:
    """全量同步：从 Markdown 文件重建图数据库中的节点。

    注意：此函数只同步节点数据（内容/元数据），不同步边。
    边由 bootstrap 或编辑操作单独管理。
    """
    report = SyncReport()
    nodes = md_store.list_nodes()

    graph_store.initialize()

    for node in nodes:
        try:
            graph_store.upsert_node(node)
            report.nodes_synced += 1
        except Exception as e:
            err = f"Failed to sync node {node.node_id}: {e}"
            report.errors.append(err)
            logger.error(err)

    return report


def sync_node_to_stores(
    node: KnowledgeNode,
    md_store: MarkdownStore,
    graph_store: BaseGraphStore,
    vector_store: BaseVectorStore | None = None,
) -> None:
    """单节点同步：Markdown + 图数据库 + 可选向量索引。"""
    # 1. Markdown（SoT 先写）
    md_store.write_node(node)

    # 2. 图数据库
    graph_store.upsert_node(node)

    # 3. 向量索引（如果有嵌入）
    if vector_store is not None and node.embedding is not None:
        vector_store.upsert_embedding(node.node_id, node.embedding)


def full_rebuild(
    md_store: MarkdownStore,
    graph_store: BaseGraphStore,
    vector_store: BaseVectorStore,
    embedder: object,
) -> SyncReport:
    """全量重建：从 Markdown 重新构建图数据库和向量索引。

    Args:
        embedder: 可调用对象 embedder(text: str) -> list[float]
    """
    report = SyncReport()
    nodes = md_store.list_nodes()

    graph_store.initialize()

    for node in nodes:
        try:
            # 生成嵌入（如果需要）
            if node.embedding is None and callable(embedder):
                node.embedding = embedder(node.content)  # type: ignore[operator]

            graph_store.upsert_node(node)

            if node.embedding is not None:
                vector_store.upsert_embedding(node.node_id, node.embedding)
                report.embeddings_updated += 1

            report.nodes_synced += 1
        except Exception as e:
            err = f"Failed to rebuild node {node.node_id}: {e}"
            report.errors.append(err)
            logger.error(err)

    return report
