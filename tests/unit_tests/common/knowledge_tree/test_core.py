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


class TestDeleteNode:
    def test_delete_node_removes_md_and_embeddings(self, kt: KnowledgeTree):
        """删除节点后 md 文件、主 embedding、title:/stored:/alias: 全无。"""
        from datetime import UTC, datetime

        from src.common.knowledge_tree.dag.node import KnowledgeNode
        from src.common.knowledge_tree.ingestion.ingest import (
            _sanitize_dirname,
            _unique_node_id,
        )
        from src.common.knowledge_tree.storage.vector_store import DirectoryAnchor

        # 普通节点：让 KT 帮我们建目录+锚点+embedding 链
        kt.ingest(
            "发现重要模式：异步路径上的同步 syscall 必须包 asyncio.to_thread。",
            trigger="user_explicit",
            source="test",
            metadata={"goal": "异步路径禁同步 syscall"},
        )

        # 手动构造一个带 alias 的 meta_rule 节点（模拟 meta_rule 入摄结果）
        meta_node = KnowledgeNode.create(
            node_id="",
            title="异步路径禁同步 syscall",
            content="meta rule: async 节点内禁止 os.getcwd / Path.resolve() 等同步 syscall。",
            source="meta_rule",
            metadata={
                "node_type": "meta_rule",
                "priority": 1,
                "aliases": ["no sync in async", "禁同步调用"],
            },
        )
        dir_name = _sanitize_dirname(meta_node.title)
        kt.md_store.ensure_directory(dir_name)
        meta_node.node_id = _unique_node_id(kt.md_store, dir_name, meta_node.title)
        meta_node.embedding = kt.embedder(meta_node.content or meta_node.title)
        kt.md_store.write_node(meta_node)
        kt.vector_store.upsert_embedding(meta_node.node_id, meta_node.embedding)
        kt.vector_store.upsert_embedding(
            f"title:{meta_node.node_id}",
            kt.embedder(meta_node.title),
        )
        for i, alias in enumerate(meta_node.metadata["aliases"]):
            kt.vector_store.upsert_embedding(
                f"alias:{meta_node.node_id}:{i}",
                kt.embedder(alias),
            )
        kt.vector_store.upsert_anchor(DirectoryAnchor(
            directory=dir_name,
            anchor_vector=meta_node.embedding,
            file_count=1,
            last_updated=datetime.now(UTC).isoformat(),
        ))

        node_id = meta_node.node_id
        # 前置：节点存在 + 全部索引存在
        assert kt.md_store.node_exists(node_id)
        stored_key = f"stored:{node_id}"
        # 直接 upsert 一个 stored 占位向量验证删除覆盖
        kt.vector_store.upsert_embedding(stored_key, kt.embedder("any"))
        assert node_id in kt.vector_store._embeddings
        assert f"title:{node_id}" in kt.vector_store._embeddings
        assert stored_key in kt.vector_store._embeddings
        # alias key 至少 2 个
        alias_keys = [k for k in kt.vector_store._embeddings if k.startswith(f"alias:{node_id}:")]
        assert len(alias_keys) == 2

        # 调 delete_node
        result = kt.delete_node(node_id)
        assert result["ok"] is True
        assert node_id in result["deleted"]

        # 验证：md 不存在
        assert not kt.md_store.node_exists(node_id)
        # 主 embedding 已删
        assert node_id not in kt.vector_store._embeddings
        # title/stored/alias 已删
        assert f"title:{node_id}" not in kt.vector_store._embeddings
        assert stored_key not in kt.vector_store._embeddings
        for k in kt.vector_store._embeddings:
            assert not k.startswith(f"alias:{node_id}:")

    def test_delete_node_clears_empty_directory_anchor(self, kt: KnowledgeTree):
        """删完目录最后一个节点后，目录锚点应被清。"""
        from datetime import UTC, datetime

        from src.common.knowledge_tree.dag.node import KnowledgeNode
        from src.common.knowledge_tree.ingestion.ingest import (
            _sanitize_dirname,
            _unique_node_id,
        )
        from src.common.knowledge_tree.storage.vector_store import DirectoryAnchor

        title = "test独占目录节点"
        dir_name = _sanitize_dirname(title)
        kt.md_store.ensure_directory(dir_name)
        node = KnowledgeNode.create(
            node_id="",
            title=title,
            content="测试独占目录的删除",
            source="test",
        )
        node.node_id = _unique_node_id(kt.md_store, dir_name, title)
        node.embedding = kt.embedder(node.content or node.title)
        kt.md_store.write_node(node)
        kt.vector_store.upsert_embedding(node.node_id, node.embedding)
        kt.vector_store.upsert_embedding(
            f"title:{node.node_id}",
            kt.embedder(node.title),
        )
        kt.vector_store.upsert_anchor(DirectoryAnchor(
            directory=dir_name,
            anchor_vector=node.embedding,
            file_count=1,
            last_updated=datetime.now(UTC).isoformat(),
        ))

        result = kt.delete_node(node.node_id)
        assert result["ok"] is True
        # 目录锚点已清
        assert kt.vector_store.get_anchor(dir_name) is None

    def test_delete_nonexistent_node_returns_skipped(self, kt: KnowledgeTree):
        """删除不存在的 node_id 不抛异常，返回 skipped 列表。"""
        result = kt.delete_node("not/exist_node.md")
        assert "not/exist_node.md" in result.get("skipped", [])
        assert result.get("deleted", []) == []