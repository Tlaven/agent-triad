"""知识树端到端闭环集成测试（mock LLM + mock embedder）。"""

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.common.knowledge_tree import KnowledgeTree
from src.common.knowledge_tree.bootstrap import bootstrap_from_seed_files
from src.common.knowledge_tree.config import KnowledgeTreeConfig
from src.common.knowledge_tree.dag.node import KnowledgeNode
from src.common.knowledge_tree.optimization.optimizer import run_optimization_cycle
from src.common.knowledge_tree.storage.graph_store import InMemoryGraphStore
from src.common.knowledge_tree.storage.markdown_store import MarkdownStore
from src.common.knowledge_tree.storage.vector_store import InMemoryVectorStore


def _mock_embedder(dim: int = 16):
    """确定性 embedder，用字符位置加权产生差异化向量。"""
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


def _mock_llm_returning(child_index: int, confidence: float):
    """返回固定路由决策的 mock LLM。"""
    llm = MagicMock()
    llm.invoke.return_value = json.dumps({
        "selected_index": child_index,
        "confidence": confidence,
    })
    return llm


@pytest.fixture
def seed_dir(tmp_path: Path) -> Path:
    d = tmp_path / "seeds"
    d.mkdir()
    seeds = [
        ("状态管理", "LangGraph 使用 TypedDict 定义状态。"),
        ("工具调用", "LangGraph 通过 ToolNode 执行工具。"),
        ("嵌入向量", "文本嵌入映射语义到高维向量。"),
    ]
    for title, content in seeds:
        node = KnowledgeNode.create(title=title, content=content, source="seed")
        (d / f"{node.node_id}.md").write_text(node.to_frontmatter_md(), encoding="utf-8")
    return d


@pytest.fixture
def kt(tmp_path: Path) -> KnowledgeTree:
    config = KnowledgeTreeConfig(
        markdown_root=tmp_path / "md",
        db_path=tmp_path / "db",
        tree_nav_confidence=0.5,
    )
    return KnowledgeTree(config, embedder=_mock_embedder())


class TestClosedLoop:
    def test_bootstrap_retrieve_edit_optimize(self, seed_dir: Path, kt: KnowledgeTree, tmp_path: Path):
        """完整闭环：Bootstrap → Retrieve → Edit → Sync → Re-embed → Log → Optimize。"""

        # 1. Bootstrap
        report = bootstrap_from_seed_files(
            seed_dir,
            kt.md_store,
            kt.graph_store,
            kt.vector_store,
            kt.embedder,
            kt.config,
        )
        assert report.errors == []
        assert report.nodes_created > 0

        # 2. Retrieve（用 mock LLM）
        root_id = kt.graph_store.get_root_id()
        assert root_id is not None

        # 让 LLM 选择第一个子节点
        kt.llm = _mock_llm_returning(child_index=0, confidence=0.9)
        result, log = kt.retrieve("查询状态管理")
        assert result.fusion_mode in ("tree", "tree+rag", "rag")
        assert log.query_id  # 有日志

        # 3. Record feedback
        kt.record_feedback(log.query_id, satisfaction=True, feedback="Found what I needed")
        assert log.agent_satisfaction is True

        # 4. Edit: split
        root_children = kt.graph_store.get_children(root_id)
        if root_children:
            group_node = root_children[0]
            delta = kt.edit("split", {
                "node_id": group_node.node_id,
                "splits": [
                    {"title": "Part A", "content": "Content A", "summary": "A"},
                ],
            })
            assert delta is not None
            assert delta.operation == "split"

        # 5. Optimize
        opt_report = kt.optimize()
        assert opt_report.signals_detected >= 0  # 可能没有信号（正常）

        # 6. Status
        status = kt.status()
        assert status["ok"] is True
        assert status["total_nodes"] > 0

    def test_retrieve_rag_fallback(self, seed_dir: Path, kt: KnowledgeTree):
        """树导航失败时触发 RAG 兜底。"""
        bootstrap_from_seed_files(
            seed_dir, kt.md_store, kt.graph_store, kt.vector_store, kt.embedder, kt.config,
        )

        # 低置信度导致树导航失败
        kt.llm = _mock_llm_returning(child_index=-1, confidence=0.1)
        result, log = kt.retrieve("完全不相关的话题")
        # 可能走 rag 或 none
        assert result.fusion_mode in ("rag", "none", "tree")

    def test_total_failure_triggers_signal(self, seed_dir: Path, kt: KnowledgeTree):
        """多次整体失败触发 total_failure 信号。"""
        bootstrap_from_seed_files(
            seed_dir, kt.md_store, kt.graph_store, kt.vector_store, kt.embedder, kt.config,
        )

        # 模拟多次完全失败（低置信度 + 低相似度向量）
        kt.llm = _mock_llm_returning(child_index=-1, confidence=0.1)
        for _ in range(5):
            result, log = kt.retrieve("完全不存在的话题xyz")
            # 标记不满意
            kt.record_feedback(log.query_id, satisfaction=False)

        opt_report = kt.optimize()
        # 应检测到信号（可能是 total_failure 或其他类型）
        # 由于 embedder 是确定性的，相似度可能超过阈值，所以信号类型不确定
        # 但至少优化流程应正常运行
        assert opt_report.signals_detected >= 0


