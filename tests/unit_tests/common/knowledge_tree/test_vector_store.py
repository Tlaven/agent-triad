"""Vector Store 测试（InMemory 实现 + 目录锚点）。"""

import pytest

from src.common.knowledge_tree.storage.vector_store import (
    DirectoryAnchor,
    InMemoryVectorStore,
    _cosine_similarity,
    compute_anchor_vector,
    cosine_similarity,
)


@pytest.fixture
def vec_store() -> InMemoryVectorStore:
    return InMemoryVectorStore(dimension=4)


class TestInMemoryVectorStore:
    def test_upsert_and_get(self, vec_store: InMemoryVectorStore):
        emb = [1.0, 0.0, 0.0, 0.0]
        vec_store.upsert_embedding("n1", emb)
        assert vec_store.get_embedding("n1") == emb

    def test_get_nonexistent(self, vec_store: InMemoryVectorStore):
        assert vec_store.get_embedding("nope") is None

    def test_similarity_search_exact_match(self, vec_store: InMemoryVectorStore):
        emb = [1.0, 0.0, 0.0, 0.0]
        vec_store.upsert_embedding("n1", emb)

        results = vec_store.similarity_search(emb, top_k=5, threshold=0.9)
        assert len(results) == 1
        assert results[0][0] == "n1"
        assert results[0][1] == pytest.approx(1.0)

    def test_similarity_search_threshold(self, vec_store: InMemoryVectorStore):
        vec_store.upsert_embedding("n1", [1.0, 0.0, 0.0, 0.0])
        vec_store.upsert_embedding("n2", [0.0, 1.0, 0.0, 0.0])

        query = [1.0, 0.0, 0.0, 0.0]
        results = vec_store.similarity_search(query, top_k=5, threshold=0.9)
        assert len(results) == 1
        assert results[0][0] == "n1"

    def test_similarity_search_top_k(self, vec_store: InMemoryVectorStore):
        vec_store.upsert_embedding("n1", [1.0, 0.0, 0.0, 0.0])
        vec_store.upsert_embedding("n2", [0.9, 0.1, 0.0, 0.0])
        vec_store.upsert_embedding("n3", [0.8, 0.2, 0.0, 0.0])

        query = [1.0, 0.0, 0.0, 0.0]
        results = vec_store.similarity_search(query, top_k=2, threshold=0.5)
        assert len(results) == 2
        assert results[0][0] == "n1"

    def test_empty_search(self, vec_store: InMemoryVectorStore):
        results = vec_store.similarity_search([1.0, 0.0, 0.0, 0.0])
        assert results == []

    def test_delete(self, vec_store: InMemoryVectorStore):
        vec_store.upsert_embedding("n1", [1.0, 0.0, 0.0, 0.0])
        assert vec_store.delete_embedding("n1") is True
        assert vec_store.get_embedding("n1") is None
        assert vec_store.delete_embedding("n1") is False

    def test_close_clears(self, vec_store: InMemoryVectorStore):
        vec_store.upsert_embedding("n1", [1.0, 0.0, 0.0, 0.0])
        vec_store.close()
        assert vec_store.get_embedding("n1") is None


