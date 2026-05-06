"""Semantic embedder test infrastructure.

Provides ``requires_semantic`` marker for tests that need the real
``BAAI/bge-small-zh-v1.5`` model.  Tests are automatically skipped when
sentence-transformers is not installed or the model is not cached locally.
"""

from __future__ import annotations

import pytest

_has_semantic: bool = False
_semantic_reason: str = ""

try:
    from sentence_transformers import SentenceTransformer

    _model = SentenceTransformer("BAAI/bge-small-zh-v1.5", local_files_only=True)
    _has_semantic = True
    del _model  # free memory; tests will load their own instance
except ImportError:
    _semantic_reason = "sentence-transformers not installed"
except Exception as exc:
    _semantic_reason = f"bge-small-zh-v1.5 not cached locally: {exc}"


requires_semantic = pytest.mark.skipif(
    not _has_semantic,
    reason=_semantic_reason or "bge-small-zh-v1.5 not available",
)


def _create_real_semantic_embedder():
    """Return a callable embedder backed by bge-small-zh-v1.5.

    Caller must ensure ``_has_semantic`` is True before calling.
    """
    from src.common.knowledge_tree.embedding.semantic import create_semantic_embedder

    embedder = create_semantic_embedder("BAAI/bge-small-zh-v1.5", 512)
    assert embedder is not None, "Semantic embedder creation failed unexpectedly"
    return embedder
