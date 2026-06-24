"""向量索引持久化集成测试：验证重启后知识树能从磁盘恢复。"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.common.knowledge_tree import KnowledgeTree
from src.common.knowledge_tree.config import KnowledgeTreeConfig


def _mock_embedder():
    def embed(text: str, dim: int = 512) -> list[float]:
        base = sum(ord(c) for c in text) / 1000.0
        return [base + i * 0.001 for i in range(dim)]
    return embed


def _default_config(md_root: Path, **overrides) -> KnowledgeTreeConfig:
    defaults = dict(
        markdown_root=md_root,
        embedder_type="hash",
        embedding_dimension=512,
        vector_persistence_enabled=True,
    )
    defaults.update(overrides)
    return KnowledgeTreeConfig(**defaults)


def _create_seed_files(md_root: Path):
    md_root.mkdir(parents=True, exist_ok=True)
    (md_root / "arch").mkdir()
    (md_root / "arch" / "design.md").write_text(
        "---\ntitle: Architecture Design\n---\nSystem uses three agents.\n", "utf-8"
    )
    (md_root / "arch" / "patterns.md").write_text(
        "---\ntitle: Design Patterns\n---\nObserver pattern for events.\n", "utf-8"
    )


class TestVectorPersistenceRoundTrip:
    """测试完整的持久化闭环：创建 → 保存 → 重新加载。"""

    def test_save_and_reload(self, tmp_path: Path):
        md_root = tmp_path / "kt"
        _create_seed_files(md_root)
        config = _default_config(md_root)
        kt1 = KnowledgeTree(config, embedder=_mock_embedder())
        result = kt1.bootstrap()
        assert result["ok"]

        assert kt1.vector_store.node_count > 0
        assert len(kt1.vector_store.get_all_anchors()) > 0
        original_count = kt1.vector_store.node_count

        assert kt1.save() is True
        assert (md_root / ".vector_index.json").is_file()

        kt2 = KnowledgeTree(config, embedder=_mock_embedder())
        result2 = kt2.bootstrap()
        assert result2["ok"]
        assert result2.get("skipped") is True
        assert "Loaded from persisted" in result2.get("message", "")

        assert kt2.vector_store.node_count == original_count
        assert len(kt2.vector_store.get_all_anchors()) > 0

        results, _ = kt2.retrieve("architecture design")
        assert len(results) > 0

    def test_reload_after_ingest(self, tmp_path: Path):
        md_root = tmp_path / "kt"
        _create_seed_files(md_root)
        config = _default_config(md_root)
        kt = KnowledgeTree(config, embedder=_mock_embedder())
        kt.bootstrap()

        report = kt.ingest(
            "Python uses GIL for thread safety. The Global Interpreter Lock ensures only one thread executes Python bytecode at a time. This has significant implications for CPU-bound multi-threaded programs.",
            trigger="task_complete",
            source="auto:executor",
        )
        assert report.nodes_ingested > 0 or report.nodes_deduplicated > 0, (
            f"Expected ingestion but got: ingested={report.nodes_ingested}, "
            f"dedup={report.nodes_deduplicated}, filtered={report.nodes_filtered}"
        )

        assert (md_root / ".vector_index.json").is_file()

        kt2 = KnowledgeTree(config, embedder=_mock_embedder())
        result = kt2.bootstrap()
        assert result.get("skipped") is True

        results, _ = kt2.retrieve("Python GIL thread")
        assert len(results) > 0

    def test_fallback_to_bootstrap_on_stale(self, tmp_path: Path):
        md_root = tmp_path / "kt"
        _create_seed_files(md_root)
        config = _default_config(md_root)
        kt = KnowledgeTree(config, embedder=_mock_embedder())
        kt.bootstrap()
        kt.save()

        (md_root / "arch" / "design.md").write_text(
            "---\ntitle: Modified\n---\nCompletely new content here.\n", "utf-8"
        )

        kt2 = KnowledgeTree(config, embedder=_mock_embedder())
        result = kt2.bootstrap()
        assert result["ok"]
        assert kt2.vector_store.node_count > 0

    def test_fallback_to_bootstrap_on_new_file(self, tmp_path: Path):
        md_root = tmp_path / "kt"
        _create_seed_files(md_root)
        config = _default_config(md_root)
        kt = KnowledgeTree(config, embedder=_mock_embedder())
        kt.bootstrap()
        kt.save()

        (md_root / "arch" / "new.md").write_text(
            "---\ntitle: New\n---\nBrand new node.\n", "utf-8"
        )

        kt2 = KnowledgeTree(config, embedder=_mock_embedder())
        result = kt2.bootstrap()
        assert result["ok"]

    def test_fallback_on_corrupted_index(self, tmp_path: Path):
        md_root = tmp_path / "kt"
        _create_seed_files(md_root)
        config = _default_config(md_root)
        kt = KnowledgeTree(config, embedder=_mock_embedder())
        kt.bootstrap()
        kt.save()

        (md_root / ".vector_index.json").write_text("corrupted", "utf-8")

        kt2 = KnowledgeTree(config, embedder=_mock_embedder())
        result = kt2.bootstrap()
        assert result["ok"]
        assert kt2.vector_store.node_count > 0

    def test_persistence_disabled(self, tmp_path: Path):
        md_root = tmp_path / "kt"
        _create_seed_files(md_root)
        config = _default_config(md_root, vector_persistence_enabled=False)
        kt = KnowledgeTree(config, embedder=_mock_embedder())
        kt.bootstrap()

        assert not (md_root / ".vector_index.json").is_file()
        assert kt.save() is False

    def test_embedder_type_mismatch_triggers_rebuild(self, tmp_path: Path):
        md_root = tmp_path / "kt"
        _create_seed_files(md_root)
        config_hash = _default_config(md_root, embedder_type="hash")
        kt = KnowledgeTree(config_hash, embedder=_mock_embedder())
        kt.bootstrap()
        kt.save()

        config_api = _default_config(md_root, embedder_type="api")
        kt2 = KnowledgeTree(config_api, embedder=_mock_embedder())
        result = kt2.bootstrap()
        assert result["ok"]

    def test_dimension_mismatch_in_vectors_triggers_rebuild(self, tmp_path: Path):
        md_root = tmp_path / "kt"
        _create_seed_files(md_root)
        config = _default_config(md_root)
        kt = KnowledgeTree(config, embedder=_mock_embedder())
        kt.bootstrap()
        kt.save()

        import json

        index_path = md_root / ".vector_index.json"
        payload = json.loads(index_path.read_text("utf-8"))
        for key in payload["vectors"]["embeddings"]:
            payload["vectors"]["embeddings"][key] = [0.1] * 256
        payload["vectors"]["dimension"] = 256
        index_path.write_text(json.dumps(payload), "utf-8")

        kt2 = KnowledgeTree(config, embedder=_mock_embedder())
        result = kt2.bootstrap()
        assert result["ok"]
        assert kt2.vector_store.node_count > 0