"""增量摄入：将候选节点嫁接到知识树。"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable

from src.common.knowledge_tree.dag.edge import KnowledgeEdge
from src.common.knowledge_tree.dag.node import KnowledgeNode
from src.common.knowledge_tree.storage.graph_store import BaseGraphStore
from src.common.knowledge_tree.storage.markdown_store import MarkdownStore
from src.common.knowledge_tree.storage.sync import sync_node_to_stores
from src.common.knowledge_tree.storage.vector_store import BaseVectorStore

logger = logging.getLogger(__name__)


@dataclass
class IngestReport:
    """摄入结果报告。"""

    nodes_ingested: int = 0
    nodes_deduplicated: int = 0
    nodes_filtered: int = 0
    errors: list[str] = field(default_factory=list)


def ingest_nodes(
    candidates: list[KnowledgeNode],
    graph_store: BaseGraphStore,
    vector_store: BaseVectorStore,
    md_store: MarkdownStore,
    embedder: Callable[[str], list[float]],
    dedup_threshold: float = 0.95,
    cluster_attach_threshold: float = 0.7,
) -> IngestReport:
    """增量嫁接候选节点到知识树。

    对每个候选节点：
    1. 生成嵌入向量
    2. 向量去重检查（similarity > dedup_threshold → 跳过）
    3. 找最匹配的现有 group → 嫁接或创建新 group
    4. 同步三层存储

    Args:
        candidates: 候选 KnowledgeNode 列表。
        graph_store: 图数据库。
        vector_store: 向量存储。
        md_store: Markdown 存储。
        embedder: 嵌入函数。
        dedup_threshold: 去重相似度阈值（默认 0.95）。
        cluster_attach_threshold: 嫁接到现有 group 的相似度阈值（默认 0.7）。

    Returns:
        IngestReport 统计信息。
    """
    report = IngestReport()

    if not candidates:
        return report

    root_id = graph_store.get_root_id()
    if root_id is None:
        report.errors.append("No root node found — tree not initialized")
        return report

    for node in candidates:
        try:
            # 1. 生成嵌入
            if node.embedding is None:
                node.embedding = embedder(node.content or node.title)

            # 2. 去重检查
            existing = vector_store.similarity_search(node.embedding, top_k=1, threshold=0.0)
            if existing:
                _, similarity = existing[0]
                if similarity > dedup_threshold:
                    logger.info(
                        "Dedup: skipping node %s (sim=%.3f with existing)",
                        node.node_id[:8],
                        similarity,
                    )
                    report.nodes_deduplicated += 1
                    continue

            # 3. 找最匹配的现有 group
            best_group_id: str | None = None
            best_sim: float = 0.0

            root_children = graph_store.get_children(root_id)
            for group in root_children:
                group_embedding = vector_store.get_embedding(group.node_id)
                if group_embedding is None:
                    # 用 group 的 content 生成嵌入
                    group_embedding = embedder(group.content or group.title)
                    vector_store.upsert_embedding(group.node_id, group_embedding)

                sim = _cosine_similarity(node.embedding, group_embedding)
                if sim > best_sim:
                    best_sim = sim
                    best_group_id = group.node_id

            # 4. 嫁接或创建新 group
            if best_group_id and best_sim > cluster_attach_threshold:
                # 嫁接到现有 group
                _attach_to_group(
                    node, best_group_id, graph_store, md_store, vector_store,
                )
                logger.info(
                    "Attached node %s to group %s (sim=%.3f)",
                    node.node_id[:8],
                    best_group_id[:8],
                    best_sim,
                )
            else:
                # 创建新 group
                _create_new_group(
                    node, root_id, graph_store, md_store, vector_store, embedder,
                )
                logger.info(
                    "Created new group for node %s (best_sim=%.3f < threshold=%.3f)",
                    node.node_id[:8],
                    best_sim,
                    cluster_attach_threshold,
                )

            report.nodes_ingested += 1

        except Exception as e:
            report.errors.append(f"Ingest failed for {node.node_id}: {e}")
            logger.warning("Ingest failed for %s: %s", node.node_id[:8], e)

    return report


def _attach_to_group(
    node: KnowledgeNode,
    group_id: str,
    graph_store: BaseGraphStore,
    md_store: MarkdownStore,
    vector_store: BaseVectorStore,
) -> None:
    """将节点嫁接到现有 group 下。"""
    # group → leaf 边
    graph_store.upsert_edge(KnowledgeEdge.create(
        parent_id=group_id,
        child_id=node.node_id,
        is_primary=True,
    ))
    # 同步到三层存储
    sync_node_to_stores(node, md_store, graph_store, vector_store)


def _create_new_group(
    node: KnowledgeNode,
    root_id: str,
    graph_store: BaseGraphStore,
    md_store: MarkdownStore,
    vector_store: BaseVectorStore,
    embedder: Callable[[str], list[float]],
) -> None:
    """为节点创建新 group 并挂到 root 下。"""
    # 新 group 节点
    group_title = node.title[:20] if node.title else "Untitled Group"
    group_node = KnowledgeNode.create(
        title=group_title,
        content=f"Category: {group_title}",
        summary=f"Auto-created group: {group_title}",
        source="ingestion",
    )
    group_node.embedding = embedder(group_node.content)

    # root → group
    graph_store.upsert_edge(KnowledgeEdge.create(
        parent_id=root_id,
        child_id=group_node.node_id,
        is_primary=True,
    ))
    sync_node_to_stores(group_node, md_store, graph_store, vector_store)

    # group → leaf
    graph_store.upsert_edge(KnowledgeEdge.create(
        parent_id=group_node.node_id,
        child_id=node.node_id,
        is_primary=True,
    ))
    sync_node_to_stores(node, md_store, graph_store, vector_store)


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """计算余弦相似度。"""
    if len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    mag_a = sum(x * x for x in a) ** 0.5
    mag_b = sum(x * x for x in b) ** 0.5
    if mag_a == 0 or mag_b == 0:
        return 0.0
    return dot / (mag_a * mag_b)
