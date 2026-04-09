"""Unit tests for executor_agent.tools input validation helpers."""


import os
import tempfile

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


def test_write_file_absolute_path_is_error() -> None:
    # Both Unix-style and Windows-style absolute paths should be rejected
    assert _validate_write_file_input("/absolute/path/file.txt", "x") is not None
    assert _validate_write_file_input("C:\\absolute\\file.txt", "x") is not None


def test_write_file_path_traversal_is_error() -> None:
    assert _validate_write_file_input("../escape/file.txt", "x") is not None
    assert _validate_write_file_input("subdir/../../escape.txt", "x") is not None


def test_write_file_oversized_content_is_error() -> None:
    big_content = "x" * (MAX_WRITE_FILE_BYTES + 1)
    assert _validate_write_file_input("output.txt", big_content) is not None


def test_write_file_valid_input_returns_none() -> None:
    assert _validate_write_file_input("output/result.txt", "hello world") is None


def test_write_file_content_at_exact_limit_is_ok() -> None:
    exact_content = "x" * MAX_WRITE_FILE_BYTES
    assert _validate_write_file_input("file.txt", exact_content) is None


# ---------------------------------------------------------------------------
# _validate_run_local_command_input
# ---------------------------------------------------------------------------

def test_run_command_empty_command_is_error() -> None:
    assert _validate_run_local_command_input("", 60, None) is not None


def test_run_command_whitespace_only_is_error() -> None:
    assert _validate_run_local_command_input("   ", 60, None) is not None


def test_run_command_too_long_is_error() -> None:
    long_cmd = "echo " + "x" * MAX_LOCAL_COMMAND_LENGTH
    assert _validate_run_local_command_input(long_cmd, 60, None) is not None


def test_run_command_zero_timeout_is_error() -> None:
    assert _validate_run_local_command_input("echo hello", 0, None) is not None


def test_run_command_negative_timeout_is_error() -> None:
    assert _validate_run_local_command_input("echo hello", -1, None) is not None


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


def test_run_command_blocked_rm_rf_root() -> None:
    result = _validate_run_local_command_input("rm -rf /tmp", 60, None)
    assert result is not None
    assert "删除" in result or "禁止" in result


def test_run_command_blocked_format_drive() -> None:
    result = _validate_run_local_command_input("format c:", 60, None)
    assert result is not None


def test_run_command_blocked_shutdown() -> None:
    result = _validate_run_local_command_input("shutdown -h now", 60, None)
    assert result is not None


def test_run_command_blocked_diskpart() -> None:
    result = _validate_run_local_command_input("diskpart", 60, None)
    assert result is not None


def test_run_command_blocked_mkfs() -> None:
    result = _validate_run_local_command_input("mkfs.ext4 /dev/sdb1", 60, None)
    assert result is not None


def test_run_command_blocked_dd_if() -> None:
    result = _validate_run_local_command_input("dd if=/dev/zero of=/dev/sdb", 60, None)
    assert result is not None


def test_run_command_blocked_reboot() -> None:
    result = _validate_run_local_command_input("reboot", 60, None)
    assert result is not None
