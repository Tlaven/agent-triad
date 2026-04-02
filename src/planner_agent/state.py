# planner_agent/state.py
from typing import Annotated, List
from langchain_core.messages import BaseMessage
from langgraph.graph import add_messages
from langgraph.managed import IsLastStep
from dataclasses import dataclass, field


@dataclass
class PlannerState:
    """Planner 专用的状态结构（极简版，方便扩展）"""
    messages: Annotated[List[BaseMessage], add_messages] = field(default_factory=list)
    is_last_step: IsLastStep = field(default=False)
    
    # 后续可以轻松扩展
    # plan_json: str | None = None
    # confidence: float = 0.0