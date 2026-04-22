"""检索日志数据模型。

V4: 简化的 RetrievalLog，RAG 为主检索路径。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4


@dataclass
class RetrievalLog:
    """单次检索的结构化日志。"""

    query_id: str
    query_text: str
    query_vector: list[float] | None = None
    rag_results: list[tuple[str, float]] = field(default_factory=list)  # (path, similarity)
    agent_satisfaction: bool | None = None
    agent_feedback: str | None = None
    manual_search_triggered: bool = False  # Agent 是否触发了手动搜索
    timestamp: str = ""

    @classmethod
    def create(cls, query_text: str) -> RetrievalLog:
        return cls(
            query_id=uuid4().hex[:12],
            query_text=query_text,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "query_id": self.query_id,
            "query_text": self.query_text,
            "rag_results": self.rag_results,
            "agent_satisfaction": self.agent_satisfaction,
            "agent_feedback": self.agent_feedback,
            "manual_search_triggered": self.manual_search_triggered,
            "timestamp": self.timestamp,
        }
