"""知识树运行时配置。

V4: 两层存储 + Overlay 架构。
文件系统目录层级 = 树结构，向量通过目录锚点聚簇。
"""

from __future__ import annotations

from dataclasses import dataclass
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

    # -- 检索阈值 --
    rag_similarity_threshold: float = 0.15
    max_tree_depth: int = 5

    # -- 嵌入 --
    embedding_model: str = "BAAI/bge-small-zh-v1.5"
    embedding_dimension: int = 512

    # -- 摄入管道 --
    ingest_enabled: bool = True
    ingest_chunk_max_tokens: int = 512
    dedup_threshold: float = 0.95
    ingest_attach_threshold: float = 0.7  # 目录锚点相似度阈值

    # -- P2 混合向量 --
    structural_weight: float = 0.2  # β
    content_weight: float = 0.8  # α

    # -- P3 优化闭环 --
    optimization_window: int = 3600
    max_optimizations_per_window: int = 10
    total_failure_threshold: int = 3
    rag_false_positive_threshold: int = 3
    content_insufficient_threshold: int = 5

    @classmethod
    def from_context(cls, ctx: Context) -> KnowledgeTreeConfig:
        """从 Context 构造配置。"""
        return cls(
            markdown_root=Path(ctx.knowledge_tree_root),
            rag_similarity_threshold=ctx.kt_rag_similarity_threshold,
            max_tree_depth=ctx.kt_max_tree_depth,
            embedding_model=ctx.kt_embedding_model,
            embedding_dimension=ctx.kt_embedding_dimension,
            ingest_enabled=ctx.kt_ingest_enabled,
            ingest_chunk_max_tokens=ctx.kt_ingest_chunk_max_tokens,
            dedup_threshold=ctx.kt_dedup_threshold,
            ingest_attach_threshold=ctx.kt_ingest_attach_threshold,
            structural_weight=ctx.kt_structural_weight,
            content_weight=ctx.kt_content_weight,
            optimization_window=ctx.kt_optimization_window,
            max_optimizations_per_window=ctx.kt_max_optimizations_per_window,
            total_failure_threshold=ctx.kt_total_failure_threshold,
            rag_false_positive_threshold=ctx.kt_rag_false_positive_threshold,
            content_insufficient_threshold=ctx.kt_content_insufficient_threshold,
        )
