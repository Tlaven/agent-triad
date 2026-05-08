"""P2: 知识树重组 — 差异计算 + 移动执行。

Agent 通过编号树提重组方案 → 系统计算差异 → 自动执行移动 + 向量调整。
安全约束：只移动文件，不删除。提议中不存在的文件保持不变。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from src.common.knowledge_tree.editing.tree_view import TreeEntry, build_proposed_paths
from src.common.knowledge_tree.storage.markdown_store import MarkdownStore
from src.common.knowledge_tree.storage.overlay import OverlayStore

logger = logging.getLogger(__name__)


@dataclass
class MoveOp:
    """一个文件移动操作。"""

    old_id: str
    new_id: str


@dataclass
class ReorganizeReport:
    """重组执行报告。"""

    moves_executed: int = 0
    moves_failed: int = 0
    directories_created: list[str] = field(default_factory=list)
    directories_removed: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    overlay_edges_updated: int = 0


def diff_trees(
    current_ids: list[str],
    proposed_entries: list[TreeEntry],
) -> list[MoveOp]:
    """计算当前树和提议树之间的差异（移动操作）。

    匹配逻辑：通过文件名 stem（去掉 .md 后缀）匹配文件。
    如果 stem 相同但路径不同，生成一个 MoveOp。

    Args:
        current_ids: 当前所有节点 ID（相对路径）。
        proposed_entries: 解析后的提议编号树条目。

    Returns:
        MoveOp 列表。
    """
    # 构建 current: stem -> node_id 映射
    current_stems: dict[str, str] = {}
    for node_id in current_ids:
        filename = node_id.rsplit("/", 1)[-1]
        stem = filename.removesuffix(".md")
        current_stems[stem] = node_id

    # 构建 proposed: stem -> proposed_path 映射
    proposed = build_proposed_paths(proposed_entries)

    moves: list[MoveOp] = []
    for stem, proposed_path in proposed.items():
        old_id = current_stems.get(stem)
        if old_id is None:
            # 提议中有但当前不存在的文件（可能是新文件，跳过）
            continue
        if old_id != proposed_path:
            moves.append(MoveOp(old_id=old_id, new_id=proposed_path))

    return moves


def execute_reorganize(
    moves: list[MoveOp],
    md_store: MarkdownStore,
    overlay_store: OverlayStore,
) -> ReorganizeReport:
    """顺序执行移动操作。

    对每个移动：
    1. 确保目标目录存在
    2. 处理命名冲突（追加 -2 后缀）
    3. md_store.move_node(old_id, new_id)
    4. 更新 overlay 边引用

    移动完成后：
    5. 清理空目录

    Args:
        moves: 移动操作列表。
        md_store: 文件系统存储。
        overlay_store: Overlay 存储。

    Returns:
        ReorganizeReport。
    """
    report = ReorganizeReport()

    # 收集所有涉及的目标目录，确保存在
    target_dirs: set[str] = set()
    for move in moves:
        parts = move.new_id.rsplit("/", 1)
        if len(parts) > 1:
            target_dirs.add(parts[0])

    for d in sorted(target_dirs):
        if not md_store.get_directory_files(d):
            md_store.ensure_directory(d)
            report.directories_created.append(d)

    # 执行移动
    executed_moves: dict[str, str] = {}  # old_id -> actual new_id
    for move in moves:
        # 处理命名冲突
        actual_new_id = _resolve_conflict(move.new_id, md_store)

        try:
            success = md_store.move_node(move.old_id, actual_new_id)
            if success:
                report.moves_executed += 1
                executed_moves[move.old_id] = actual_new_id
                logger.info("Reorganize: moved %s -> %s", move.old_id, actual_new_id)
            else:
                report.moves_failed += 1
                report.errors.append(f"Source not found: {move.old_id}")
        except Exception as e:
            report.moves_failed += 1
            report.errors.append(f"Failed to move {move.old_id}: {e}")
            logger.warning("Reorganize failed: %s -> %s: %s", move.old_id, actual_new_id, e)

    # 更新 overlay 边
    if executed_moves:
        report.overlay_edges_updated = _update_overlay_edges(
            executed_moves, overlay_store
        )

    # 清理空目录
    for d in md_store.list_directories():
        if not md_store.get_directory_files(d):
            if md_store.remove_directory_if_empty(d):
                report.directories_removed.append(d)

    return report


def _resolve_conflict(new_id: str, md_store: MarkdownStore) -> str:
    """处理目标路径冲突：如果目标已存在，追加 -2, -3 等后缀。"""
    if not md_store.node_exists(new_id):
        return new_id

    parts = new_id.rsplit("/", 1)
    directory = parts[0] if len(parts) > 1 else ""
    filename = parts[-1]
    stem = filename.removesuffix(".md")

    suffix = 2
    while True:
        new_filename = f"{stem}-{suffix}.md"
        candidate = f"{directory}/{new_filename}" if directory else new_filename
        if not md_store.node_exists(candidate):
            return candidate
        suffix += 1


def _update_overlay_edges(
    moves: dict[str, str],
    overlay_store: OverlayStore,
) -> int:
    """更新 overlay 边中的路径引用。

    Args:
        moves: old_id -> new_id 映射。
        overlay_store: Overlay 存储。

    Returns:
        更新的边数。
    """
    updated = 0
    all_edges = overlay_store.get_all_edges()
    for edge in all_edges:
        new_source = moves.get(edge.source_path)
        new_target = moves.get(edge.target_path)

        if new_source or new_target:
            # 移除旧边
            overlay_store.remove_edge(
                edge.source_path, edge.target_path, edge.relation
            )
            # 添加新边
            from src.common.knowledge_tree.storage.overlay import OverlayEdge

            new_edge = OverlayEdge(
                source_path=new_source or edge.source_path,
                target_path=new_target or edge.target_path,
                relation=edge.relation,
                strength=edge.strength,
                created_by=edge.created_by,
                note=edge.note,
            )
            overlay_store.add_edge(new_edge)
            updated += 1

    return updated
