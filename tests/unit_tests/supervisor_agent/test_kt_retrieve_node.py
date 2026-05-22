"""Unit tests for the kt_retrieve graph node (auto-injection)."""

import asyncio
from dataclasses import dataclass
from typing import Any
from unittest.mock import MagicMock, patch

from langchain_core.messages import AIMessage, HumanMessage

from src.supervisor_agent.graph import kt_retrieve
from src.supervisor_agent.state import State

# ---------------------------------------------------------------------------
# Minimal runtime stub — matches langgraph Runtime[Context] interface
# ---------------------------------------------------------------------------

@dataclass
class _MockContext:
    """Minimal Context stub for kt_retrieve tests."""
    enable_knowledge_tree: bool = True
    knowledge_tree_root: str = "workspace/knowledge_tree"
    kt_embedding_model: str = "hash"


class _MockRuntime:
    """Minimal Runtime stub."""

    def __init__(self, ctx: _MockContext | None = None) -> None:
        self.context = ctx or _MockContext()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_node(title: str, content: str) -> MagicMock:
    """Create a mock KnowledgeNode."""
    node = MagicMock()
    node.title = title
    node.content = content
    return node


def _state_with_messages(*msgs: Any) -> State:
    return State(messages=list(msgs))


def _run(coro: Any) -> Any:
    return asyncio.get_event_loop().run_until_complete(coro)


def _mock_kt_setup(mock_get_kt: MagicMock, results: list[tuple]) -> MagicMock:
    """Configure a mock KT instance with given retrieve results."""
    mock_kt = MagicMock()
    mock_kt.retrieve.return_value = (results, None)
    mock_kt.embedder_type = "hash"
    mock_get_kt.return_value = mock_kt
    return mock_kt


# ---------------------------------------------------------------------------
# Tests: kt_retrieve
# ---------------------------------------------------------------------------

class TestKtRetrieveDisabled:
    """When KT is disabled, kt_retrieve returns empty context."""

    def test_kt_disabled_returns_empty(self) -> None:
        ctx = _MockContext(enable_knowledge_tree=False)
        runtime = _MockRuntime(ctx)
        state = _state_with_messages(HumanMessage(content="查询"))

        result = _run(kt_retrieve(state, runtime))
        assert result == {"kt_context": "", "kt_meta_rules": "", "kt_optimization_suggestions": "", "kt_snapshot_data": {}}


class TestKtRetrieveNoQuery:
    """When there's no user message to query, returns empty."""

    def test_no_messages(self) -> None:
        runtime = _MockRuntime()
        state = State(messages=[])

        result = _run(kt_retrieve(state, runtime))
        assert result == {"kt_context": "", "kt_meta_rules": "", "kt_optimization_suggestions": "", "kt_snapshot_data": {}}

    def test_only_ai_messages(self) -> None:
        runtime = _MockRuntime()
        state = _state_with_messages(AIMessage(content="AI reply"))

        result = _run(kt_retrieve(state, runtime))
        assert result == {"kt_context": "", "kt_meta_rules": "", "kt_optimization_suggestions": "", "kt_snapshot_data": {}}


class TestKtRetrieveThreshold:
    """Verify embedder-aware quality threshold for auto-injection (hash: 0.25, semantic: 0.6)."""

    @patch("src.common.knowledge_tree.config.KnowledgeTreeConfig.from_context")
    @patch("src.common.knowledge_tree.get_or_create_kt")
    def test_below_threshold_filtered_out(self, mock_get_kt: MagicMock, mock_from_ctx: MagicMock) -> None:
        """Results with sim < hash threshold (0.25) should not be injected."""
        _mock_kt_setup(mock_get_kt, [(_make_node("Low", "content"), 0.10)])

        runtime = _MockRuntime()
        state = _state_with_messages(HumanMessage(content="测试查询"))

        result = _run(kt_retrieve(state, runtime))
        assert result == {"kt_context": "", "kt_meta_rules": "", "kt_optimization_suggestions": "", "kt_snapshot_data": {}}

    @patch("src.common.knowledge_tree.config.KnowledgeTreeConfig.from_context")
    @patch("src.common.knowledge_tree.get_or_create_kt")
    def test_exactly_at_threshold_included(self, mock_get_kt: MagicMock, mock_from_ctx: MagicMock) -> None:
        """Result at hash threshold (0.25) should be included."""
        _mock_kt_setup(mock_get_kt, [(_make_node("Exact", "threshold content"), 0.4)])

        runtime = _MockRuntime()
        state = _state_with_messages(HumanMessage(content="测试查询"))

        result = _run(kt_retrieve(state, runtime))
        assert result["kt_context"] != ""
        assert "Exact" in result["kt_context"]

    @patch("src.common.knowledge_tree.config.KnowledgeTreeConfig.from_context")
    @patch("src.common.knowledge_tree.get_or_create_kt")
    def test_above_threshold_included(self, mock_get_kt: MagicMock, mock_from_ctx: MagicMock) -> None:
        """Results with sim > 0.4 should be injected."""
        _mock_kt_setup(mock_get_kt, [(_make_node("High", "quality content"), 0.65)])

        runtime = _MockRuntime()
        state = _state_with_messages(HumanMessage(content="测试查询"))

        result = _run(kt_retrieve(state, runtime))
        assert "High" in result["kt_context"]
        assert "0.65" in result["kt_context"]


