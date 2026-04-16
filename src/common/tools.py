"""跨 Agent 共享的工具注册中心。

约定：
- 本地可复用的只读工具统一放在本模块。
"""

from __future__ import annotations

import json
import re
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


@tool
def search_files(
    pattern: str = "*",
    relative_path: str = ".",
    max_results: int = 50,
    root_dir: str = "",
) -> str:
    """按 glob 模式搜索工作区内文件名（只读）。

    Args:
        pattern: glob 模式，如 "*.py"、"**/*.md"、"test_*.txt"。
        relative_path: 搜索起始目录（相对于工作区根）。
        max_results: 最大返回条目数。
        root_dir: 工作区根目录（通常由系统注入）。
    """
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

    limit = max(1, min(int(max_results), 200))
    matches: list[dict[str, str]] = []
    for p in sorted(base.glob(pattern)):
        if len(matches) >= limit:
            break
        rel = p.relative_to(root).as_posix()
        matches.append({"relative_path": rel, "type": "dir" if p.is_dir() else "file"})
    return json.dumps(
        {"ok": True, "root": str(root), "pattern": pattern, "matches": matches, "count": len(matches)},
        ensure_ascii=False,
        indent=2,
    )


@tool
def grep_content(
    pattern: str,
    relative_path: str = ".",
    file_pattern: str = "*",
    max_results: int = 30,
    root_dir: str = "",
) -> str:
    """在工作区文件内容中搜索正则匹配（只读）。

    Args:
        pattern: 正则表达式模式。
        relative_path: 搜索起始目录。
        file_pattern: 文件名 glob 过滤，如 "*.py"。
        max_results: 最大返回匹配数。
        root_dir: 工作区根目录。
    """
    effective_root_dir = (root_dir or _filesystem_default_root_dir).strip() or "workspace/agent"
    root = _resolve_filesystem_root(effective_root_dir)
    try:
        base = _resolve_in_root(root, relative_path)
    except ValueError as e:
        return json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False, indent=2)
    if not base.exists():
        return json.dumps({"ok": False, "error": "目标路径不存在"}, ensure_ascii=False, indent=2)

    try:
        regex = re.compile(pattern, re.IGNORECASE)
    except re.error as e:
        return json.dumps({"ok": False, "error": f"正则表达式无效: {e}"}, ensure_ascii=False, indent=2)

    limit = max(1, min(int(max_results), 100))
    results: list[dict[str, object]] = []
    for fp in sorted(base.rglob(file_pattern)):
        if not fp.is_file():
            continue
        try:
            text = fp.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line_no, line in enumerate(text.splitlines(), 1):
            m = regex.search(line)
            if m:
                rel = fp.relative_to(root).as_posix()
                results.append({
                    "file": rel,
                    "line": line_no,
                    "match": line.strip()[:200],
                })
                if len(results) >= limit:
                    break
        if len(results) >= limit:
            break
    return json.dumps(
        {"ok": True, "root": str(root), "pattern": pattern, "results": results, "count": len(results)},
        ensure_ascii=False,
        indent=2,
    )


@tool
def read_file_structure(
    relative_path: str = ".",
    max_depth: int = 3,
    max_entries: int = 200,
    root_dir: str = "",
) -> str:
    """读取工作区目录树结构概览（只读）。

    Args:
        relative_path: 起始目录。
        max_depth: 目录遍历最大深度。
        max_entries: 最大返回条目数。
        root_dir: 工作区根目录。
    """
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

    depth_limit = max(1, min(int(max_depth), 6))
    entry_limit = max(1, min(int(max_entries), 500))

    lines: list[str] = []
    count = 0

    def _walk(directory: Path, prefix: str, depth: int) -> None:
        nonlocal count
        if depth > depth_limit or count >= entry_limit:
            return
        try:
            entries = sorted(directory.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        except PermissionError:
            return
        for entry in entries:
            if count >= entry_limit:
                lines.append(f"{prefix}... (truncated)")
                return
            name = entry.name
            if entry.is_dir():
                lines.append(f"{prefix}{name}/")
                count += 1
                _walk(entry, prefix + "  ", depth + 1)
            else:
                lines.append(f"{prefix}{name}")
                count += 1

    _walk(base, "", 0)
    return json.dumps(
        {
            "ok": True,
            "root": str(root),
            "base": base.relative_to(root).as_posix(),
            "structure": "\n".join(lines),
            "entry_count": count,
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