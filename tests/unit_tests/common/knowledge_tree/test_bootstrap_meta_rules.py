"""Tests for meta rule seeding during bootstrap."""

from __future__ import annotations

from unittest.mock import MagicMock

from src.common.knowledge_tree.bootstrap import seed_meta_rules


class TestSeedMetaRules:
    """验证元规则种子写入。"""

    def test_seeds_all_five_rules(self):
        """应写入 5 条元规则。"""
        kt = MagicMock()
        kt.get_meta_rules.return_value = []
        seed_meta_rules(kt)
        assert kt.ingest.call_count == 6

    def test_seed_content_contains_kt_guidance(self):
        """种子内容应包含 KT 操作指导。"""
        kt = MagicMock()
        kt.get_meta_rules.return_value = []
        seed_meta_rules(kt)
        calls = kt.ingest.call_args_list
        all_content = " ".join(c[0][0] for c in calls)
        assert "ingest" in all_content
        assert "retrieve" in all_content

    def test_seed_does_not_duplicate_existing_rules(self):
        """已存在的元规则不应重复写入。"""
        from src.common.knowledge_tree.dag.node import KnowledgeNode

        existing = KnowledgeNode.create(
            node_id="meta_1",
            title="主动沉淀",
            content="当用户分享了项目特定信息（路径、配置、约定、偏好）时，用 knowledge_tree_ingest 沉淀到知识树",
            source="bootstrap",
            metadata={"node_type": "meta_rule"},
        )
        kt = MagicMock()
        kt.get_meta_rules.return_value = [existing]
        seed_meta_rules(kt)
        assert kt.ingest.call_count == 5

    def test_seed_metadata_is_meta_rule(self):
        """每条种子的 metadata 应包含 node_type=meta_rule。"""
        kt = MagicMock()
        kt.get_meta_rules.return_value = []
        seed_meta_rules(kt)
        for call in kt.ingest.call_args_list:
            metadata = call[1].get("metadata", {})
            assert metadata.get("node_type") == "meta_rule"
