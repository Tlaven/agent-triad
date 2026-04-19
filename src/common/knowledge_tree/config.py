"""知识树运行时配置。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.common.context import Context


@dataclass(kw_only=True)
class KnowledgeTreeConfig:
    """知识树运行时配置，由 Context 字段构造。

    所有字段支持通过 Context 的 env-var 覆盖机制配置。
    """

    markdown_root: Path = Path("workspace/knowledge_tree")
    db_path: Path = Path("workspace/knowledge_tree/.kuzu")

    # -- 检索阈值 --
    tree_nav_confidence: float = 0.7
    rag_similarity_threshold: float = 0.85
    max_tree_depth: int = 5

    # -- 优化闭环 --
    optimization_window: int = 3600  # 秒
    max_optimizations_per_window: int = 10
    nav_failure_threshold: int = 5
    rag_false_positive_threshold: int = 3
    total_failure_threshold: int = 3
    content_insufficient_threshold: int = 5

    # -- 嵌入 --
    embedding_model: str = "BAAI/bge-small-zh-v1.5"
    embedding_dimension: int = 512

    # -- 聚类 --
    cluster_method: str = "auto"  # "auto" | "gmm" | "simple"
    cluster_size: int = 20  # GMM 目标每簇节点数

    # -- 摄入管道 --
    ingest_enabled: bool = True
    ingest_chunk_max_tokens: int = 512
    dedup_threshold: float = 0.95
    cluster_attach_threshold: float = 0.7

    @classmethod
    def from_context(cls, ctx: Context) -> KnowledgeTreeConfig:
        """从 Context 构造配置。"""
        return cls(
            markdown_root=Path(ctx.knowledge_tree_root),
            db_path=Path(ctx.knowledge_tree_db_path),
            tree_nav_confidence=ctx.kt_tree_nav_confidence,
            rag_similarity_threshold=ctx.kt_rag_similarity_threshold,
            max_tree_depth=ctx.kt_max_tree_depth,
            optimization_window=ctx.kt_optimization_window,
            max_optimizations_per_window=ctx.kt_max_optimizations_per_window,
            nav_failure_threshold=ctx.kt_nav_failure_threshold,
            rag_false_positive_threshold=ctx.kt_rag_false_positive_threshold,
            total_failure_threshold=ctx.kt_total_failure_threshold,
            content_insufficient_threshold=ctx.kt_content_insufficient_threshold,
            embedding_model=ctx.kt_embedding_model,
            embedding_dimension=ctx.kt_embedding_dimension,
            ingest_enabled=ctx.kt_ingest_enabled,
            ingest_chunk_max_tokens=ctx.kt_ingest_chunk_max_tokens,
            dedup_threshold=ctx.kt_dedup_threshold,
            cluster_attach_threshold=ctx.kt_cluster_attach_threshold,
        )
