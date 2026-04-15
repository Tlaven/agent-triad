"""Model default loading for agent context."""

from __future__ import annotations

from pathlib import Path
from typing import Any

DEFAULT_SUPERVISOR_MODEL = "siliconflow:stepfun-ai/Step-3.5-Flash"
DEFAULT_PLANNER_MODEL = "siliconflow:Pro/zai-org/GLM-5"
DEFAULT_EXECUTOR_MODEL = "siliconflow:stepfun-ai/Step-3.5-Flash"
MODEL_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "agent_models.toml"


def _as_float(value: Any, fallback: float) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    return fallback


def _as_int(value: Any, fallback: int) -> int:
    if isinstance(value, int):
        return value
    return fallback


def _as_bool(value: Any, fallback: bool) -> bool:
    if isinstance(value, bool):
        return value
    return fallback


def _as_non_empty_str(value: Any, fallback: str) -> str:
    if isinstance(value, str) and value:
        return value
    return fallback


def _load_context_defaults() -> dict[str, Any]:
    """Load context defaults from config file."""
    defaults = {
        "supervisor_model": DEFAULT_SUPERVISOR_MODEL,
        "planner_model": DEFAULT_PLANNER_MODEL,
        "executor_model": DEFAULT_EXECUTOR_MODEL,
        "supervisor_temperature": -1.0,
        "supervisor_top_p": -1.0,
        "supervisor_max_tokens": 0,
        "supervisor_seed": -1,
        "planner_temperature": -1.0,
        "planner_top_p": -1.0,
        "planner_max_tokens": 0,
        "planner_seed": -1,
        "executor_temperature": -1.0,
        "executor_top_p": -1.0,
        "executor_max_tokens": 0,
        "executor_seed": -1,
        "enable_implicit_thinking": True,
        "supervisor_thinking_visibility": "implicit",
    }
    if not MODEL_CONFIG_PATH.exists():
        return defaults

    try:
        import tomllib

        with MODEL_CONFIG_PATH.open("rb") as file:
            data = tomllib.load(file)
    except (OSError, ValueError):
        return defaults

    models = data.get("models")
    if isinstance(models, dict):
        for agent in ("supervisor", "planner", "executor"):
            defaults[f"{agent}_model"] = _as_non_empty_str(
                models.get(agent),
                defaults[f"{agent}_model"],
            )

    sampling = data.get("sampling")
    if isinstance(sampling, dict):
        for agent in ("supervisor", "planner", "executor"):
            agent_sampling = sampling.get(agent)
            if not isinstance(agent_sampling, dict):
                continue

            defaults[f"{agent}_temperature"] = _as_float(
                agent_sampling.get("temperature"),
                defaults[f"{agent}_temperature"],
            )
            defaults[f"{agent}_top_p"] = _as_float(
                agent_sampling.get("top_p"),
                defaults[f"{agent}_top_p"],
            )
            defaults[f"{agent}_max_tokens"] = _as_int(
                agent_sampling.get("max_tokens"),
                defaults[f"{agent}_max_tokens"],
            )
            defaults[f"{agent}_seed"] = _as_int(
                agent_sampling.get("seed"),
                defaults[f"{agent}_seed"],
            )

    reasoning = data.get("reasoning")
    if isinstance(reasoning, dict):
        defaults["enable_implicit_thinking"] = _as_bool(
            reasoning.get("enable_implicit_thinking"),
            defaults["enable_implicit_thinking"],
        )
        defaults["supervisor_thinking_visibility"] = _as_non_empty_str(
            reasoning.get("supervisor_thinking_visibility"),
            defaults["supervisor_thinking_visibility"],
        )

    return defaults


MODEL_DEFAULTS = _load_context_defaults()
