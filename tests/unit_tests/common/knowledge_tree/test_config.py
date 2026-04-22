"""KnowledgeTreeConfig 测试。"""

from pathlib import Path

import pytest

from src.common.knowledge_tree.config import KnowledgeTreeConfig


class TestKnowledgeTreeConfig:
    def test_defaults(self):
        cfg = KnowledgeTreeConfig()
        assert cfg.rag_similarity_threshold == 0.7
        assert cfg.max_tree_depth == 5
        assert cfg.embedding_dimension == 512
        assert cfg.max_optimizations_per_window == 10
        assert cfg.structural_weight == 0.2
        assert cfg.content_weight == 0.8

    def test_custom_paths(self, tmp_path: Path):
        cfg = KnowledgeTreeConfig(markdown_root=tmp_path / "md")
        assert cfg.markdown_root == tmp_path / "md"

    def test_from_context(self):
        from src.common.context import Context

        ctx = Context(enable_knowledge_tree=True)
        cfg = KnowledgeTreeConfig.from_context(ctx)
        assert isinstance(cfg, KnowledgeTreeConfig)
        assert cfg.rag_similarity_threshold == 0.7  # 默认值

    def test_from_context_custom_values(self):
        from src.common.context import Context

        ctx = Context(
            kt_rag_similarity_threshold=0.5,
            kt_max_tree_depth=3,
            kt_embedding_model="custom-model",
        )
        cfg = KnowledgeTreeConfig.from_context(ctx)
        assert cfg.rag_similarity_threshold == 0.5
        assert cfg.max_tree_depth == 3
        assert cfg.embedding_model == "custom-model"
