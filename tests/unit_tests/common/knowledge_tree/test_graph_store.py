"""Graph Store 测试（InMemory 实现）。"""

import pytest

from src.common.knowledge_tree.dag.edge import KnowledgeEdge
from src.common.knowledge_tree.dag.node import KnowledgeNode
from src.common.knowledge_tree.storage.graph_store import InMemoryGraphStore


@pytest.fixture
def graph() -> InMemoryGraphStore:
    g = InMemoryGraphStore()
    g.initialize()
    return g


@pytest.fixture
def populated_graph(
    graph: InMemoryGraphStore, sample_nodes: list[KnowledgeNode], sample_edges: list[KnowledgeEdge]
) -> InMemoryGraphStore:
    for n in sample_nodes:
        graph.upsert_node(n)
    for e in sample_edges:
        graph.upsert_edge(e)
    return graph


class TestInMemoryNodeOps:
    def test_upsert_and_get(self, graph: InMemoryGraphStore, sample_node: KnowledgeNode):
        graph.upsert_node(sample_node)
        got = graph.get_node(sample_node.node_id)
        assert got is not None
        assert got.title == sample_node.title

    def test_get_nonexistent(self, graph: InMemoryGraphStore):
        assert graph.get_node("nope") is None

    def test_delete(self, graph: InMemoryGraphStore, sample_node: KnowledgeNode):
        graph.upsert_node(sample_node)
        assert graph.delete_node(sample_node.node_id) is True
        assert graph.get_node(sample_node.node_id) is None
        assert graph.delete_node(sample_node.node_id) is False

    def test_delete_cascades_edges(self, populated_graph: InMemoryGraphStore, sample_nodes: list[KnowledgeNode]):
        # 删除 node0，其子边应也被清理
        populated_graph.delete_node(sample_nodes[0].node_id)
        children = populated_graph.get_children(sample_nodes[0].node_id)
        assert len(children) == 0


class TestInMemoryEdgeOps:
    def test_upsert_and_get_children(
        self, populated_graph: InMemoryGraphStore, sample_nodes: list[KnowledgeNode]
    ):
        children = populated_graph.get_children("root", primary_only=True)
        assert len(children) == 3

    def test_get_children_all(
        self, populated_graph: InMemoryGraphStore, sample_nodes: list[KnowledgeNode]
    ):
        # 添加一条非主边
        edge = KnowledgeEdge.create(
            parent_id=sample_nodes[2].node_id,
            child_id=sample_nodes[3].node_id,
            is_primary=False,
        )
        populated_graph.upsert_edge(edge)

        # primary_only=True 应不包含非主边
        children_primary = populated_graph.get_children(sample_nodes[2].node_id, primary_only=True)
        assert len(children_primary) == 0

        # primary_only=False 应包含
        children_all = populated_graph.get_children(sample_nodes[2].node_id, primary_only=False)
        assert len(children_all) == 1

    def test_primary_path(
        self, populated_graph: InMemoryGraphStore, sample_nodes: list[KnowledgeNode]
    ):
        # node3 的主路径：root → node0 → node3
        path = populated_graph.get_primary_path(sample_nodes[3].node_id)
        assert path[0] == "root"
        assert path[1] == sample_nodes[0].node_id
        assert path[2] == sample_nodes[3].node_id

    def test_root_path_is_self(
        self, populated_graph: InMemoryGraphStore, sample_nodes: list[KnowledgeNode]
    ):
        path = populated_graph.get_primary_path("root")
        assert path == ["root"]

    def test_get_root_id(
        self, populated_graph: InMemoryGraphStore, sample_nodes: list[KnowledgeNode]
    ):
        root = populated_graph.get_root_id()
        assert root == "root"

    def test_get_all_edges(
        self, populated_graph: InMemoryGraphStore, sample_edges: list[KnowledgeEdge]
    ):
        all_edges = populated_graph.get_all_edges()
        assert len(all_edges) == len(sample_edges)

    def test_get_edges_for_node(
        self, populated_graph: InMemoryGraphStore, sample_nodes: list[KnowledgeNode]
    ):
        edges = populated_graph.get_edges_for_node(sample_nodes[0].node_id)
        # node0 有：1条来自root的入边 + 2条到子节点的出边 = 3条
        assert len(edges) == 3


class TestInMemoryClose:
    def test_close_clears_data(self, populated_graph: InMemoryGraphStore):
        populated_graph.close()
        assert populated_graph.get_root_id() is None
        assert populated_graph.get_all_edges() == []
