"""Unit tests for EmbeddingCache."""

import json
from pathlib import Path

from src.common.knowledge_tree.embedding.cache import EmbeddingCache, _content_hash


def test_content_hash_deterministic() -> None:
    h1 = _content_hash("hello world")
    h2 = _content_hash("hello world")
    assert h1 == h2
    assert len(h1) == 64  # SHA-256 hex


def test_content_hash_different_inputs() -> None:
    assert _content_hash("a") != _content_hash("b")


def test_cache_disabled_returns_none() -> None:
    cache = EmbeddingCache(cache_path=None)
    assert cache.get("anything") is None
    cache.put("anything", [1.0, 2.0])
    assert cache.get("anything") is None


def test_cache_hit_and_miss(tmp_path: Path) -> None:
    cache = EmbeddingCache(tmp_path / "test_cache.json")
    assert cache.get("hello") is None  # miss
    cache.put("hello", [1.0, 2.0, 3.0])
    assert cache.get("hello") == [1.0, 2.0, 3.0]  # hit


def test_cache_persistence(tmp_path: Path) -> None:
    path = tmp_path / "persist.json"
    cache1 = EmbeddingCache(path)
    cache1.put("text", [0.5, 0.6])
    cache1.flush()

    cache2 = EmbeddingCache(path)
    assert cache2.get("text") == [0.5, 0.6]


def test_cache_no_flush_when_not_dirty(tmp_path: Path) -> None:
    path = tmp_path / "nodirty.json"
    cache = EmbeddingCache(path)
    cache.flush()  # not dirty, should not create file
    assert not path.exists()


def test_cache_corruption_recovery(tmp_path: Path) -> None:
    path = tmp_path / "corrupt.json"
    path.write_text("{invalid json", "utf-8")
    cache = EmbeddingCache(path)
    assert cache.get("anything") is None  # should not crash


def test_cache_overwrite_on_put(tmp_path: Path) -> None:
    cache = EmbeddingCache(tmp_path / "overwrite.json")
    cache.put("text", [1.0])
    cache.put("text", [2.0])
    assert cache.get("text") == [2.0]


def test_stats(tmp_path: Path) -> None:
    cache = EmbeddingCache(tmp_path / "stats.json")
    assert cache.stats["enabled"] is True
    assert cache.stats["entries"] == 0
    cache.put("a", [1.0])
    assert cache.stats["entries"] == 1
    assert cache.stats["dirty"] is True
