"""Layer 2: 图数据库抽象接口 + Kùzu 实现。

通过 BaseGraphStore 抽象基类隔离具体实现，便于未来迁移到 DuckDB 等。
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any

from src.common.knowledge_tree.dag.edge import KnowledgeEdge
from src.common.knowledge_tree.dag.node import KnowledgeNode

logger = logging.getLogger(__name__)


class BaseGraphStore(ABC):
    """图数据库抽象接口。"""

    @abstractmethod
    def initialize(self) -> None:
        """初始化数据库 schema（幂等）。"""

    @abstractmethod
    def upsert_node(self, node: KnowledgeNode) -> None:
        """插入或更新节点。"""

    @abstractmethod
    def get_node(self, node_id: str) -> KnowledgeNode | None:
        """获取节点。"""

    @abstractmethod
    def delete_node(self, node_id: str) -> bool:
        """删除节点。返回是否成功。"""

    @abstractmethod
    def upsert_edge(self, edge: KnowledgeEdge) -> None:
        """插入或更新边。"""

    @abstractmethod
    def delete_edge(self, edge_id: str) -> bool:
        """删除边。"""

    @abstractmethod
    def get_children(self, parent_id: str, primary_only: bool = True) -> list[KnowledgeNode]:
        """获取子节点列表。

        Args:
            parent_id: 父节点 ID。
            primary_only: 仅返回主路径子节点。
        """

    @abstractmethod
    def get_primary_path(self, node_id: str) -> list[str]:
        """获取从根到指定节点的主路径（node_id 列表）。"""

    @abstractmethod
    def get_root_id(self) -> str | None:
        """获取根节点 ID（无父节点且 is_primary 的节点）。"""

    @abstractmethod
    def get_all_edges(self) -> list[KnowledgeEdge]:
        """获取所有边。"""

    @abstractmethod
    def get_edges_for_node(self, node_id: str) -> list[KnowledgeEdge]:
        """获取与节点关联的所有边。"""

    @abstractmethod
    def close(self) -> None:
        """关闭数据库连接。"""


class InMemoryGraphStore(BaseGraphStore):
    """内存图数据库实现（用于测试和 Kùzu 不可用时的回退）。

    使用字典存储，P1 功能完整但不持久化。
    """

    def __init__(self) -> None:
        self._nodes: dict[str, KnowledgeNode] = {}
        self._edges: dict[str, KnowledgeEdge] = {}
        # child_id → list of parent edges (快速查找子节点的父边)
        self._child_to_edges: dict[str, list[KnowledgeEdge]] = {}

    def initialize(self) -> None:
        pass  # 内存实现无需初始化

    def upsert_node(self, node: KnowledgeNode) -> None:
        self._nodes[node.node_id] = node

    def get_node(self, node_id: str) -> KnowledgeNode | None:
        return self._nodes.get(node_id)

    def delete_node(self, node_id: str) -> bool:
        if node_id in self._nodes:
            del self._nodes[node_id]
            # 清理关联边
            edges_to_remove = [
                e for e in self._edges.values()
                if e.parent_id == node_id or e.child_id == node_id
            ]
            for e in edges_to_remove:
                self.delete_edge(e.edge_id)
            return True
        return False

    def upsert_edge(self, edge: KnowledgeEdge) -> None:
        self._edges[edge.edge_id] = edge
        if edge.child_id not in self._child_to_edges:
            self._child_to_edges[edge.child_id] = []
        # 去重
        existing = [e for e in self._child_to_edges[edge.child_id] if e.edge_id != edge.edge_id]
        existing.append(edge)
        self._child_to_edges[edge.child_id] = existing

    def delete_edge(self, edge_id: str) -> bool:
        edge = self._edges.pop(edge_id, None)
        if edge is None:
            return False
        if edge.child_id in self._child_to_edges:
            self._child_to_edges[edge.child_id] = [
                e for e in self._child_to_edges[edge.child_id] if e.edge_id != edge_id
            ]
        return True

    def get_children(self, parent_id: str, primary_only: bool = True) -> list[KnowledgeNode]:
        child_edges = [
            e for e in self._edges.values()
            if e.parent_id == parent_id
            and (not primary_only or e.is_primary)
        ]
        nodes: list[KnowledgeNode] = []
        for e in child_edges:
            node = self._nodes.get(e.child_id)
            if node is not None:
                nodes.append(node)
        return nodes

    def get_primary_path(self, node_id: str) -> list[str]:
        """从叶子到根回溯主路径，然后反转。"""
        path: list[str] = [node_id]
        current = node_id
        visited: set[str] = {current}

        while True:
            parent_edges = [
                e for e in self._child_to_edges.get(current, [])
                if e.is_primary
            ]
            if not parent_edges:
                break
            parent_id = parent_edges[0].parent_id
            if parent_id in visited:
                break  # 防环
            path.append(parent_id)
            visited.add(parent_id)
            current = parent_id

        path.reverse()
        return path

    def get_root_id(self) -> str | None:
        """根节点：无主父边的节点。

        同时检查 _nodes 和 _edges 中的 parent_id，
        因为根节点可能不作为 KnowledgeNode 存在（如虚拟根）。
        """
        children_with_primary_parent: set[str] = set()
        parents: set[str] = set()
        for e in self._edges.values():
            if e.is_primary:
                children_with_primary_parent.add(e.child_id)
                parents.add(e.parent_id)

        # 先检查 _nodes 中是否有非子节点的节点
        for node_id in self._nodes:
            if node_id not in children_with_primary_parent:
                return node_id

        # 再检查边中的 parent_id（虚拟根可能不在 _nodes 中）
        for parent_id in parents:
            if parent_id not in children_with_primary_parent:
                return parent_id

        return None

    def get_all_edges(self) -> list[KnowledgeEdge]:
        return list(self._edges.values())

    def get_edges_for_node(self, node_id: str) -> list[KnowledgeEdge]:
        return [
            e for e in self._edges.values()
            if e.parent_id == node_id or e.child_id == node_id
        ]

    def close(self) -> None:
        self._nodes.clear()
        self._edges.clear()
        self._child_to_edges.clear()
