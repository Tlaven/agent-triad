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


def test_context_default_enable_implicit_thinking_is_true() -> None:
    ctx = Context()
    assert ctx.enable_implicit_thinking is True


def test_context_default_supervisor_thinking_visibility_is_implicit() -> None:
    ctx = Context()
    assert ctx.supervisor_thinking_visibility == "implicit"


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


def test_env_overrides_enable_implicit_thinking_false(monkeypatch) -> None:
    monkeypatch.setenv("ENABLE_IMPLICIT_THINKING", "false")
    ctx = Context()
    assert ctx.enable_implicit_thinking is False


def test_env_overrides_supervisor_thinking_visibility(monkeypatch) -> None:
    monkeypatch.setenv("SUPERVISOR_THINKING_VISIBILITY", "visible")
    ctx = Context()
    assert ctx.supervisor_thinking_visibility == "visible"


def test_deprecated_thinking_visibility_env_applies_when_supervisor_env_unset(monkeypatch) -> None:
    monkeypatch.delenv("SUPERVISOR_THINKING_VISIBILITY", raising=False)
    monkeypatch.setenv("THINKING_VISIBILITY", "visible")
    ctx = Context()
    assert ctx.supervisor_thinking_visibility == "visible"


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


def test_get_agent_llm_kwargs_returns_only_valid_values() -> None:
    ctx = Context(
        supervisor_temperature=0.2,
        supervisor_top_p=0.9,
        supervisor_max_tokens=1024,
        supervisor_seed=7,
    )
    kwargs = ctx.get_agent_llm_kwargs("supervisor")
    assert kwargs == {
        "temperature": 0.2,
        "top_p": 0.9,
        "max_tokens": 1024,
        "seed": 7,
        "extra_body": {"enable_thinking": True},
    }


def test_get_agent_llm_kwargs_skips_sentinel_defaults() -> None:
    ctx = Context()
    kwargs = ctx.get_agent_llm_kwargs("executor")
    assert kwargs.get("extra_body") == {"enable_thinking": True}


# ---------------------------------------------------------------------------
# V3+ enable_v3plus_async field
# ---------------------------------------------------------------------------

def test_context_default_enable_v3plus_async_is_false() -> None:
    """测试 enable_v3plus_async 默认值为 False。"""
    ctx = Context()
    assert ctx.enable_v3plus_async is False


def test_env_enables_v3plus_async_true(monkeypatch) -> None:
    """测试环境变量 ENABLE_V3PLUS_ASYNC=true 启用功能。"""
    monkeypatch.setenv("ENABLE_V3PLUS_ASYNC", "true")
    ctx = Context()
    assert ctx.enable_v3plus_async is True


def test_env_enables_v3plus_async_numeric(monkeypatch) -> None:
    """测试环境变量 ENABLE_V3PLUS_ASYNC=1 启用功能。"""
    monkeypatch.setenv("ENABLE_V3PLUS_ASYNC", "1")
    ctx = Context()
    assert ctx.enable_v3plus_async is True


def test_env_false_disables_v3plus_async(monkeypatch) -> None:
    """测试环境变量 ENABLE_V3PLUS_ASYNC=false 禁用功能。"""
    monkeypatch.setenv("ENABLE_V3PLUS_ASYNC", "false")
    ctx = Context()
    assert ctx.enable_v3plus_async is False


def test_env_yes_enables_v3plus_async(monkeypatch) -> None:
    """测试环境变量 ENABLE_V3PLUS_ASYNC=yes 启用功能。"""
    monkeypatch.setenv("ENABLE_V3PLUS_ASYNC", "yes")
    ctx = Context()
    assert ctx.enable_v3plus_async is True


def test_env_on_enables_v3plus_async(monkeypatch) -> None:
    """测试环境变量 ENABLE_V3PLUS_ASYNC=on 启用功能。"""
    monkeypatch.setenv("ENABLE_V3PLUS_ASYNC", "on")
    ctx = Context()
    assert ctx.enable_v3plus_async is True


def test_explicit_non_default_v3plus_async_blocks_env(monkeypatch) -> None:
    """测试显式设置非默认值会阻止环境变量覆盖。"""
    monkeypatch.setenv("ENABLE_V3PLUS_ASYNC", "false")
    ctx = Context(enable_v3plus_async=True)
    # 显式设置 True != 默认 False，环境变量不应覆盖
    assert ctx.enable_v3plus_async is True
