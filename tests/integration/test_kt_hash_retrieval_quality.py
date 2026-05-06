"""Hash embedder 检索质量基线验证。

用 hash embedder（非语义模型）bootstrap 生产种子文档，
验证在无语义理解能力的情况下，KT 是否能通过 n-gram 匹配
正确检索到相关种子文档。

这是最关键的质量基线——如果 hash embedder 下检索不可用，
大多数用户环境（无 GPU、无模型缓存）的 KT 就是废的。
"""

import shutil
from pathlib import Path

import pytest

from src.common.knowledge_tree import KnowledgeTree
from src.common.knowledge_tree.config import KnowledgeTreeConfig

# 生产种子文档目录
SEED_DIR = Path("workspace/knowledge_tree")


@pytest.fixture
def kt_hash(tmp_path: Path) -> KnowledgeTree:
    """从生产种子目录创建使用 hash embedder 的 KnowledgeTree。"""
    seed_copy = tmp_path / "kt_md"
    shutil.copytree(SEED_DIR, seed_copy)
    config = KnowledgeTreeConfig(
        markdown_root=seed_copy,
        embedding_model="hash",
        rag_similarity_threshold=0.15,
    )
    kt = KnowledgeTree(config)
    report = kt.bootstrap()
    assert report["ok"], f"Bootstrap failed: {report}"
    return kt


# ---------------------------------------------------------------------------
# Hash Embedder 检索质量测试
# ---------------------------------------------------------------------------


class TestHashBootstrap:
    """Hash embedder 下 bootstrap 应正常工作。"""

    def test_bootstrap_creates_nodes(self, kt_hash: KnowledgeTree):
        """Bootstrap 应创建至少 11 个种子节点。"""
        status = kt_hash.status()
        assert status["total_nodes"] >= 11, (
            f"Expected >= 11 seed nodes, got {status['total_nodes']}"
        )

    def test_bootstrap_creates_anchors(self, kt_hash: KnowledgeTree):
        """Bootstrap 应创建目录锚点。"""
        status = kt_hash.status()
        # 3 directories: architecture, conventions, patterns
        assert status["total_anchors"] >= 3, (
            f"Expected >= 3 anchors, got {status['total_anchors']}"
        )


class TestHashExactMatchRetrieval:
    """精确匹配查询——hash embedder 应能通过关键词命中。"""

    def test_architecture_query(self, kt_hash: KnowledgeTree):
        """查询"AgentTriad 架构"应命中架构相关文档。"""
        results, _ = kt_hash.retrieve("AgentTriad 三层架构 Supervisor Planner Executor")
        assert len(results) > 0, "Expected results for architecture query"
        top_node = results[0][0]
        assert "architecture" in top_node.node_id, (
            f"Expected architecture doc, got: {top_node.node_id}"
        )

    def test_executor_protocol_query(self, kt_hash: KnowledgeTree):
        """查询 executor 通信协议应命中协议文档。"""
        results, _ = kt_hash.retrieve("Executor 子进程 FastAPI Mailbox 通信协议")
        assert len(results) > 0
        found = any("executor-protocol" in n.node_id for n, _ in results[:3])
        assert found, (
            f"executor-protocol not in top-3: {[n.node_id for n, _ in results[:3]]}"
        )

    def test_plan_json_query(self, kt_hash: KnowledgeTree):
        """查询 Plan JSON 结构应命中 plan-json 文档。"""
        results, _ = kt_hash.retrieve("Plan JSON plan_id steps intent expected_output")
        assert len(results) > 0
        found = any("plan-json" in n.node_id for n, _ in results[:3])
        assert found, (
            f"plan-json not in top-3: {[n.node_id for n, _ in results[:3]]}"
        )

    def test_error_handling_query(self, kt_hash: KnowledgeTree):
        """查询错误处理应命中 error-handling 文档。"""
        results, _ = kt_hash.retrieve("失败 failed 重规划 replan MAX_REPLAN")
        assert len(results) > 0
        found = any("error-handling" in n.node_id for n, _ in results[:3])
        assert found, (
            f"error-handling not in top-3: {[n.node_id for n, _ in results[:3]]}"
        )

    def test_knowledge_tree_design_query(self, kt_hash: KnowledgeTree):
        """查询知识树设计应命中 knowledge-tree-design 文档。"""
        results, _ = kt_hash.retrieve("知识树 向量 向量索引 embedding RAG RRF")
        assert len(results) > 0
        found = any("knowledge-tree-design" in n.node_id for n, _ in results[:3])
        assert found, (
            f"knowledge-tree-design not in top-3: {[n.node_id for n, _ in results[:3]]}"
        )

    def test_testing_query(self, kt_hash: KnowledgeTree):
        """查询测试模式应命中 testing-patterns 文档。"""
        results, _ = kt_hash.retrieve("pytest 单元测试 集成测试 e2e coverage")
        assert len(results) > 0
        found = any("testing-patterns" in n.node_id for n, _ in results[:3])
        assert found, (
            f"testing-patterns not in top-3: {[n.node_id for n, _ in results[:3]]}"
        )

    def test_observation_query(self, kt_hash: KnowledgeTree):
        """查询 Observation/Reflection 应命中 observation-and-reflection 文档。"""
        results, _ = kt_hash.retrieve("Observation Reflection 截断 快照 snapshot")
        assert len(results) > 0
        found = any("observation-and-reflection" in n.node_id for n, _ in results[:3])
        assert found, (
            f"observation-and-reflection not in top-3: {[n.node_id for n, _ in results[:3]]}"
        )


