"""知识树端到端闭环集成测试（V4: 两层存储 + Overlay）。

Bootstrap → Retrieve → Ingest → Retrieve again → Status
"""

from pathlib import Path

import pytest

from src.common.knowledge_tree import KnowledgeTree
from src.common.knowledge_tree.config import KnowledgeTreeConfig
from src.common.knowledge_tree.dag.node import KnowledgeNode
from src.common.knowledge_tree.storage.vector_store import InMemoryVectorStore


def _mock_embedder(dim: int = 16):
    """确定性 embedder。"""
    def embed(text: str) -> list[float]:
        vec = [0.0] * dim
        for i, c in enumerate(text):
            idx = (ord(c) + i) % dim
            vec[idx] += 1.0
        mag = sum(x * x for x in vec) ** 0.5
        if mag > 0:
            vec = [x / mag for x in vec]
        return vec
    return embed


def _create_seed_dir(tmp_path: Path) -> Path:
    """创建带目录结构的种子目录。"""
    d = tmp_path / "kt_md"
    d.mkdir()

    (d / "development").mkdir()
    (d / "patterns").mkdir()

    seeds = [
        ("development/state.md", "状态管理", "LangGraph 使用 TypedDict 定义状态。"),
        ("development/tools.md", "工具调用", "LangGraph 通过 ToolNode 执行工具。"),
        ("patterns/embedding.md", "嵌入向量", "文本嵌入映射语义到高维向量。"),
    ]
    for rel_path, title, content in seeds:
        node = KnowledgeNode.create(
            node_id=rel_path, title=title, content=content, source="seed",
        )
        path = d / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(node.to_frontmatter_md(), encoding="utf-8")
    return d


@pytest.fixture
def kt_with_seeds(tmp_path: Path) -> KnowledgeTree:
    """指向种子目录的 KnowledgeTree。"""
    seed_dir = _create_seed_dir(tmp_path)
    config = KnowledgeTreeConfig(markdown_root=seed_dir)
    return KnowledgeTree(config, embedder=_mock_embedder())


@pytest.fixture
def kt_empty(tmp_path: Path) -> KnowledgeTree:
    """空 KnowledgeTree（用于 ingest 测试）。"""
    config = KnowledgeTreeConfig(markdown_root=tmp_path / "kt_md")
    return KnowledgeTree(config, embedder=_mock_embedder())


class TestClosedLoop:
    def test_bootstrap_retrieve_ingest_retrieve(self, kt_with_seeds: KnowledgeTree):
        """完整闭环：Bootstrap → Retrieve → Ingest → Retrieve again。"""
        kt = kt_with_seeds

        # 1. Bootstrap from seed directory
        report = kt.bootstrap()
        assert report["ok"]
        assert report["nodes_created"] == 3
        assert report["anchors_computed"] >= 1

        # 2. Status check
        status = kt.status()
        assert status["ok"] is True
        assert status["total_nodes"] == 3
        assert status["total_anchors"] >= 1

        # 3. Retrieve
        results, log = kt.retrieve("状态管理")
        assert log.query_id

        # 4. Record feedback
        kt.record_feedback(log.query_id, satisfaction=True, feedback="Found what I needed")
        assert log.agent_satisfaction is True

    def test_retrieve_no_results(self, kt_with_seeds: KnowledgeTree):
        """查询不相关内容可能无结果。"""
        kt_with_seeds.bootstrap()

        results, log = kt_with_seeds.retrieve("完全不相关的宇宙物理学黑洞话题xyz")
        assert log.query_id

    def test_status_shows_directories(self, kt_with_seeds: KnowledgeTree):
        """status 返回目录信息。"""
        kt_with_seeds.bootstrap()

        status = kt_with_seeds.status()
        assert "development" in status["directories"]
        assert "patterns" in status["directories"]
        assert status["total_anchors"] >= 2


class TestIngestionPipeline:
    """知识摄入管道集成测试。"""

    def test_ingest_after_bootstrap(self, kt_with_seeds: KnowledgeTree):
        """Bootstrap → Ingest 新内容 → 验证入树。"""
        kt_with_seeds.bootstrap()
        initial_nodes = kt_with_seeds.status()["total_nodes"]

        report = kt_with_seeds.ingest(
            "发现了一个重要的规则：系统架构应该遵循模块化设计原则。"
            "这个经验教训值得记录下来。",
            trigger="task_complete",
            source="agent:supervisor",
            metadata={"plan_id": "plan_001"},
        )
        assert report.nodes_ingested >= 1
        assert report.errors == []

        final_nodes = kt_with_seeds.status()["total_nodes"]
        assert final_nodes > initial_nodes

    def test_ingest_user_explicit(self, kt_with_seeds: KnowledgeTree):
        """用户显式指令触发 ingest。"""
        kt_with_seeds.bootstrap()

        report = kt_with_seeds.ingest(
            "记住：Supervisor 应该在每次任务失败后进行反思总结。",
            trigger="user_explicit",
        )
        assert report.nodes_ingested >= 1
        assert report.nodes_filtered == 0

    def test_ingest_filtered_short_content(self, kt_with_seeds: KnowledgeTree):
        """过短内容被过滤。"""
        kt_with_seeds.bootstrap()

        report = kt_with_seeds.ingest("ok", trigger="")
        assert report.nodes_filtered >= 1
        assert report.nodes_ingested == 0

    def test_ingest_disabled(self, tmp_path: Path):
        """ingest_enabled=False 时不执行摄入。"""
        config = KnowledgeTreeConfig(
            markdown_root=tmp_path / "kt_md",
            ingest_enabled=False,
        )
        kt = KnowledgeTree(config, embedder=_mock_embedder())

        report = kt.ingest("这是一个重要的发现：系统需要重构。", trigger="task_complete")
        assert report.nodes_ingested == 0

    def test_full_closed_loop(self, kt_with_seeds: KnowledgeTree):
        """完整闭环：Bootstrap → Ingest → Retrieve → Status。"""
        kt = kt_with_seeds

        # 1. Bootstrap
        kt.bootstrap()

        # 2. Ingest 新知识
        kt.ingest(
            "发现一个重要规则：向量搜索需要设置合适的相似度阈值。"
            "阈值过高会导致漏检，过低会引入噪声。最佳实践是0.7。",
            trigger="task_complete",
            metadata={"plan_id": "p002"},
        )

        # 3. Retrieve
        results, log = kt.retrieve("查询阈值设置")
        assert log.query_id

        # 4. Record feedback
        kt.record_feedback(log.query_id, satisfaction=True)

        # 5. Status
        status = kt.status()
        assert status["ok"] is True
        assert status["total_nodes"] > 3  # bootstrap(3) + ingested
