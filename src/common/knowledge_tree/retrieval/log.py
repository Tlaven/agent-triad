"""检索日志数据模型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4


@dataclass
class RetrievalLog:
    """单次检索的结构化日志（决策 21）。"""

    query_id: str
    query_text: str
    query_vector: list[float] | None = None
    tree_path: list[str] = field(default_factory=list)
    tree_confidence: float | None = None
    tree_success: bool = False
    rag_triggered: bool = False
    rag_results: list[tuple[str, float]] = field(default_factory=list)
    fusion_mode: str = "none"  # "tree" | "tree+rag" | "rag" | "none"
    final_node_ids: list[str] = field(default_factory=list)
    agent_satisfaction: bool | None = None
    agent_feedback: str | None = None
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
            "tree_path": self.tree_path,
            "tree_confidence": self.tree_confidence,
            "tree_success": self.tree_success,
            "rag_triggered": self.rag_triggered,
            "rag_results": self.rag_results,
            "fusion_mode": self.fusion_mode,
            "final_node_ids": self.final_node_ids,
            "agent_satisfaction": self.agent_satisfaction,
            "agent_feedback": self.agent_feedback,
            "timestamp": self.timestamp,
        }