class TestHashScoreQuality:
    """Hash embedder 检索分数应在合理范围。"""

    def test_relevant_scores_above_threshold(self, kt_hash: KnowledgeTree):
        """精确匹配的分数应显著高于阈值。"""
        results, _ = kt_hash.retrieve("AgentTriad Supervisor Planner Executor 架构")
        assert len(results) > 0
        top_score = results[0][1]
        # hash embedder 下，精确关键词匹配分数通常在 0.3-0.6
        assert top_score >= 0.15, (
            f"Top score {top_score:.3f} below threshold 0.15"
        )

    def test_score_ranking_order(self, kt_hash: KnowledgeTree):
        """结果应按相似度降序排列。"""
        results, _ = kt_hash.retrieve("executor 子进程")
        scores = [s for _, s in results]
        for i in range(len(scores) - 1):
            assert scores[i] >= scores[i + 1], (
                f"Scores not descending: {scores}"
            )


class TestHashPrecision:
    """不相关查询不应返回高分结果。"""

    def test_irrelevant_query_low_score(self, kt_hash: KnowledgeTree):
        """不相关查询的分数应低于阈值或不存在结果。"""
        results, _ = kt_hash.retrieve("红烧肉的做法 怎么炒菜")
        for node, score in results:
            assert score < 0.4, (
                f"Irrelevant query returned high score: "
                f"{node.node_id} = {score:.3f}"
            )


class TestHashIngestAndRetrieve:
    """Hash embedder 下 ingest → retrieve 闭环。"""

    def test_ingest_then_retrieve(self, kt_hash: KnowledgeTree):
        """新摄入的知识应可被检索。"""
        report = kt_hash.ingest(
            "测试知识：AgentTriad 使用 uv 作为包管理器，开发服务器端口是 2024。",
            trigger="user_explicit",
        )
        assert report.nodes_ingested > 0, "Expected at least one node ingested"

        results, _ = kt_hash.retrieve("uv 包管理器 端口 2024")
        assert len(results) > 0, "Expected results after ingest"
        top_content = results[0][0].content
        assert "uv" in top_content or "2024" in top_content, (
            f"Retrieved content doesn't match: {top_content[:100]}"
        )

    def test_ingest_dedup(self, kt_hash: KnowledgeTree):
        """重复摄入应被去重。"""
        text = "去重测试：这是一条唯一的测试知识内容。"
        kt_hash.ingest(text, trigger="user_explicit")
        r2 = kt_hash.ingest(text, trigger="user_explicit")
        assert r2.nodes_deduplicated > 0, "Expected dedup on second ingest"
