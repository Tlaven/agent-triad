"""Bootstrap 测试（V4: 目录继承）。"""

from pathlib import Path

import pytest

from src.common.knowledge_tree.bootstrap import bootstrap_from_directory
from src.common.knowledge_tree.dag.node import KnowledgeNode
from src.common.knowledge_tree.storage.markdown_store import MarkdownStore
from src.common.knowledge_tree.storage.overlay import OverlayStore
from src.common.knowledge_tree.storage.vector_store import InMemoryVectorStore


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def seed_dir(tmp_path: Path) -> Path:
    """创建种子目录（含子目录和 .md 文件）。"""
    d = tmp_path / "seeds"
    d.mkdir()

    # 创建子目录结构
    (d / "development").mkdir()
    (d / "patterns").mkdir()

    # 写入种子 .md 文件
    seeds = [
        ("development/langgraph.md", "LangGraph 状态管理", "LangGraph 使用 TypedDict 定义状态模式。"),
        ("development/tools.md", "LangGraph 工具调用", "LangGraph 通过 ToolNode 自动执行工具。"),
        ("patterns/react.md", "Agent ReAct 模式", "ReAct 模式结合推理和行动。"),
        ("patterns/embedding.md", "向量嵌入原理", "文本嵌入将语义映射为高维向量。"),
    ]

    for rel_path, title, content in seeds:
        node = KnowledgeNode.create(
            node_id=rel_path,
            title=title,
            content=content,
            source="test_seed",
        )
        path = d / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(node.to_frontmatter_md(), encoding="utf-8")

    return d


@pytest.fixture
def stores(tmp_path: Path):
    md_store = MarkdownStore(tmp_path / "md")
    vector_store = InMemoryVectorStore(dimension=16)
    overlay_store = OverlayStore(tmp_path / "md" / ".overlay.json")
    return md_store, vector_store, overlay_store


def _mock_embedder(dim: int = 16):
    """确定性 mock embedder。"""
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


# ---------------------------------------------------------------------------
# 测试
# ---------------------------------------------------------------------------


class TestBootstrapFromDirectory:
    def test_bootstrap_creates_nodes_and_anchors(self, seed_dir: Path, stores):
        md_store, vector_store, overlay_store = stores
        embedder = _mock_embedder()

        report = bootstrap_from_directory(
            seed_dir, md_store, vector_store, overlay_store, embedder,
        )

        assert report.errors == []
        assert report.nodes_created == 4
        assert report.embeddings_generated == 4
        assert report.anchors_computed >= 1  # 至少有 development 和 patterns
        assert report.max_depth >= 1

    def test_bootstrap_anchors_match_directories(self, seed_dir: Path, stores):
        md_store, vector_store, overlay_store = stores
        embedder = _mock_embedder()

        report = bootstrap_from_directory(
            seed_dir, md_store, vector_store, overlay_store, embedder,
        )

        anchors = vector_store.get_all_anchors()
        anchor_dirs = {a.directory for a in anchors}
        assert "development" in anchor_dirs
        assert "patterns" in anchor_dirs

    def test_bootstrap_embeddings_stored(self, seed_dir: Path, stores):
        md_store, vector_store, overlay_store = stores
        embedder = _mock_embedder()

        bootstrap_from_directory(
            seed_dir, md_store, vector_store, overlay_store, embedder,
        )

        # 所有节点应有 embedding
        for anchor in vector_store.get_all_anchors():
            assert anchor.file_count > 0

    def test_bootstrap_empty_dir(self, tmp_path: Path, stores):
        md_store, vector_store, overlay_store = stores
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()

        report = bootstrap_from_directory(
            empty_dir, md_store, vector_store, overlay_store, _mock_embedder(),
        )

        assert len(report.errors) > 0
        assert report.nodes_created == 0

    def test_bootstrap_nonexistent_dir(self, stores):
        md_store, vector_store, overlay_store = stores

        report = bootstrap_from_directory(
            Path("/nonexistent/path"), md_store, vector_store, overlay_store, _mock_embedder(),
        )

        assert len(report.errors) > 0

    def test_bootstrap_clears_old_data(self, seed_dir: Path, stores):
        """Bootstrap 应清空旧数据。"""
        md_store, vector_store, overlay_store = stores
        embedder = _mock_embedder()

        # 第一次 bootstrap
        bootstrap_from_directory(
            seed_dir, md_store, vector_store, overlay_store, embedder,
        )
        assert len(vector_store.get_all_anchors()) > 0

        # 第二次 bootstrap（应清空重建）
        report = bootstrap_from_directory(
            seed_dir, md_store, vector_store, overlay_store, embedder,
        )
        assert report.nodes_created == 4
        assert len(overlay_store.get_all_edges()) == 0

    def test_bootstrap_flat_files(self, tmp_path: Path, stores):
        """没有子目录的扁平结构也能 bootstrap。"""
        md_store, vector_store, overlay_store = stores
        seed_dir = tmp_path / "flat"
        seed_dir.mkdir()

        # 扁平：只有根目录下的文件
        node = KnowledgeNode.create(
            node_id="readme.md",
            title="README",
            content="This is the readme.",
        )
        (seed_dir / "readme.md").write_text(node.to_frontmatter_md(), encoding="utf-8")

        report = bootstrap_from_directory(
            seed_dir, md_store, vector_store, overlay_store, _mock_embedder(),
        )

        assert report.errors == []
        assert report.nodes_created == 1
        assert report.max_depth == 0
