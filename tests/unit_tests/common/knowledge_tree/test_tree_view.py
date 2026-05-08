"""P2 编号树视图测试。

验证 render_numbered_tree 和 parse_numbered_tree。
"""

import pytest

from src.common.knowledge_tree.dag.node import KnowledgeNode
from src.common.knowledge_tree.editing.tree_view import (
    TreeEntry,
    build_proposed_paths,
    parse_numbered_tree,
    render_numbered_tree,
)
from src.common.knowledge_tree.storage.markdown_store import MarkdownStore


@pytest.fixture
def md_store(tmp_path):
    store = MarkdownStore(tmp_path / "kt_md")
    # 创建测试结构
    for path, title in [
        ("architecture/three-agent.md", "Three Agent System"),
        ("architecture/langgraph-state.md", "LangGraph State"),
        ("conventions/plan-json.md", "Plan JSON"),
        ("setup/environment.md", "Environment Config"),
    ]:
        node = KnowledgeNode.create(
            node_id=path,
            title=title,
            content=f"Content of {title}",
            source="test",
        )
        store.write_node(node)
    return store


# -- render_numbered_tree --


class TestRenderNumberedTree:
    def test_renders_directories(self, md_store):
        result = render_numbered_tree(md_store)
        assert "architecture/" in result
        assert "conventions/" in result
        assert "setup/" in result

    def test_renders_files_under_dirs(self, md_store):
        result = render_numbered_tree(md_store)
        # 文件应在目录下面，有缩进
        assert "three-agent.md" in result
        assert "plan-json.md" in result

    def test_has_numbering(self, md_store):
        result = render_numbered_tree(md_store)
        lines = [l for l in result.split("\n") if l.strip()]
        for line in lines:
            assert line.strip()[:2].isdigit() or line.strip()[0].isdigit()

    def test_empty_store(self, tmp_path):
        store = MarkdownStore(tmp_path / "empty")
        result = render_numbered_tree(store)
        assert result == "(empty)"

    def test_root_files(self, tmp_path):
        store = MarkdownStore(tmp_path / "kt_md")
        node = KnowledgeNode.create(
            node_id="readme.md",
            title="Readme",
            content="Hello",
            source="test",
        )
        store.write_node(node)
        result = render_numbered_tree(store)
        assert "readme.md" in result


# -- parse_numbered_tree --


class TestParseNumberedTree:
    def test_parse_basic(self):
        text = """\
01 architecture/
    01 three-agent.md
    02 langgraph-state.md
02 conventions/
    01 plan-json.md"""
        entries = parse_numbered_tree(text)
        assert len(entries) == 5
        assert entries[0].is_directory is True
        assert entries[0].name == "architecture"
        assert entries[0].level == 0
        assert entries[1].name == "three-agent.md"
        assert entries[1].is_directory is False
        assert entries[1].level == 1

    def test_parse_empty(self):
        entries = parse_numbered_tree("")
        assert entries == []

    def test_parse_empty_marker(self):
        entries = parse_numbered_tree("(empty)")
        assert entries == []

    def test_rejects_bad_format(self):
        with pytest.raises(ValueError, match="expected format"):
            parse_numbered_tree("not a valid line")

    def test_rejects_bad_indentation(self):
        with pytest.raises(ValueError, match="indentation"):
            parse_numbered_tree("  01 bad.md")  # 2 spaces, not 4

    def test_root_level_files(self):
        text = "01 readme.md\n02 guide.md"
        entries = parse_numbered_tree(text)
        assert len(entries) == 2
        assert all(e.level == 0 for e in entries)
        assert all(not e.is_directory for e in entries)


# -- build_proposed_paths --


class TestBuildProposedPaths:
    def test_basic_mapping(self):
        entries = [
            TreeEntry(level=0, number=1, name="architecture", is_directory=True),
            TreeEntry(level=1, number=1, name="debugging.md", is_directory=False),
            TreeEntry(level=0, number=2, name="conventions", is_directory=True),
            TreeEntry(level=1, number=1, name="plan-json.md", is_directory=False),
        ]
        mapping = build_proposed_paths(entries)
        assert mapping["debugging"] == "architecture/debugging.md"
        assert mapping["plan-json"] == "conventions/plan-json.md"

    def test_root_files(self):
        entries = [
            TreeEntry(level=0, number=1, name="readme.md", is_directory=False),
        ]
        mapping = build_proposed_paths(entries)
        assert mapping["readme"] == "readme.md"
