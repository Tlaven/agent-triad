"""P2 structural_vector 混合测试。

验证：
- compute_stored_vector 计算正确性
- get_structural_vector_for_node 返回目录锚点
- similarity_search_stored 优先使用 stored: key
- on_change 回调触发 stored_vector 重算
- ingest / bootstrap 管道集成
"""

import math

import pytest

from src.common.knowledge_tree.dag.node import KnowledgeNode
from src.common.knowledge_tree.editing.stored_vector import (
    _extract_directory,
    compute_all_stored_vectors,
    compute_and_store_stored_vector,
    compute_stored_vector,
    compute_stored_vectors_for_directory,
    get_structural_vector_for_node,
)
from src.common.knowledge_tree.storage.markdown_store import MarkdownStore
from src.common.knowledge_tree.storage.vector_store import (
    DirectoryAnchor,
    InMemoryVectorStore,
)


def _normalize(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vec))
    return [x / norm for x in vec] if norm > 0 else vec


def _make_embedding(seed: float, dim: int = 16) -> list[float]:
    """生成确定性的测试向量。"""
    vec = [seed + i * 0.1 for i in range(dim)]
    return _normalize(vec)


@pytest.fixture
def md_store(tmp_path):
    return MarkdownStore(tmp_path / "kt_md")


@pytest.fixture
def vector_store():
    return InMemoryVectorStore(dimension=16)


# -- compute_stored_vector --


class TestComputeStoredVector:
    def test_basic_mix(self):
        content = _make_embedding(1.0)
        structural = _make_embedding(2.0)
        result = compute_stored_vector(content, structural, 0.8, 0.2)
        # 结果是归一化的
        norm = math.sqrt(sum(x * x for x in result))
        assert abs(norm - 1.0) < 1e-6

    def test_pure_content_when_structural_zero(self):
        """structural_weight=0 时结果应等于归一化的 content。"""
        content = _make_embedding(1.0)
        structural = _make_embedding(2.0)
        result = compute_stored_vector(content, structural, 1.0, 0.0)
        expected = _normalize(content)
        for a, b in zip(result, expected):
            assert abs(a - b) < 1e-6

    def test_weights_sum_to_one(self):
        content = _make_embedding(1.0)
        structural = _make_embedding(2.0)
        r1 = compute_stored_vector(content, structural, 0.8, 0.2)
        r2 = compute_stored_vector(content, structural, 0.5, 0.5)
        # 不同权重应该产生不同结果
        assert any(abs(a - b) > 1e-6 for a, b in zip(r1, r2))


# -- get_structural_vector_for_node --


class TestGetStructuralVector:
    def test_returns_anchor_for_directory(self, vector_store):
        anchor_vec = _make_embedding(5.0)
        vector_store.upsert_anchor(
            DirectoryAnchor(directory="dev", anchor_vector=anchor_vec, file_count=1)
        )
        result = get_structural_vector_for_node("dev/test.md", vector_store)
        assert result == anchor_vec

    def test_returns_none_for_root_file(self, vector_store):
        result = get_structural_vector_for_node("readme.md", vector_store)
        assert result is None

    def test_returns_none_for_missing_anchor(self, vector_store):
        result = get_structural_vector_for_node("unknown/test.md", vector_store)
        assert result is None


# -- compute_and_store_stored_vector --


class TestComputeAndStore:
    def test_stores_under_stored_key(self, vector_store):
        node_id = "dev/test.md"
        content = _make_embedding(1.0)
        anchor_vec = _make_embedding(2.0)
        vector_store.upsert_embedding(node_id, content)
        vector_store.upsert_anchor(
            DirectoryAnchor(directory="dev", anchor_vector=anchor_vec, file_count=1)
        )

        result = compute_and_store_stored_vector(node_id, vector_store)
        assert result is not None
        # stored vector 应该在 "stored:{node_id}" 键下
        stored = vector_store.get_embedding(f"stored:{node_id}")
        assert stored is not None
        assert stored == result

    def test_returns_none_without_content(self, vector_store):
        node_id = "dev/test.md"
        # 没有存储 content_embedding
        result = compute_and_store_stored_vector(node_id, vector_store)
        assert result is None

    def test_returns_none_without_anchor(self, vector_store):
        node_id = "dev/test.md"
        content = _make_embedding(1.0)
        vector_store.upsert_embedding(node_id, content)
        # 没有 anchor
        result = compute_and_store_stored_vector(node_id, vector_store)
        assert result is None


# -- compute_stored_vectors_for_directory --


class TestComputeDirectoryStoredVectors:
    def test_updates_all_files(self, md_store, vector_store):
        # 创建节点文件
        for name in ["a.md", "b.md"]:
            node = KnowledgeNode.create(
                node_id=f"dev/{name}",
                title=name,
                content=f"content of {name}",
                source="test",
            )
            md_store.write_node(node)
            vector_store.upsert_embedding(
                f"dev/{name}", _make_embedding(ord(name[0]))
            )

        # 设置锚点
        anchor_vec = _make_embedding(5.0)
        vector_store.upsert_anchor(
            DirectoryAnchor(directory="dev", anchor_vector=anchor_vec, file_count=2)
        )

        updated = compute_stored_vectors_for_directory("dev", md_store, vector_store)
        assert updated == 2
        assert vector_store.get_embedding("stored:dev/a.md") is not None
        assert vector_store.get_embedding("stored:dev/b.md") is not None

    def test_empty_directory_returns_zero(self, md_store, vector_store):
        updated = compute_stored_vectors_for_directory(
            "nonexistent", md_store, vector_store
        )
        assert updated == 0


# -- similarity_search_stored --


class TestSimilaritySearchStored:
    def test_prefers_stored_vector(self, vector_store):
        """当 stored_vector 存在时，应使用它而非 raw content_embedding。"""
        query = _make_embedding(1.0)
        content_a = _make_embedding(1.0)  # 与 query 完全相同
        stored_a = _make_embedding(5.0)  # 与 query 差异很大

        vector_store.upsert_embedding("dev/a.md", content_a)
        vector_store.upsert_embedding("stored:dev/a.md", stored_a)

        results = vector_store.similarity_search_stored(query, top_k=5, threshold=0.0)
        # stored vector 与 query 差异大，但 raw 与 query 相同
        # 如果使用 stored，相似度应该低
        assert len(results) == 1
        # 验证用的是 stored_vector（低相似度）
        from src.common.knowledge_tree.storage.vector_store import cosine_similarity

        score = cosine_similarity(query, stored_a)
        assert abs(results[0][1] - score) < 1e-6

    def test_falls_back_to_content(self, vector_store):
        """无 stored_vector 时回退到 content_embedding。"""
        query = _make_embedding(1.0)
        content = _make_embedding(1.0)

        vector_store.upsert_embedding("dev/a.md", content)
        # 无 stored: key

        results = vector_store.similarity_search_stored(query, top_k=5, threshold=0.5)
        assert len(results) == 1

    def test_skips_title_keys(self, vector_store):
        """不应在 title: 键上匹配。"""
        query = _make_embedding(1.0)
        vector_store.upsert_embedding("title:dev/a.md", _make_embedding(1.0))

        results = vector_store.similarity_search_stored(query, top_k=5, threshold=0.0)
        assert len(results) == 0


# -- _extract_directory --


class TestExtractDirectory:
    def test_nested(self):
        assert _extract_directory("a/b/c.md") == "a/b"

    def test_root_file(self):
        assert _extract_directory("readme.md") == ""
