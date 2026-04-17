"""局部重嵌入：编辑后更新受影响节点的向量。"""

from __future__ import annotations

import logging
from typing import Callable

from src.common.knowledge_tree.storage.graph_store import BaseGraphStore
from src.common.knowledge_tree.storage.vector_store import BaseVectorStore

logger = logging.getLogger(__name__)


def re_embed_nodes(
    node_ids: list[str],
    graph_store: BaseGraphStore,
    vector_store: BaseVectorStore,
    embedder: Callable[[str], list[float]],
) -> int:
    """对受影响的节点重新生成嵌入并更新。

    Args:
        node_ids: 需要重嵌入的节点 ID。
        graph_store: 图数据库（读取节点内容）。
        vector_store: 向量存储（更新嵌入）。
        embedder: 嵌入函数 embedder(text) -> list[float]。

    Returns:
        成功更新的节点数。
    """
    updated = 0
    for node_id in node_ids:
        node = graph_store.get_node(node_id)
        if node is None:
            logger.warning("re_embed: node %s not found, skipping", node_id)
            continue

        try:
            embedding = embedder(node.content)
            node.embedding = embedding
            graph_store.upsert_node(node)
            vector_store.upsert_embedding(node_id, embedding)
            updated += 1
        except Exception as e:
            logger.error("re_embed: failed for node %s: %s", node_id, e)

    return updated
