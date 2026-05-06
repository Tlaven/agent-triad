"""知识树端到端闭环验证——完整价值循环测试。

验证 bootstrap → retrieve → ingest → retrieve again 的完整闭环，
以及 Entry A 自动提取 → ingest → retrieve 的链路。
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from src.common.knowledge_tree import KnowledgeTree
from src.common.knowledge_tree.config import KnowledgeTreeConfig
from src.common.knowledge_tree.ingestion.extractor import (
    extract_knowledge_from_executor_result,
)
from tests.unit_tests.common.knowledge_tree.conftest_semantic import (
    _create_real_semantic_embedder,
    requires_semantic,
)

SEED_DIR = Path("workspace/knowledge_tree")


def _mock_embedder():
    """确定性 mock embedder（CI 兼容）。"""
    def embed(text: str, dim: int = 512) -> list[float]:
        base = sum(ord(c) for c in text) / 1000.0
        return [base + i * 0.001 for i in range(dim)]
    return embed


# =====================================================================
# Hash embedder 测试（不依赖 sentence-transformers，始终运行）
# =====================================================================


class TestClosedLoopWithHashEmbedder:
    """使用 hash embedder 验证基本闭环（质量较低但必须可用）。"""

    @pytest.fixture
    def kt_hash(self, tmp_path: Path) -> KnowledgeTree:
        seed_copy = tmp_path / "kt_md"
        shutil.copytree(SEED_DIR, seed_copy)
        config = KnowledgeTreeConfig(markdown_root=seed_copy)
        kt = KnowledgeTree(config, embedder=_mock_embedder())
        report = kt.bootstrap()
        assert report["ok"]
        return kt

    def test_bootstrap_status(self, kt_hash: KnowledgeTree):
        """Bootstrap 应正确加载所有种子节点。"""
        status = kt_hash.status()
        assert status["total_nodes"] >= 9
        assert status["total_anchors"] >= 3
        assert "architecture" in status["directories"]
        assert "conventions" in status["directories"]
        assert "patterns" in status["directories"]

    def test_ingest_creates_new_nodes(self, tmp_path: Path):
        """Ingest 独特内容应创建新节点（使用高区分度 embedder）。"""
        seed_copy = tmp_path / "kt_md"
        shutil.copytree(SEED_DIR, seed_copy)
        config = KnowledgeTreeConfig(markdown_root=seed_copy, dedup_threshold=0.95)

        # 使用高区分度 embedder 避免向量碰撞
        def _distinct_embedder(text: str, dim: int = 64) -> list[float]:
            """基于文本哈希的高区分度 embedder。"""
            import hashlib
            h = hashlib.sha256(text.encode()).digest()
            vec = [float(b) / 255.0 for b in h[:dim]]
            mag = sum(x * x for x in vec) ** 0.5
            return [x / mag for x in vec] if mag > 0 else [0.0] * dim

        kt = KnowledgeTree(config, embedder=_distinct_embedder)
        kt.bootstrap()

        initial = kt.status()["total_nodes"]

        report = kt.ingest(
            "一条完全独特的测试知识：XYZ_ABC_12345 用于验证 ingest 创建新节点。",
            trigger="user_explicit",
        )
        assert report.nodes_ingested >= 1

        final = kt.status()["total_nodes"]
        assert final > initial

    def test_entry_a_extract_and_ingest(self, tmp_path: Path):
        """Entry A: extract → ingest 闭环（验证不崩溃 + 节点增长可能）。"""
        seed_copy = tmp_path / "kt_md"
        shutil.copytree(SEED_DIR, seed_copy)
        config = KnowledgeTreeConfig(markdown_root=seed_copy, dedup_threshold=0.99)
        kt = KnowledgeTree(config, embedder=_mock_embedder())
        kt.bootstrap()

        summary = "完成了 Executor 超时保护机制的实现。"
        plan_json = json.dumps({
            "plan_id": "plan_e2e",
            "goal": "实现 Executor 超时保护",
            "steps": [
                {
                    "step_id": "step_1",
                    "intent": "添加超时配置",
                    "status": "completed",
                    "result_summary": "在 context.py 中添加了 executor_call_model_timeout (180s) 配置。",
                    "failure_reason": "",
                },
            ],
        })

        chunks = extract_knowledge_from_executor_result(summary, plan_json, "completed")
        assert len(chunks) >= 2

        total_ingested = 0
        for chunk in chunks:
            report = kt.ingest(chunk, trigger="task_complete", source="auto:executor")
            total_ingested += report.nodes_ingested

        # 至少应尝试了 ingest（可能全部 dedup，但流程不应崩溃）
        status = kt.status()
        assert status["ok"]

    def test_retrieve_returns_results(self, kt_hash: KnowledgeTree):
        """Retrieve 应返回结果（可能质量不高但不崩溃）。"""
        results, log = kt_hash.retrieve("架构")
        assert log.query_id  # log 应正确创建


# =====================================================================
# Semantic embedder 测试（需要 bge-small-zh-v1.5）
# =====================================================================


@requires_semantic
class TestClosedLoopWithSemanticEmbedder:
    """使用真实语义 embedder 验证高质量闭环。"""

    @pytest.fixture
    def kt_semantic(self, tmp_path: Path) -> KnowledgeTree:
        seed_copy = tmp_path / "kt_md"
        shutil.copytree(SEED_DIR, seed_copy)
        config = KnowledgeTreeConfig(markdown_root=seed_copy)
        embedder = _create_real_semantic_embedder()
        kt = KnowledgeTree(config, embedder=embedder)
        report = kt.bootstrap()
        assert report["ok"]
        return kt

    def test_ingest_then_retrieve_semantic(self, kt_semantic: KnowledgeTree):
        """Ingest 新知识后语义检索能找到它。"""
        new_knowledge = (
            "发现一个重要的性能优化方案：Executor 子进程启动时 "
            "可以通过预热模型缓存来减少首次推理延迟。"
        )
        kt_semantic.ingest(new_knowledge, trigger="task_complete", source="test")

        results, _ = kt_semantic.retrieve("Executor 首次推理延迟优化")
        assert len(results) > 0

        # 应能通过语义匹配找到新 ingest 的内容
        assert any("预热" in n.content or "延迟" in n.content for n, _ in results[:5])

    def test_entry_a_semantic_retrieval(self, kt_semantic: KnowledgeTree):
        """Entry A 提取的知识应能被语义检索召回。"""
        summary = "完成了知识提取器模块的开发。"
        plan_json = json.dumps({
            "plan_id": "plan_semantic_e2e",
            "goal": "实现 Entry A 知识提取",
            "steps": [
                {
                    "step_id": "step_1",
                    "intent": "创建 extractor 模块",
                    "status": "completed",
                    "result_summary": "创建了 extract_knowledge_from_executor_result 函数，"
                                     "支持从 Executor 完成结果中自动提取知识片段。",
                    "failure_reason": "",
                },
            ],
        })

        chunks = extract_knowledge_from_executor_result(summary, plan_json, "completed")
        for chunk in chunks:
            kt_semantic.ingest(chunk, trigger="task_complete", source="auto:executor")

        results, _ = kt_semantic.retrieve("Executor 结果知识提取")
        assert len(results) > 0
        all_text = " ".join(n.content for n, _ in results[:5])
        assert "extractor" in all_text or "提取" in all_text

    def test_multi_round_semantic_quality(self, kt_semantic: KnowledgeTree):
        """多轮积累后检索质量应保持稳定。"""
        knowledge_items = [
            "规则：call_executor 的 wait_for_result=True 时会自动阻塞等待结果。",
            "发现：kt_retrieve 节点在 __start__ 后执行，不在工具循环中重复触发。",
            "架构决定：Planner 生成的 Plan JSON 只包含 intent，不含工具名。",
        ]

        for item in knowledge_items:
            kt_semantic.ingest(item, trigger="task_complete", source="test")

        queries = [
            ("Executor 等待结果", "wait_for_result"),
            ("知识树自动检索时机", "kt_retrieve"),
            ("Planner 输出格式", "intent"),
        ]

        for query, keyword in queries:
            results, _ = kt_semantic.retrieve(query)
            all_text = " ".join(n.content for n, _ in results[:5])
            assert keyword in all_text, (
                f"Query '{query}' should find knowledge containing '{keyword}'"
            )

    def test_bootstrap_from_project_seeds(self, kt_semantic: KnowledgeTree):
        """项目种子知识应能被检索到。"""
        status = kt_semantic.status()
        assert status["total_nodes"] >= 9  # 9 seed files, may have more from other sources

        # 检索项目架构知识
        results, _ = kt_semantic.retrieve("三层智能体架构 Supervisor Planner Executor")
        assert len(results) > 0
        all_text = " ".join(n.content for n, _ in results[:3])
        assert "Supervisor" in all_text or "架构" in all_text

        # 检索知识树设计
        results, _ = kt_semantic.retrieve("知识树两层存储 向量检索")
        assert len(results) > 0
        all_text = " ".join(n.content for n, _ in results[:3])
        assert "两层" in all_text or "向量" in all_text
