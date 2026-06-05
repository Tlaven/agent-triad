"""Disk-backed embedding cache for bootstrap acceleration.

Stores text_hash -> embedding vector mappings as JSON.
Safe for concurrent reads; writes are single-threaded during bootstrap.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class EmbeddingCache:
    """Persistent embedding cache backed by a JSON file.

    If *cache_path* is ``None`` the cache is disabled and all operations
    become no-ops, making it safe to use with the hash embedder.
    """

    def __init__(self, cache_path: Path | None = None) -> None:
        self._enabled = cache_path is not None
        self._path = cache_path
        self._cache: dict[str, list[float]] = {}
        self._dirty = False
        self._lock = threading.Lock()
        if self._enabled and cache_path is not None:
            self._load()

    def _load(self) -> None:
        if self._path is None or not self._path.exists():
            return
        try:
            raw = self._path.read_text("utf-8")
            self._cache = json.loads(raw)
            logger.debug("EmbeddingCache: loaded %d entries from %s", len(self._cache), self._path)
        except (json.JSONDecodeError, OSError):
            logger.warning("EmbeddingCache: failed to load %s, starting empty", self._path)
            self._cache = {}

    def get(self, text: str) -> list[float] | None:
        if not self._enabled:
            return None
        return self._cache.get(_content_hash(text))

    def put(self, text: str, embedding: list[float]) -> None:
        if not self._enabled:
            return
        self._cache[_content_hash(text)] = embedding
        self._dirty = True

    def flush(self) -> None:
        if not self._enabled or not self._dirty or self._path is None:
            return
        with self._lock:
            try:
                self._path.parent.mkdir(parents=True, exist_ok=True)
                self._path.write_text(json.dumps(self._cache), "utf-8")
                self._dirty = False
                logger.debug("EmbeddingCache: flushed %d entries to %s", len(self._cache), self._path)
            except OSError:
                pass  # Non-critical; cache is best-effort

    @property
    def stats(self) -> dict[str, Any]:
        return {
            "enabled": self._enabled,
            "entries": len(self._cache),
            "dirty": self._dirty,
            "path": str(self._path) if self._path else None,
        }
