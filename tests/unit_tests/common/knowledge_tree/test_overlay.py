"""Overlay Store CRUD 测试。"""

from pathlib import Path

import pytest

from src.common.knowledge_tree.storage.overlay import OverlayEdge, OverlayStore


@pytest.fixture
def store(tmp_path: Path) -> OverlayStore:
    return OverlayStore(tmp_path / "sub" / ".overlay.json")


class TestOverlayEdge:
    def test_to_dict_roundtrip(self):
        edge = OverlayEdge(
            source_path="dev/a.md",
            target_path="skills/b.md",
            relation="depends_on",
            strength=0.8,
            created_by="agent",
            note="test note",
        )
        d = edge.to_dict()
        restored = OverlayEdge.from_dict(d)
        assert restored.source_path == "dev/a.md"
        assert restored.target_path == "skills/b.md"
        assert restored.relation == "depends_on"
        assert restored.strength == 0.8
        assert restored.created_by == "agent"
        assert restored.note == "test note"

    def test_from_dict_defaults(self):
        edge = OverlayEdge.from_dict({"source": "a.md", "target": "b.md"})
        assert edge.relation == "related"
        assert edge.strength == 1.0
        assert edge.created_by == ""
        assert edge.note == ""


class TestOverlayStoreCRUD:
    def test_add_and_get_all(self, store: OverlayStore):
        store.add_edge(OverlayEdge("a.md", "b.md"))
        store.add_edge(OverlayEdge("c.md", "d.md", relation="depends_on"))
        assert len(store.get_all_edges()) == 2

    def test_add_dedup_same_source_target_relation(self, store: OverlayStore):
        store.add_edge(OverlayEdge("a.md", "b.md", strength=0.5))
        store.add_edge(OverlayEdge("a.md", "b.md", strength=0.9))
        edges = store.get_all_edges()
        assert len(edges) == 1
        assert edges[0].strength == 0.9  # 更新

    def test_add_different_relations_not_deduped(self, store: OverlayStore):
        store.add_edge(OverlayEdge("a.md", "b.md", relation="related"))
        store.add_edge(OverlayEdge("a.md", "b.md", relation="depends_on"))
        assert len(store.get_all_edges()) == 2

    def test_remove_edge(self, store: OverlayStore):
        store.add_edge(OverlayEdge("a.md", "b.md"))
        assert store.remove_edge("a.md", "b.md") is True
        assert len(store.get_all_edges()) == 0

    def test_remove_nonexistent(self, store: OverlayStore):
        assert store.remove_edge("x.md", "y.md") is False

    def test_get_edges_for(self, store: OverlayStore):
        store.add_edge(OverlayEdge("a.md", "b.md"))
        store.add_edge(OverlayEdge("c.md", "a.md"))
        store.add_edge(OverlayEdge("x.md", "y.md"))

        edges = store.get_edges_for("a.md")
        assert len(edges) == 2
        sources = {e.source_path for e in edges}
        targets = {e.target_path for e in edges}
        assert "a.md" in sources or "a.md" in targets

    def test_remove_all_for(self, store: OverlayStore):
        store.add_edge(OverlayEdge("a.md", "b.md"))
        store.add_edge(OverlayEdge("c.md", "a.md"))
        store.add_edge(OverlayEdge("x.md", "y.md"))

        removed = store.remove_all_for("a.md")
        assert removed == 2
        assert len(store.get_all_edges()) == 1

    def test_remove_all_for_no_match(self, store: OverlayStore):
        store.add_edge(OverlayEdge("x.md", "y.md"))
        assert store.remove_all_for("z.md") == 0

    def test_persistence(self, tmp_path: Path):
        path = tmp_path / ".overlay.json"
        store1 = OverlayStore(path)
        store1.add_edge(OverlayEdge("a.md", "b.md", strength=0.7))

        # 重新加载
        store2 = OverlayStore(path)
        edges = store2.get_all_edges()
        assert len(edges) == 1
        assert edges[0].strength == 0.7

    def test_empty_file_loads_gracefully(self, tmp_path: Path):
        path = tmp_path / ".overlay.json"
        path.write_text("", encoding="utf-8")
        store = OverlayStore(path)
        assert len(store.get_all_edges()) == 0

    def test_invalid_json_loads_gracefully(self, tmp_path: Path):
        path = tmp_path / ".overlay.json"
        path.write_text("not json {{{", encoding="utf-8")
        store = OverlayStore(path)
        assert len(store.get_all_edges()) == 0

    def test_nonexistent_file_loads_empty(self, tmp_path: Path):
        path = tmp_path / "deep" / "nested" / ".overlay.json"
        store = OverlayStore(path)
        assert len(store.get_all_edges()) == 0
        # add_edge 会创建目录和文件
        store.add_edge(OverlayEdge("a.md", "b.md"))
        assert path.exists()
