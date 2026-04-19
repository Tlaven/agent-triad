"""Wiki 文件夹适配器单元测试。"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from src.common.knowledge_tree.dag.node import KnowledgeNode
from src.common.knowledge_tree.ingestion.wiki_adapter import (
    RelationHint,
    WikiFolderReport,
    _extract_summary,
    _split_frontmatter,
    parse_wiki_folder,
)


# -- Helpers --


def _write_md(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


CONCEPT_MD = textwrap.dedent("""\
    ---
    type: concept
    title: "Test Concept"
    tags:
      - test
      - concept
    status: mature
    related:
      - "[[Other Concept]]"
    ---
    # Test Concept

    This is a test concept with some content.
""")


ENTITY_MD = textwrap.dedent("""\
    ---
    type: entity
    title: "Test Entity"
    aliases: ["TE"]
    created: 2026-04-19
    ---
    # Test Entity

    Entity body with [[Test Concept]] link.
""")


META_MD = textwrap.dedent("""\
    ---
    type: meta
    title: "Index"
    related:
      - "[[Test Concept]]"
      - "[[Test Entity]]"
    ---
    # Index

    - [[Test Concept]]
    - [[Test Entity]]
""")


NO_FM_MD = textwrap.dedent("""\
    # No Frontmatter

    Just a plain markdown file.
""")


# -- Tests: _split_frontmatter --


class TestSplitFrontmatter:
    def test_valid_frontmatter(self):
        fm, body = _split_frontmatter("---\ntype: concept\n---\nBody text")
        assert fm == {"type": "concept"}
        assert body == "Body text"

    def test_no_frontmatter(self):
        fm, body = _split_frontmatter("Just text\nNo frontmatter")
        assert fm is None
        assert body == "Just text\nNo frontmatter"

    def test_invalid_yaml(self):
        fm, body = _split_frontmatter("---\n: invalid: [yaml\n---\nBody")
        assert fm is None  # YAML parse error -> fallback

    def test_empty_frontmatter(self):
        fm, body = _split_frontmatter("---\n---\nBody")
        # Empty YAML -> None (not dict)
        assert fm is None


# -- Tests: _extract_summary --


class TestExtractSummary:
    def test_first_paragraph(self):
        assert _extract_summary("# Title\n\nHello world\nMore text") == "Hello world"

    def test_empty_body(self):
        assert _extract_summary("") == ""

    def test_only_headers(self):
        assert _extract_summary("# H1\n## H2") == ""

    def test_truncation(self):
        long_line = "x" * 300
        assert len(_extract_summary(long_line, max_chars=100)) == 100


# -- Tests: parse_wiki_folder --


class TestParseWikiFolder:
    def test_basic_parse(self, tmp_path: Path):
        _write_md(tmp_path / "concepts" / "test.md", CONCEPT_MD)
        _write_md(tmp_path / "entities" / "te.md", ENTITY_MD)

        nodes, hints, report = parse_wiki_folder(tmp_path)
        assert report.files_scanned == 2
        assert report.nodes_created == 2
        assert len(hints) >= 2  # related + body links

    def test_skip_meta(self, tmp_path: Path):
        _write_md(tmp_path / "index.md", META_MD)
        _write_md(tmp_path / "concept.md", CONCEPT_MD)

        nodes, hints, report = parse_wiki_folder(tmp_path, skip_meta=True)
        assert report.nodes_created == 1  # Only concept, not meta
        assert report.meta_skipped == 1
        # Meta's links still extracted as hints
        assert len(hints) >= 3  # meta has 2 links + concept has 1

    def test_include_meta(self, tmp_path: Path):
        _write_md(tmp_path / "index.md", META_MD)

        nodes, hints, report = parse_wiki_folder(tmp_path, skip_meta=False)
        assert report.nodes_created == 1

    def test_skip_templates(self, tmp_path: Path):
        _write_md(tmp_path / "_templates" / "concept.md", CONCEPT_MD)
        _write_md(tmp_path / "real.md", CONCEPT_MD)

        nodes, _, report = parse_wiki_folder(tmp_path, skip_templates=True)
        assert report.files_scanned == 1
        assert report.nodes_created == 1

    def test_no_frontmatter_files_skipped(self, tmp_path: Path):
        _write_md(tmp_path / "plain.md", NO_FM_MD)

        nodes, _, report = parse_wiki_folder(tmp_path)
        assert report.files_scanned == 1
        assert report.nodes_created == 0
        assert report.files_scanned == 1

    def test_empty_directory(self, tmp_path: Path):
        nodes, hints, report = parse_wiki_folder(tmp_path)
        assert nodes == []
        assert hints == []
        assert report.files_scanned == 0

    def test_nonexistent_directory(self, tmp_path: Path):
        nodes, hints, report = parse_wiki_folder(tmp_path / "nonexistent")
        assert len(report.errors) == 1

    def test_node_metadata(self, tmp_path: Path):
        _write_md(tmp_path / "test.md", CONCEPT_MD)

        nodes, _, _ = parse_wiki_folder(tmp_path)
        assert len(nodes) == 1
        node = nodes[0]
        assert node.title == "Test Concept"
        assert node.metadata["page_type"] == "concept"
        assert node.metadata["tags"] == ["test", "concept"]
        assert node.metadata["status"] == "mature"
        assert "wiki:" in node.source

    def test_relation_hints(self, tmp_path: Path):
        _write_md(tmp_path / "a.md", CONCEPT_MD)  # links to [[Other Concept]]
        _write_md(tmp_path / "b.md", ENTITY_MD)    # links to [[Test Concept]]

        _, hints, _ = parse_wiki_folder(tmp_path)
        hint_pairs = {(h.source_title, h.target_title) for h in hints}
        assert ("Test Concept", "Other Concept") in hint_pairs
        assert ("Test Entity", "Test Concept") in hint_pairs

    def test_real_wiki_folder(self):
        """Test against the actual workspace/knowledge_tree seed."""
        wiki_root = Path("workspace/knowledge_tree")
        if not wiki_root.is_dir():
            pytest.skip("workspace/knowledge_tree not found")

        nodes, hints, report = parse_wiki_folder(wiki_root)

        # Should parse all content pages (excluding meta + templates)
        assert report.nodes_created >= 10, f"Expected >=10 nodes, got {report.nodes_created}"
        assert report.meta_skipped >= 3, f"Expected >=3 meta pages skipped"
        assert len(hints) >= 10, f"Expected >=10 relation hints, got {len(hints)}"
        assert report.errors == []

        # Check specific known pages exist
        titles = {n.title for n in nodes}
        assert "Three-Agent Architecture" in titles
        assert "Plan JSON" in titles
        assert "Supervisor Agent" in titles
        assert "Knowledge Tree" in titles

        # Check metadata
        for node in nodes:
            assert node.metadata.get("page_type") in {
                "concept", "entity", "source", "question", "comparison",
            }, f"Unexpected page_type for {node.title}: {node.metadata.get('page_type')}"

    def test_node_ids_unique(self, tmp_path: Path):
        _write_md(tmp_path / "a.md", CONCEPT_MD)
        _write_md(tmp_path / "b.md", ENTITY_MD)

        nodes, _, _ = parse_wiki_folder(tmp_path)
        ids = [n.node_id for n in nodes]
        assert len(ids) == len(set(ids)), "Node IDs must be unique"
