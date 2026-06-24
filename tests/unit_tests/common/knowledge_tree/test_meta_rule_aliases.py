"""元规则 aliases 检索扩展测试。"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from src.common.knowledge_tree import KnowledgeTree
from src.common.knowledge_tree.config import KnowledgeTreeConfig
from src.common.knowledge_tree.dag.node import KnowledgeNode

META_RULES_DIR = Path(__file__).resolve().parents[4] / "workspace" / "knowledge_tree" / "meta_rules"


def _make_kt(tmp_path: Path) -> KnowledgeTree:
    """创建 KT 并 bootstrap（含 alias embedding 索引）。"""
    meta_dir = tmp_path / "meta_rules"
    meta_dir.mkdir()
    for f in META_RULES_DIR.glob("*.md"):
        text = f.read_text(encoding="utf-8")
        if text.startswith("---"):
            _, fm_text, _ = text.split("---", 2)
            fm = yaml.safe_load(fm_text)
            if fm.get("source") != "bootstrap:meta_rule":
                continue
        (meta_dir / f.name).write_text(text, encoding="utf-8")
    cfg = KnowledgeTreeConfig(markdown_root=tmp_path, embedder_type="hash", embedding_dimension=64)
    kt = KnowledgeTree(cfg)
    kt.bootstrap()
    return kt


class TestMetaRuleSeedFiles:
    """验证种子文件格式和内容。"""

    def test_seed_files_exist(self):
        seed_files = list(META_RULES_DIR.glob("*.md"))
        assert len(seed_files) >= 6, f"Expected >= 6 seed files, found {len(seed_files)}"

    def test_all_seeds_have_aliases(self):
        for md_file in META_RULES_DIR.glob("*.md"):
            text = md_file.read_text(encoding="utf-8")
            if not text.startswith("---"):
                continue
            _, fm_text, _ = text.split("---", 2)
            fm = yaml.safe_load(fm_text)
            if fm.get("metadata", {}).get("node_type") != "meta_rule":
                continue
            # 只验证 source=bootstrap 的种子文件
            if fm.get("source") != "bootstrap:meta_rule":
                continue
            aliases = fm.get("metadata", {}).get("aliases", [])
            assert aliases, f"Seed file {md_file.name} missing aliases"
            assert "元规则" in aliases, f"Seed file {md_file.name} missing '元规则' alias"

    def test_all_seeds_have_priority(self):
        for md_file in META_RULES_DIR.glob("*.md"):
            text = md_file.read_text(encoding="utf-8")
            if not text.startswith("---"):
                continue
            _, fm_text, _ = text.split("---", 2)
            fm = yaml.safe_load(fm_text)
            if fm.get("metadata", {}).get("node_type") != "meta_rule":
                continue
            assert "priority" in fm.get("metadata", {}), f"Seed file {md_file.name} missing priority"


class TestAliasEmbeddingCreation:
    """验证 alias embedding 被正确索引。"""

    def test_aliases_stored_in_metadata(self, tmp_path: Path):
        kt = _make_kt(tmp_path)
        rules = kt.get_meta_rules()
        assert len(rules) > 0
        for rule in rules:
            aliases = rule.metadata.get("aliases", [])
            if aliases:
                assert isinstance(aliases, list)

    def test_alias_vectors_exist(self, tmp_path: Path):
        kt = _make_kt(tmp_path)
        rules = kt.get_meta_rules()
        for rule in rules:
            aliases = rule.metadata.get("aliases", [])
            for i, _alias in enumerate(aliases):
                key = f"alias:{rule.node_id}:{i}"
                assert key in kt.vector_store._embeddings, f"Missing alias vector: {key}"

    def test_no_alias_vectors_without_aliases(self, tmp_path: Path):
        cfg = KnowledgeTreeConfig(markdown_root=tmp_path, embedder_type="hash", embedding_dimension=64)
        kt = KnowledgeTree(cfg)
        kt.ingest(
            "test rule without aliases content that is long enough to pass filters",
            trigger="user_explicit",
            source="test",
            metadata={"node_type": "meta_rule", "priority": 1},
        )
        alias_keys = [k for k in kt.vector_store._embeddings if k.startswith("alias:")]
        assert len(alias_keys) == 0


class TestAliasRetrieval:
    """验证 alias 能被 RAG 检索命中。"""

    def test_retrieve_by_alias(self, tmp_path: Path):
        kt = _make_kt(tmp_path)
        results, _log = kt.retrieve("目标模糊")
        found = any("聪明提问" in n.title or "澄清" in n.content for n, _s in results)
        assert found, f"alias '目标模糊' 未命中聪明提问规则。结果: {[(n.title, s) for n, s in results[:5]]}"

    def test_retrieve_by_meta_rule_keyword(self, tmp_path: Path):
        kt = _make_kt(tmp_path)
        results, _log = kt.retrieve("元规则")
        node_ids = [n.node_id for n, _s in results]
        assert len(node_ids) > 0, "'元规则' 关键词应命中至少一条元规则"


class TestAliasCleanup:
    """验证 alias 向量的清理。"""

    def test_delete_node_removes_aliases(self, tmp_path: Path):
        kt = _make_kt(tmp_path)
        rules = kt.get_meta_rules()
        assert len(rules) > 0
        rule = rules[0]
        aliases = rule.metadata.get("aliases", [])
        if not aliases:
            return
        alias_keys_before = [k for k in kt.vector_store._embeddings if k.startswith(f"alias:{rule.node_id}:")]
        assert len(alias_keys_before) > 0
        kt.md_store.delete_node(rule.node_id)
        kt.vector_store.delete_embedding(rule.node_id)
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


class TestMetaRuleConflictResolution:
    """元规则冲突仲裁（决策 28 + P1 语义矛盾检测）。"""

    def _make_bare_kt(self, tmp_path: Path) -> KnowledgeTree:
        cfg = KnowledgeTreeConfig(
            markdown_root=tmp_path,
            embedder_type="hash",
            embedding_dimension=64,
        )
        return KnowledgeTree(cfg)

    def test_semantic_conflict_no_alias_still_resolved(self, tmp_path: Path):
        """无共享 alias 但语义矛盾（title 相同 content 字符集不重叠）也应被消解。"""
        from src.supervisor_agent.graph import _resolve_meta_rule_conflicts

        kt = self._make_bare_kt(tmp_path)

        rule_a = KnowledgeNode.create(
            node_id="meta_rules/a.md",
            title="冲突规则",
            content="aaaaaaaaaa",
            source="test",
            metadata={"node_type": "meta_rule", "priority": 100},
        )
        rule_a.created_at = "2026-06-01T00:00:00+00:00"
        rule_a.embedding = kt.embedder(rule_a.content)

        rule_b = KnowledgeNode.create(
            node_id="meta_rules/b.md",
            title="冲突规则",
            content="bbbbbbbbbb",
            source="test",
            metadata={"node_type": "meta_rule", "priority": 100},
        )
        rule_b.created_at = "2026-06-24T00:00:00+00:00"
        rule_b.embedding = kt.embedder(rule_b.content)

        resolved, suppressed, _notes = _resolve_meta_rule_conflicts(
            [rule_a, rule_b], kt=kt
        )

        assert len(resolved) == 1, f"Expected 1 winner, got {len(resolved)}"
        assert suppressed == 1
        assert resolved[0].content == "bbbbbbbbbb"

    def test_same_priority_latest_created_at_wins(self, tmp_path: Path):
        """同 alias + 同 priority 时，created_at 最新的获胜（替代全抑制）。"""
        from src.supervisor_agent.graph import _resolve_meta_rule_conflicts

        kt = self._make_bare_kt(tmp_path)

        rule_old = KnowledgeNode.create(
            node_id="meta_rules/old.md",
            title="规则A",
            content="old rule content",
            source="test",
            metadata={
                "node_type": "meta_rule",
                "priority": 100,
                "aliases": ["终极规则"],
            },
        )
        rule_old.created_at = "2026-06-01T00:00:00+00:00"
        rule_old.embedding = kt.embedder(rule_old.content)

        rule_new = KnowledgeNode.create(
            node_id="meta_rules/new.md",
            title="规则B",
            content="new rule content",
            source="test",
            metadata={
                "node_type": "meta_rule",
                "priority": 100,
                "aliases": ["终极规则"],
            },
        )
        rule_new.created_at = "2026-06-24T00:00:00+00:00"
        rule_new.embedding = kt.embedder(rule_new.content)

        resolved, suppressed, notes = _resolve_meta_rule_conflicts(
            [rule_old, rule_new], kt=kt
        )

        assert len(resolved) == 1, f"Expected 1 winner, got {len(resolved)}"
        assert suppressed == 1
        assert resolved[0].content == "new rule content"
        assert notes == [], "Same-priority path should no longer emit unresolved notes"
