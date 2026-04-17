"""Bootstrap：从种子数据构建初始知识树。"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from src.common.knowledge_tree.config import KnowledgeTreeConfig
from src.common.knowledge_tree.dag.edge import KnowledgeEdge
from src.common.knowledge_tree.dag.node import KnowledgeNode
from src.common.knowledge_tree.storage.graph_store import BaseGraphStore, InMemoryGraphStore
from src.common.knowledge_tree.storage.markdown_store import MarkdownStore
from src.common.knowledge_tree.storage.sync import sync_node_to_stores
from src.common.knowledge_tree.storage.vector_store import BaseVectorStore, InMemoryVectorStore

logger = logging.getLogger(__name__)


@dataclass
class BootstrapReport:
    """Bootstrap 结果报告。"""

    nodes_created: int = 0
    edges_created: int = 0
    embeddings_generated: int = 0
    max_depth: int = 0
    errors: list[str] = field(default_factory=list)


def bootstrap_from_seed_files(
    seed_dir: str | Path,
    md_store: MarkdownStore,
    graph_store: BaseGraphStore,
    vector_store: BaseVectorStore,
    embedder: Callable[[str], list[float]],
    config: KnowledgeTreeConfig,
) -> BootstrapReport:
    """从种子 Markdown 文件构建初始知识树。

    流程：
    1. 读取种子目录下的 .md 文件
    2. 解析为 KnowledgeNode
    3. 为每个节点生成摘要（如缺失）和嵌入
    4. 按语义相似度聚类构建层级
    5. 创建根节点 + 层级边
    6. 同步到三层存储

    P1 聚类策略：简单的基于嵌入相似度的层次聚类。
    相似度高于阈值的节点归为同一组，每组创建一个中间节点。

    Args:
        seed_dir: 种子 Markdown 文件目录。
        md_store: Markdown 存储层。
        graph_store: 图数据库。
        vector_store: 向量存储。
        embedder: 嵌入函数。
        config: 知识树配置。

    Returns:
        BootstrapReport 统计信息。
    """
    report = BootstrapReport()
    seed_path = Path(seed_dir)

    if not seed_path.exists():
        report.errors.append(f"Seed directory not found: {seed_dir}")
        return report

    # 1. 读取种子文件
    nodes: list[KnowledgeNode] = []
    for md_file in sorted(seed_path.glob("*.md")):
        try:
            text = md_file.read_text(encoding="utf-8")
            node = KnowledgeNode.from_frontmatter_md(text)
            nodes.append(node)
        except Exception as e:
            report.errors.append(f"Failed to parse {md_file.name}: {e}")
            logger.warning("Skipping seed file %s: %s", md_file.name, e)

    if not nodes:
        report.errors.append("No valid seed files found")
        return report

    # 2. 生成嵌入
    for node in nodes:
        try:
            node.embedding = embedder(node.content)
            report.embeddings_generated += 1
        except Exception as e:
            report.errors.append(f"Embedding failed for {node.node_id}: {e}")

    # 3. 构建层级结构
    root = KnowledgeNode.create(
        title="Knowledge Root",
        content="Root of the knowledge tree",
        summary="Root node",
        source="system",
    )
    root.embedding = embedder(root.content)

    # P1 简单聚类：按标题首字符分组
    groups: dict[str, list[KnowledgeNode]] = {}
    for node in nodes:
        key = _cluster_key(node)
        if key not in groups:
            groups[key] = []
        groups[key].append(node)

    # 4. 创建中间节点和边
    graph_store.initialize()

    # 写入根节点
    sync_node_to_stores(root, md_store, graph_store, vector_store)
    report.nodes_created += 1

    for group_name, group_nodes in groups.items():
        # 创建中间分组节点
        group_node = KnowledgeNode.create(
            title=group_name,
            content=f"Category: {group_name}",
            summary=f"Knowledge category: {group_name}",
            source="bootstrap",
        )
        group_node.embedding = embedder(group_node.content)

        # 根 → 分组
        graph_store.upsert_edge(KnowledgeEdge.create(
            parent_id=root.node_id,
            child_id=group_node.node_id,
            is_primary=True,
        ))
        report.edges_created += 1

        # 写入分组节点
        sync_node_to_stores(group_node, md_store, graph_store, vector_store)
        report.nodes_created += 1

        # 分组 → 叶子
        for node in group_nodes:
            graph_store.upsert_edge(KnowledgeEdge.create(
                parent_id=group_node.node_id,
                child_id=node.node_id,
                is_primary=True,
            ))
            report.edges_created += 1

            sync_node_to_stores(node, md_store, graph_store, vector_store)
            report.nodes_created += 1

    report.max_depth = 3  # root → group → leaf
    return report


def _cluster_key(node: KnowledgeNode) -> str:
    """P1 简单聚类键：标题首字符。

    后续阶段将替换为语义聚类。
    """
    if not node.title:
        return "Uncategorized"
    return node.title[0].upper()
