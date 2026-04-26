"""增量摄入：将新知识嫁接到知识树。

V4: 通过目录锚点定位放置目录。
新知识 → content_embedding → 比较目录锚点 → 放入对应目录。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable

from src.common.knowledge_tree.dag.node import KnowledgeNode
from src.common.knowledge_tree.storage.markdown_store import MarkdownStore
from src.common.knowledge_tree.storage.overlay import OverlayStore
from src.common.knowledge_tree.storage.sync import _refresh_anchor
from src.common.knowledge_tree.storage.vector_store import (
    BaseVectorStore,
    DirectoryAnchor,
    compute_anchor_vector,
)

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
    vector_store: BaseVectorStore,
    md_store: MarkdownStore,
    overlay_store: OverlayStore,
    embedder: Callable[[str], list[float]],
    dedup_threshold: float = 0.95,
    attach_threshold: float = 0.7,
) -> IngestReport:
    """增量嫁接候选节点到知识树。

    对每个候选节点：
    1. embed → content_embedding
    2. vector_store.search(top-1) → 去重检查
    3. 找最相似的目录锚点 → 确定放置目录
    4. 写入 Markdown 文件到对应目录
    5. 更新向量索引

    Args:
        candidates: 候选 KnowledgeNode 列表。
        vector_store: 向量索引。
        md_store: 文件系统存储。
        overlay_store: Overlay 存储。
        embedder: text → embedding 向量化函数。
        dedup_threshold: 去重相似度阈值。
        attach_threshold: 目录锚点相似度阈值。

    Returns:
        IngestReport 统计信息。
    """
    report = IngestReport()

    if not candidates:
        return report

    for node in candidates:
        try:
            # 1. 生成 content_embedding
            if node.embedding is None:
                node.embedding = embedder(node.content or node.title)

            # 2. 去重检查
            existing = vector_store.similarity_search(
                node.embedding, top_k=1, threshold=dedup_threshold
            )
            if existing:
                _, similarity = existing[0]
                logger.info(
                    "Dedup: skipping %s (sim=%.3f)",
                    node.title[:20],
                    similarity,
                )
                report.nodes_deduplicated += 1
                continue

            # 3. 找最相似的目录锚点
            best_anchor = vector_store.find_nearest_anchor(
                node.embedding, threshold=attach_threshold
            )

            if best_anchor is not None:
                # 放入对应目录
                directory = best_anchor.directory
                filename = _sanitize_filename(node.title) + ".md"
                node.node_id = f"{directory}/{filename}" if directory else filename
                node.directory = directory

                logger.info(
                    "Ingest: placing %s in %s (anchor sim ok)",
                    node.title[:20],
                    directory,
                )
            else:
                # 创建新目录
                dir_name = _sanitize_dirname(node.title)
                md_store.ensure_directory(dir_name)
                filename = _sanitize_filename(node.title) + ".md"
                node.node_id = f"{dir_name}/{filename}"
                node.directory = dir_name

                # 新目录锚点 = 该文件的 content_embedding
                anchor = DirectoryAnchor(
                    directory=dir_name,
                    anchor_vector=node.embedding,
                    file_count=1,
                    last_updated=datetime.now(timezone.utc).isoformat(),
                )
                vector_store.upsert_anchor(anchor)

                logger.info(
                    "Ingest: created new directory %s for %s",
                    dir_name,
                    node.title[:20],
                )

            # 4. 写入 Markdown 文件
            md_store.write_node(node)

            # 5. 更新向量索引
            vector_store.upsert_embedding(node.node_id, node.embedding)

            # 5b. 同时索引 title embedding
            if node.title:
                title_embedding = embedder(node.title)
                vector_store.upsert_embedding(f"title:{node.node_id}", title_embedding)

            # 6. 刷新目录锚点
            _refresh_anchor(node.directory, md_store, vector_store)

            report.nodes_ingested += 1

        except Exception as e:
            report.errors.append(f"Ingest failed for {node.title[:20]}: {e}")
            logger.warning("Ingest failed: %s", e)

    return report


def _sanitize_filename(title: str) -> str:
    """从标题生成安全的文件名。"""
    # 移除不安全字符
    safe = "".join(c if c.isalnum() or c in ("_", "-", " ", ".") else "_" for c in title)
    safe = safe.strip()[:40]  # 限制长度
    return safe or "untitled"


def _sanitize_dirname(title: str) -> str:
    """从标题生成安全的目录名。优先使用 ASCII，避免超长中文路径。"""
    # 只保留 ASCII 字母数字、下划线和连字符
    ascii_parts = []
    has_alpha = False
    for c in title:
        if c.isascii() and c.isalpha():
            ascii_parts.append(c.lower())
            has_alpha = True
        elif c.isascii() and c.isdigit():
            ascii_parts.append(c)
        elif c == " " or c == "_":
            ascii_parts.append("_")
        elif c == "-":
            ascii_parts.append("-")
        else:
            ascii_parts.append("_")
    safe = "".join(ascii_parts)
    # 合并连续下划线
    while "__" in safe:
        safe = safe.replace("__", "_")
    safe = safe.strip("_")[:25]
    # 如果结果太短或全是数字，回退
    if not safe or not has_alpha:
        return "misc"
    return safe
