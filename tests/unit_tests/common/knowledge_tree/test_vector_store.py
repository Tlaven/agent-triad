"""Vector Store 测试（InMemory 实现）。"""

import pytest

from src.common.knowledge_tree.storage.vector_store import InMemoryVectorStore


@pytest.fixture
def vec_store() -> InMemoryVectorStore:
    return InMemoryVectorStore(dimension=4)


def _vec(values: list[float]) -> list[float]:
    return values


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
        # 正交向量
        vec_store.upsert_embedding("n1", [1.0, 0.0, 0.0, 0.0])
        vec_store.upsert_embedding("n2", [0.0, 1.0, 0.0, 0.0])

        query = [1.0, 0.0, 0.0, 0.0]
        results = vec_store.similarity_search(query, top_k=5, threshold=0.9)
        # n2 与 query 正交（相似度=0），应被过滤
        assert len(results) == 1
        assert results[0][0] == "n1"

    def test_similarity_search_top_k(self, vec_store: InMemoryVectorStore):
        vec_store.upsert_embedding("n1", [1.0, 0.0, 0.0, 0.0])
        vec_store.upsert_embedding("n2", [0.9, 0.1, 0.0, 0.0])
        vec_store.upsert_embedding("n3", [0.8, 0.2, 0.0, 0.0])

        query = [1.0, 0.0, 0.0, 0.0]
        results = vec_store.similarity_search(query, top_k=2, threshold=0.5)
        assert len(results) == 2
        # n1 应排第一（最高相似度）
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
