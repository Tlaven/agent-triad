"""Planner 侧可绑定的本地工具（只读工作区；实现本体在 `src.common.tools`）。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.common.tools import (
    apply_context_workspace_root,
    list_workspace_entries,
    read_workspace_text_file,
)

if TYPE_CHECKING:
    from src.common.context import Context


def get_planner_tools(ctx: "Context | None" = None) -> list[object]:
    """返回 Planner 绑定的本地只读工具列表。"""
    apply_context_workspace_root(ctx)
    return [read_workspace_text_file, list_workspace_entries]
