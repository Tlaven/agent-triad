"""Unit tests for executor_agent.tools tool functions (write_file, run_local_command)."""

import os

import pytest

from src.executor_agent.tools import (
    _agent_workspace_root,
    _resolve_workspace_path,
    write_file,
)

# ---------------------------------------------------------------------------
# write_file — integration tests using temp workspace
# ---------------------------------------------------------------------------


class TestWriteFile:
    def test_write_new_file(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv("AGENT_WORKSPACE_DIR", str(tmp_path))
        result = write_file.invoke({"path": "test.txt", "content": "hello world"})
        assert result["ok"] is True
        assert result["bytes"] > 0
        assert os.path.isfile(result["path"])
        with open(result["path"], encoding="utf-8") as f:
            assert f.read() == "hello world"

    def test_write_to_subdirectory(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv("AGENT_WORKSPACE_DIR", str(tmp_path))
        result = write_file.invoke({"path": "sub/dir/file.txt", "content": "nested"})
        assert result["ok"] is True
        assert "sub" in result["path"]

    def test_overwrite_existing_file(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv("AGENT_WORKSPACE_DIR", str(tmp_path))
        # First write
        r1 = write_file.invoke({"path": "existing.txt", "content": "original"})
        assert r1["ok"] is True
        assert r1["overwritten"] is False
        # Overwrite
        r2 = write_file.invoke({"path": "existing.txt", "content": "updated"})
        assert r2["ok"] is True
        assert r2["overwritten"] is True

    def test_no_overwrite_fails_if_exists(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv("AGENT_WORKSPACE_DIR", str(tmp_path))
        write_file.invoke({"path": "no_overwrite.txt", "content": "first"})
        result = write_file.invoke({"path": "no_overwrite.txt", "content": "second", "overwrite": False})
        assert result["ok"] is False
        assert "已存在" in result["error"]

    def test_empty_path_fails(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv("AGENT_WORKSPACE_DIR", str(tmp_path))
        result = write_file.invoke({"path": "", "content": "data"})
        assert result["ok"] is False
        assert result["error"] is not None

    def test_path_traversal_fails(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv("AGENT_WORKSPACE_DIR", str(tmp_path))
        result = write_file.invoke({"path": "../../../etc/passwd", "content": "evil"})
        assert result["ok"] is False


# ---------------------------------------------------------------------------
# _resolve_workspace_path
# ---------------------------------------------------------------------------


class TestResolveWorkspacePath:
    def test_relative_path_resolved(self, monkeypatch, tmp_path) -> None:
        monkeypatch.setenv("AGENT_WORKSPACE_DIR", str(tmp_path))
        result = _resolve_workspace_path("subdir/file.txt")
        assert tmp_path.name in result
        assert "subdir" in result

    def test_absolute_path_inside_workspace(self, monkeypatch, tmp_path) -> None:
        monkeypatch.setenv("AGENT_WORKSPACE_DIR", str(tmp_path))
        abs_path = os.path.join(str(tmp_path), "file.txt")
        result = _resolve_workspace_path(abs_path)
        assert result == os.path.abspath(abs_path)

    def test_path_escape_raises(self, monkeypatch, tmp_path) -> None:
        monkeypatch.setenv("AGENT_WORKSPACE_DIR", str(tmp_path))
        with pytest.raises(ValueError, match="超出"):
            _resolve_workspace_path("../../outside")

    def test_empty_env_uses_default(self, monkeypatch, tmp_path) -> None:
        monkeypatch.setenv("AGENT_WORKSPACE_DIR", "")
        # Should not raise — falls back to DEFAULT_AGENT_WORKSPACE_DIR
        result = _resolve_workspace_path("test.txt")
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# _agent_workspace_root
# ---------------------------------------------------------------------------


class TestAgentWorkspaceRoot:
    def test_custom_dir(self, monkeypatch, tmp_path) -> None:
        monkeypatch.setenv("AGENT_WORKSPACE_DIR", str(tmp_path))
        root = _agent_workspace_root()
        assert root == str(tmp_path)

    def test_empty_env_uses_default(self, monkeypatch) -> None:
        monkeypatch.setenv("AGENT_WORKSPACE_DIR", "")
        root = _agent_workspace_root()
        assert "workspace" in root