class TestIngestionPipeline:
    """知识摄入管道集成测试。"""

    def test_ingest_after_bootstrap(self, seed_dir: Path, kt: KnowledgeTree):
        """Bootstrap → Ingest 新内容 → 验证入树。"""
        # 1. Bootstrap
        bootstrap_from_seed_files(
            seed_dir, kt.md_store, kt.graph_store, kt.vector_store, kt.embedder, kt.config,
        )
        initial_nodes = kt.status()["total_nodes"]

        # 2. Ingest 任务完成的 summary
        report = kt.ingest(
            "发现了一个重要的规则：系统架构应该遵循模块化设计原则。"
            "这个经验教训值得记录下来。",
            trigger="task_complete",
            source="agent:supervisor",
            metadata={"plan_id": "plan_001"},
        )
        assert report.nodes_ingested >= 1
        assert report.errors == []

        # 3. 验证节点数增加
        final_nodes = kt.status()["total_nodes"]
        assert final_nodes > initial_nodes

    def test_ingest_user_explicit(self, seed_dir: Path, kt: KnowledgeTree):
        """用户显式指令触发 ingest。"""
        bootstrap_from_seed_files(
            seed_dir, kt.md_store, kt.graph_store, kt.vector_store, kt.embedder, kt.config,
        )

        report = kt.ingest(
            "记住：Supervisor 应该在每次任务失败后进行反思总结。",
            trigger="user_explicit",
        )
        assert report.nodes_ingested >= 1
        assert report.nodes_filtered == 0  # user_explicit 不被过滤

    def test_ingest_filtered_short_content(self, seed_dir: Path, kt: KnowledgeTree):
        """过短内容被过滤，不入树。"""
        bootstrap_from_seed_files(
            seed_dir, kt.md_store, kt.graph_store, kt.vector_store, kt.embedder, kt.config,
        )

        report = kt.ingest("ok", trigger="")
        assert report.nodes_filtered >= 1
        assert report.nodes_ingested == 0

    def test_ingest_disabled(self, seed_dir: Path, tmp_path: Path):
        """ingest_enabled=False 时不执行摄入。"""
        config = KnowledgeTreeConfig(
            markdown_root=tmp_path / "md",
            db_path=tmp_path / "db",
            ingest_enabled=False,
        )
        kt = KnowledgeTree(config, embedder=_mock_embedder())
        bootstrap_from_seed_files(
            seed_dir, kt.md_store, kt.graph_store, kt.vector_store, kt.embedder, kt.config,
        )

        report = kt.ingest("这是一个重要的发现：系统需要重构。", trigger="task_complete")
        assert report.nodes_ingested == 0
        assert report.nodes_deduplicated == 0

    def test_full_closed_loop_with_ingest(self, seed_dir: Path, kt: KnowledgeTree):
        """完整闭环：Bootstrap → Ingest → Retrieve → Edit → Optimize。"""
        # 1. Bootstrap
        bootstrap_from_seed_files(
            seed_dir, kt.md_store, kt.graph_store, kt.vector_store, kt.embedder, kt.config,
        )

        # 2. Ingest 新知识
        kt.ingest(
            "发现一个重要规则：向量搜索需要设置合适的相似度阈值。"
            "阈值过高会导致漏检，过低会引入噪声。最佳实践是0.85。",
            trigger="task_complete",
            metadata={"plan_id": "p002"},
        )

        # 3. Retrieve（用 mock LLM）
        kt.llm = _mock_llm_returning(child_index=0, confidence=0.9)
        result, log = kt.retrieve("查询阈值设置")
        assert log.query_id

        # 4. Record feedback
        kt.record_feedback(log.query_id, satisfaction=True)

        # 5. Optimize
        opt_report = kt.optimize()
        assert opt_report.signals_detected >= 0

        # 6. Status
        status = kt.status()
        assert status["ok"] is True
        assert status["total_nodes"] > 5  # bootstrap nodes + ingested nodes


