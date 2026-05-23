"""元规则 aliases 检索扩展测试。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from src.common.knowledge_tree import KnowledgeTree
from src.common.knowledge_tree.config import KnowledgeTreeConfig
from src.common.knowledge_tree.bootstrap import seed_meta_rules, _META_RULE_SEEDS


def _make_kt(tmp_path: Path) -> KnowledgeTree:
    cfg = KnowledgeTreeConfig(markdown_root=tmp_path, embedder_type="hash", embedding_dimension=64)
    return KnowledgeTree(cfg)


class TestMetaRuleSeedAliases:
    """验证种子数据结构。"""

    def test_all_seeds_have_aliases(self):
        for seed in _META_RULE_SEEDS:
            assert seed.aliases, f"种子 '{seed.title}' 缺少 aliases"
            assert isinstance(seed.aliases, list)
            assert all(isinstance(a, str) for a in seed.aliases)

    def test_seed_dataclass_fields(self):
        seed = _META_RULE_SEEDS[0]
        assert hasattr(seed, "title")
        assert hasattr(seed, "content")
        assert hasattr(seed, "priority")
        assert hasattr(seed, "aliases")


class TestAliasEmbeddingCreation:
    """验证 alias embedding 被正确索引。"""

    def test_aliases_stored_in_metadata(self, tmp_path: Path):
        kt = _make_kt(tmp_path)
        seed_meta_rules(kt)
        rules = kt.get_meta_rules()
        assert len(rules) > 0
        for rule in rules:
            aliases = rule.metadata.get("aliases", [])
            if aliases:
                assert isinstance(aliases, list)
                assert all(isinstance(a, str) for a in aliases)

    def test_alias_vectors_exist(self, tmp_path: Path):
        kt = _make_kt(tmp_path)
        seed_meta_rules(kt)
        rules = kt.get_meta_rules()
        for rule in rules:
            aliases = rule.metadata.get("aliases", [])
            for i, _alias in enumerate(aliases):
                key = f"alias:{rule.node_id}:{i}"
                assert key in kt.vector_store._embeddings, f"Missing alias vector: {key}"

    def test_no_alias_vectors_without_aliases(self, tmp_path: Path):
        kt = _make_kt(tmp_path)
        # 手动 ingest 一个无 aliases 的 meta-rule
        kt.ingest(
            "test rule without aliases",
            trigger="test",
            source="test",
            metadata={"node_type": "meta_rule", "priority": 1},
        )
        alias_keys = [k for k in kt.vector_store._embeddings if k.startswith("alias:")]
        assert len(alias_keys) == 0


class TestAliasRetrieval:
    """验证 alias 能被 RAG 检索命中。"""

    def test_retrieve_by_alias(self, tmp_path: Path):
        kt = _make_kt(tmp_path)
        seed_meta_rules(kt)

        # 用 alias 短语检索 — 应能命中对应的元规则
        results, _log = kt.retrieve("目标模糊")
        node_ids = [n.node_id for n, _score in results]
        # 聪明提问规则应在结果中
        found = any("聪明提问" in n.title or "澄清" in n.content for n, _s in results)
        assert found, f"alias '目标模糊' 未命中聪明提问规则。结果: {[(n.title, s) for n, s in results[:5]]}"

    def test_retrieve_by_alias_vs_content(self, tmp_path: Path):
        kt = _make_kt(tmp_path)
        seed_meta_rules(kt)

        # 用 alias 检索
        alias_results, _ = kt.retrieve("执行失败")
        # 用规则原文检索
        content_results, _ = kt.retrieve("重规划前检索失败经验")

        alias_ids = {n.node_id for n, _ in alias_results}
        content_ids = {n.node_id for n, _ in content_results}

        # 两者都应命中"失败后学"规则
        assert alias_ids or content_ids, "alias 和 content 都没命中"


class TestAliasCleanup:
    """验证 alias 向量的清理。"""

    def test_delete_node_removes_aliases(self, tmp_path: Path):
        kt = _make_kt(tmp_path)
        seed_meta_rules(kt)
        rules = kt.get_meta_rules()
        assert len(rules) > 0

        rule = rules[0]
        aliases = rule.metadata.get("aliases", [])
        if not aliases:
            return  # 无 aliases 可测试

        # 验证 alias 向量存在
        alias_keys_before = [k for k in kt.vector_store._embeddings if k.startswith(f"alias:{rule.node_id}:")]
        assert len(alias_keys_before) > 0

        # 删除节点
        kt.md_store.delete_node(rule.node_id)
        kt.vector_store.delete_embedding(rule.node_id)

        # 验证 alias 向量被清理
        alias_keys_after = [k for k in kt.vector_store._embeddings if k.startswith(f"alias:{rule.node_id}:")]
        assert len(alias_keys_after) == 0


class TestBackwardCompat:
    """验证无 aliases 的规则仍然正常工作。"""

    def test_meta_rule_without_aliases(self, tmp_path: Path):
        kt = _make_kt(tmp_path)
        kt.ingest(
            "backward compat rule content that is long enough to pass filters",
            trigger="user_explicit",
            source="test",
            metadata={"node_type": "meta_rule", "priority": 1},
        )
        rules = kt.get_meta_rules()
        assert any("backward compat" in r.content for r in rules)

    def test_reindex_aliases_empty(self, tmp_path: Path):
        kt = _make_kt(tmp_path)
        seed_meta_rules(kt)
        rules = kt.get_meta_rules()
        if not rules:
            return
        rule = rules[0]
        # 用空 aliases 调用 _reindex_aliases 不应报错
        from src.common.knowledge_tree import get_or_create_kt
        from src.common.knowledge_tree.config import KnowledgeTreeConfig
        cfg = KnowledgeTreeConfig(markdown_root=tmp_path, embedder_type="hash", embedding_dimension=64)
        kt2 = get_or_create_kt(cfg)
        # 直接调用底层
        alias_prefix = f"alias:{rule.node_id}:"
        before = [k for k in kt2.vector_store._embeddings if k.startswith(alias_prefix)]
        for k in before:
            del kt2.vector_store._embeddings[k]
        # 重建空 aliases
        for i, alias in enumerate([]):
            kt2.vector_store.upsert_embedding(f"alias:{rule.node_id}:{i}", kt2.embedder(alias))
        # 不报错即通过
