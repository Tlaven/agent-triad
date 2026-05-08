"""P2: 编号树视图 — 渲染和解析知识树的编号缩进格式。

格式：
    01 architecture/
        01 three-agent-system.md
        02 langgraph-state.md
    02 conventions/
        01 plan-json-format.md

用于 Agent 查看当前结构并提重组方案。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from src.common.knowledge_tree.storage.markdown_store import MarkdownStore


@dataclass
class TreeEntry:
    """编号树中的一个条目。"""

    level: int  # 缩进层级（0-based）
    number: int  # 该层级的编号
    name: str  # 文件或目录名
    is_directory: bool  # True 如果以 / 结尾


def render_numbered_tree(md_store: MarkdownStore) -> str:
    """渲染当前知识树为编号缩进文本。

    Args:
        md_store: 文件系统存储。

    Returns:
        编号树文本。
    """
    directories = md_store.list_directories()
    # directory -> sorted list of filenames (no path, just stems)
    dir_files: dict[str, list[str]] = {}
    for d in directories:
        files = md_store.get_directory_files(d)
        # 提取纯文件名
        dir_files[d] = [f.rsplit("/", 1)[-1] for f in files]

    # 根目录文件（无目录前缀的）
    root_files = []
    for nid in md_store.list_node_ids():
        if "/" not in nid:
            root_files.append(nid)

    if not directories and not root_files:
        return "(empty)"

    lines: list[str] = []
    dir_idx = 0
    file_idx = 0

    # 根级文件
    for fname in sorted(root_files):
        file_idx += 1
        lines.append(f"{file_idx:02d} {fname}")

    # 目录及其内容
    for d in sorted(directories):
        dir_idx += 1
        dir_name = d.rsplit("/", 1)[-1] if "/" in d else d
        lines.append(f"{dir_idx:02d} {dir_name}/")

        files = dir_files.get(d, [])
        for fi, fname in enumerate(sorted(files), 1):
            lines.append(f"    {fi:02d} {fname}")

    return "\n".join(lines)


def parse_numbered_tree(text: str) -> list[TreeEntry]:
    """解析编号树格式文本为结构化条目列表。

    Args:
        text: 编号树文本。

    Returns:
        TreeEntry 列表。

    Raises:
        ValueError: 格式错误时。
    """
    if not text or text.strip() == "(empty)":
        return []

    lines = text.rstrip().split("\n")
    entries: list[TreeEntry] = []
    indent_size = 4  # 每级缩进 4 空格

    for line_num, line in enumerate(lines, 1):
        if not line.strip():
            continue

        # 计算缩进层级
        stripped = line.lstrip(" ")
        indent = len(line) - len(stripped)
        level = indent // indent_size

        if indent % indent_size != 0:
            raise ValueError(
                f"Line {line_num}: indentation must be a multiple of {indent_size} spaces"
            )

        # 解析编号和名称
        match = re.match(r"^(\d{1,2})\s+(.+)$", stripped)
        if not match:
            raise ValueError(
                f"Line {line_num}: expected format 'NN name' but got: {stripped!r}"
            )

        number = int(match.group(1))
        name = match.group(2).strip()
        is_directory = name.endswith("/")

        if is_directory:
            name = name.rstrip("/")

        entries.append(
            TreeEntry(
                level=level,
                number=number,
                name=name,
                is_directory=is_directory,
            )
        )

    return entries


def build_proposed_paths(entries: list[TreeEntry]) -> dict[str, str]:
    """从编号树条目构建 filename_stem → proposed_path 映射。

    Args:
        entries: 解析后的编号树条目。

    Returns:
        {filename_stem: proposed_relative_path} 映射。
    """
    # 追踪当前目录路径
    dir_stack: list[str] = []
    mapping: dict[str, str] = {}

    for entry in entries:
        if entry.is_directory:
            # 更新目录栈
            while len(dir_stack) < entry.level:
                dir_stack.append("")
            if len(dir_stack) > entry.level:
                dir_stack = dir_stack[: entry.level]
            dir_stack.append(entry.name)
        else:
            # 文件条目：确定所属目录
            if entry.level == 0:
                path = entry.name
            else:
                # 取 entry.level 层的目录
                dirs = dir_stack[: entry.level]
                path = "/".join(dirs) + "/" + entry.name if dirs else entry.name

            stem = entry.name.removesuffix(".md")
            mapping[stem] = path

    return mapping
