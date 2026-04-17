"""知识树边数据模型。"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4


@dataclass
class KnowledgeEdge:
    """知识树边（DAG 中节点间的关系）。

    每个子节点有且仅有一条 is_primary=True 的父边——定义遍历主路径。
    其余父边 is_primary=False，作为关联引用。
    """

    edge_id: str
    parent_id: str
    child_id: str
    is_primary: bool
    edge_type: str = "parent_child"  # "parent_child" | "association"

    @classmethod
    def create(
        cls,
        parent_id: str,
        child_id: str,
        is_primary: bool = True,
        edge_type: str = "parent_child",
    ) -> KnowledgeEdge:
        """创建新边（自动生成 edge_id）。"""
        return cls(
            edge_id=uuid4().hex[:12],
            parent_id=parent_id,
            child_id=child_id,
            is_primary=is_primary,
            edge_type=edge_type,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "edge_id": self.edge_id,
            "parent_id": self.parent_id,
            "child_id": self.child_id,
            "is_primary": self.is_primary,
            "edge_type": self.edge_type,
        }

    @classmethod
    def from_dict(cls, d: dict[str, object]) -> KnowledgeEdge:
        return cls(
            edge_id=d["edge_id"],  # type: ignore[arg-type]
            parent_id=d["parent_id"],  # type: ignore[arg-type]
            child_id=d["child_id"],  # type: ignore[arg-type]
            is_primary=bool(d["is_primary"]),
            edge_type=d.get("edge_type", "parent_child"),  # type: ignore[arg-type]
        )
