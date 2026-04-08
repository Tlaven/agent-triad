"""Define the configurable parameters for the agent."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated

DEFAULT_SUPERVISOR_MODEL = "siliconflow:stepfun-ai/Step-3.5-Flash"
DEFAULT_PLANNER_MODEL = "siliconflow:Pro/zai-org/GLM-5"
DEFAULT_EXECUTOR_MODEL = "siliconflow:stepfun-ai/Step-3.5-Flash"
MODEL_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "agent_models.toml"


def _load_model_defaults() -> dict[str, str]:
    """Load agent model defaults from config file."""
    defaults = {
        "supervisor_model": DEFAULT_SUPERVISOR_MODEL,
        "planner_model": DEFAULT_PLANNER_MODEL,
        "executor_model": DEFAULT_EXECUTOR_MODEL,
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
    if not isinstance(models, dict):
        return defaults

    model_key_map = {
        "supervisor": "supervisor_model",
        "planner": "planner_model",
        "executor": "executor_model",
    }
    for file_key, field_name in model_key_map.items():
        model_value = models.get(file_key)
        if isinstance(model_value, str) and model_value:
            defaults[field_name] = model_value

    return defaults


MODEL_DEFAULTS = _load_model_defaults()


@dataclass(kw_only=True)
class Context:
    """The context for the agent.

    三层 Agent 共享同一套运行时配置：通过 LangGraph `context=Context(...)` 传入；
    环境变量仅在 `__post_init__` 中用于填充默认字段，各 Agent 节点只读 `runtime.context`，不直接读 `os.environ`。
    """

    supervisor_model: Annotated[str, {"__template_metadata__": {"kind": "llm"}}] = field(
        default=MODEL_DEFAULTS["supervisor_model"],
        metadata={
            "description": "The name of the language model to use for the supervisor agent. "
            "Should be in the form: provider:model-name.",
            "json_schema_extra": {"langgraph_nodes": ["call_model"]},
        },
    )

    planner_model: Annotated[str, {"__template_metadata__": {"kind": "llm"}}] = field(
        default=MODEL_DEFAULTS["planner_model"],
        metadata={
            "description": "The name of the language model to use for the planner agent. "
            "Should be in the form: provider:model-name.",
        },
    )

    executor_model: Annotated[str, {"__template_metadata__": {"kind": "llm"}}] = field(
        default=MODEL_DEFAULTS["executor_model"],
        metadata={
            "description": "The name of the language model to use for the executor agent. "
            "Should be in the form: provider:model-name.",
        },
    )
    enable_llm_streaming: bool = field(
        default=True,
        metadata={
            "description": "Whether to use streaming (astream) for LLM calls and aggregate chunks into a final AIMessage.",
        },
    )

    max_search_results: int = field(
        default=5,
        metadata={
            "description": "The maximum number of search results to return for each search query.",
            "json_schema_extra": {"langgraph_nodes": ["tools"]},
        },
    )

    enable_deepwiki: bool = field(
        default=False,
        metadata={
            "description": "Whether to enable the DeepWiki MCP tool for accessing open source project documentation.",
            "json_schema_extra": {"langgraph_nodes": ["tools"]},
        },
    )
    max_replan: int = field(
        default=3,
        metadata={
            "description": "Maximum number of failed execute cycles before supervisor stops replanning.",
            "json_schema_extra": {"langgraph_nodes": ["call_model", "tools"]},
        },
    )

    max_executor_iterations: int = field(
        default=20,
        metadata={
            "description": "Maximum ReAct iterations for the executor graph.",
        },
    )

    max_planner_iterations: int = field(
        default=25,
        metadata={
            "description": "Maximum ReAct iterations for the planner graph (tool loops).",
        },
    )

    # V2-a: Observation / tool output governance
    max_observation_chars: int = field(
        default=50_000,
        metadata={
            "description": "Max characters for a single tool observation injected into ReAct history.",
        },
    )
    observation_offload_threshold_chars: int = field(
        default=50_000,
        metadata={
            "description": "When raw tool output exceeds this size, offload to disk (if enabled).",
        },
    )
    enable_observation_offload: bool = field(
        default=True,
        metadata={
            "description": "If true, very large tool outputs are written under workspace/.observations/.",
        },
    )
    enable_observation_summary: bool = field(
        default=False,
        metadata={
            "description": "If true, optionally summarize oversized observations (extra LLM cost).",
        },
    )
    observation_workspace_dir: str = field(
        default="workspace/.observations",
        metadata={
            "description": "Relative directory for offloaded observation files.",
        },
    )

    # V2-b: Planner binds read-only tools only; Executor may add side-effect tools.
    readonly_tools_only: bool = field(
        default=False,
        metadata={
            "description": "When true (Planner), only read-only / MCP tools are exposed.",
        },
    )

    # V2-c: Executor reflection / snapshot（默认 0 关闭，避免改变既有单测/短任务行为；可用环境变量开启）
    reflection_interval: int = field(
        default=0,
        metadata={
            "description": "Run reflection every N tool rounds in Executor (0 disables interval trigger).",
        },
    )
    confidence_threshold: float = field(
        default=0.6,
        metadata={
            "description": "Executor reflection: trigger snapshot when model confidence is below this.",
        },
    )

    def __post_init__(self) -> None:
        """Fetch env vars for attributes that were not passed as args."""
        import os
        from dataclasses import fields

        # Backward compatibility: if only MODEL is set, use it as supervisor_model.
        if (
            self.supervisor_model == DEFAULT_SUPERVISOR_MODEL
            and os.environ.get("SUPERVISOR_MODEL") is None
            and os.environ.get("MODEL") is not None
        ):
            self.supervisor_model = os.environ["MODEL"]

        for f in fields(self):
            if not f.init:
                continue

            current_value = getattr(self, f.name)
            default_value = f.default
            env_var_name = f.name.upper()
            env_value = os.environ.get(env_var_name)

            # Only override with environment variable if current value equals default
            # This preserves explicit configuration from LangGraph configurable
            if current_value == default_value and env_value is not None:
                if isinstance(default_value, bool):
                    # Handle boolean environment variables
                    env_bool_value = env_value.lower() in ("true", "1", "yes", "on")
                    setattr(self, f.name, env_bool_value)
                elif isinstance(default_value, int):
                    try:
                        setattr(self, f.name, int(env_value))
                    except ValueError:
                        # Keep default value if env parsing fails.
                        pass
                elif isinstance(default_value, float):
                    try:
                        setattr(self, f.name, float(env_value))
                    except ValueError:
                        pass
                else:
                    setattr(self, f.name, env_value)
