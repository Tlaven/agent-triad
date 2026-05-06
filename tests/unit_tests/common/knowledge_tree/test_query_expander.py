"""Unit tests for knowledge_tree.retrieval.query_expander."""

from unittest.mock import MagicMock

from src.common.knowledge_tree.retrieval.query_expander import expand_query


def _mock_llm(response_text: str) -> MagicMock:
    """Create a mock LLM that returns the given text."""
    mock = MagicMock()
    mock.invoke.return_value = MagicMock(content=response_text)
    return mock


def test_expand_query_returns_original_plus_variants() -> None:
    llm = _mock_llm("variant one\nvariant two\nvariant three")
    result = expand_query("original query", llm)
    assert result[0] == "original query"
    assert len(result) >= 2
    assert "variant one" in result


def test_expand_query_deduplicates() -> None:
    llm = _mock_llm("original query\noriginal query\ndifferent variant")
    result = expand_query("original query", llm)
    # original + different variant = 2
    assert result.count("original query") == 1


def test_expand_query_limits_to_n_plus_one() -> None:
    llm = _mock_llm("v1\nv2\nv3\nv4\nv5")
    result = expand_query("q", llm, n=2)
    # original + at most 2 variants
    assert len(result) <= 3


def test_expand_query_filters_short_variants() -> None:
    llm = _mock_llm("ab\nvalid variant text")
    result = expand_query("q", llm)
    assert all(len(v) > 2 or v == "q" for v in result)


def test_expand_query_llm_failure_returns_original_only() -> None:
    mock = MagicMock()
    mock.invoke.side_effect = RuntimeError("API error")
    result = expand_query("original", mock)
    assert result == ["original"]


def test_expand_query_empty_response_returns_original() -> None:
    llm = _mock_llm("")
    result = expand_query("original", llm)
    assert result == ["original"]


def test_expand_query_non_string_content() -> None:
    """When LLM returns list content, str() is used."""
    mock = MagicMock()
    mock.invoke.return_value = MagicMock(content=["line1", "line2"])
    result = expand_query("q", mock)
    assert result[0] == "q"
