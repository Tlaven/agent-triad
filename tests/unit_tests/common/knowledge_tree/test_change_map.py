"""Change Mapping 测试。"""

import pytest

from src.common.knowledge_tree.dag.node import KnowledgeNode
from src.common.knowledge_tree.editing.change_map import (
    apply_json_patch,
    compute_delta,
    validate_delta,
)


class TestValidateDelta:
    def test_valid_patches(self):
        patches = [
            {"op": "replace", "path": "/title", "value": "New Title"},
            {"op": "replace", "path": "/content", "value": "New Content"},
        ]
        errors = validate_delta(patches)
        assert errors == []

    def test_invalid_op(self):
        patches = [{"op": "copy", "path": "/title", "value": "X"}]
        errors = validate_delta(patches)
        assert len(errors) == 1
        assert "invalid op" in errors[0]

    def test_invalid_path(self):
        patches = [{"op": "replace", "path": "/embedding", "value": [1.0]}]
        errors = validate_delta(patches)
        assert len(errors) == 1
        assert "not in allowed" in errors[0]

    def test_missing_value(self):
        patches = [{"op": "replace", "path": "/title"}]
        errors = validate_delta(patches)
        assert len(errors) == 1
        assert "requires 'value'" in errors[0]

    def test_metadata_path_allowed(self):
        patches = [
            {"op": "add", "path": "/metadata/tag", "value": "new"},
            {"op": "remove", "path": "/metadata/old"},
        ]
        errors = validate_delta(patches)
        assert errors == []


class TestComputeDelta:
    def test_title_change(self):
        before = KnowledgeNode.create(title="Old", content="C")
        after = KnowledgeNode.create(title="New", content="C")
        # 保持同一 ID
        after.node_id = before.node_id

        delta = compute_delta("update_content", before, after)
        assert delta.operation == "update_content"
        assert any(p["path"] == "/title" and p["value"] == "New" for p in delta.patches)

    def test_no_change(self):
        node = KnowledgeNode.create(title="T", content="C")
        delta = compute_delta("update_content", node, node)
        assert delta.patches == []

    def test_metadata_change(self):
        before = KnowledgeNode.create(title="T", content="C", metadata={"a": 1})
        after = KnowledgeNode.create(title="T", content="C", metadata={"a": 2, "b": 3})
        after.node_id = before.node_id

        delta = compute_delta("update_content", before, after)
        meta_patches = [p for p in delta.patches if p["path"].startswith("/metadata/")]
        assert len(meta_patches) >= 2  # a changed + b added

    def test_affected_node_ids(self):
        before = KnowledgeNode.create(title="T", content="C")
        after = KnowledgeNode.create(title="New", content="C")
        after.node_id = before.node_id

        delta = compute_delta("update_content", before, after, affected_node_ids=["a", "b"])
        assert delta.affected_node_ids == ["a", "b"]


class TestApplyJsonPatch:
    def test_apply_replace(self):
        node = KnowledgeNode.create(title="Old", content="Old content", summary="Old sum")
        patches = [
            {"op": "replace", "path": "/title", "value": "New"},
            {"op": "replace", "path": "/content", "value": "New content"},
        ]
        result = apply_json_patch(node, patches)
        assert result.title == "New"
        assert result.content == "New content"
        assert result.summary == "Old sum"  # 未修改
        # 原节点不变
        assert node.title == "Old"

    def test_apply_metadata(self):
        node = KnowledgeNode.create(title="T", content="C", metadata={"x": 1})
        patches = [
            {"op": "add", "path": "/metadata/y", "value": 2},
            {"op": "remove", "path": "/metadata/x"},
        ]
        result = apply_json_patch(node, patches)
        assert result.metadata == {"y": 2}

    def test_apply_invalid_patch_skipped(self):
        node = KnowledgeNode.create(title="T", content="C")
        patches = [
            {"op": "replace", "path": "/embedding", "value": [1.0]},  # 不允许
        ]
        result = apply_json_patch(node, patches)
        assert result.title == "T"  # 未被修改
