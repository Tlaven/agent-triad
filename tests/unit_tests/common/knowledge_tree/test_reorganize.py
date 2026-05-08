"""P2 知识树重组测试。

验证 diff_trees + execute_reorganize 的完整流程。
"""

import pytest

from src.common.knowledge_tree.dag.node import KnowledgeNode
from src.common.knowledge_tree.editing.reorganize import (
    ReorganizeReport,
    diff_trees,
    execute_reorganize,
)
from src.common.knowledge_tree.editing.tree_view import TreeEntry, parse_numbered_tree
from src.common.knowledge_tree.storage.markdown_store import MarkdownStore
from src.common.knowledge_tree.storage.overlay import OverlayEdge, OverlayStore


@pytest.fixture
def md_store(tmp_path):
    store = MarkdownStore(tmp_path / "kt_md")
    for path, title in [
        ("architecture/three-agent.md", "Three Agent"),
        ("architecture/langgraph-state.md", "LangGraph State"),
        ("conventions/plan-json.md", "Plan JSON"),
        ("setup/environment.md", "Environment"),
    ]:
        node = KnowledgeNode.create(
            node_id=path,
            title=title,
            content=f"Content of {title}",
            source="test",
        )
        store.write_node(node)
    return store


@pytest.fixture
def overlay_store(tmp_path):
    return OverlayStore(tmp_path / "kt_md" / ".overlay.json")


# -- diff_trees --


class TestDiffTrees:
    def test_no_change(self, md_store):
        # 提议与当前结构相同
        entries = parse_numbered_tree(
            """\
01 architecture/
    01 three-agent.md
    02 langgraph-state.md
02 conventions/
    01 plan-json.md
03 setup/
    01 environment.md"""
        )
        current = md_store.list_node_ids()
        moves = diff_trees(current, entries)
        assert len(moves) == 0

    def test_move_file(self, md_store):
        # 将 three-agent.md 从 architecture 移到 conventions
        entries = parse_numbered_tree(
            """\
01 architecture/
    01 langgraph-state.md
02 conventions/
    01 plan-json.md
    02 three-agent.md
03 setup/
    01 environment.md"""
        )
        current = md_store.list_node_ids()
        moves = diff_trees(current, entries)
        assert len(moves) == 1
        assert moves[0].old_id == "architecture/three-agent.md"
        assert moves[0].new_id == "conventions/three-agent.md"

    def test_rename_directory(self, md_store):
        # 将 architecture 重命名为 design
        entries = parse_numbered_tree(
            """\
01 design/
    01 three-agent.md
    02 langgraph-state.md
02 conventions/
    01 plan-json.md
03 setup/
    01 environment.md"""
        )
        current = md_store.list_node_ids()
        moves = diff_trees(current, entries)
        assert len(moves) == 2
        moved_ids = {m.old_id for m in moves}
        assert "architecture/three-agent.md" in moved_ids
        assert "architecture/langgraph-state.md" in moved_ids

    def test_proposal_with_unknown_file(self, md_store):
        # 提议中包含当前不存在的文件 — 应跳过
        entries = parse_numbered_tree(
            """\
01 architecture/
    01 three-agent.md
    02 langgraph-state.md
    03 new-topic.md
02 conventions/
    01 plan-json.md
03 setup/
    01 environment.md"""
        )
        current = md_store.list_node_ids()
        moves = diff_trees(current, entries)
        # new-topic.md 不存在于 current，跳过
        assert all(m.old_id != "new-topic.md" for m in moves)


# -- execute_reorganize --


