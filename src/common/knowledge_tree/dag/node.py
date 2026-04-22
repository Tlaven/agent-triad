"""知识树节点数据模型。

V4: node_id 为文件相对路径（如 "development/debugging.md"），
文件系统目录层级即树结构，不再使用 UUID。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import yaml


@dataclass
class KnowledgeNode:
    """知识树节点——对应文件系统中的一个 Markdown 文件。

    node_id = 文件相对于 markdown_root 的路径（如 "development/debugging.md"）。
    目录层级天然表达父子关系，无需额外 Graph 层。
    """

    node_id: str  # 文件相对路径
    title: str
    content: str
    source: str
    created_at: str  # ISO 8601
    summary: str = ""
    embedding: list[float] | None = None  # content_embedding（纯内容语义，永不变）
    stored_vector: list[float] | None = None  # P2: α·content + β·structural
    metadata: dict[str, Any] = field(default_factory=dict)

    # -- 目录锚点相关（P2）--
    directory: str = ""  # 所属目录路径
    anchor: list[float] | None = None  # 所属目录的锚点向量

    # -- 工厂方法 --

    @classmethod
    def create(
        cls,
        node_id: str,
        title: str,
        content: str,
        source: str = "",
        summary: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> KnowledgeNode:
        """创建新节点。node_id 为文件相对路径。"""
        return cls(
            node_id=node_id,
            title=title,
            content=content,
            source=source,
            created_at=datetime.now(timezone.utc).isoformat(),
            summary=summary,
            metadata=metadata or {},
        )

    # -- Markdown 序列化（文件系统 SoT）--

    def to_frontmatter_md(self) -> str:
        """序列化为带 YAML frontmatter 的 Markdown 字符串。"""
        fm: dict[str, Any] = {
            "title": self.title,
            "source": self.source,
            "created_at": self.created_at,
        }
        if self.summary:
            fm["summary"] = self.summary
        if self.metadata:
            fm["metadata"] = self.metadata
        # node_id / embedding / stored_vector / directory / anchor 不写入 Markdown
        # node_id 由文件路径推导，向量由 Vector 层管理
        return f"---\n{yaml.dump(fm, allow_unicode=True, default_flow_style=False).strip()}\n---\n\n{self.content}"

    @classmethod
    def from_frontmatter_md(
        cls,
        text: str,
        node_id: str,
    ) -> KnowledgeNode:
        """从带 YAML frontmatter 的 Markdown 反序列化。

        Args:
            text: Markdown 文本（含 frontmatter）。
            node_id: 文件相对路径（从文件位置推导，不存于 frontmatter）。

        Raises:
            ValueError: frontmatter 格式错误或缺少必要字段。
        """
        if not text.startswith("---"):
            # 无 frontmatter——整个文本就是 content
            return cls(
                node_id=node_id,
                title=node_id.rsplit("/", 1)[-1].removesuffix(".md"),
                content=text.strip(),
                source="",
                created_at="",
            )

        parts = text.split("---", 2)
        if len(parts) < 3:
            raise ValueError("Invalid frontmatter: expected opening and closing '---'")

        fm = yaml.safe_load(parts[1])
        if fm is not None and not isinstance(fm, dict):
            raise ValueError("Frontmatter must be a YAML mapping")
        if fm is None:
            fm = {}

        content = parts[2].strip()

        return cls(
            node_id=node_id,
            title=fm.get("title", node_id.rsplit("/", 1)[-1].removesuffix(".md")),
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
            "directory": self.directory,
        }
        if include_embedding:
            if self.embedding is not None:
                d["embedding"] = self.embedding
            if self.stored_vector is not None:
                d["stored_vector"] = self.stored_vector
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
            stored_vector=d.get("stored_vector"),
            metadata=d.get("metadata", {}),
            directory=d.get("directory", ""),
            anchor=d.get("anchor"),
        )