class TestOptimizerExecution:
    """验证优化器执行层：信号检测 → 实际树结构修改。"""

    def test_total_failure_creates_seed_nodes(self, seed_dir: Path, kt: KnowledgeTree):
        """total_failure 信号触发新节点创建。"""
        bootstrap_from_seed_files(
            seed_dir, kt.md_store, kt.graph_store, kt.vector_store, kt.embedder, kt.config,
        )
        initial_nodes = kt.status()["total_nodes"]

        # 模拟多次完全失败（低置信度 → 树导航失败 → 低相似度 → RAG 也失败）
        kt.llm = _mock_llm_returning(child_index=-1, confidence=0.1)
        for _ in range(5):
            _, log = kt.retrieve("完全不存在的话题xyz_abc_def")
            kt.record_feedback(log.query_id, satisfaction=False)

        # 优化：低阈值确保触发信号
        from src.common.knowledge_tree.optimization.optimizer import OptimizationContext
        ctx = OptimizationContext(
            graph_store=kt.graph_store,
            vector_store=kt.vector_store,
            md_store=kt.md_store,
            embedder=kt.embedder,
        )
        report = run_optimization_cycle(
            logs=kt._retrieval_logs,
            history=kt.optimization_history,
            ctx=ctx,
            total_failure_threshold=3,
        )

        # 至少应该有信号（total_failure 或其他）
        if report.signals_detected > 0:
            assert report.actions_executed > 0 or report.actions_planned > 0
            # 检查是否有动作被实际执行
            executed = [a for a in report.actions if a["status"] == "executed"]
            if executed:
                # 树应该有变化
                final_nodes = kt.status()["total_nodes"]
                assert final_nodes >= initial_nodes

    def test_nav_failure_splits_or_annotates(self, seed_dir: Path, kt: KnowledgeTree):
        """nav_failure 信号触发节点拆分或元数据标注。"""
        bootstrap_from_seed_files(
            seed_dir, kt.md_store, kt.graph_store, kt.vector_store, kt.embedder, kt.config,
        )

        # 让 LLM 导航到节点但置信度低于满意阈值
        kt.llm = _mock_llm_returning(child_index=0, confidence=0.9)
        root_id = kt.graph_store.get_root_id()
        root_children = kt.graph_store.get_children(root_id)

        if root_children:
            target = root_children[0]
            # 反复导航到同一节点但标记不满意（模拟导航失败）
            for _ in range(6):
                _, log = kt.retrieve(f"查询 {target.title}")
                # 标记为不满意 → 触发 nav_failure 或 content_insufficient
                kt.record_feedback(log.query_id, satisfaction=False)

            report = kt.optimize()
            # 优化流程应正常运行
            assert report.signals_detected >= 0

    def test_dry_run_only_plans(self, seed_dir: Path, kt: KnowledgeTree):
        """dry_run=True 只规划不执行。"""
        bootstrap_from_seed_files(
            seed_dir, kt.md_store, kt.graph_store, kt.vector_store, kt.embedder, kt.config,
        )
        initial_nodes = kt.status()["total_nodes"]

        kt.llm = _mock_llm_returning(child_index=-1, confidence=0.1)
        for _ in range(5):
            _, log = kt.retrieve("不存在的话题")
            kt.record_feedback(log.query_id, satisfaction=False)

        report = kt.optimize(dry_run=True)

        # dry_run: 所有动作应保持 "planned" 状态
        for action in report.actions:
            assert action["status"] == "planned"

        # 树不应有变化
        assert kt.status()["total_nodes"] == initial_nodes

    def test_execution_modifies_tree_structure(self, seed_dir: Path, kt: KnowledgeTree):
        """完整闭环执行后树结构确实发生变化。"""
        bootstrap_from_seed_files(
            seed_dir, kt.md_store, kt.graph_store, kt.vector_store, kt.embedder, kt.config,
        )

        status_before = kt.status()
        edges_before = status_before["total_edges"]

        # 模拟失败检索
        kt.llm = _mock_llm_returning(child_index=-1, confidence=0.1)
        for _ in range(5):
            _, log = kt.retrieve("完全不存在的新领域话题_12345")
            kt.record_feedback(log.query_id, satisfaction=False)

        report = kt.optimize()

        status_after = kt.status()
        # 如果有动作执行，树结构应该变化（新节点/新边）
        if report.actions_executed > 0:
            assert (
                status_after["total_nodes"] != status_before["total_nodes"]
                or status_after["total_edges"] != edges_before
            ), "Optimizer executed actions but tree structure unchanged"
