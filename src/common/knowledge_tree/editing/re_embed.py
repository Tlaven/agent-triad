"""局部重嵌入：编辑后更新受影响节点的向量。"""

from __future__ import annotations

import logging
from typing import Callable

from src.common.knowledge_tree.storage.markdown_store import MarkdownStore
from src.common.knowledge_tree.storage.vector_store import BaseVectorStore

logger = logging.getLogger(__name__)


def re_embed_nodes(
    node_ids: list[str],
    md_store: MarkdownStore,
    vector_store: BaseVectorStore,
    embedder: Callable[[str], list[float]],
) -> int:
    """对受影响的节点重新生成嵌入并更新。

    重嵌入后自动刷新受影响目录的锚点（Change Mapping 闭环）。

    Args:
        node_ids: 需要重嵌入的节点 ID（文件相对路径）。
        md_store: 文件系统存储（读取节点内容）。
        vector_store: 向量存储（更新嵌入）。
        embedder: 嵌入函数 embedder(text) -> list[float]。

    Returns:
        成功更新的节点数。
    """
    from src.common.knowledge_tree.storage.markdown_store import MarkdownStore as _MS

    updated = 0
    affected_dirs: set[str] = set()

    for node_id in node_ids:
        node = md_store.read_node(node_id)
        if node is None:
            logger.warning("re_embed: node %s not found, skipping", node_id)
            continue

        try:
            embedding = embedder(node.content)
            vector_store.upsert_embedding(node_id, embedding)
            updated += 1

            directory = _MS._extract_directory(node_id)
            if directory:
                affected_dirs.add(directory)
        except Exception as e:
            logger.error("re_embed: failed for node %s: %s", node_id, e)

    # Change Mapping: 刷新受影响目录的锚点
    if affected_dirs:
        from src.common.knowledge_tree.storage.sync import _refresh_anchor

        for directory in affected_dirs:
            _refresh_anchor(directory, md_store, vector_store)

    return updated
