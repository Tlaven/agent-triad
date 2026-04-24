"""Overlay JSON 存储——跨目录关联边。

V4: 文件系统目录层级表达 primary 父子关系。
Overlay 仅存储 is_primary=False 的跨目录关联边，
保存在 markdown_root 下的 .overlay.json 文件中。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class OverlayEdge:
    """跨目录关联边（is_primary=False）。"""

    source_path: str  # 源文件相对路径
    target_path: str  # 目标文件相对路径
    relation: str = "related"  # 关系类型
    strength: float = 1.0  # 关联强度 0.0-1.0
    created_by: str = ""  # "agent" | "wiki_link" | "rag_co_occurrence"
    note: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "source": self.source_path,
            "target": self.target_path,
            "relation": self.relation,
            "strength": self.strength,
            "created_by": self.created_by,
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> OverlayEdge:
        return cls(
            source_path=d["source"],
            target_path=d["target"],
            relation=d.get("relation", "related"),
            strength=float(d.get("strength", 1.0)),
            created_by=d.get("created_by", ""),
            note=d.get("note", ""),
        )


class OverlayStore:
    """Overlay JSON 文件读写。

    文件格式：
    [
      {"source": "dev/debugging.md", "target": "skills/review.md", ...},
      ...
    ]
    """

    def __init__(self, overlay_path: Path) -> None:
        self._path = overlay_path
        self._edges: list[OverlayEdge] = []
        self._load()

    # -- 持久化 --

    def _load(self) -> None:
        """从磁盘加载。文件不存在或为空则初始化为空列表。"""
        if self._path.exists():
            try:
                raw = json.loads(self._path.read_text(encoding="utf-8"))
                if isinstance(raw, list):
                    self._edges = [
                        OverlayEdge.from_dict(e) for e in raw if isinstance(e, dict)
                    ]
                    return
            except (json.JSONDecodeError, OSError):
                pass
        self._edges = []

    def _save(self) -> None:
        """持久化到磁盘（原子写入：先写临时文件再重命名）。"""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        data = [e.to_dict() for e in self._edges]
        content = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
        tmp_path = self._path.with_suffix(".tmp")
        tmp_path.write_text(content, encoding="utf-8")
        os.replace(tmp_path, self._path)

    # -- CRUD --

    def add_edge(self, edge: OverlayEdge) -> None:
        """添加关联边（去重：同 source+target+relation 只保留一条）。"""
        for existing in self._edges:
            if (
                existing.source_path == edge.source_path
                and existing.target_path == edge.target_path
                and existing.relation == edge.relation
            ):
                existing.strength = edge.strength
                existing.note = edge.note
                self._save()
                return
        self._edges.append(edge)
        self._save()

    def remove_edge(self, source_path: str, target_path: str, relation: str = "related") -> bool:
        """移除指定关联边。"""
        before = len(self._edges)
        self._edges = [
            e
            for e in self._edges
            if not (e.source_path == source_path and e.target_path == target_path and e.relation == relation)
        ]
        if len(self._edges) < before:
            self._save()
            return True
        return False

    def get_edges_for(self, path: str) -> list[OverlayEdge]:
        """获取与指定文件相关的所有关联边。"""
        return [
            e for e in self._edges if e.source_path == path or e.target_path == path
        ]

    def get_all_edges(self) -> list[OverlayEdge]:
        """返回所有关联边。"""
        return list(self._edges)

    def remove_all_for(self, path: str) -> int:
        """移除与指定文件相关的所有边，返回移除数量。"""
        before = len(self._edges)
        self._edges = [e for e in self._edges if e.source_path != path and e.target_path != path]
        removed = before - len(self._edges)
        if removed:
            self._save()
        return removed
