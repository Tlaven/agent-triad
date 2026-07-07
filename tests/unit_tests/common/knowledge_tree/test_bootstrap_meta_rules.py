"""Tests for meta rule seeding during bootstrap."""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from src.common.knowledge_tree import KnowledgeTree
from src.common.knowledge_tree.config import KnowledgeTreeConfig


META_RULES_DIR = Path(__file__).resolve().parents[4] / "workspace" / "knowledge_tree" / "meta_rules"


def _make_kt_with_seeds(tmp_path: Path) -> KnowledgeTree:
    """创建 KT 并 bootstrap（含种子文件和 embedding 索引）。"""
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


class TestSeedMetaRules:
    """验证元规则从种子文件写入。"""

    def test_seeds_six_rules(self, tmp_path: Path):
        # 原 6 条 bootstrap:meta_rule 种子中 proactive_ingest 与 auto_ingest
        # 语义高度重复（"用户分享项目信息→ingest"），Task 9 删除 proactive_ingest
        # 后剩 5 条 bootstrap:meta_rule 种子 + 1 条 source:agent:supervisor 的
        # auto_ingest（不进 _make_kt_with_seeds 的复制路径）。
        # 后续 dedup_meta_rules 清理把 auto_ingest 的 source 也统一为
        # bootstrap:meta_rule 并补 aliases，使其纳入种子复制路径——故 6 条全 seed。
        kt = _make_kt_with_seeds(tmp_path)
        rules = kt.get_meta_rules()
        assert len(rules) == 6

    def test_seed_content_contains_kt_guidance(self, tmp_path: Path):
        kt = _make_kt_with_seeds(tmp_path)
        rules = kt.get_meta_rules()
        all_content = " ".join(r.content for r in rules)
        assert "ingest" in all_content
        assert "retrieve" in all_content

    def test_bootstrap_idempotent(self, tmp_path: Path):
        kt = _make_kt_with_seeds(tmp_path)
        rules_before = kt.get_meta_rules()
        # 第二次 bootstrap 应被跳过
        result = kt.bootstrap()
        assert result.get("skipped")
        rules_after = kt.get_meta_rules()
        assert len(rules_before) == len(rules_after)

    def test_seed_metadata_is_meta_rule(self, tmp_path: Path):
        kt = _make_kt_with_seeds(tmp_path)
        rules = kt.get_meta_rules()
        for rule in rules:
            assert rule.metadata.get("node_type") == "meta_rule"

    def test_seed_files_have_valid_frontmatter(self):
        """验证所有种子文件有合法的 YAML frontmatter。"""
        src_dir = Path(__file__).resolve().parents[4] / "workspace" / "knowledge_tree" / "meta_rules"
        for md_file in src_dir.glob("*.md"):
            text = md_file.read_text(encoding="utf-8")
            assert text.startswith("---"), f"{md_file.name} missing frontmatter"
            parts = text.split("---", 2)
            assert len(parts) >= 3, f"{md_file.name} malformed frontmatter"
            fm = yaml.safe_load(parts[1])
            assert fm.get("metadata", {}).get("node_type") == "meta_rule", f"{md_file.name} not meta_rule"
            assert "title" in fm, f"{md_file.name} missing title"
