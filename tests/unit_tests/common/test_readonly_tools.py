"""Tests for read-only workspace tools: list_workspace_entries, read_workspace_text_file, search_files, grep_content, read_file_structure."""

import json

import pytest

from src.common.tools import (
    grep_content,
    list_workspace_entries,
    read_file_structure,
    read_workspace_text_file,
    search_files,
)


@pytest.fixture
def workspace(tmp_path):
    """Create a temporary workspace with files for testing."""
    # Create structure
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("def hello():\n    print('hello world')\n", encoding="utf-8")
    (tmp_path / "src" / "utils.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_main.py").write_text("def test_hello():\n    assert True\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("# Test Project\n", encoding="utf-8")
    return tmp_path


# ---------------------------------------------------------------------------
# list_workspace_entries
# ---------------------------------------------------------------------------

class TestListWorkspaceEntries:
    def test_list_root(self, workspace):
        result = json.loads(list_workspace_entries.invoke({"root_dir": str(workspace)}))
        assert result["ok"] is True
        names = [e["name"] for e in result["entries"]]
        assert "src" in names
        assert "README.md" in names

    def test_list_subdirectory(self, workspace):
        result = json.loads(list_workspace_entries.invoke({"relative_path": "src", "root_dir": str(workspace)}))
        assert result["ok"] is True
        assert all(e["type"] == "file" for e in result["entries"])

    def test_list_nonexistent_path(self, workspace):
        result = json.loads(list_workspace_entries.invoke({"relative_path": "nope", "root_dir": str(workspace)}))
        assert result["ok"] is False
        assert "不存在" in result["error"]

    def test_list_file_instead_of_dir(self, workspace):
        result = json.loads(list_workspace_entries.invoke({"relative_path": "README.md", "root_dir": str(workspace)}))
        assert result["ok"] is False
        assert "不是目录" in result["error"]

    def test_list_path_traversal(self, workspace):
        result = json.loads(list_workspace_entries.invoke({"relative_path": "../..", "root_dir": str(workspace)}))
        assert result["ok"] is False

    def test_list_max_entries(self, workspace):
        result = json.loads(list_workspace_entries.invoke({"max_entries": 1, "root_dir": str(workspace)}))
        assert result["ok"] is True
        assert len(result["entries"]) <= 1


# ---------------------------------------------------------------------------
# read_workspace_text_file
# ---------------------------------------------------------------------------

class TestReadWorkspaceTextFile:
    def test_read_existing_file(self, workspace):
        result = json.loads(read_workspace_text_file.invoke({"relative_path": "README.md", "root_dir": str(workspace)}))
        assert result["ok"] is True
        assert "Test Project" in result["content"]

    def test_read_nonexistent_file(self, workspace):
        result = json.loads(read_workspace_text_file.invoke({"relative_path": "nope.txt", "root_dir": str(workspace)}))
        assert result["ok"] is False
        assert "不存在" in result["error"]

    def test_read_directory_instead_of_file(self, workspace):
        result = json.loads(read_workspace_text_file.invoke({"relative_path": "src", "root_dir": str(workspace)}))
        assert result["ok"] is False
        assert "不是文件" in result["error"]

    def test_read_truncation(self, workspace):
        long_file = workspace / "long.txt"
        long_file.write_text("x" * 500, encoding="utf-8")
        result = json.loads(read_workspace_text_file.invoke({"relative_path": "long.txt", "max_chars": 300, "root_dir": str(workspace)}))
        assert result["ok"] is True
        assert result["truncated"] is True

    def test_read_path_traversal(self, workspace):
        result = json.loads(read_workspace_text_file.invoke({"relative_path": "../../etc/passwd", "root_dir": str(workspace)}))
        assert result["ok"] is False


# ---------------------------------------------------------------------------
# search_files
# ---------------------------------------------------------------------------


class TestSearchFiles:
    def test_search_py_files_recursive(self, workspace):
        result = json.loads(search_files.invoke({"pattern": "**/*.py", "relative_path": ".", "root_dir": str(workspace)}))
        assert result["ok"] is True
        assert result["count"] >= 3
        paths = [m["relative_path"] for m in result["matches"]]
        assert any("main.py" in p for p in paths)

    def test_search_top_level_only(self, workspace):
        result = json.loads(search_files.invoke({"pattern": "*.md", "relative_path": ".", "root_dir": str(workspace)}))
        assert result["ok"] is True
        assert result["count"] == 1

    def test_search_nonexistent_pattern(self, workspace):
        result = json.loads(search_files.invoke({"pattern": "*.xyz", "relative_path": ".", "root_dir": str(workspace)}))
        assert result["ok"] is True
        assert result["count"] == 0

    def test_search_max_results(self, workspace):
        result = json.loads(search_files.invoke({"pattern": "**/*", "relative_path": ".", "max_results": 2, "root_dir": str(workspace)}))
        assert result["count"] <= 2

    def test_search_nonexistent_dir(self, workspace):
        result = json.loads(search_files.invoke({"pattern": "*.py", "relative_path": "nope", "root_dir": str(workspace)}))
        assert result["ok"] is False

    def test_search_path_traversal(self, workspace):
        result = json.loads(search_files.invoke({"pattern": "*", "relative_path": "../..", "root_dir": str(workspace)}))
        assert result["ok"] is False


class TestGrepContent:
    def test_grep_finds_match(self, workspace):
        result = json.loads(grep_content.invoke({"pattern": "hello", "relative_path": ".", "root_dir": str(workspace)}))
        assert result["ok"] is True
        assert result["count"] >= 1
        assert any("main.py" in r["file"] for r in result["results"])

    def test_grep_no_match(self, workspace):
        result = json.loads(grep_content.invoke({"pattern": "nonexistent_pattern_xyz", "relative_path": ".", "root_dir": str(workspace)}))
        assert result["ok"] is True
        assert result["count"] == 0

    def test_grep_file_pattern_filter(self, workspace):
        result = json.loads(grep_content.invoke({"pattern": "def", "relative_path": ".", "file_pattern": "*.py", "root_dir": str(workspace)}))
        assert result["ok"] is True
        assert all(r["file"].endswith(".py") for r in result["results"])

    def test_grep_invalid_regex(self, workspace):
        result = json.loads(grep_content.invoke({"pattern": "[invalid", "relative_path": ".", "root_dir": str(workspace)}))
        assert result["ok"] is False
        assert "正则" in result.get("error", "")

    def test_grep_nonexistent_dir(self, workspace):
        result = json.loads(grep_content.invoke({"pattern": "test", "relative_path": "nope", "root_dir": str(workspace)}))
        assert result["ok"] is False

    def test_grep_max_results(self, workspace):
        result = json.loads(grep_content.invoke({"pattern": "def", "relative_path": ".", "max_results": 1, "root_dir": str(workspace)}))
        assert result["ok"] is True
        assert result["count"] <= 1


class TestReadFileStructure:
    def test_read_structure(self, workspace):
        result = json.loads(read_file_structure.invoke({"relative_path": ".", "root_dir": str(workspace)}))
        assert result["ok"] is True
        assert "src/" in result["structure"]
        assert "main.py" in result["structure"]

    def test_read_structure_depth_limit(self, workspace):
        result = json.loads(read_file_structure.invoke({"relative_path": ".", "max_depth": 1, "root_dir": str(workspace)}))
        assert result["ok"] is True
        # With depth 1, should show top-level dirs but not their contents
        assert "src/" in result["structure"]

    def test_read_structure_nonexistent_path(self, workspace):
        result = json.loads(read_file_structure.invoke({"relative_path": "nonexistent", "root_dir": str(workspace)}))
        assert result["ok"] is False

    def test_read_structure_path_traversal(self, workspace):
        result = json.loads(read_file_structure.invoke({"relative_path": "../..", "root_dir": str(workspace)}))
        assert result["ok"] is False

    def test_read_structure_file_instead_of_dir(self, workspace):
        result = json.loads(read_file_structure.invoke({"relative_path": "README.md", "root_dir": str(workspace)}))
        assert result["ok"] is False
        assert "不是目录" in result["error"]

    def test_read_structure_max_entries(self, workspace):
        result = json.loads(read_file_structure.invoke({"relative_path": ".", "max_entries": 1, "root_dir": str(workspace)}))
        assert result["ok"] is True
        # Should have truncated indicator
        if "..." in result["structure"]:
            assert "truncated" in result["structure"]