class TestDirectoryAnchors:
    def test_upsert_and_get_anchor(self, vec_store: InMemoryVectorStore):
        anchor = DirectoryAnchor(
            directory="dev",
            anchor_vector=[1.0, 0.0, 0.0, 0.0],
            file_count=3,
        )
        vec_store.upsert_anchor(anchor)
        retrieved = vec_store.get_anchor("dev")
        assert retrieved is not None
        assert retrieved.directory == "dev"
        assert retrieved.file_count == 3

    def test_get_nonexistent_anchor(self, vec_store: InMemoryVectorStore):
        assert vec_store.get_anchor("nonexistent") is None

    def test_delete_anchor(self, vec_store: InMemoryVectorStore):
        anchor = DirectoryAnchor(
            directory="dev",
            anchor_vector=[1.0, 0.0, 0.0, 0.0],
            file_count=1,
        )
        vec_store.upsert_anchor(anchor)
        assert vec_store.delete_anchor("dev") is True
        assert vec_store.get_anchor("dev") is None

    def test_find_nearest_anchor(self, vec_store: InMemoryVectorStore):
        vec_store.upsert_anchor(DirectoryAnchor(
            directory="dev", anchor_vector=[1.0, 0.0, 0.0, 0.0], file_count=1,
        ))
        vec_store.upsert_anchor(DirectoryAnchor(
            directory="test", anchor_vector=[0.0, 1.0, 0.0, 0.0], file_count=1,
        ))

        # 查询接近 dev 的向量
        result = vec_store.find_nearest_anchor([0.9, 0.1, 0.0, 0.0], threshold=0.5)
        assert result is not None
        assert result.directory == "dev"

    def test_find_nearest_no_match(self, vec_store: InMemoryVectorStore):
        vec_store.upsert_anchor(DirectoryAnchor(
            directory="dev", anchor_vector=[1.0, 0.0, 0.0, 0.0], file_count=1,
        ))
        # 正交向量，低于阈值
        result = vec_store.find_nearest_anchor([0.0, 1.0, 0.0, 0.0], threshold=0.9)
        assert result is None

    def test_get_all_anchors(self, vec_store: InMemoryVectorStore):
        vec_store.upsert_anchor(DirectoryAnchor(
            directory="dev", anchor_vector=[1.0, 0.0, 0.0, 0.0], file_count=1,
        ))
        vec_store.upsert_anchor(DirectoryAnchor(
            directory="test", anchor_vector=[0.0, 1.0, 0.0, 0.0], file_count=1,
        ))
        anchors = vec_store.get_all_anchors()
        assert len(anchors) == 2

    def test_get_embeddings_in_directory(self, vec_store: InMemoryVectorStore):
        vec_store.upsert_embedding("dev/a.md", [1.0, 0.0, 0.0, 0.0])
        vec_store.upsert_embedding("dev/b.md", [0.9, 0.1, 0.0, 0.0])
        vec_store.upsert_embedding("test/c.md", [0.0, 1.0, 0.0, 0.0])

        result = vec_store.get_embeddings_in_directory("dev")
        assert len(result) == 2
        assert "dev/a.md" in result
        assert "dev/b.md" in result


class TestComputeAnchorVector:
    def test_single_embedding(self):
        emb = [[1.0, 0.0, 0.0, 0.0]]
        anchor = compute_anchor_vector(emb)
        assert len(anchor) == 4
        assert anchor[0] == pytest.approx(1.0)

    def test_multiple_embeddings(self):
        embs = [
            [1.0, 0.0, 0.0, 0.0],
            [0.0, 1.0, 0.0, 0.0],
        ]
        anchor = compute_anchor_vector(embs)
        # 均值 = [0.5, 0.5, 0, 0]，归一化后 [0.707, 0.707, 0, 0]
        assert len(anchor) == 4
        assert anchor[0] == pytest.approx(anchor[1])
        assert anchor[0] > 0

    def test_empty_embeddings(self):
        assert compute_anchor_vector([]) == []


class TestCosineSimilarity:
    def test_parallel_vectors(self):
        assert cosine_similarity([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)

    def test_orthogonal_vectors(self):
        assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)

    def test_anti_parallel_vectors(self):
        assert cosine_similarity([1.0, 0.0], [-1.0, 0.0]) == pytest.approx(-1.0)

    def test_zero_vector(self):
        assert cosine_similarity([0.0, 0.0], [1.0, 0.0]) == 0.0

    def test_public_matches_private(self):
        a, b = [0.5, 0.3, 0.1], [0.2, 0.8, 0.4]
        assert cosine_similarity(a, b) == pytest.approx(_cosine_similarity(a, b))
