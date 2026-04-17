"""Bootstrap 测试。"""

from pathlib import Path

import pytest
import yaml

from src.common.knowledge_tree.bootstrap import bootstrap_from_seed_files
from src.common.knowledge_tree.config import KnowledgeTreeConfig
from src.common.knowledge_tree.dag.node import KnowledgeNode
from src.common.knowledge_tree.storage.graph_store import InMemoryGraphStore
from src.common.knowledge_tree.storage.markdown_store import MarkdownStore
from src.common.knowledge_tree.storage.vector_store import InMemoryVectorStore


@pytest.fixture
def seed_dir(tmp_path: Path) -> Path:
    """创建种子 Markdown 文件目录。"""
    d = tmp_path / "seeds"
    d.mkdir()

    seeds = [
        ("LangGraph 状态管理", "LangGraph 使用 TypedDict 定义状态模式。"),
        ("LangGraph 工具调用", "LangGraph 通过 ToolNode 自动执行工具。"),
        ("Agent ReAct 模式", "ReAct 模式结合推理和行动。"),
        ("向量嵌入原理", "文本嵌入将语义映射为高维向量。"),
    ]

    for title, content in seeds:
        node = KnowledgeNode.create(title=title, content=content, source="test_seed")
        (d / f"{node.node_id}.md").write_text(node.to_frontmatter_md(), encoding="utf-8")

    return d


@pytest.fixture
def stores(tmp_path: Path):
    config = KnowledgeTreeConfig(
        markdown_root=tmp_path / "md",
        db_path=tmp_path / "db",
    )
    md_store = MarkdownStore(config.markdown_root)
    graph_store = InMemoryGraphStore()
    graph_store.initialize()
    vector_store = InMemoryVectorStore(dimension=4)
    return md_store, graph_store, vector_store, config


def _mock_embedder(dim: int = 4):
    def embed(text: str) -> list[float]:
        base = sum(ord(c) for c in text) % 100 / 100.0
        return [base + i * 0.01 for i in range(dim)]
    return embed


class TestBootstrap:
    def test_bootstrap_creates_tree(self, seed_dir: Path, stores):
        md_store, graph_store, vector_store, config = stores
        embedder = _mock_embedder()

        report = bootstrap_from_seed_files(
            seed_dir, md_store, graph_store, vector_store, embedder, config
        )

        assert report.errors == []
        assert report.nodes_created > 0
        assert report.edges_created > 0
        assert report.embeddings_generated > 0
        assert report.max_depth == 3  # root → group → leaf

    def test_bootstrap_root_exists(self, seed_dir: Path, stores):
        md_store, graph_store, vector_store, config = stores
        embedder = _mock_embedder()

        bootstrap_from_seed_files(seed_dir, md_store, graph_store, vector_store, embedder, config)

        root_id = graph_store.get_root_id()
        assert root_id is not None

    def test_bootstrap_leaf_nodes_have_embeddings(self, seed_dir: Path, stores):
        md_store, graph_store, vector_store, config = stores
        embedder = _mock_embedder()

        bootstrap_from_seed_files(seed_dir, md_store, graph_store, vector_store, embedder, config)

        # 检查根节点的子节点
        root_id = graph_store.get_root_id()
        assert root_id is not None
        groups = graph_store.get_children(root_id)
        assert len(groups) > 0

        # 检查叶子节点有嵌入
        for group in groups:
            children = graph_store.get_children(group.node_id)
            for child in children:
                assert vector_store.get_embedding(child.node_id) is not None

    def test_bootstrap_empty_dir(self, tmp_path: Path, stores):
        md_store, graph_store, vector_store, config = stores
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()
        embedder = _mock_embedder()

        report = bootstrap_from_seed_files(
            empty_dir, md_store, graph_store, vector_store, embedder, config
        )
        assert len(report.errors) > 0
        assert report.nodes_created == 0

    def test_bootstrap_nonexistent_dir(self, stores):
        md_store, graph_store, vector_store, config = stores
        embedder = _mock_embedder()

        report = bootstrap_from_seed_files(
            "/nonexistent/path", md_store, graph_store, vector_store, embedder, config
        )
        assert len(report.errors) > 0
