"""Unit tests for KnowledgeTree.get_meta_rules() and meta-rule tools."""

import json
import tempfile
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import MagicMock

from src.common.knowledge_tree import KnowledgeTree, KnowledgeTreeConfig, build_knowledge_tree_tools
from src.common.knowledge_tree.dag.node import KnowledgeNode


@dataclass
class _FakeContext:
    enable_knowledge_tree: bool = True
    knowledge_tree_root: str = ""
    kt_rag_similarity_threshold: float = 0.15
    kt_embedder_type: str = "hash"
    kt_embedding_model: str = "hash"
    kt_embedding_dimension: int = 64
    kt_max_tree_depth: int = 5
    kt_ingest_enabled: bool = True
    kt_ingest_chunk_max_tokens: int = 512
    kt_dedup_threshold: float = 0.95
    kt_ingest_attach_threshold: float = 0.7
    kt_structural_weight: float = 0.2
    kt_content_weight: float = 0.8
    kt_optimization_window: int = 3600
    kt_max_optimizations_per_window: int = 10
    kt_total_failure_threshold: int = 3
    kt_rag_false_positive_threshold: int = 3
    kt_content_insufficient_threshold: int = 5


class TestGetMetaRules:
    """KnowledgeTree.get_meta_rules() filters by metadata.node_type."""

    def test_returns_only_meta_rule_nodes(self, tmp_path: Path) -> None:
        cfg = KnowledgeTreeConfig(markdown_root=tmp_path, embedder_type="hash", embedding_dimension=64)
        kt = KnowledgeTree(cfg)

        rule_node = KnowledgeNode.create(
            node_id="meta_rules/r1.md", title="Rule 1",
            content="Always test.", source="test",
            metadata={"node_type": "meta_rule", "priority": 5},
        )
        regular_node = KnowledgeNode.create(
            node_id="setup/info.md", title="Setup info",
            content="Port is 2024.", source="test",
            metadata={},
        )
        kt.md_store.ensure_directory("meta_rules")
        kt.md_store.ensure_directory("setup")
        kt.md_store.write_node(rule_node)
        kt.md_store.write_node(regular_node)

        rules = kt.get_meta_rules()
        assert len(rules) == 1
        assert rules[0].title == "Rule 1"
        assert rules[0].metadata["node_type"] == "meta_rule"

    def test_returns_empty_when_no_meta_rules(self, tmp_path: Path) -> None:
        cfg = KnowledgeTreeConfig(markdown_root=tmp_path, embedder_type="hash", embedding_dimension=64)
        kt = KnowledgeTree(cfg)

        regular_node = KnowledgeNode.create(
            node_id="setup/info.md", title="Info",
            content="Some content.", source="test",
        )
        kt.md_store.ensure_directory("setup")
        kt.md_store.write_node(regular_node)

        assert kt.get_meta_rules() == []


class TestMetaRuleTools:
    """knowledge_tree_add_meta_rule and knowledge_tree_list_meta_rules."""

    def test_add_meta_rule_creates_node(self, tmp_path: Path) -> None:
        cfg = KnowledgeTreeConfig(markdown_root=tmp_path, embedder_type="hash", embedding_dimension=64)
        ctx = _FakeContext(knowledge_tree_root=str(tmp_path))
        tools = build_knowledge_tree_tools(ctx)

        tool_map = {t.name: t for t in tools}
        add_tool = tool_map["knowledge_tree_add_meta_rule"]
        list_tool = tool_map["knowledge_tree_list_meta_rules"]

        result = json.loads(asyncio_run(add_tool.ainvoke({"title": "Test Rule", "content": "Always verify.", "priority": 3})))
        assert result["ok"] is True
        assert result["action"] == "created"
        assert "meta_rules/" in result["node_id"]

        rules = kt_from_ctx(cfg).get_meta_rules()
        assert len(rules) == 1
        assert rules[0].content == "Always verify."
        assert rules[0].metadata["priority"] == 3

    def test_add_meta_rule_updates_existing(self, tmp_path: Path) -> None:
        cfg = KnowledgeTreeConfig(markdown_root=tmp_path, embedder_type="hash", embedding_dimension=64)
        ctx = _FakeContext(knowledge_tree_root=str(tmp_path))
        tools = build_knowledge_tree_tools(ctx)

        tool_map = {t.name: t for t in tools}
        add_tool = tool_map["knowledge_tree_add_meta_rule"]

        asyncio_run(add_tool.ainvoke({"title": "Rule", "content": "v1", "priority": 1}))
        result = json.loads(asyncio_run(add_tool.ainvoke({"title": "Rule", "content": "v2 updated", "priority": 2})))
        assert result["action"] == "updated"

        rules = kt_from_ctx(cfg).get_meta_rules()
        assert len(rules) == 1
        assert "v2 updated" in rules[0].content

    def test_list_meta_rules(self, tmp_path: Path) -> None:
        cfg = KnowledgeTreeConfig(markdown_root=tmp_path, embedder_type="hash", embedding_dimension=64)
        ctx = _FakeContext(knowledge_tree_root=str(tmp_path))
        tools = build_knowledge_tree_tools(ctx)

        tool_map = {t.name: t for t in tools}
        add_tool = tool_map["knowledge_tree_add_meta_rule"]
        list_tool = tool_map["knowledge_tree_list_meta_rules"]

        asyncio_run(add_tool.ainvoke({"title": "Low", "content": "Low priority rule", "priority": 1}))
        asyncio_run(add_tool.ainvoke({"title": "High", "content": "High priority rule", "priority": 10}))

        result = json.loads(asyncio_run(list_tool.ainvoke({})))
        assert result["ok"] is True
        assert result["total"] == 2
        assert result["rules"][0]["title"] == "High"  # Higher priority first


def kt_from_ctx(cfg: KnowledgeTreeConfig) -> KnowledgeTree:
    from src.common.knowledge_tree import get_or_create_kt
    return get_or_create_kt(cfg)


def asyncio_run(coro):
    import asyncio
    return asyncio.get_event_loop().run_until_complete(coro)
