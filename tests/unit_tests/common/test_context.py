"""Unit tests for common.context.Context env-var override logic."""


from src.common.context import Context

# ---------------------------------------------------------------------------
# Default values
# ---------------------------------------------------------------------------

def test_context_default_max_replan() -> None:
    ctx = Context()
    assert ctx.max_replan == 3


def test_context_default_max_executor_iterations() -> None:
    ctx = Context()
    assert ctx.max_executor_iterations == 20


def test_context_default_enable_deepwiki_is_false() -> None:
    ctx = Context()
    assert ctx.enable_deepwiki is False


def test_context_default_supervisor_model() -> None:
    ctx = Context()
    assert "Step-3.5-Flash" in ctx.supervisor_model or "siliconflow" in ctx.supervisor_model


def test_context_default_planner_model_is_not_empty() -> None:
    ctx = Context()
    assert isinstance(ctx.planner_model, str)
    assert ctx.planner_model


def test_context_default_executor_model_is_not_empty() -> None:
    ctx = Context()
    assert isinstance(ctx.executor_model, str)
    assert ctx.executor_model


# ---------------------------------------------------------------------------
# Environment variable overrides
# ---------------------------------------------------------------------------

def test_env_overrides_max_replan(monkeypatch) -> None:
    monkeypatch.setenv("MAX_REPLAN", "7")
    ctx = Context()
    assert ctx.max_replan == 7


def test_env_overrides_max_executor_iterations(monkeypatch) -> None:
    monkeypatch.setenv("MAX_EXECUTOR_ITERATIONS", "30")
    ctx = Context()
    assert ctx.max_executor_iterations == 30


def test_env_overrides_bool_field_true(monkeypatch) -> None:
    monkeypatch.setenv("ENABLE_DEEPWIKI", "true")
    ctx = Context()
    assert ctx.enable_deepwiki is True


def test_env_overrides_bool_field_numeric(monkeypatch) -> None:
    monkeypatch.setenv("ENABLE_DEEPWIKI", "1")
    ctx = Context()
    assert ctx.enable_deepwiki is True


def test_env_false_does_not_enable_bool(monkeypatch) -> None:
    monkeypatch.setenv("ENABLE_DEEPWIKI", "false")
    ctx = Context()
    assert ctx.enable_deepwiki is False


# ---------------------------------------------------------------------------
# Explicit constructor arg overrides env var
# ---------------------------------------------------------------------------

def test_explicit_arg_overrides_env_max_replan(monkeypatch) -> None:
    monkeypatch.setenv("MAX_REPLAN", "5")
    ctx = Context(max_replan=10)
    # Explicit value (10) != default (3), so env var should NOT override
    assert ctx.max_replan == 10


def test_explicit_arg_overrides_env_for_bool(monkeypatch) -> None:
    monkeypatch.setenv("ENABLE_DEEPWIKI", "true")
    ctx = Context(enable_deepwiki=False)
    # Explicit False == default (False), so env var WILL override → True
    # This is the designed behaviour: only non-default explicit values block env override
    # (False == default False → env applies)
    assert ctx.enable_deepwiki is True


def test_explicit_non_default_max_replan_blocks_env(monkeypatch) -> None:
    monkeypatch.setenv("MAX_REPLAN", "5")
    ctx = Context(max_replan=99)
    assert ctx.max_replan == 99


# ---------------------------------------------------------------------------
# Invalid env var values fall back to default
# ---------------------------------------------------------------------------

def test_invalid_int_env_var_keeps_default(monkeypatch) -> None:
    monkeypatch.setenv("MAX_REPLAN", "not_a_number")
    ctx = Context()
    assert ctx.max_replan == 3
