"""Layer 1: Markdown 文件 CRUD（Source of Truth）。

每个节点一个 .md 文件，YAML frontmatter 存元数据，正文存 content。
"""

from __future__ import annotations

import logging
from pathlib import Path

from src.common.knowledge_tree.dag.node import KnowledgeNode

logger = logging.getLogger(__name__)


class MarkdownStore:
    """Markdown 文件存储层。"""

    def __init__(self, root: Path) -> None:
        self.root = root
        self._initialized = False

    def _ensure_root(self) -> None:
        """延迟创建根目录，避免在 __init__ 中触发 blocking 调用。"""
        if not self._initialized:
            self.root.mkdir(parents=True, exist_ok=True)
            self._initialized = True

    def _node_path(self, node_id: str) -> Path:
        """节点文件路径。"""
        return self.root / f"{node_id}.md"

    def write_node(self, node: KnowledgeNode) -> Path:
        """写入节点到 Markdown 文件。返回文件路径。"""
        self._ensure_root()
        path = self._node_path(node.node_id)
        path.write_text(node.to_frontmatter_md(), encoding="utf-8")
        logger.debug("Wrote node %s to %s", node.node_id, path)
        return path

    def read_node(self, node_id: str) -> KnowledgeNode | None:
        """读取节点。不存在返回 None。"""
        self._ensure_root()
        path = self._node_path(node_id)
        if not path.exists():
            return None
        text = path.read_text(encoding="utf-8")
        return KnowledgeNode.from_frontmatter_md(text)

    def delete_node(self, node_id: str) -> bool:
        """删除节点文件。返回是否成功删除。"""
        self._ensure_root()
        path = self._node_path(node_id)
        if path.exists():
            path.unlink()
            logger.debug("Deleted node %s", node_id)
            return True
        return False

    def list_node_ids(self) -> list[str]:
        """列出所有节点 ID。"""
        self._ensure_root()
        return [p.stem for p in self.root.glob("*.md")]

    def list_nodes(self) -> list[KnowledgeNode]:
        """列出所有节点。跳过格式错误的文件并记录警告。"""
        nodes: list[KnowledgeNode] = []
        for path in sorted(self.root.glob("*.md")):
            try:
                text = path.read_text(encoding="utf-8")
                nodes.append(KnowledgeNode.from_frontmatter_md(text))
            except Exception:
                logger.warning("Skipping invalid node file: %s", path, exc_info=True)
        return nodes

    def node_exists(self, node_id: str) -> bool:
        """检查节点是否存在。"""
        return self._node_path(node_id).exists()
