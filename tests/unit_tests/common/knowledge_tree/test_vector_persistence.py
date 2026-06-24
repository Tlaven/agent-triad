"""向量索引持久化模块测试。"""

import json

import pytest

from src.common.knowledge_tree.storage.vector_persistence import (
    VERSION,
    VectorIndexManifest,
    _compute_file_hashes,
    _content_hash,
    load_vector_index,
    save_vector_index,
)
from src.common.knowledge_tree.storage.vector_store import (
    DirectoryAnchor,
    InMemoryVectorStore,
)


@pytest.fixture
def vector_store():
    store = InMemoryVectorStore(dimension=4)
    store.upsert_embedding("dir1/node1.md", [0.1, 0.2, 0.3, 0.4])
    store.upsert_embedding("title:dir1/node1.md", [0.5, 0.6, 0.7, 0.8])
    store.upsert_embedding("stored:dir1/node1.md", [0.2, 0.3, 0.4, 0.5])
    store.upsert_anchor(DirectoryAnchor(
        directory="dir1",
        anchor_vector=[0.15, 0.25, 0.35, 0.45],
        file_count=1,
        last_updated="2026-01-01T00:00:00Z",
    ))
    return store


@pytest.fixture
def md_root(tmp_path):
    root = tmp_path / "kt"
    root.mkdir()
    (root / "dir1").mkdir()
    (root / "dir1" / "node1.md").write_text(
        "---\ntitle: Test Node\n---\nTest content\n", "utf-8"
    )
    return root


class TestVectorIndexManifest:
    def test_to_dict_round_trip(self):
        m = VectorIndexManifest(
            saved_at="2026-01-01T00:00:00Z",
            embedder_type="api",
            embedding_dimension=1024,
            node_count=25,
            anchor_count=10,
            file_hashes={"dir/node.md": "abc123"},
        )
        d = m.to_dict()
        m2 = VectorIndexManifest.from_dict(d)
        assert m2.version == VERSION
        assert m2.embedder_type == "api"
        assert m2.embedding_dimension == 1024
        assert m2.node_count == 25
        assert m2.file_hashes == {"dir/node.md": "abc123"}

    def test_from_dict_defaults(self):
        m = VectorIndexManifest.from_dict({})
        assert m.version == 0
        assert m.embedder_type == ""
        assert m.file_hashes == {}


class TestContentHash:
    def test_same_content_same_hash(self):
        assert _content_hash("hello") == _content_hash("hello")

    def test_different_content_different_hash(self):
        assert _content_hash("hello") != _content_hash("world")


class TestComputeFileHashes:
    def test_computes_hashes_for_md_files(self, md_root):
        hashes = _compute_file_hashes(md_root)
        assert "dir1/node1.md" in hashes
        assert len(hashes["dir1/node1.md"]) == 16

    def test_skips_dotfiles(self, md_root):
        (md_root / ".hidden.md").write_text("hidden", "utf-8")
        hashes = _compute_file_hashes(md_root)
        assert ".hidden.md" not in hashes

    def test_nonexistent_directory(self, tmp_path):
        hashes = _compute_file_hashes(tmp_path / "nonexistent")
        assert hashes == {}


class TestSaveVectorIndex:
    def test_saves_to_json(self, vector_store, md_root):
        result = save_vector_index(vector_store, md_root, "api")
        assert result is True

        index_path = md_root / ".vector_index.json"
        assert index_path.is_file()

        payload = json.loads(index_path.read_text("utf-8"))
        assert payload["manifest"]["version"] == VERSION
        assert payload["manifest"]["embedder_type"] == "api"
        assert payload["manifest"]["node_count"] == 1
        assert payload["manifest"]["anchor_count"] == 1
        assert "dir1/node1.md" in payload["manifest"]["file_hashes"]

    def test_atomic_write(self, vector_store, md_root):
        save_vector_index(vector_store, md_root, "hash")
        assert not (md_root / ".vector_index.tmp").exists()
        assert (md_root / ".vector_index.json").is_file()

    def test_creates_parent_dirs(self, vector_store, tmp_path):
        nested = tmp_path / "a" / "b" / "kt"
        result = save_vector_index(vector_store, nested, "hash", nested / ".vector_index.json")
        assert result is True
        assert (nested / ".vector_index.json").is_file()


