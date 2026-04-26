"""文件系统存储层（Layer 1 Source of Truth）。

V4: 目录层级 = 树结构。每个 Markdown 文件是一个知识节点，
文件的相对路径就是 node_id（如 "development/debugging.md"）。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

from src.common.knowledge_tree.dag.node import KnowledgeNode

logger = logging.getLogger(__name__)


class MarkdownStore:
    """文件系统存储层。

    markdown_root 下的目录结构即知识树结构。
    """

    def __init__(
        self,
        root: Path,
        on_change: Callable[[str, str], None] | None = None,
    ) -> None:
        self.root = root
        self._initialized = False
        self._node_cache: dict[str, KnowledgeNode] = {}
        self._on_change = on_change

    def _ensure_root(self) -> None:
        if not self._initialized:
            self.root.mkdir(parents=True, exist_ok=True)
            self._initialized = True

    def _node_path(self, node_id: str) -> Path:
        """节点文件路径。node_id 是相对路径（如 "development/debugging.md"）。"""
        return self.root / node_id

    # -- 节点 CRUD --

    def write_node(self, node: KnowledgeNode) -> Path:
        """写入节点到 Markdown 文件。自动创建中间目录。"""
        self._ensure_root()
        path = self._node_path(node.node_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(node.to_frontmatter_md(), encoding="utf-8")
        self._node_cache[node.node_id] = node
        logger.debug("Wrote node %s to %s", node.node_id, path)
        if self._on_change:
            self._on_change("write", self._extract_directory(node.node_id))
        return path

    def read_node(self, node_id: str) -> KnowledgeNode | None:
        """读取节点（带缓存）。不存在返回 None。"""
        cached = self._node_cache.get(node_id)
        if cached is not None:
            return cached
        self._ensure_root()
        path = self._node_path(node_id)
        if not path.exists():
            return None
        text = path.read_text(encoding="utf-8")
        node = KnowledgeNode.from_frontmatter_md(text, node_id=node_id)
        self._node_cache[node_id] = node
        return node

    def delete_node(self, node_id: str) -> bool:
        """删除节点文件。返回是否成功删除。"""
        path = self._node_path(node_id)
        self._node_cache.pop(node_id, None)
        if path.exists():
            path.unlink()
            logger.debug("Deleted node %s", node_id)
            if self._on_change:
                self._on_change("delete", self._extract_directory(node_id))
            return True
        return False

    def move_node(self, old_id: str, new_id: str) -> bool:
        """移动节点文件（重命名路径）。返回是否成功。"""
        old_path = self._node_path(old_id)
        new_path = self._node_path(new_id)
        if not old_path.exists():
            return False
        new_path.parent.mkdir(parents=True, exist_ok=True)
        old_path.rename(new_path)
        # 迁移缓存
        cached = self._node_cache.pop(old_id, None)
        if cached is not None:
            cached.node_id = new_id
            self._node_cache[new_id] = cached
        logger.debug("Moved node %s -> %s", old_id, new_id)
        if self._on_change:
            old_dir = self._extract_directory(old_id)
            new_dir = self._extract_directory(new_id)
            self._on_change("delete", old_dir)
            self._on_change("write", new_dir)
        return True

    def node_exists(self, node_id: str) -> bool:
        return self._node_path(node_id).exists()

    @staticmethod
    def _extract_directory(node_id: str) -> str:
        """从 node_id（相对路径）提取目录部分。"""
        parts = node_id.rsplit("/", 1)
        return parts[0] if len(parts) > 1 else ""

    # -- 列举 --

    def list_node_ids(self) -> list[str]:
        """递归列举所有节点 ID（相对路径）。"""
        self._ensure_root()
        return [
            str(p.relative_to(self.root)).replace("\\", "/")
            for p in sorted(self.root.rglob("*.md"))
            if p.is_file()
            # 跳过隐藏文件和 overlay
            and not p.name.startswith(".")
        ]

    def list_nodes(self) -> list[KnowledgeNode]:
        """递归列举所有节点。跳过格式错误的文件。"""
        nodes: list[KnowledgeNode] = []
        for node_id in self.list_node_ids():
            try:
                node = self.read_node(node_id)
                if node is not None:
                    nodes.append(node)
            except Exception:
                logger.warning("Skipping invalid node: %s", node_id, exc_info=True)
        return nodes

    def list_directories(self) -> list[str]:
        """列举所有子目录（相对路径）。"""
        self._ensure_root()
        dirs: list[str] = []
        for d in sorted(self.root.rglob("*")):
            if d.is_dir() and not d.name.startswith("."):
                rel = str(d.relative_to(self.root)).replace("\\", "/")
                if rel != ".":
                    dirs.append(rel)
        return dirs

    def get_directory_files(self, directory: str) -> list[str]:
        """获取指定目录下的所有节点 ID。"""
        dir_path = self.root / directory
        if not dir_path.is_dir():
            return []
        return [
            str(p.relative_to(self.root)).replace("\\", "/")
            for p in sorted(dir_path.glob("*.md"))
            if p.is_file() and not p.name.startswith(".")
        ]

    # -- 目录操作 --

    def ensure_directory(self, directory: str) -> Path:
        """确保目录存在，返回完整路径。"""
        dir_path = self.root / directory
        dir_path.mkdir(parents=True, exist_ok=True)
        return dir_path

    def remove_directory_if_empty(self, directory: str) -> bool:
        """如果目录为空则删除。返回是否删除。"""
        dir_path = self.root / directory
        if dir_path.is_dir():
            try:
                dir_path.rmdir()  # 只能删空目录
                return True
            except OSError:
                return False
        return False
