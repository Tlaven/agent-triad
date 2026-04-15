"""Unit tests for common.context.Context env-var override logic."""

import pytest

from src.common.context import Context


# ---------------------------------------------------------------------------
# Default values — parametrized for simple int/bool fields
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("field,expected", [
    ("max_replan", 3),
    ("max_executor_iterations", 20),
    ("enable_deepwiki", False),
    ("enable_implicit_thinking", True),
    ("supervisor_thinking_visibility", "implicit"),
])
def test_context_defaults(field, expected) -> None:
    ctx = Context()
    assert getattr(ctx, field) == expected


def test_context_default_supervisor_model_is_set() -> None:
    ctx = Context()
    assert "Step-3.5-Flash" in ctx.supervisor_model or "siliconflow" in ctx.supervisor_model


def test_context_default_planner_and_executor_model_are_non_empty() -> None:
    ctx = Context()
    assert isinstance(ctx.planner_model, str) and ctx.planner_model
    assert isinstance(ctx.executor_model, str) and ctx.executor_model


# ---------------------------------------------------------------------------
# Environment variable overrides
# ---------------------------------------------------------------------------

def test_env_overrides_max_replan(monkeypatch) -> None:
    monkeypatch.setenv("MAX_REPLAN", "7")
    assert Context().max_replan == 7


def test_env_overrides_max_executor_iterations(monkeypatch) -> None:
    monkeypatch.setenv("MAX_EXECUTOR_ITERATIONS", "30")
    assert Context().max_executor_iterations == 30


@pytest.mark.parametrize("value,expected", [("true", True), ("1", True), ("false", False)])
def test_env_overrides_bool_field(monkeypatch, value, expected) -> None:
    monkeypatch.setenv("ENABLE_DEEPWIKI", value)
    assert Context().enable_deepwiki is expected


def test_env_overrides_enable_implicit_thinking_false(monkeypatch) -> None:
    monkeypatch.setenv("ENABLE_IMPLICIT_THINKING", "false")
    assert Context().enable_implicit_thinking is False


def test_env_overrides_supervisor_thinking_visibility(monkeypatch) -> None:
    monkeypatch.setenv("SUPERVISOR_THINKING_VISIBILITY", "visible")
    assert Context().supervisor_thinking_visibility == "visible"


def test_deprecated_thinking_visibility_env_applies_when_supervisor_env_unset(monkeypatch) -> None:
    monkeypatch.delenv("SUPERVISOR_THINKING_VISIBILITY", raising=False)
    monkeypatch.setenv("THINKING_VISIBILITY", "visible")
    assert Context().supervisor_thinking_visibility == "visible"


# ---------------------------------------------------------------------------
# Explicit constructor arg vs env var precedence
# ---------------------------------------------------------------------------

def test_explicit_non_default_arg_blocks_env(monkeypatch) -> None:
    monkeypatch.setenv("MAX_REPLAN", "5")
    assert Context(max_replan=99).max_replan == 99


def test_invalid_int_env_var_keeps_default(monkeypatch) -> None:
    monkeypatch.setenv("MAX_REPLAN", "not_a_number")
    assert Context().max_replan == 3


# ---------------------------------------------------------------------------
# get_agent_llm_kwargs
# ---------------------------------------------------------------------------

def test_get_agent_llm_kwargs_returns_only_valid_values() -> None:
    ctx = Context(
        supervisor_temperature=0.2,
        supervisor_top_p=0.9,
        supervisor_max_tokens=1024,
        supervisor_seed=7,
    )
    assert ctx.get_agent_llm_kwargs("supervisor") == {
        "temperature": 0.2,
        "top_p": 0.9,
        "max_tokens": 1024,
        "seed": 7,
        "extra_body": {"enable_thinking": True},
    }


def test_get_agent_llm_kwargs_skips_sentinel_defaults() -> None:
    ctx = Context()
    assert ctx.get_agent_llm_kwargs("executor").get("extra_body") == {"enable_thinking": True}
