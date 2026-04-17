"""Merge/Split 编辑操作（决策 22 P1）。"""

from __future__ import annotations

import logging
from typing import Any

from src.common.knowledge_tree.dag.edge import KnowledgeEdge
from src.common.knowledge_tree.dag.node import KnowledgeNode
from src.common.knowledge_tree.storage.graph_store import BaseGraphStore

logger = logging.getLogger(__name__)


def merge_nodes(
    node_ids: list[str],
    graph_store: BaseGraphStore,
    title: str | None = None,
    content: str | None = None,
    summary: str | None = None,
) -> KnowledgeNode:
    """合并多个节点为一个。

    流程：
    1. 读取所有源节点
    2. 创建新的合并节点（标题/内容/摘要可由 Agent 指定或自动合并）
    3. 继承所有源节点的边（子边重指向新节点，父边保留）
    4. 删除源节点

    Raises:
        ValueError: 节点不存在或只有一个节点。
    """
    if len(node_ids) < 2:
        raise ValueError("Merge requires at least 2 node IDs")

    source_nodes: list[KnowledgeNode] = []
    for nid in node_ids:
        node = graph_store.get_node(nid)
        if node is None:
            raise ValueError(f"Node not found: {nid}")
        source_nodes.append(node)

    # 自动合并内容
    merged_title = title or " + ".join(n.title for n in source_nodes)
    merged_content = content or "\n\n---\n\n".join(
        f"## {n.title}\n{n.content}" for n in source_nodes
    )
    merged_summary = summary or "; ".join(n.summary for n in source_nodes if n.summary)

    merged = KnowledgeNode.create(
        title=merged_title,
        content=merged_content,
        summary=merged_summary,
        source="merged",
        metadata={"merged_from": node_ids},
    )

    # 继承边
    for source in source_nodes:
        edges = graph_store.get_edges_for_node(source.node_id)
        for edge in edges:
            if edge.child_id == source.node_id:
                # 入边：重新指向合并节点
                graph_store.upsert_edge(KnowledgeEdge.create(
                    parent_id=edge.parent_id,
                    child_id=merged.node_id,
                    is_primary=edge.is_primary,
                    edge_type=edge.edge_type,
                ))
            elif edge.parent_id == source.node_id:
                # 出边：重新指向合并节点
                graph_store.upsert_edge(KnowledgeEdge.create(
                    parent_id=merged.node_id,
                    child_id=edge.child_id,
                    is_primary=edge.is_primary,
                    edge_type=edge.edge_type,
                ))
            graph_store.delete_edge(edge.edge_id)

    # 写入合并节点，删除源节点
    graph_store.upsert_node(merged)
    for source in source_nodes:
        graph_store.delete_node(source.node_id)

    return merged


def split_node(
    node_id: str,
    splits: list[dict[str, str]],
    graph_store: BaseGraphStore,
) -> list[KnowledgeNode]:
    """拆分一个节点为多个子节点。

    Args:
        node_id: 要拆分的节点 ID。
        splits: 子节点定义列表，每项含 title, content, summary。
        graph_store: 图数据库。

    Returns:
        新创建的子节点列表。

    Raises:
        ValueError: 节点不存在或 splits 为空。
    """
    if not splits:
        raise ValueError("Splits cannot be empty")

    parent = graph_store.get_node(node_id)
    if parent is None:
        raise ValueError(f"Node not found: {node_id}")

    children: list[KnowledgeNode] = []
    for i, spec in enumerate(splits):
        child = KnowledgeNode.create(
            title=spec.get("title", f"{parent.title} (part {i + 1})"),
            content=spec.get("content", ""),
            summary=spec.get("summary", ""),
            source=parent.source,
        )
        graph_store.upsert_node(child)
        graph_store.upsert_edge(KnowledgeEdge.create(
            parent_id=node_id,
            child_id=child.node_id,
            is_primary=True,
        ))
        children.append(child)

    # 更新父节点摘要
    parent.summary = f"Split into {len(splits)} parts: " + ", ".join(c.title for c in children)
    parent.metadata["split_into"] = [c.node_id for c in children]
    graph_store.upsert_node(parent)

    return children
