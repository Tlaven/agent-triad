"""Bootstrap：从种子目录构建初始知识树。

V4: 目录结构直接成为树结构，不需要聚类算法。
种子目录下的子目录 = 树的分支，.md 文件 = 叶子节点。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
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
class BootstrapReport:
    """Bootstrap 结果报告。"""

    nodes_created: int = 0
    directories_created: int = 0
    embeddings_generated: int = 0
    anchors_computed: int = 0
    max_depth: int = 0
    errors: list[str] = field(default_factory=list)


def bootstrap_from_directory(
    seed_dir: Path,
    md_store: MarkdownStore,
    vector_store: BaseVectorStore,
    overlay_store: OverlayStore,
    embedder: Callable[[str], list[float]],
) -> BootstrapReport:
    """从种子目录构建初始知识树。

    流程：
    1. 递归扫描 seed_dir，读取目录层级 = 树结构
    2. 解析每个 .md 文件 → KnowledgeNode（node_id = 相对路径）
    3. 为每个文件生成 content_embedding
    4. 计算每个目录的锚点 = 目录内文件 content_embedding 的质心
    5. 写入向量索引

    Args:
        seed_dir: 种子目录（如 workspace/knowledge_tree/）。
        md_store: 文件系统存储（root 应为 seed_dir 或与之一致）。
        vector_store: 向量索引。
        overlay_store: Overlay 存储（bootstrap 时清空）。
        embedder: text → embedding 向量化函数。

    Returns:
        BootstrapReport 统计信息。
    """
    report = BootstrapReport()

    if not seed_dir.exists():
        report.errors.append(f"Seed directory not found: {seed_dir}")
        return report

    # 清空旧数据
    vector_store.close()
    overlay_store._edges.clear()
    overlay_store._save()

    # 收集所有 .md 文件
    md_files = sorted(
        p for p in seed_dir.rglob("*.md")
        if p.is_file() and not p.name.startswith(".")
    )

    if not md_files:
        report.errors.append(f"No .md files found in {seed_dir}")
        return report

    # directory → list[content_embedding] 用于锚点计算
    dir_embeddings: dict[str, list[list[float]]] = {}
    max_depth = 0

    for md_file in md_files:
        try:
            rel_path = str(md_file.relative_to(seed_dir)).replace("\\", "/")

            # 读取文件内容
            text = md_file.read_text(encoding="utf-8")
            node = KnowledgeNode.from_frontmatter_md(text, node_id=rel_path)

            # 生成 content_embedding
            embedding = embedder(node.content)
            node.embedding = embedding
            node.directory = _get_directory(rel_path)

            # 写入向量索引
            vector_store.upsert_embedding(node.node_id, embedding)
            report.embeddings_generated += 1

            # 收集目录信息
            directory = node.directory
            dir_embeddings.setdefault(directory, []).append(embedding)

            # 计算深度
            depth = rel_path.count("/")
            if depth > max_depth:
                max_depth = depth

            report.nodes_created += 1

        except Exception as e:
            err = f"Failed to process {md_file}: {e}"
            report.errors.append(err)
            logger.warning(err)

    # 计算目录锚点
    for directory, embeddings in dir_embeddings.items():
        try:
            anchor_vec = compute_anchor_vector(embeddings)
            if anchor_vec:
                anchor = DirectoryAnchor(
                    directory=directory,
                    anchor_vector=anchor_vec,
                    file_count=len(embeddings),
                    last_updated=datetime.now(timezone.utc).isoformat(),
                )
                vector_store.upsert_anchor(anchor)
                report.anchors_computed += 1
        except Exception as e:
            err = f"Failed to compute anchor for {directory}: {e}"
            report.errors.append(err)
            logger.warning(err)

    # 统计目录数
    directories: set[str] = set()
    for md_file in md_files:
        rel = str(md_file.relative_to(seed_dir)).replace("\\", "/")
        parts = rel.rsplit("/", 1)
        if len(parts) > 1:
            # 添加所有父目录
            dir_parts = parts[0].split("/")
            for i in range(len(dir_parts)):
                directories.add("/".join(dir_parts[: i + 1]))

    report.directories_created = len(directories)
    report.max_depth = max_depth

    logger.info(
        "Bootstrap complete: %d nodes, %d dirs, %d anchors, depth=%d",
        report.nodes_created,
        report.directories_created,
        report.anchors_computed,
        report.max_depth,
    )

    return report


def _get_directory(rel_path: str) -> str:
    """从相对路径提取目录部分。"""
    parts = rel_path.rsplit("/", 1)
    return parts[0] if len(parts) > 1 else ""
