"""Unit tests for src.common.observation (tool output governance)."""

import json
import os
import tempfile

from src.common.context import Context
from src.common.observation import normalize_observation, normalize_tool_message_content


def test_normalize_observation_small_string_unchanged() -> None:
    ctx = Context()
    r = normalize_observation("hello", context=ctx)
    assert r.text == "hello"
    assert not r.truncated
    assert not r.offloaded
    assert r.original_char_length == 5


def test_normalize_observation_truncates_when_over_max() -> None:
    ctx = Context(max_observation_chars=10, enable_observation_offload=False)
    s = "a" * 100
    r = normalize_observation(s, context=ctx)
    assert r.truncated
    assert "已截断" in r.text
    assert "100" in r.text
    assert len(r.text) <= 200


def test_normalize_observation_offloads_large_content() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        ctx = Context(
            max_observation_chars=50_000,
            observation_offload_threshold_chars=100,
            enable_observation_offload=True,
            observation_workspace_dir=".observations",
        )
        big = "x" * 500
        r = normalize_observation(big, context=ctx, cwd=tmp)
        assert r.offloaded
        assert r.offload_path is not None
        assert os.path.isfile(r.offload_path)
        with open(r.offload_path, encoding="utf-8") as f:
            assert f.read() == big
        assert "外置" in r.text


def test_normalize_tool_message_content_list_blocks() -> None:
    ctx = Context(max_observation_chars=1000)
    content = [{"type": "text", "text": "line1"}, {"type": "text", "text": "line2"}]
    out = normalize_tool_message_content(content, context=ctx)
    assert "line1" in out and "line2" in out


def test_normalize_observation_serializes_dict() -> None:
    ctx = Context()
    r = normalize_observation({"ok": True, "n": 3}, context=ctx)
    assert json.loads(r.text)["ok"] is True


# ---------------------------------------------------------------------------
# JSON serialization fallback
# ---------------------------------------------------------------------------

class _Unserializable:
    def __str__(self) -> str:
        return "<unserializable>"


def test_normalize_observation_non_json_serializable_falls_back_to_str() -> None:
    ctx = Context()
    obj = _Unserializable()
    r = normalize_observation(obj, context=ctx)
    assert "<unserializable>" in r.text


# ---------------------------------------------------------------------------
# OSError during offload → falls back to truncation
# ---------------------------------------------------------------------------

def test_normalize_observation_offload_oserror_fallback() -> None:
    """When offload fails, falls back to smart truncation."""
    import unittest.mock

    with tempfile.TemporaryDirectory() as tmp:
        ctx = Context(
            max_observation_chars=500,
            observation_offload_threshold_chars=100,
            enable_observation_offload=True,
            observation_workspace_dir=".observations",
        )
        big = "y" * 500
        # Mock open to raise OSError to simulate write failure
        with unittest.mock.patch("builtins.open", side_effect=OSError("permission denied")):
            r = normalize_observation(big, context=ctx, cwd=tmp)
        assert r.truncated
        assert not r.offloaded


# ---------------------------------------------------------------------------
# Smart truncation edge cases
# ---------------------------------------------------------------------------

def test_normalize_observation_truncate_extremely_small_budget() -> None:
    ctx = Context(max_observation_chars=50, enable_observation_offload=False)
    long_text = "abcdefghij" * 100
    r = normalize_observation(long_text, context=ctx)
    assert r.truncated
    assert len(r.text) <= 200  # includes notice


def test_normalize_observation_truncate_preserves_head_and_tail() -> None:
    ctx = Context(max_observation_chars=500, enable_observation_offload=False)
    text = "HEADER_LINE\n" + "x" * 2000 + "\nTAIL_LINE"
    r = normalize_observation(text, context=ctx)
    assert r.truncated
    assert "HEADER_LINE" in r.text
    assert "TAIL_LINE" in r.text


# ---------------------------------------------------------------------------
# normalize_tool_message_content — additional variants
# ---------------------------------------------------------------------------

def test_normalize_tool_message_content_string_passthrough() -> None:
    ctx = Context()
    result = normalize_tool_message_content("plain text", context=ctx)
    assert result == "plain text"


def test_normalize_tool_message_content_dict_block_without_type() -> None:
    ctx = Context(max_observation_chars=1000)
    content = [{"arbitrary": "data", "num": 42}]
    out = normalize_tool_message_content(content, context=ctx)
    assert "arbitrary" in out


def test_normalize_tool_message_content_non_str_non_list() -> None:
    ctx = Context()
    result = normalize_tool_message_content(12345, context=ctx)
    assert "12345" in result


def test_default_offload_path_within_agent_workspace() -> None:
    """默认外置路径必须在 agent workspace 根目录内，否则 agent 无法读取。"""
    from src.common.context import Context

    ctx = Context()
    # 默认的 observation_workspace_dir 必须以 agent workspace root 开头
    assert ctx.observation_workspace_dir.startswith("workspace/agent"), (
        f"observation_workspace_dir={ctx.observation_workspace_dir} "
        "不在 agent workspace root (workspace/agent) 内"
    )
