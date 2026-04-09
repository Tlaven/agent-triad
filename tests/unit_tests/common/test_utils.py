"""Unit tests for common.utils helper functions."""

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from src.common.utils import extract_reasoning_text, get_message_text, invoke_chat_model, normalize_region

# ---------------------------------------------------------------------------
# normalize_region
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("input_val,expected", [
    ("prc", "prc"),
    ("PRC", "prc"),
    ("cn", "prc"),
    ("CN", "prc"),
    ("international", "international"),
    ("INTERNATIONAL", "international"),
    ("en", "international"),
    ("EN", "international"),
])
def test_normalize_region_known_aliases(input_val: str, expected: str) -> None:
    assert normalize_region(input_val) == expected


@pytest.mark.parametrize("input_val", ["", "us", "eu", "unknown", "zh"])
def test_normalize_region_unknown_returns_none(input_val: str) -> None:
    assert normalize_region(input_val) is None


# ---------------------------------------------------------------------------
# get_message_text
# ---------------------------------------------------------------------------

def test_get_message_text_string_content() -> None:
    msg = HumanMessage(content="hello world")
    assert get_message_text(msg) == "hello world"


def test_get_message_text_dict_content() -> None:
    # dict content is supported via AIMessage (HumanMessage only accepts str/list)
    from unittest.mock import MagicMock

    from langchain_core.messages import BaseMessage

    mock_msg = MagicMock(spec=BaseMessage)
    mock_msg.content = {"text": "from dict"}
    assert get_message_text(mock_msg) == "from dict"


def test_get_message_text_dict_missing_text_key() -> None:
    from unittest.mock import MagicMock

    from langchain_core.messages import BaseMessage

    mock_msg = MagicMock(spec=BaseMessage)
    mock_msg.content = {"other": "value"}
    assert get_message_text(mock_msg) == ""


def test_get_message_text_list_of_strings() -> None:
    msg = AIMessage(content=["hello ", "world"])
    assert get_message_text(msg) == "hello world"


def test_get_message_text_list_of_dicts() -> None:
    msg = AIMessage(content=[{"text": "foo"}, {"text": "bar"}])
    assert get_message_text(msg) == "foobar"


def test_get_message_text_mixed_list() -> None:
    msg = AIMessage(content=["plain", {"text": " text"}])
    assert get_message_text(msg) == "plain text"


class _FakeChunk:
    def __init__(self, text: str) -> None:
        self.content = text

    def __add__(self, other: "_FakeChunk") -> "_FakeChunk":
        return _FakeChunk(self.content + other.content)

    def to_message(self) -> AIMessage:
        return AIMessage(content=self.content)


class _FakeStreamingModel:
    def __init__(self, chunks: list[str], *, fail_after: int | None = None) -> None:
        self._chunks = chunks
        self._fail_after = fail_after
        self.ainvoke_calls = 0

    async def astream(self, _messages):
        for idx, ch in enumerate(self._chunks):
            if self._fail_after is not None and idx >= self._fail_after:
                raise RuntimeError("stream error")
            yield _FakeChunk(ch)

    async def ainvoke(self, _messages):
        self.ainvoke_calls += 1
        return AIMessage(content="ainvoke fallback")


async def test_invoke_chat_model_streaming_success_aggregates_chunks() -> None:
    model = _FakeStreamingModel(["Hello ", "World"])
    result = await invoke_chat_model(model, [], enable_streaming=True)
    assert isinstance(result, AIMessage)
    assert result.content == "Hello World"
    assert model.ainvoke_calls == 0


async def test_invoke_chat_model_streaming_partial_error_uses_received_chunks() -> None:
    model = _FakeStreamingModel(["partial ", "ignored"], fail_after=1)
    result = await invoke_chat_model(model, [], enable_streaming=True)
    assert isinstance(result, AIMessage)
    assert result.content == "partial "
    assert model.ainvoke_calls == 0


async def test_invoke_chat_model_streaming_error_before_any_chunk_raises() -> None:
    model = _FakeStreamingModel(["x"], fail_after=0)
    with pytest.raises(RuntimeError, match="收到任何 chunk 前失败"):
        await invoke_chat_model(model, [], enable_streaming=True)
    assert model.ainvoke_calls == 0


def test_extract_reasoning_text_from_additional_kwargs() -> None:
    msg = AIMessage(
        content="ok",
        additional_kwargs={"reasoning_content": "step1 -> step2"},
    )
    assert extract_reasoning_text(msg) == "step1 -> step2"
