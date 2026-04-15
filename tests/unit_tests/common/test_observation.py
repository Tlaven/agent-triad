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
