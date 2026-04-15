"""Unit tests for executor_agent.tools input validation helpers."""

import os
import tempfile

import pytest

from src.executor_agent.tools import (
    MAX_LOCAL_COMMAND_LENGTH,
    MAX_LOCAL_COMMAND_TIMEOUT,
    MAX_WRITE_FILE_BYTES,
    _validate_run_local_command_input,
    _validate_write_file_input,
)

# ---------------------------------------------------------------------------
# _validate_write_file_input
# ---------------------------------------------------------------------------

def test_write_file_empty_path_is_error() -> None:
    assert _validate_write_file_input("", "content") is not None


@pytest.mark.parametrize("path", ["/absolute/path/file.txt", "C:\\absolute\\file.txt"])
def test_write_file_absolute_path_is_error(path) -> None:
    assert _validate_write_file_input(path, "x") is not None


@pytest.mark.parametrize("path", ["../escape/file.txt", "subdir/../../escape.txt"])
def test_write_file_path_traversal_is_error(path) -> None:
    assert _validate_write_file_input(path, "x") is not None


def test_write_file_oversized_content_is_error() -> None:
    assert _validate_write_file_input("output.txt", "x" * (MAX_WRITE_FILE_BYTES + 1)) is not None


def test_write_file_valid_input_returns_none() -> None:
    assert _validate_write_file_input("output/result.txt", "hello world") is None
    assert _validate_write_file_input("file.txt", "x" * MAX_WRITE_FILE_BYTES) is None


# ---------------------------------------------------------------------------
# _validate_run_local_command_input
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("cmd", ["", "   "])
def test_run_command_blank_is_error(cmd) -> None:
    assert _validate_run_local_command_input(cmd, 60, None) is not None


def test_run_command_too_long_is_error() -> None:
    assert _validate_run_local_command_input("echo " + "x" * MAX_LOCAL_COMMAND_LENGTH, 60, None) is not None


@pytest.mark.parametrize("timeout", [0, -1])
def test_run_command_invalid_timeout_is_error(timeout) -> None:
    assert _validate_run_local_command_input("echo hello", timeout, None) is not None


def test_run_command_timeout_over_max_is_error() -> None:
    assert _validate_run_local_command_input("echo hello", MAX_LOCAL_COMMAND_TIMEOUT + 1, None) is not None


def test_run_command_nonexistent_cwd_is_error() -> None:
    assert _validate_run_local_command_input("echo hello", 60, "/path/that/does/not/exist/xyzxyz") is not None


def test_run_command_valid_without_cwd_returns_none() -> None:
    assert _validate_run_local_command_input("echo hello", 60, None) is None


def test_run_command_absolute_cwd_is_error() -> None:
    assert _validate_run_local_command_input("echo hello", 60, "C:\\tmp") is not None


def test_run_command_absolute_cwd_inside_workspace_is_ok() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        os.environ["AGENT_WORKSPACE_DIR"] = tmpdir
        try:
            assert _validate_run_local_command_input("echo hello", 60, tmpdir) is None
        finally:
            os.environ.pop("AGENT_WORKSPACE_DIR", None)


def test_run_command_blocked_rm_rf_includes_rejection_reason() -> None:
    result = _validate_run_local_command_input("rm -rf /tmp", 60, None)
    assert result is not None
    assert "删除" in result or "禁止" in result


@pytest.mark.parametrize("cmd", [
    "format c:",
    "shutdown -h now",
    "diskpart",
    "mkfs.ext4 /dev/sdb1",
    "dd if=/dev/zero of=/dev/sdb",
    "reboot",
])
def test_run_command_blocked_destructive_commands(cmd) -> None:
    assert _validate_run_local_command_input(cmd, 60, None) is not None
