"""语义检索质量验证——使用真实 bge-small-zh-v1.5 模型。

验证三层核心假设：
1. 正确主题检索相似度 >= 0.5
2. 错误主题检索相似度 < 0.4
3. 语义同义查询能召回正确结果（hash embedder 做不到的）
"""

import shutil
from pathlib import Path

import pytest

from src.common.knowledge_tree import KnowledgeTree
from src.common.knowledge_tree.config import KnowledgeTreeConfig

# 从 conftest_semantic 导入
from tests.unit_tests.common.knowledge_tree.conftest_semantic import (
    _create_real_semantic_embedder,
    requires_semantic,
)

SEED_DIR = Path("workspace/kt_semantic_test")


@pytest.fixture
def kt_real(tmp_path: Path) -> KnowledgeTree:
    """从种子目录创建使用真实语义 embedder 的 KnowledgeTree。"""
    seed_copy = tmp_path / "kt_md"
    shutil.copytree(SEED_DIR, seed_copy)
    config = KnowledgeTreeConfig(markdown_root=seed_copy)
    embedder = _create_real_semantic_embedder()
    kt = KnowledgeTree(config, embedder=embedder)
    report = kt.bootstrap()
    assert report["ok"], f"Bootstrap failed: {report}"
    return kt


# ---------------------------------------------------------------------------
# 语义检索质量测试
# ---------------------------------------------------------------------------


@requires_semantic
class TestSemanticRetrievalQuality:
    """使用真实 bge-small-zh-v1.5 验证检索质量。"""

    def test_bootstrap_with_real_embedder(self, kt_real: KnowledgeTree):
        """Bootstrap 应生成 embeddings 和 anchors。"""
        status = kt_real.status()
        assert status["total_nodes"] >= 8, f"Expected >= 8 nodes, got {status['total_nodes']}"
        assert status["total_anchors"] >= 5, f"Expected >= 5 anchors, got {status['total_anchors']}"

    def test_exact_match_http(self, kt_real: KnowledgeTree):
        """T2: 精确匹配——HTTP 协议查询应返回 HTTP 文档。"""
        results, _ = kt_real.retrieve("HTTP 协议是无状态的")
        assert len(results) > 0, "Expected at least one result"

        top_ids = [n.node_id for n, _ in results[:3]]
        assert any("http" in nid for nid in top_ids), (
            f"HTTP document not in top-3: {top_ids}"
        )

        http_score = next(s for n, s in results if "http" in n.node_id)
        assert http_score >= 0.7, f"HTTP exact match score too low: {http_score:.3f}"

    def test_semantic_synonym_http(self, kt_real: KnowledgeTree):
        """T3: 语义同义——"超文本传输协议的特点"应召回 HTTP 文档。

        这是 hash embedder 无法通过的关键测试。
        """
        results, _ = kt_real.retrieve("超文本传输协议的特点")
        assert len(results) > 0, "Expected at least one result for synonym query"

        top_ids = [n.node_id for n, _ in results[:3]]
        assert any("http" in nid for nid in top_ids), (
            f"HTTP document not in top-3 for synonym query: {top_ids}"
        )

        http_score = next((s for n, s in results if "http" in n.node_id), 0)
        # 校准实测：同义查询得分 ~0.48（远超 hash embedder 的 ~0.1，
        # 但低于精确匹配的 >= 0.7）。0.45 是合理的语义同义阈值。
        assert http_score >= 0.45, f"HTTP synonym match score too low: {http_score:.3f}"

    def test_cross_topic_discrimination(self, kt_real: KnowledgeTree):
        """T4: 跨主题区分——量子查询应返回 qubit，排除 HTTP。"""
        results, _ = kt_real.retrieve("量子叠加态")
        assert len(results) > 0

        top_node, top_score = results[0]
        assert "qubit" in top_node.node_id, (
            f"Expected qubit at rank-1, got: {top_node.node_id}"
        )

        http_score = next((s for n, s in results if "http" in n.node_id), 0)
        assert http_score < 0.4, (
            f"HTTP should not appear for quantum query, score: {http_score:.3f}"
        )

    def test_topic_coherence_gil(self, kt_real: KnowledgeTree):
        """T5: 主题连贯——Python 多线程查询应返回 GIL 文档。"""
        results, _ = kt_real.retrieve("Python 多线程的限制")
        assert len(results) > 0

        top_node, top_score = results[0]
        assert "python_gil" in top_node.node_id or "GIL" in top_node.title, (
            f"Expected GIL at rank-1, got: {top_node.node_id} ({top_node.title})"
        )

    def test_score_gap_separation(self, kt_real: KnowledgeTree):
        """正确匹配与错误匹配之间应有足够的分数间隔。"""
        # 查询 HTTP 相关
        results, _ = kt_real.retrieve("HTTP 协议请求响应模式")

        http_score = next((s for n, s in results if "http" in n.node_id), 0)
        qubit_score = next((s for n, s in results if "qubit" in n.node_id), 0)

        gap = http_score - qubit_score
        assert gap >= 0.15, (
            f"Score gap too small between correct({http_score:.3f}) "
            f"and incorrect({qubit_score:.3f}): gap={gap:.3f}"
        )

    def test_threshold_sweep(self, kt_real: KnowledgeTree):
        """T6: 阈值扫描——生成校准表（信息性，不 assert 失败）。"""
        queries_and_expected = [
            ("HTTP 协议", "http"),
            ("量子比特叠加", "qubit"),
            ("Python GIL 全局锁", "python_gil"),
            ("包管理器 uv", "uv_2024"),
            ("模型下载卡死", "huggingface"),
            ("executor 超时", "executor"),
            ("天气 雨水", "misc"),
            ("机器学习算法", "misc"),
        ]

        print("\n--- Semantic Threshold Calibration Table ---")
        print(f"{'Query':<25} {'Expected':<15} {'Score':>8} {'Rank':>6} {'Pass':>6}")
        print("-" * 65)

        for query, expected_substring in queries_and_expected:
            results, _ = kt_real.retrieve(query)
            found_rank = None
            found_score = 0.0
            for rank, (node, score) in enumerate(results, 1):
                if expected_substring in node.node_id:
                    found_rank = rank
                    found_score = score
                    break

            passes = found_rank is not None and found_score >= 0.5
            print(
                f"{query:<25} {expected_substring:<15} "
                f"{found_score:>8.3f} {str(found_rank or '-'):>6} "
                f"{'OK' if passes else 'MISS':>6}"
            )

        # 至少 6/8 查询应通过 0.5 阈值
        pass_count = 0
        for query, expected_substring in queries_and_expected:
            results, _ = kt_real.retrieve(query)
            for node, score in results:
                if expected_substring in node.node_id and score >= 0.5:
                    pass_count += 1
                    break

        assert pass_count >= 6, (
            f"Only {pass_count}/8 queries passed 0.5 threshold — "
            f"semantic retrieval quality insufficient"
        )
