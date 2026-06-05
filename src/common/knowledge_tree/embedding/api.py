"""SiliconFlow / OpenAI-compatible embedding API provider.

Calls remote embedding API to get semantic vectors.
Requires: SILICONFLOW_API_KEY (or OPENAI_API_KEY) in environment.
"""

from __future__ import annotations

import logging
import os
import threading
from collections.abc import Callable
from pathlib import Path

logger = logging.getLogger(__name__)


def create_api_embedder(
    api_key: str | None = None,
    model: str = "BAAI/bge-large-zh-v1.5",
    base_url: str = "https://api.siliconflow.cn/v1",
    dimension: int = 1024,
    timeout: float = 30.0,
    cache_path: Path | None = None,
) -> Callable[[str], list[float]] | None:
    """Create an API-based embedder that calls SiliconFlow or OpenAI-compatible endpoint.

    Args:
        api_key: API key. Falls back to SILICONFLOW_API_KEY or OPENAI_API_KEY env var.
        model: Embedding model name.
        base_url: API base URL.
        dimension: Expected vector dimension (for validation).
        timeout: Request timeout in seconds.

    Returns:
        Callable[[str], list[float]] or None if setup fails.
    """
    if api_key is None:
        api_key = os.environ.get("SILICONFLOW_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if not api_key:
        logger.warning("No API key found for embedding. Set SILICONFLOW_API_KEY.")
        return None

    try:
        import httpx
    except ImportError:
        logger.warning("httpx not installed. Falling back to hash embedder.")
        return None

    _lock = threading.Lock()
    _url = f"{base_url.rstrip('/')}/embeddings"
    _headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    from src.common.knowledge_tree.embedding.cache import EmbeddingCache

    cache = EmbeddingCache(cache_path)

    def embed(text: str) -> list[float]:
        cached = cache.get(text)
        if cached is not None:
            return cached

        payload = {
            "model": model,
            "input": text,
            "encoding_format": "float",
        }
        with _lock:
            resp = httpx.post(_url, json=payload, headers=_headers, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
        vec = data["data"][0]["embedding"]

        if len(vec) != dimension:
            logger.warning(
                "API embedder: expected %d-dim, got %d-dim. Using actual.",
                dimension,
                len(vec),
            )
        cache.put(text, vec)
        return vec

    try:
        test_vec = embed("test")
        logger.info(
            "API embedder ready: model=%s dim=%d (%.0fms test latency)",
            model,
            len(test_vec),
            0,
        )
    except Exception as e:
        logger.warning("API embedder test failed: %s. Falling back.", e)
        return None

    embed._cache = cache  # type: ignore[attr-defined]
    return embed