class TestKtRetrieveTopK:
    """Verify that at most 3 results are injected."""

    @patch("src.common.knowledge_tree.config.KnowledgeTreeConfig.from_context")
    @patch("src.common.knowledge_tree.get_or_create_kt")
    def test_max_three_results(self, mock_get_kt: MagicMock, mock_from_ctx: MagicMock) -> None:
        _mock_kt_setup(
            mock_get_kt,
            [(_make_node(f"Node{i}", f"content {i}"), 0.5 + i * 0.05) for i in range(5)],
        )

        runtime = _MockRuntime()
        state = _state_with_messages(HumanMessage(content="测试查询"))

        result = _run(kt_retrieve(state, runtime))
        ctx = result["kt_context"]
        assert "Node0" in ctx
        assert "Node1" in ctx
        assert "Node2" in ctx
        assert "Node3" not in ctx
        assert "Node4" not in ctx


class TestKtRetrieveFormat:
    """Verify the output format of injected context."""

    @patch("src.common.knowledge_tree.config.KnowledgeTreeConfig.from_context")
    @patch("src.common.knowledge_tree.get_or_create_kt")
    def test_format_contains_header(self, mock_get_kt: MagicMock, mock_from_ctx: MagicMock) -> None:
        _mock_kt_setup(mock_get_kt, [(_make_node("Test", "body"), 0.5)])

        runtime = _MockRuntime()
        state = _state_with_messages(HumanMessage(content="查询"))

        result = _run(kt_retrieve(state, runtime))
        assert result["kt_context"].startswith("[相关知识]")

    @patch("src.common.knowledge_tree.config.KnowledgeTreeConfig.from_context")
    @patch("src.common.knowledge_tree.get_or_create_kt")
    def test_format_contains_similarity(self, mock_get_kt: MagicMock, mock_from_ctx: MagicMock) -> None:
        _mock_kt_setup(mock_get_kt, [(_make_node("Test", "body"), 0.723)])

        runtime = _MockRuntime()
        state = _state_with_messages(HumanMessage(content="查询"))

        result = _run(kt_retrieve(state, runtime))
        assert "0.72" in result["kt_context"]

    @patch("src.common.knowledge_tree.config.KnowledgeTreeConfig.from_context")
    @patch("src.common.knowledge_tree.get_or_create_kt")
    def test_high_confidence_tag(self, mock_get_kt: MagicMock, mock_from_ctx: MagicMock) -> None:
        _mock_kt_setup(mock_get_kt, [(_make_node("Test", "body"), 0.75)])

        runtime = _MockRuntime()
        state = _state_with_messages(HumanMessage(content="查询"))

        result = _run(kt_retrieve(state, runtime))
        assert "[高可信]" in result["kt_context"]

    @patch("src.common.knowledge_tree.config.KnowledgeTreeConfig.from_context")
    @patch("src.common.knowledge_tree.get_or_create_kt")
    def test_reference_tag(self, mock_get_kt: MagicMock, mock_from_ctx: MagicMock) -> None:
        _mock_kt_setup(mock_get_kt, [(_make_node("Test", "body"), 0.40)])

        runtime = _MockRuntime()
        state = _state_with_messages(HumanMessage(content="查询"))

        result = _run(kt_retrieve(state, runtime))
        assert "[参考]" in result["kt_context"]

    @patch("src.common.knowledge_tree.config.KnowledgeTreeConfig.from_context")
    @patch("src.common.knowledge_tree.get_or_create_kt")
    def test_inject_source_disclaimer(self, mock_get_kt: MagicMock, mock_from_ctx: MagicMock) -> None:
        _mock_kt_setup(mock_get_kt, [(_make_node("Test", "body"), 0.5)])

        runtime = _MockRuntime()
        state = _state_with_messages(HumanMessage(content="查询"))

        result = _run(kt_retrieve(state, runtime))
        assert "非用户输入" in result["kt_context"]

    @patch("src.common.knowledge_tree.config.KnowledgeTreeConfig.from_context")
    @patch("src.common.knowledge_tree.get_or_create_kt")
    def test_content_truncated_at_300(self, mock_get_kt: MagicMock, mock_from_ctx: MagicMock) -> None:
        _mock_kt_setup(mock_get_kt, [(_make_node("Test", "x" * 500), 0.5)])

        runtime = _MockRuntime()
        state = _state_with_messages(HumanMessage(content="查询"))

        result = _run(kt_retrieve(state, runtime))
        lines = result["kt_context"].split("\n")
        content_lines = [l for l in lines if l.startswith("  ")]
        assert len(content_lines) == 1
        assert len(content_lines[0].strip()) <= 300


