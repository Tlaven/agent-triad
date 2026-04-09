"""Define the state structures for the agent."""

from dataclasses import dataclass, field
from collections.abc import Sequence

from langchain_core.messages import AnyMessage
from langgraph.graph import add_messages
from langgraph.managed import IsLastStep
from typing_extensions import Annotated


@dataclass
class InputState:
    """为智能体定义输入状态，代表与外界交互的一个更窄的接口。

    这个类用于定义传入数据的初始状态和结构。
    """

    messages: Annotated[Sequence[AnyMessage], add_messages] = field(
        default_factory=list
    )

@dataclass
class PlannerSession:
    """表示一次活跃的 planner 会话的上下文"""
    session_id: str
    plan_json: str | None = None             # 最新的规划 JSON 字符串（含步骤执行状态）
    last_executor_status: str | None = None  # 最近一次 Executor 的结果："completed" / "failed"
    last_executor_error: str | None = None   # 最近一次 Executor 失败时的原因（异常信息或摘要）
    last_executor_summary: str | None = None  # 最近一次 Executor 返回的 summary
    last_executor_full_output: str | None = None  # 完整执行详情（含 updated_plan_json 步骤级细节），供 Supervisor 按需查阅
    # 按 plan_id 复用 Planner 对话上下文（V1: 仅内存，不持久化）
    planner_history_by_plan_id: dict[str, list[dict[str, str]]] = field(default_factory=dict)
    planner_last_version_by_plan_id: dict[str, int] = field(default_factory=dict)
    planner_last_output_by_plan_id: dict[str, str] = field(default_factory=dict)
    # 旧版计划归档（只读快照，按版本追加）
    plan_archive_by_plan_id: dict[str, list[str]] = field(default_factory=dict)


@dataclass
class SupervisorDecision:
    """Supervisor 本轮决策的结构化摘要。"""

    mode: int  # 1=Direct Response, 2=Tool-use ReAct, 3=Plan->Execute
    reason: str
    confidence: float


@dataclass
class ExecutorRef:
    """一条 Executor 的引用"""
    executor_session_id: str
    planner_session_id: str              # 这个 Executor 使用的 Planner 会话
    plan_json: str                       # 启动时拿到的计划
    status: str = "running"              # running / paused / completed / failed
    experiment_name: str = ""            # 可选：人类可读的名字，如 "resnet50_bs32"
    started_at: str = ""                 # ISO 时间
    # 后续可加 metrics_summary, checkpoint_path 等

@dataclass
class State(InputState):
    """表示智能体的完整状态，在InputState的基础上扩展了其他属性。

    这个类可用于存储代理生命周期中所需的任何信息。
    """
    messages: Annotated[list[AnyMessage], add_messages] = field(default_factory=list)
    planner_session: PlannerSession | None = None
    supervisor_decision: SupervisorDecision | None = None
    replan_count: int = 0
    executors: dict[str, ExecutorRef] = field(default_factory=dict)
    is_last_step: IsLastStep = field(default=False)

    # Additional attributes can be added here as needed.
    # Common examples include:
    # retrieved_documents: List[Document] = field(default_factory=list)
    # extracted_entities: Dict[str, Any] = field(default_factory=dict)
    # api_connections: Dict[str, Any] = field(default_factory=dict)
