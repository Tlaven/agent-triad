"""KnowledgeTree API 单元测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.common.knowledge_tree.config import KnowledgeTreeConfig
from src.common.knowledge_tree.core import KnowledgeTree


@pytest.fixture
def kt(tmp_path: Path) -> KnowledgeTree:
    """最简 KnowledgeTree 实例（hash embedder, dim=64）。"""
    cfg = KnowledgeTreeConfig(
        markdown_root=tmp_path,
        embedder_type="hash",
        embedding_dimension=64,
    )
    return KnowledgeTree(cfg)


class TestIngestTitleFallback:
    def test_title_prefers_goal_metadata(self, kt: KnowledgeTree):
        """ingest 时若 metadata 含 goal，title 用 goal 前 60 字符。"""
        text = (
            "重要发现：worker_timeout 与 supervisor_timeout 必须同步修改，"
            "否则会产生进程假死，需要修复部署脚本。"
        )
        report = kt.ingest(
            text,
            trigger="task_complete",
            source="auto:executor",
            metadata={"goal": "配置超时参数同步修改", "primary_intent": "修改 config.toml"},
        )
        assert report.nodes_ingested == 1
        nodes = kt.md_store.list_nodes()
        assert len(nodes) == 1
        # title 应来自 goal 而非 chunk
        assert "配置超时参数同步修改" in nodes[0].title
        # chunk 前 50 字符不应作为 title
        assert "worker_timeout" not in nodes[0].title

    def test_title_falls_back_to_primary_intent(self, kt: KnowledgeTree):
        """无 goal 但有 primary_intent 时，title 用 primary_intent 前 60 字符。"""
        text = (
            "重要发现：v3 进程分离架构在 spawn 子进程时必须显式传入 cwd 参数，"
            "否则会导致子进程工作目录异常，需要重写 spawn 调用。"
        )
        report = kt.ingest(
            text,
            trigger="task_complete",
            source="auto:executor",
            metadata={"primary_intent": "重写 v3 子进程 spawn 调用"},
        )
        assert report.nodes_ingested == 1
        nodes = kt.md_store.list_nodes()
        assert len(nodes) == 1
        assert "重写 v3 子进程 spawn 调用" in nodes[0].title

    def test_title_falls_back_to_chunk_when_no_metadata(self, kt: KnowledgeTree):
        """无 goal/primary_intent 时维持原逻辑（chunk[:50]）。"""
        text = "发现" + ("x" * 80) + "总结"  # 50+ 字符触发 sufficient_length
        report = kt.ingest(text, trigger="user_explicit", source="test")
        assert report.nodes_ingested == 1
        nodes = kt.md_store.list_nodes()
        assert len(nodes) == 1
        assert nodes[0].title == text[:50]