class TestLoadVectorIndex:
    def test_load_after_save(self, vector_store, md_root):
        save_vector_index(vector_store, md_root, "api")

        fresh_store = InMemoryVectorStore(dimension=4)
        result = load_vector_index(fresh_store, md_root, "api")
        assert result is True
        assert fresh_store.get_embedding("dir1/node1.md") == pytest.approx([0.1, 0.2, 0.3, 0.4], abs=1e-10)
        assert fresh_store.get_embedding("title:dir1/node1.md") == pytest.approx([0.5, 0.6, 0.7, 0.8], abs=1e-10)
        assert fresh_store.get_anchor("dir1") is not None

    def test_returns_false_when_no_file(self, tmp_path):
        store = InMemoryVectorStore(dimension=4)
        result = load_vector_index(store, tmp_path / "nope", "hash")
        assert result is False

    def test_returns_false_on_corrupted_json(self, md_root):
        (md_root / ".vector_index.json").write_text("not json{", "utf-8")
        store = InMemoryVectorStore(dimension=4)
        assert load_vector_index(store, md_root, "hash") is False

    def test_returns_false_on_version_mismatch(self, vector_store, md_root):
        save_vector_index(vector_store, md_root, "hash")
        index_path = md_root / ".vector_index.json"
        payload = json.loads(index_path.read_text("utf-8"))
        payload["manifest"]["version"] = 99
        index_path.write_text(json.dumps(payload), "utf-8")

        store = InMemoryVectorStore(dimension=4)
        result = load_vector_index(store, md_root, "hash")
        assert result is False

    def test_returns_false_on_embedder_type_mismatch(self, vector_store, md_root):
        save_vector_index(vector_store, md_root, "api")

        store = InMemoryVectorStore(dimension=4)
        result = load_vector_index(store, md_root, "hash")
        assert result is False

    def test_returns_false_on_stale_files(self, vector_store, md_root):
        save_vector_index(vector_store, md_root, "api")
        (md_root / "dir1" / "node1.md").write_text("---\ntitle: Changed\n---\nNew content\n", "utf-8")

        store = InMemoryVectorStore(dimension=4)
        result = load_vector_index(store, md_root, "api")
        assert result is False

    def test_returns_false_on_new_file(self, vector_store, md_root):
        save_vector_index(vector_store, md_root, "api")
        (md_root / "dir1" / "new_node.md").write_text("---\ntitle: New\n---\nNew\n", "utf-8")

        store = InMemoryVectorStore(dimension=4)
        result = load_vector_index(store, md_root, "api")
        assert result is False

    def test_returns_false_on_dimension_mismatch(self, vector_store, md_root):
        save_vector_index(vector_store, md_root, "api")

        store_512 = InMemoryVectorStore(dimension=512)
        result = load_vector_index(store_512, md_root, "api")
        assert result is False

    def test_round_trip_preserves_all_data(self, md_root):
        store = InMemoryVectorStore(dimension=4)
        store.upsert_embedding("a.md", [0.1, 0.2, 0.3, 0.4])
        store.upsert_embedding("title:a.md", [0.5, 0.6, 0.7, 0.8])
        store.upsert_embedding("stored:a.md", [0.15, 0.25, 0.35, 0.45])
        store.upsert_embedding("b.md", [0.9, 0.8, 0.7, 0.6])
        store.upsert_anchor(DirectoryAnchor(
            directory="test",
            anchor_vector=[0.5, 0.5, 0.5, 0.5],
            file_count=2,
            last_updated="2026-06-01T00:00:00Z",
        ))

        (md_root / "a.md").write_text("---\ntitle: A\n---\nContent A\n", "utf-8")
        (md_root / "b.md").write_text("---\ntitle: B\n---\nContent B\n", "utf-8")

        save_vector_index(store, md_root, "hash")

        fresh = InMemoryVectorStore(dimension=4)
        assert load_vector_index(fresh, md_root, "hash") is True

        assert fresh.get_embedding("a.md") is not None
        assert fresh.get_embedding("a.md") == pytest.approx([0.1, 0.2, 0.3, 0.4], abs=1e-10)
        assert fresh.get_embedding("title:a.md") is not None
        assert fresh.get_embedding("stored:a.md") is not None
        assert fresh.get_embedding("b.md") is not None
        assert fresh.get_anchor("test") is not None
        assert fresh.get_anchor("test").file_count == 2


class TestLoadFromDictHardening:
    """Test load_from_dict validation and non-destructive failure."""

    def test_load_from_dict_preserves_existing_data_on_bad_version(self):
        store = InMemoryVectorStore(dimension=4)
        store.upsert_embedding("existing.md", [0.1, 0.2, 0.3, 0.4])
        with pytest.raises(ValueError, match="Unsupported vector index version"):
            store.load_from_dict({"version": 99, "dimension": 4, "embeddings": {}, "anchors": []})
        assert store.get_embedding("existing.md") is not None

    def test_load_from_dict_preserves_existing_data_on_dimension_mismatch(self):
        store = InMemoryVectorStore(dimension=4)
        store.upsert_embedding("existing.md", [0.1, 0.2, 0.3, 0.4])
        with pytest.raises(ValueError, match="Dimension mismatch"):
            store.load_from_dict({"version": 1, "dimension": 8, "embeddings": {}, "anchors": []})
        assert store.get_embedding("existing.md") is not None

    def test_load_from_dict_rejects_wrong_dimension_vectors(self):
        store = InMemoryVectorStore(dimension=4)
        data = {
            "version": 1,
            "dimension": 4,
            "embeddings": {"node.md": [0.1, 0.2]},
            "anchors": [],
        }
        with pytest.raises(ValueError, match="Dimension mismatch for key"):
            store.load_from_dict(data)

    def test_load_from_dict_rejects_non_list_vectors(self):
        store = InMemoryVectorStore(dimension=4)
        data = {
            "version": 1,
            "dimension": 4,
            "embeddings": {"node.md": "not_a_list"},
            "anchors": [],
        }
        with pytest.raises(ValueError, match="Invalid embedding"):
            store.load_from_dict(data)
        assert store.node_count == 0

    def test_delete_embedding_cleans_stored_key(self):
        store = InMemoryVectorStore(dimension=4)
        store.upsert_embedding("node.md", [0.1, 0.2, 0.3, 0.4])
        store.upsert_embedding("title:node.md", [0.5, 0.6, 0.7, 0.8])
        store.upsert_embedding("stored:node.md", [0.15, 0.25, 0.35, 0.45])
        store.upsert_embedding("alias:node.md:0", [0.2, 0.3, 0.4, 0.5])
        assert store.delete_embedding("node.md") is True
        assert store.get_embedding("node.md") is None
        assert store.get_embedding("title:node.md") is None
        assert store.get_embedding("stored:node.md") is None
        assert store.get_embedding("alias:node.md:0") is None