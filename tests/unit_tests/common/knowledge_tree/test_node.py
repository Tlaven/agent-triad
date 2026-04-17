"""KnowledgeNode 数据模型测试。"""

import yaml
import pytest

from src.common.knowledge_tree.dag.node import KnowledgeNode


class TestKnowledgeNodeCreate:
    """节点工厂方法测试。"""

    def test_create_generates_id_and_timestamp(self):
        node = KnowledgeNode.create(title="测试", content="内容")
        assert len(node.node_id) == 12
        assert node.title == "测试"
        assert node.content == "内容"
        assert node.created_at  # 非空
        assert node.source == ""
        assert node.summary == ""

    def test_create_with_all_fields(self):
        node = KnowledgeNode.create(
            title="T",
            content="C",
            source="S",
            summary="Sum",
            metadata={"key": "val"},
        )
        assert node.source == "S"
        assert node.summary == "Sum"
        assert node.metadata == {"key": "val"}

    def test_unique_ids(self):
        n1 = KnowledgeNode.create(title="A", content="A")
        n2 = KnowledgeNode.create(title="B", content="B")
        assert n1.node_id != n2.node_id


class TestKnowledgeNodeMarkdown:
    """Markdown frontmatter 序列化/反序列化测试。"""

    def test_roundtrip(self, sample_node: KnowledgeNode):
        md = sample_node.to_frontmatter_md()
        restored = KnowledgeNode.from_frontmatter_md(md)
        assert restored.node_id == sample_node.node_id
        assert restored.title == sample_node.title
        assert restored.content == sample_node.content
        assert restored.source == sample_node.source
        assert restored.summary == sample_node.summary

    def test_frontmatter_format(self, sample_node: KnowledgeNode):
        md = sample_node.to_frontmatter_md()
        assert md.startswith("---\n")
        parts = md.split("---", 2)
        assert len(parts) == 3
        fm = yaml.safe_load(parts[1])
        assert fm["node_id"] == sample_node.node_id
        assert fm["title"] == sample_node.title

    def test_from_invalid_frontmatter(self):
        with pytest.raises(ValueError, match="Missing"):
            KnowledgeNode.from_frontmatter_md("no frontmatter")

        with pytest.raises(ValueError, match="Invalid"):
            KnowledgeNode.from_frontmatter_md("---\nnot closed")

    def test_metadata_preserved(self):
        node = KnowledgeNode.create(
            title="T", content="C", metadata={"tags": ["a", "b"]}
        )
        md = node.to_frontmatter_md()
        restored = KnowledgeNode.from_frontmatter_md(md)
        assert restored.metadata == {"tags": ["a", "b"]}

    def test_chinese_content(self):
        node = KnowledgeNode.create(
            title="中文标题", content="这是一段中文内容，包含特殊字符：<>&\"'"
        )
        md = node.to_frontmatter_md()
        restored = KnowledgeNode.from_frontmatter_md(md)
        assert restored.title == "中文标题"
        assert "特殊字符" in restored.content


class TestKnowledgeNodeDict:
    """字典序列化/反序列化测试。"""

    def test_roundtrip_without_embedding(self, sample_node: KnowledgeNode):
        d = sample_node.to_dict()
        assert "embedding" not in d
        restored = KnowledgeNode.from_dict(d)
        assert restored.node_id == sample_node.node_id
        assert restored.embedding is None

    def test_roundtrip_with_embedding(self):
        node = KnowledgeNode.create(title="T", content="C")
        node.embedding = [0.1, 0.2, 0.3]
        d = node.to_dict(include_embedding=True)
        assert d["embedding"] == [0.1, 0.2, 0.3]
        restored = KnowledgeNode.from_dict(d)
        assert restored.embedding == [0.1, 0.2, 0.3]
