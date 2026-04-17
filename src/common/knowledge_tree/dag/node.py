"""知识树节点数据模型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import yaml


@dataclass
class KnowledgeNode:
    """知识树叶子/中间节点。

    序列化为带 YAML frontmatter 的 Markdown 文件（Layer 1 SoT）。
    """

    node_id: str
    title: str
    content: str
    source: str
    created_at: str  # ISO 8601
    summary: str = ""
    embedding: list[float] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    # -- 工厂方法 --

    @classmethod
    def create(
        cls,
        title: str,
        content: str,
        source: str = "",
        summary: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> KnowledgeNode:
        """创建新节点（自动生成 node_id 和时间戳）。"""
        return cls(
            node_id=uuid4().hex[:12],
            title=title,
            content=content,
            source=source,
            created_at=datetime.now(timezone.utc).isoformat(),
            summary=summary,
            metadata=metadata or {},
        )

    # -- Markdown 序列化（Layer 1: Source of Truth）--

    def to_frontmatter_md(self) -> str:
        """序列化为带 YAML frontmatter 的 Markdown 字符串。"""
        fm: dict[str, Any] = {
            "node_id": self.node_id,
            "title": self.title,
            "source": self.source,
            "created_at": self.created_at,
            "summary": self.summary,
        }
        if self.metadata:
            fm["metadata"] = self.metadata
        # embedding 不写入 Markdown（由 Layer 3 管理）
        return f"---\n{yaml.dump(fm, allow_unicode=True, default_flow_style=False).strip()}\n---\n\n{self.content}"

    @classmethod
    def from_frontmatter_md(cls, text: str) -> KnowledgeNode:
        """从带 YAML frontmatter 的 Markdown 反序列化。

        Raises:
            ValueError: frontmatter 格式错误或缺少必要字段。
        """
        if not text.startswith("---"):
            raise ValueError("Missing YAML frontmatter delimiter '---'")

        parts = text.split("---", 2)
        if len(parts) < 3:
            raise ValueError("Invalid frontmatter: expected opening and closing '---'")

        fm = yaml.safe_load(parts[1])
        if not isinstance(fm, dict):
            raise ValueError("Frontmatter must be a YAML mapping")

        required = {"node_id", "title", "content"}
        # content is the body after frontmatter
        content = parts[2].strip()
        fm["content"] = content

        missing = required - set(fm.keys())
        if missing:
            raise ValueError(f"Missing required fields: {missing}")

        return cls(
            node_id=fm["node_id"],
            title=fm["title"],
            content=content,
            source=fm.get("source", ""),
            created_at=fm.get("created_at", ""),
            summary=fm.get("summary", ""),
            metadata=fm.get("metadata", {}),
        )

    # -- 通用字典序列化 --

    def to_dict(self, include_embedding: bool = False) -> dict[str, Any]:
        """序列化为字典。默认不含 embedding（体积大）。"""
        d: dict[str, Any] = {
            "node_id": self.node_id,
            "title": self.title,
            "content": self.content,
            "source": self.source,
            "created_at": self.created_at,
            "summary": self.summary,
            "metadata": self.metadata,
        }
        if include_embedding and self.embedding is not None:
            d["embedding"] = self.embedding
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> KnowledgeNode:
        """从字典反序列化。"""
        return cls(
            node_id=d["node_id"],
            title=d["title"],
            content=d["content"],
            source=d.get("source", ""),
            created_at=d.get("created_at", ""),
            summary=d.get("summary", ""),
            embedding=d.get("embedding"),
            metadata=d.get("metadata", {}),
        )
