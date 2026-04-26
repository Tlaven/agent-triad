"""Sentence-transformers 语义 embedder（线程安全）。

P2 组件：替换默认的 n-gram hash embedder，提供真正的语义向量。
需要 sentence-transformers 包：`pip install "agent-triad[knowledge-tree]"`.
"""

from __future__ import annotations

import logging
import threading
from typing import Callable

logger = logging.getLogger(__name__)


def create_semantic_embedder(
    model_name: str,
    dimension: int,
) -> Callable[[str], list[float]] | None:
    """创建语义 embedder，失败返回 None。

    Args:
        model_name: sentence-transformers 模型名（如 "BAAI/bge-small-zh-v1.5"）。
        dimension: 期望的向量维度（用于验证）。

    Returns:
        Callable[[str], list[float]] 或 None（如果加载失败）。
    """
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError:
        logger.warning(
            "sentence-transformers not installed. "
            "Install with: pip install 'agent-triad[knowledge-tree]'. "
            "Falling back to hash embedder."
        )
        return None

    try:
        model = SentenceTransformer(model_name)
    except Exception as e:
        logger.warning("Failed to load embedding model '%s': %s. Falling back to hash embedder.", model_name, e)
        return None

    # 验证维度
    test_vec = model.encode("test")
    actual_dim = len(test_vec)
    if actual_dim != dimension:
        logger.info(
            "Embedding model '%s' outputs %d-dim vectors (config says %d). Using actual dimension.",
            model_name, actual_dim, dimension,
        )

    lock = threading.Lock()

    def embed(text: str) -> list[float]:
        with lock:
            return model.encode(text).tolist()

    logger.info("Loaded semantic embedder: %s (%d-dim)", model_name, actual_dim)
    return embed
