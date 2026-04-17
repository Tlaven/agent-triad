"""KnowledgeEdge 数据模型测试。"""

from src.common.knowledge_tree.dag.edge import KnowledgeEdge


class TestKnowledgeEdgeCreate:
    def test_create_generates_id(self):
        e = KnowledgeEdge.create(parent_id="a", child_id="b")
        assert len(e.edge_id) == 12
        assert e.parent_id == "a"
        assert e.child_id == "b"
        assert e.is_primary is True
        assert e.edge_type == "parent_child"

    def test_create_non_primary(self):
        e = KnowledgeEdge.create(parent_id="a", child_id="b", is_primary=False)
        assert e.is_primary is False

    def test_create_association_type(self):
        e = KnowledgeEdge.create(
            parent_id="a", child_id="b", edge_type="association"
        )
        assert e.edge_type == "association"

    def test_unique_ids(self):
        e1 = KnowledgeEdge.create(parent_id="a", child_id="b")
        e2 = KnowledgeEdge.create(parent_id="a", child_id="b")
        assert e1.edge_id != e2.edge_id


class TestKnowledgeEdgeDict:
    def test_roundtrip(self):
        e = KnowledgeEdge.create(parent_id="p", child_id="c", is_primary=False, edge_type="association")
        d = e.to_dict()
        restored = KnowledgeEdge.from_dict(d)
        assert restored.edge_id == e.edge_id
        assert restored.parent_id == "p"
        assert restored.child_id == "c"
        assert restored.is_primary is False
        assert restored.edge_type == "association"
