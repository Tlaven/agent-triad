"""Unit tests for common.utils helper functions."""

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from src.common.utils import get_message_text, normalize_region

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