class TestKtRetrieveErrorHandling:
    """Verify graceful handling of KT failures."""

    @patch("src.common.knowledge_tree.config.KnowledgeTreeConfig.from_context")
    @patch("src.common.knowledge_tree.get_or_create_kt")
    def test_exception_returns_empty(self, mock_get_kt: MagicMock, mock_from_ctx: MagicMock) -> None:
        mock_get_kt.side_effect = RuntimeError("KT init failed")

        runtime = _MockRuntime()
        state = _state_with_messages(HumanMessage(content="查询"))

        result = _run(kt_retrieve(state, runtime))
        assert result == {"kt_context": "", "kt_meta_rules": "", "kt_optimization_suggestions": "", "kt_snapshot_data": {}}


class TestKtRetrieveQueryExtraction:
    """Verify the query is extracted from the last HumanMessage."""

    @patch("src.common.knowledge_tree.config.KnowledgeTreeConfig.from_context")
    @patch("src.common.knowledge_tree.get_or_create_kt")
    def test_uses_last_human_message(self, mock_get_kt: MagicMock, mock_from_ctx: MagicMock) -> None:
        mock_kt = _mock_kt_setup(mock_get_kt, [])

        runtime = _MockRuntime()
        state = _state_with_messages(
            HumanMessage(content="first query"),
            AIMessage(content="reply"),
            HumanMessage(content="second query"),
        )

        _run(kt_retrieve(state, runtime))
        mock_kt.retrieve.assert_called_once_with("second query")


class TestKtRetrieveMetaRules:
    """Verify persistent meta-rules are fetched and returned."""

    @patch("src.common.knowledge_tree.config.KnowledgeTreeConfig.from_context")
    @patch("src.common.knowledge_tree.get_or_create_kt")
    def test_meta_rules_returned_when_present(self, mock_get_kt: MagicMock, mock_from_ctx: MagicMock) -> None:
        from src.common.knowledge_tree.dag.node import KnowledgeNode

        rule1 = KnowledgeNode.create(
            node_id="meta_rules/r1.md", title="Rule 1",
            content="Always confirm before deleting.", source="test",
            metadata={"node_type": "meta_rule", "priority": 10},
        )
        rule2 = KnowledgeNode.create(
            node_id="meta_rules/r2.md", title="Rule 2",
            content="Never skip testing.", source="test",
            metadata={"node_type": "meta_rule", "priority": 5},
        )

        _mock_kt_setup(mock_get_kt, [(_make_node("K1", "content"), 0.7)])
        mock_kt = mock_get_kt.return_value
        mock_kt.get_meta_rules.return_value = [rule1, rule2]

        runtime = _MockRuntime()
        state = _state_with_messages(HumanMessage(content="查询"))

        result = _run(kt_retrieve(state, runtime))
        assert "kt_meta_rules" in result
        assert "Always confirm before deleting" in result["kt_meta_rules"]
        assert "Never skip testing" in result["kt_meta_rules"]
        # Higher priority first
        assert result["kt_meta_rules"].index("Always confirm") < result["kt_meta_rules"].index("Never skip")

    @patch("src.common.knowledge_tree.config.KnowledgeTreeConfig.from_context")
    @patch("src.common.knowledge_tree.get_or_create_kt")
    def test_meta_rules_empty_when_none(self, mock_get_kt: MagicMock, mock_from_ctx: MagicMock) -> None:
        _mock_kt_setup(mock_get_kt, [(_make_node("K1", "content"), 0.7)])
        mock_kt = mock_get_kt.return_value
        mock_kt.get_meta_rules.return_value = []

        runtime = _MockRuntime()
        state = _state_with_messages(HumanMessage(content="查询"))

        result = _run(kt_retrieve(state, runtime))
        assert result["kt_meta_rules"] == ""

    @patch("src.common.knowledge_tree.config.KnowledgeTreeConfig.from_context")
    @patch("src.common.knowledge_tree.get_or_create_kt")
    def test_meta_rules_failure_is_non_critical(self, mock_get_kt: MagicMock, mock_from_ctx: MagicMock) -> None:
        _mock_kt_setup(mock_get_kt, [(_make_node("K1", "content"), 0.7)])
        mock_kt = mock_get_kt.return_value
        mock_kt.get_meta_rules.side_effect = RuntimeError("meta rules failed")

        runtime = _MockRuntime()
        state = _state_with_messages(HumanMessage(content="查询"))

        result = _run(kt_retrieve(state, runtime))
        # kt_context should still work, meta_rules should be empty
        assert "kt_context" in result
        assert result["kt_meta_rules"] == ""
