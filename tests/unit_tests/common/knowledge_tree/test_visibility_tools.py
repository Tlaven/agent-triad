"""知识树可见性工具（status/list）测试。"""

from pathlib import Path

import pytest

from src.common.knowledge_tree import KnowledgeTree, KnowledgeTreeConfig
from src.common.knowledge_tree.dag.node import KnowledgeNode


@pytest.fixture
def kt_with_seeds(tmp_path: Path) -> KnowledgeTree:
    """有种子数据的 KnowledgeTree 实例。"""
    seed_dir = tmp_path / "kt"
    seed_dir.mkdir()

    # 创建 2 个目录各 2 个文件
    for dirname in ("architecture", "patterns"):
        d = seed_dir / dirname
        d.mkdir()
        for i in range(1, 3):
            node = KnowledgeNode.create(
                node_id=f"{dirname}/topic_{i}.md",
                title=f"Topic {i} in {dirname}",
                content=f"Content for topic {i} about {dirname} patterns and design.",
                source="test",
            )
            path = d / f"topic_{i}.md"
            path.write_text(node.to_frontmatter_md(), encoding="utf-8")

    config = KnowledgeTreeConfig(markdown_root=seed_dir, embedding_model="hash")
    kt = KnowledgeTree(config)
    kt.bootstrap()
    return kt


class TestKnowledgeTreeStatus:
    """knowledge_tree_status 工具测试。"""

    def test_status_returns_overview(self, kt_with_seeds: KnowledgeTree):
        s = kt_with_seeds.status()
        assert s["ok"] is True
        assert s["total_nodes"] == 4
        assert s["total_directories"] == 2
        assert s["total_anchors"] == 2
        assert "architecture" in s["directories"]
        assert "patterns" in s["directories"]

    def test_status_empty_tree(self, tmp_path: Path):
        empty_dir = tmp_path / "empty_kt"
        empty_dir.mkdir()
        config = KnowledgeTreeConfig(markdown_root=empty_dir, embedding_model="hash")
        kt = KnowledgeTree(config)
        s = kt.status()
        assert s["ok"] is True
        assert s["total_nodes"] == 0
        assert s["total_directories"] == 0
        assert s["total_anchors"] == 0


class TestKnowledgeTreeList:
    """knowledge_tree_list 工具测试。"""

    def test_list_all_nodes(self, kt_with_seeds: KnowledgeTree):
        nodes = kt_with_seeds.md_store.list_nodes()
        assert len(nodes) == 4
        for n in nodes:
            assert n.title
            assert n.content

    def test_list_filtered_by_directory(self, kt_with_seeds: KnowledgeTree):
        nodes = kt_with_seeds.md_store.list_nodes()
        arch_nodes = [n for n in nodes if n.directory == "architecture"]
        assert len(arch_nodes) == 2
        assert all(n.directory == "architecture" for n in arch_nodes)

    def test_list_empty_directory(self, kt_with_seeds: KnowledgeTree):
        nodes = kt_with_seeds.md_store.list_nodes()
        nonexistent = [n for n in nodes if n.directory == "nonexistent"]
        assert len(nonexistent) == 0

    def test_list_node_has_required_fields(self, kt_with_seeds: KnowledgeTree):
        nodes = kt_with_seeds.md_store.list_nodes()
        for n in nodes:
            assert n.node_id
            assert n.title
            assert n.directory
            assert n.content


class TestVisibilityToolsIntegration:
    """测试 status + list 组合使用场景。"""

    def test_status_then_list_consistent(self, kt_with_seeds: KnowledgeTree):
        """status 报告的节点数与 list 返回的数量一致。"""
        s = kt_with_seeds.status()
        nodes = kt_with_seeds.md_store.list_nodes()
        assert s["total_nodes"] == len(nodes)

    def test_status_directories_match_list(self, kt_with_seeds: KnowledgeTree):
        """status 报告的目录与 list 中的目录字段一致。"""
        s = kt_with_seeds.status()
        nodes = kt_with_seeds.md_store.list_nodes()
        actual_dirs = set(n.directory for n in nodes)
        assert actual_dirs == set(s["directories"])
