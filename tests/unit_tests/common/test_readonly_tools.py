"""Tests for new read-only workspace tools: search_files, grep_content, read_file_structure."""

import json
import os
import tempfile

import pytest

from src.common.tools import grep_content, read_file_structure, search_files


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
