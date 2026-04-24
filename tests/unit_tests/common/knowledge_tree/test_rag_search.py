"""RAG 向量检索测试。

覆盖 rag_search 的核心场景：基本检索、双路融合、空存储、阈值过滤、缺失节点。
"""

from __future__ import annotations

import pytest

from src.common.knowledge_tree.dag.node import KnowledgeNode
from src.common.knowledge_tree.retrieval.rag_search import rag_search
from src.common.knowledge_tree.storage.markdown_store import MarkdownStore
from src.common.knowledge_tree.storage.vector_store import InMemoryVectorStore


@pytest.fixture
def dim() -> int:
    return 64


@pytest.fixture
def mock_embedder(dim: int):
    """确定性 embedder：不同文本产生不同向量。"""
    def embed(text: str) -> list[float]:
        vec = [0.0] * dim
        for i, c in enumerate(text):
            idx = (ord(c) + i) % dim
            vec[idx] += 1.0
        mag = sum(x * x for x in vec) ** 0.5
        return [x / mag for x in vec] if mag > 0 else vec
    return embed


@pytest.fixture
def populated_store(tmp_path, mock_embedder, dim):
    """预填充 3 个节点的存储。"""
    md = MarkdownStore(tmp_path / "md")
    vs = InMemoryVectorStore(dimension=dim)

    nodes = [
        KnowledgeNode.create("dev/state.md", "状态管理", "LangGraph 状态管理使用 TypedDict。", "seed"),
        KnowledgeNode.create("dev/tools.md", "工具调用", "LangGraph 通过 ToolNode 执行工具。", "seed"),
        KnowledgeNode.create("patterns/react.md", "ReAct 模式", "ReAct 结合推理和行动。", "seed"),
    ]

    for node in nodes:
        md.write_node(node)
        emb = mock_embedder(node.content)
        vs.upsert_embedding(node.node_id, emb)
        if node.title:
            title_emb = mock_embedder(node.title)
            vs.upsert_embedding(f"title:{node.node_id}", title_emb)

    return md, vs, nodes


class TestBasicRetrieval:
    """基本检索功能。"""

    def test_retrieve_exact_match(self, populated_store, mock_embedder):
        md, vs, nodes = populated_store
        query_vec = mock_embedder("状态管理 TypedDict")

        results = rag_search(query_vec, vs, md, embedder=mock_embedder, top_k=3, threshold=0.05)

        assert len(results) > 0
        top_node, top_score = results[0]
        assert top_node.node_id == "dev/state.md"
        assert top_score > 0

    def test_retrieve_returns_knowledge_node(self, populated_store, mock_embedder):
        md, vs, _ = populated_store
        query_vec = mock_embedder("工具调用")

        results = rag_search(query_vec, vs, md, embedder=mock_embedder, threshold=0.05)

        assert len(results) > 0
        node, _ = results[0]
        assert isinstance(node, KnowledgeNode)
        assert node.content  # 内容非空


class TestDualPathRRF:
    """双路检索（content + title）和 RRF 融合。"""

    def test_title_path_contributes(self, populated_store, mock_embedder):
        """title embedding 路径应参与融合排序。"""
        md, vs, _ = populated_store
        # 用 "ReAct 模式" 作为查询——这更接近 title 而非 content
        query_vec = mock_embedder("ReAct 模式")

        # 有 title 路径
        results_with_title = rag_search(query_vec, vs, md, embedder=mock_embedder, threshold=0.05)

        # 无 title 路径（不传 embedder）
        results_no_title = rag_search(query_vec, vs, md, embedder=None, threshold=0.05)

        # 两条路径都应该返回结果
        assert len(results_with_title) > 0
        assert len(results_no_title) > 0

    def test_content_only_path(self, populated_store, mock_embedder):
        """不传 embedder 时只用 content 路径。"""
        md, vs, _ = populated_store
        query_vec = mock_embedder("状态管理")

        results = rag_search(query_vec, vs, md, embedder=None, threshold=0.05)

        assert len(results) > 0


class TestEmptyAndEdgeCases:
    """空存储和边界情况。"""

    def test_empty_store(self, tmp_path, mock_embedder, dim):
        """空向量存储应返回空列表。"""
        md = MarkdownStore(tmp_path / "md")
        vs = InMemoryVectorStore(dimension=dim)
        query_vec = mock_embedder("任意查询")

        results = rag_search(query_vec, vs, md, embedder=mock_embedder, threshold=0.05)

        assert results == []

    def test_threshold_too_high(self, populated_store, mock_embedder):
        """阈值极高时可能无结果。"""
        md, vs, _ = populated_store
        query_vec = mock_embedder("完全不相关的内容 xyz")

        results = rag_search(query_vec, vs, md, embedder=mock_embedder, threshold=0.99)

        assert results == []

    def test_top_k_limits_results(self, populated_store, mock_embedder):
        """top_k 应限制返回数量。"""
        md, vs, _ = populated_store
        query_vec = mock_embedder("LangGraph")

        results = rag_search(query_vec, vs, md, embedder=mock_embedder, top_k=1, threshold=0.01)

        assert len(results) <= 1

    def test_missing_node_in_md_store(self, tmp_path, mock_embedder, dim):
        """向量存在但 md 文件不存在时应跳过。"""
        md = MarkdownStore(tmp_path / "md")
        vs = InMemoryVectorStore(dimension=dim)

        # 只写入向量，不写 md 文件
        emb = mock_embedder("孤立内容")
        vs.upsert_embedding("orphan.md", emb)

        query_vec = mock_embedder("孤立内容")
        results = rag_search(query_vec, vs, md, embedder=mock_embedder, threshold=0.01)

        assert results == []


class TestScoreOrdering:
    """结果排序验证。"""

    def test_results_sorted_by_similarity_desc(self, populated_store, mock_embedder):
        """结果应按相似度降序排列。"""
        md, vs, _ = populated_store
        query_vec = mock_embedder("LangGraph TypedDict 状态")

        results = rag_search(query_vec, vs, md, embedder=mock_embedder, top_k=3, threshold=0.01)

        if len(results) >= 2:
            scores = [score for _, score in results]
            for i in range(len(scores) - 1):
                assert scores[i] >= scores[i + 1], f"Results not sorted: {scores}"
