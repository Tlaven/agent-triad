"""验证项目种子知识数据的质量和格式。"""

from pathlib import Path

import pytest

from src.common.knowledge_tree.dag.node import KnowledgeNode

SEED_DIR = Path("workspace/knowledge_tree")

# 只扫描预期的种子目录，排除自动生成的垃圾文件（misc/、step_*/）
_SEED_CATEGORIES = {"architecture", "conventions", "patterns", "setup", "troubleshooting"}


def _seed_files() -> list[Path]:
    """收集所有预期目录下的种子 .md 文件。"""
    files = []
    for cat_dir in _SEED_CATEGORIES:
        cat_path = SEED_DIR / cat_dir
        if cat_path.is_dir():
            files.extend(cat_path.glob("*.md"))
    return sorted(files)


class TestSeedDirectory:
    """种子目录结构验证。"""

    def test_directory_exists(self):
        assert SEED_DIR.is_dir(), f"Seed directory not found: {SEED_DIR}"

    def test_has_minimum_files(self):
        md_files = _seed_files()
        assert len(md_files) >= 5, f"Expected >= 5 seed files, found {len(md_files)}"

    def test_has_subdirectories(self):
        dirs = [d for d in SEED_DIR.iterdir() if d.is_dir() and not d.name.startswith(".")]
        assert len(dirs) >= 2, f"Expected >= 2 subdirectories, found {len(dirs)}"

    def test_expected_categories(self):
        """应包含 architecture/conventions/patterns 三个分类目录。"""
        expected = {"architecture", "conventions", "patterns"}
        actual = {d.name for d in SEED_DIR.iterdir() if d.is_dir() and not d.name.startswith(".")}
        assert expected <= actual, f"Missing categories: {expected - actual}"


class TestSeedFileFormat:
    """每个种子文件应能被 KnowledgeNode 正确解析。"""

    @pytest.fixture(params=_seed_files(), ids=lambda p: str(p.relative_to(SEED_DIR)))
    def seed_file(self, request):
        return request.param

    def test_file_parses_as_node(self, seed_file):
        content = seed_file.read_text(encoding="utf-8")
        node = KnowledgeNode.from_frontmatter_md(content, node_id=str(seed_file.relative_to(Path("workspace/knowledge_tree"))))
        assert node is not None, f"Failed to parse: {seed_file}"

    def test_node_has_title(self, seed_file):
        content = seed_file.read_text(encoding="utf-8")
        node = KnowledgeNode.from_frontmatter_md(content, node_id="test")
        assert node.title and len(node.title) > 0, f"Missing title in: {seed_file}"

    def test_node_has_content(self, seed_file):
        content = seed_file.read_text(encoding="utf-8")
        node = KnowledgeNode.from_frontmatter_md(content, node_id="test")
        assert node.content and len(node.content) >= 20, (
            f"Content too short (< 20 chars) in: {seed_file}"
        )
