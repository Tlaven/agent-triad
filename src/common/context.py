"""Define the configurable parameters for the agent."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Annotated, Any, Mapping

from .context_model_defaults import DEFAULT_SUPERVISOR_MODEL, MODEL_DEFAULTS


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
        default=False,
        metadata={
            "description": "Whether to use streaming (astream) for LLM calls and aggregate chunks into a final AIMessage.",
        },
    )
    # Global reasoning switch (best-effort): when enabled, pass `enable_thinking`
    # to provider adapters that support OpenAI-compatible extra_body payloads.
    enable_implicit_thinking: bool = field(
        default=MODEL_DEFAULTS["enable_implicit_thinking"],
        metadata={
            "description": "Whether to request implicit thinking/reasoning when provider supports it.",
        },
    )
    supervisor_thinking_visibility: str = field(
        default=MODEL_DEFAULTS["supervisor_thinking_visibility"],
        metadata={
            "description": "Supervisor only: whether to merge model reasoning into message "
            "content (visible) or keep it out of content (implicit). Planner/Executor stay implicit.",
        },
    )

    # Per-agent LLM sampling configs. Sentinel values mean "use provider/model defaults".
    # temperature/top_p: < 0 => unset; max_tokens: <= 0 => unset; seed: < 0 => unset
    supervisor_temperature: float = field(
        default=MODEL_DEFAULTS["supervisor_temperature"],
        metadata={"description": "Supervisor LLM temperature. <0 uses model default."},
    )
    supervisor_top_p: float = field(
        default=MODEL_DEFAULTS["supervisor_top_p"],
        metadata={"description": "Supervisor LLM top_p. <0 uses model default."},
    )
    supervisor_max_tokens: int = field(
        default=MODEL_DEFAULTS["supervisor_max_tokens"],
        metadata={"description": "Supervisor LLM max_tokens. <=0 uses model default."},
    )
    supervisor_seed: int = field(
        default=MODEL_DEFAULTS["supervisor_seed"],
        metadata={"description": "Supervisor LLM seed. <0 uses model default."},
    )
    planner_temperature: float = field(
        default=MODEL_DEFAULTS["planner_temperature"],
        metadata={"description": "Planner LLM temperature. <0 uses model default."},
    )
    planner_top_p: float = field(
        default=MODEL_DEFAULTS["planner_top_p"],
        metadata={"description": "Planner LLM top_p. <0 uses model default."},
    )
    planner_max_tokens: int = field(
        default=MODEL_DEFAULTS["planner_max_tokens"],
        metadata={"description": "Planner LLM max_tokens. <=0 uses model default."},
    )
    planner_seed: int = field(
        default=MODEL_DEFAULTS["planner_seed"],
        metadata={"description": "Planner LLM seed. <0 uses model default."},
    )
    executor_temperature: float = field(
        default=MODEL_DEFAULTS["executor_temperature"],
        metadata={"description": "Executor LLM temperature. <0 uses model default."},
    )
    executor_top_p: float = field(
        default=MODEL_DEFAULTS["executor_top_p"],
        metadata={"description": "Executor LLM top_p. <0 uses model default."},
    )
    executor_max_tokens: int = field(
        default=MODEL_DEFAULTS["executor_max_tokens"],
        metadata={"description": "Executor LLM max_tokens. <=0 uses model default."},
    )
    executor_seed: int = field(
        default=MODEL_DEFAULTS["executor_seed"],
        metadata={"description": "Executor LLM seed. <0 uses model default."},
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
    enable_filesystem_mcp: bool = field(
        default=False,
        metadata={
            "description": "Whether to enable shared local filesystem tools (read-only) for workspace inspection.",
            "json_schema_extra": {"langgraph_nodes": ["tools"]},
        },
    )
    filesystem_mcp_root_dir: str = field(
        default="workspace",
        metadata={
            "description": "Relative root directory exposed to shared local filesystem tools.",
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

    # Observation / 工具输出治理
    max_observation_chars: int = field(
        default=6500,
        metadata={
            "description": "Max characters for a single tool observation injected into ReAct history.",
        },
    )
    observation_offload_threshold_chars: int = field(
        default=28000,
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

    # Planner 仅绑定只读工具；Executor 可追加副作用工具。
    readonly_tools_only: bool = field(
        default=False,
        metadata={
            "description": "When true (Planner), only read-only / MCP tools are exposed.",
        },
    )

    # Executor 中途 Reflection / snapshot（默认 0 关闭，避免改变既有单测/短任务行为；可用环境变量开启）
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

    # Process-separated execution (Executor runs as subprocess with HTTP communication)
    executor_host: str = field(
        default="localhost",
        metadata={
            "description": "Hostname for the Executor server (Process B).",
        },
    )
    executor_port: int = field(
        default=0,
        metadata={
            "description": "Port for the Executor server (Process B). 0 = dynamic allocation.",
        },
    )
    snapshot_interval: int = field(
        default=0,
        metadata={
            "description": "Emit lightweight snapshot every N tool rounds in Executor (0 disables).",
        },
    )
    executor_startup_timeout: float = field(
        default=30.0,
        metadata={
            "description": "Seconds to wait for Executor process /health to respond.",
        },
    )
    executor_call_model_timeout: float = field(
        default=180.0,
        metadata={
            "description": "Wall-clock timeout in seconds for a single Executor LLM call. "
                           "Exceeding this terminates the executor process. 0 disables.",
        },
    )
    executor_tool_timeout: float = field(
        default=300.0,
        metadata={
            "description": "Wall-clock timeout in seconds for Executor tools_node execution. "
                           "Exceeding this returns a timeout warning to the LLM. 0 disables.",
        },
    )

    mailbox_port: int = field(
        default=0,
        metadata={
            "description": "Port for the Mailbox HTTP server thread (0 = dynamic).",
            "env_var": "MAILBOX_PORT",
        },
    )

    # --- V4: Knowledge Tree ---
    enable_knowledge_tree: bool = field(
        default=False,
        metadata={"description": "Enable the V4 Knowledge Tree module."},
    )
    knowledge_tree_root: str = field(
        default="workspace/knowledge_tree",
        metadata={"description": "Root directory for Knowledge Tree Markdown files."},
    )
    knowledge_tree_db_path: str = field(
        default="workspace/knowledge_tree/.kuzu",
        metadata={"description": "Path to Kuzu database file for Knowledge Tree."},
    )
    kt_tree_nav_confidence: float = field(
        default=0.7,
        metadata={"description": "Confidence threshold for LLM tree navigation."},
    )
    kt_rag_similarity_threshold: float = field(
        default=0.85,
        metadata={"description": "Similarity threshold for RAG fallback retrieval."},
    )
    kt_optimization_window: int = field(
        default=3600,
        metadata={"description": "Time window in seconds for optimization frequency cap."},
    )
    kt_max_optimizations_per_window: int = field(
        default=10,
        metadata={"description": "Maximum optimization actions per time window."},
    )
    kt_nav_failure_threshold: int = field(
        default=5,
        metadata={"description": "Navigation failure count threshold per node per window."},
    )
    kt_rag_false_positive_threshold: int = field(
        default=3,
        metadata={"description": "RAG false positive count threshold per window."},
    )
    kt_total_failure_threshold: int = field(
        default=3,
        metadata={"description": "Total retrieval failure count threshold per query pattern."},
    )
    kt_content_insufficient_threshold: int = field(
        default=5,
        metadata={"description": "Content insufficiency count threshold per node."},
    )
    kt_embedding_model: str = field(
        default="BAAI/bge-small-zh-v1.5",
        metadata={"description": "Embedding model for Knowledge Tree vector index."},
    )
    kt_embedding_dimension: int = field(
        default=512,
        metadata={"description": "Embedding vector dimension."},
    )
    kt_max_tree_depth: int = field(
        default=5,
        metadata={"description": "Maximum tree depth for navigation."},
    )

    def __post_init__(self) -> None:
        """Fetch env vars for attributes that were not passed as args."""
        import os

        # Backward compatibility: if only MODEL is set, use it as supervisor_model.
        if (
            self.supervisor_model == DEFAULT_SUPERVISOR_MODEL
            and os.environ.get("SUPERVISOR_MODEL") is None
            and os.environ.get("MODEL") is not None
        ):
            self.supervisor_model = os.environ["MODEL"]

        self._apply_field_env_overrides(os.environ)
        self._apply_legacy_thinking_visibility(os.environ)

    @staticmethod
    def _parse_env_for_default(default_value: Any, env_value: str) -> Any | None:
        if isinstance(default_value, bool):
            return env_value.lower() in ("true", "1", "yes", "on")
        if isinstance(default_value, int):
            try:
                return int(env_value)
            except ValueError:
                return None
        if isinstance(default_value, float):
            try:
                return float(env_value)
            except ValueError:
                return None
        return env_value

    def _apply_field_env_overrides(self, environ: Mapping[str, str]) -> None:
        from dataclasses import fields

        for f in fields(self):
            if not f.init:
                continue

            current_value = getattr(self, f.name)
            default_value = f.default
            env_value = environ.get(f.name.upper())

            # Only override with environment variable if current value equals default
            # This preserves explicit configuration from LangGraph configurable
            if current_value == default_value and env_value is not None:
                parsed_value = self._parse_env_for_default(default_value, env_value)
                if parsed_value is not None:
                    setattr(self, f.name, parsed_value)

    def _apply_legacy_thinking_visibility(self, environ: Mapping[str, str]) -> None:
        from dataclasses import fields

        # Deprecated: THINKING_VISIBILITY → supervisor_thinking_visibility when
        # SUPERVISOR_THINKING_VISIBILITY is unset (same semantics as old name).
        if environ.get("SUPERVISOR_THINKING_VISIBILITY") is not None:
            return

        legacy_tv = environ.get("THINKING_VISIBILITY")
        if legacy_tv is None:
            return

        supervisor_tv_field = next(
            (f for f in fields(self) if f.name == "supervisor_thinking_visibility"),
            None,
        )
        if supervisor_tv_field is None:
            return
        if getattr(self, "supervisor_thinking_visibility") == supervisor_tv_field.default:
            self.supervisor_thinking_visibility = legacy_tv.strip()

    def get_agent_llm_kwargs(self, agent: str) -> dict[str, Any]:
        """Build per-agent model kwargs from context.

        Args:
            agent: One of "supervisor" | "planner" | "executor".
        """
        prefix = agent.strip().lower()
        if prefix not in ("supervisor", "planner", "executor"):
            return {}

        kwargs: dict[str, Any] = {}
        sampling_rules = (
            ("temperature", -1.0, lambda value: isinstance(value, (int, float)) and value >= 0, float),
            ("top_p", -1.0, lambda value: isinstance(value, (int, float)) and value >= 0, float),
            ("max_tokens", 0, lambda value: isinstance(value, int) and value > 0, lambda value: value),
            ("seed", -1, lambda value: isinstance(value, int) and value >= 0, lambda value: value),
        )
        for param_name, default_value, validator, converter in sampling_rules:
            value = getattr(self, f"{prefix}_{param_name}", default_value)
            if validator(value):
                kwargs[param_name] = converter(value)

        # Best-effort reasoning toggle for OpenAI-compatible providers.
        # Unsupported providers should ignore this field.
        kwargs["extra_body"] = {"enable_thinking": bool(self.enable_implicit_thinking)}
        return kwargs
