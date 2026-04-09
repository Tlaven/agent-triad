"""跨 Agent 共享的工具注册中心。

约定：
- 本地可复用的只读工具统一放在本模块。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from langchain_core.tools import tool

if TYPE_CHECKING:
    from src.common.context import Context

_filesystem_default_root_dir = "workspace/agent"


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _resolve_filesystem_root(root_dir: str) -> Path:
    rel = (root_dir or "workspace/agent").strip() or "workspace/agent"
    root = (_repo_root() / rel).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _resolve_in_root(root: Path, relative_path: str) -> Path:
    target = (root / (relative_path or "").strip()).resolve()
    if root != target and root not in target.parents:
        raise ValueError("path 超出允许的根目录范围")
    return target


@tool
def list_workspace_entries(
    relative_path: str = ".",
    max_entries: int = 200,
    root_dir: str = "",
) -> str:
    """列出工作区内目录项（只读）。"""
    effective_root_dir = (root_dir or _filesystem_default_root_dir).strip() or "workspace/agent"
    root = _resolve_filesystem_root(effective_root_dir)
    try:
        base = _resolve_in_root(root, relative_path)
    except ValueError as e:
        return json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False, indent=2)
    if not base.exists():
        return json.dumps({"ok": False, "error": "目标路径不存在"}, ensure_ascii=False, indent=2)
    if not base.is_dir():
        return json.dumps({"ok": False, "error": "目标路径不是目录"}, ensure_ascii=False, indent=2)

    limit = max(1, min(int(max_entries), 500))
    items: list[dict[str, object]] = []
    for idx, entry in enumerate(sorted(base.iterdir(), key=lambda p: p.name.lower())):
        if idx >= limit:
            break
        rel = entry.relative_to(root).as_posix()
        items.append(
            {
                "name": entry.name,
                "relative_path": rel,
                "type": "dir" if entry.is_dir() else "file",
            }
        )
    return json.dumps(
        {"ok": True, "root": str(root), "base": base.relative_to(root).as_posix(), "entries": items},
        ensure_ascii=False,
        indent=2,
    )


@tool
def read_workspace_text_file(
    relative_path: str,
    max_chars: int = 12000,
    root_dir: str = "",
) -> str:
    """读取工作区内文本文件（只读）。"""
    effective_root_dir = (root_dir or _filesystem_default_root_dir).strip() or "workspace/agent"
    root = _resolve_filesystem_root(effective_root_dir)
    try:
        file_path = _resolve_in_root(root, relative_path)
    except ValueError as e:
        return json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False, indent=2)
    if not file_path.exists():
        return json.dumps({"ok": False, "error": "文件不存在"}, ensure_ascii=False, indent=2)
    if not file_path.is_file():
        return json.dumps({"ok": False, "error": "目标路径不是文件"}, ensure_ascii=False, indent=2)

    limit = max(256, min(int(max_chars), 100_000))
    content = file_path.read_text(encoding="utf-8", errors="replace")
    truncated = len(content) > limit
    preview = content[:limit]
    return json.dumps(
        {
            "ok": True,
            "root": str(root),
            "relative_path": file_path.relative_to(root).as_posix(),
            "truncated": truncated,
            "content": preview,
        },
        ensure_ascii=False,
        indent=2,
    )


def apply_context_workspace_root(context: "Context" | None) -> None:
    """从 Context 同步 `list_workspace_entries` / `read_workspace_text_file` 使用的默认根目录。"""
    if context is None:
        return
    root_dir = str(getattr(context, "filesystem_mcp_root_dir", "") or "").strip()
    if root_dir:
        global _filesystem_default_root_dir
        _filesystem_default_root_dir = root_dir