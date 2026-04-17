"""KnowledgeTreeConfig 测试。"""

from pathlib import Path

import pytest

from src.common.knowledge_tree.config import KnowledgeTreeConfig


class TestKnowledgeTreeConfig:
    def test_defaults(self):
        cfg = KnowledgeTreeConfig()
        assert cfg.tree_nav_confidence == 0.7
        assert cfg.rag_similarity_threshold == 0.85
        assert cfg.max_tree_depth == 5
        assert cfg.embedding_dimension == 512
        assert cfg.max_optimizations_per_window == 10

    def test_custom_paths(self, tmp_path: Path):
        cfg = KnowledgeTreeConfig(
            markdown_root=tmp_path / "md",
            db_path=tmp_path / "db",
        )
        assert cfg.markdown_root == tmp_path / "md"
        assert cfg.db_path == tmp_path / "db"

    def test_from_context(self):
        from src.common.context import Context

        ctx = Context(enable_knowledge_tree=True)
        cfg = KnowledgeTreeConfig.from_context(ctx)
        assert isinstance(cfg, KnowledgeTreeConfig)
        assert cfg.tree_nav_confidence == 0.7  # 默认值

    def test_from_context_custom_values(self):
        from src.common.context import Context

        ctx = Context(
            kt_tree_nav_confidence=0.8,
            kt_max_tree_depth=3,
            kt_embedding_model="custom-model",
        )
        cfg = KnowledgeTreeConfig.from_context(ctx)
        assert cfg.tree_nav_confidence == 0.8
        assert cfg.max_tree_depth == 3
        assert cfg.embedding_model == "custom-model"