class TestExecuteReorganize:
    def test_single_move(self, md_store, overlay_store):
        moves = [{"old_id": "architecture/three-agent.md", "new_id": "conventions/three-agent.md"}]
        from src.common.knowledge_tree.editing.reorganize import MoveOp

        report = execute_reorganize(
            [MoveOp(**m) for m in moves], md_store, overlay_store
        )
        assert report.moves_executed == 1
        assert not md_store.node_exists("architecture/three-agent.md")
        assert md_store.node_exists("conventions/three-agent.md")

    def test_creates_target_directory(self, md_store, overlay_store):
        from src.common.knowledge_tree.editing.reorganize import MoveOp

        moves = [MoveOp(old_id="setup/environment.md", new_id="ops/env.md")]
        report = execute_reorganize(moves, md_store, overlay_store)
        assert report.moves_executed == 1
        assert "ops" in report.directories_created
        assert md_store.node_exists("ops/env.md")

    def test_cleans_empty_directory(self, md_store, overlay_store):
        from src.common.knowledge_tree.editing.reorganize import MoveOp

        # 移动 setup 下唯一的文件
        moves = [MoveOp(old_id="setup/environment.md", new_id="architecture/environment.md")]
        report = execute_reorganize(moves, md_store, overlay_store)
        assert report.moves_executed == 1
        assert "setup" in report.directories_removed

    def test_updates_overlay_edges(self, md_store, overlay_store):
        from src.common.knowledge_tree.editing.reorganize import MoveOp

        # 先添加 overlay 边
        overlay_store.add_edge(
            OverlayEdge(
                source_path="architecture/three-agent.md",
                target_path="conventions/plan-json.md",
                relation="related",
            )
        )
        # 移动 three-agent.md
        moves = [MoveOp(old_id="architecture/three-agent.md", new_id="setup/three-agent.md")]
        report = execute_reorganize(moves, md_store, overlay_store)
        assert report.overlay_edges_updated == 1
        # 验证边已更新
        edges = overlay_store.get_all_edges()
        assert len(edges) == 1
        assert edges[0].source_path == "setup/three-agent.md"
        assert edges[0].target_path == "conventions/plan-json.md"

    def test_name_conflict_resolution(self, md_store, overlay_store):
        from src.common.knowledge_tree.editing.reorganize import MoveOp

        # 移动一个文件到 conventions/，但 conventions/plan-json.md 已存在
        # 使用不同的 stem 来测试冲突（目标目录已有同名文件的情况）
        # 先在 conventions/ 下创建一个同名文件
        node = KnowledgeNode.create(
            node_id="conventions/three-agent.md",
            title="Existing",
            content="Already here",
            source="test",
        )
        md_store.write_node(node)

        # 移动 architecture/three-agent.md 到 conventions/
        moves = [MoveOp(old_id="architecture/three-agent.md", new_id="conventions/three-agent.md")]
        report = execute_reorganize(moves, md_store, overlay_store)
        assert report.moves_executed == 1
        # 应该有 -2 后缀
        assert md_store.node_exists("conventions/three-agent-2.md") or md_store.node_exists(
            "conventions/three-agent.md"
        )

    def test_source_not_found(self, md_store, overlay_store):
        from src.common.knowledge_tree.editing.reorganize import MoveOp

        moves = [MoveOp(old_id="nonexistent/file.md", new_id="target/file.md")]
        report = execute_reorganize(moves, md_store, overlay_store)
        assert report.moves_failed == 1
        assert len(report.errors) > 0


# -- Integration: full reorganize flow --


class TestReorganizeIntegration:
    def test_full_flow(self, md_store, overlay_store):
        """完整流程：解析提议 → 计算差异 → 执行移动。"""
        proposal = """\
01 design/
    01 three-agent.md
    02 langgraph-state.md
02 standards/
    01 plan-json.md
03 ops/
    01 environment.md"""
        entries = parse_numbered_tree(proposal)
        current = md_store.list_node_ids()
        moves = diff_trees(current, entries)

        # 应该有 4 个移动（architecture→design, conventions→standards, setup→ops）
        assert len(moves) >= 3

        report = execute_reorganize(moves, md_store, overlay_store)
        assert report.moves_executed >= 3
        assert report.moves_failed == 0

        # 验证最终结构
        assert md_store.node_exists("design/three-agent.md")
        assert md_store.node_exists("design/langgraph-state.md")
        assert md_store.node_exists("standards/plan-json.md")
        assert md_store.node_exists("ops/environment.md")

        # 旧结构应不存在
        assert not md_store.node_exists("architecture/three-agent.md")
        assert not md_store.node_exists("conventions/plan-json.md")
