"""Wiki 文件夹适配器：解析 claude-obsidian 风格的 wiki/ 目录。

将 wiki/ 下的 Markdown 文件（含 YAML frontmatter + [[wiki-links]]）
解析为 KnowledgeNode 列表和关系边提示，供 Bootstrap 或 ingest_nodes 使用。

Frontmatter 约定（兼容 claude-obsidian 格式）：
  type: concept | entity | source | question | comparison | meta
  title: str
  tags: list[str]
  status: seed | developing | mature | evergreen
  related: list["[[xxx]]"]  — wiki-link 格式的关系提示

不包含 type=meta 的文件（索引页）会被跳过，但其 [[wiki-links]] 仍用于关系解析。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from src.common.knowledge_tree.dag.node import KnowledgeNode

logger = logging.getLogger(__name__)

# [[xxx]] wiki-link pattern
_WIKI_LINK_RE = re.compile(r"\[\[([^\]]+)\]\]")


@dataclass
class WikiFolderReport:
    """解析报告。"""

    files_scanned: int = 0
    nodes_created: int = 0
    meta_skipped: int = 0
    errors: list[str] = field(default_factory=list)


@dataclass
class RelationHint:
    """从 [[wiki-link]] 解析出的关系提示。"""

    source_title: str
    target_title: str


def parse_wiki_folder(
    root: Path,
    *,
    skip_meta: bool = True,
    skip_templates: bool = True,
) -> tuple[list[KnowledgeNode], list[RelationHint], WikiFolderReport]:
    """解析 wiki 风格目录为 KnowledgeNode 列表。

    Args:
        root: wiki/ 目录根路径。
        skip_meta: 是否跳过 type=meta 的索引页（仍解析其 wiki-links）。
        skip_templates: 是否跳过 _templates/ 目录。

    Returns:
        (nodes, relation_hints, report) 三元组。
    """
    report = WikiFolderReport()
    nodes: list[KnowledgeNode] = []
    hints: list[RelationHint] = []

    if not root.is_dir():
        report.errors.append(f"Not a directory: {root}")
        return nodes, hints, report

    md_files = sorted(root.rglob("*.md"))
    for fpath in md_files:
        # 跳过模板
        if skip_templates and "_templates" in fpath.parts:
            continue

        report.files_scanned += 1

        try:
            text = fpath.read_text(encoding="utf-8")
        except Exception as e:
            report.errors.append(f"Read error {fpath}: {e}")
            continue

        fm, body = _split_frontmatter(text)
        if fm is None:
            # 无 frontmatter 的文件跳过
            continue

        page_type = fm.get("type", "")
        title = fm.get("title", "") or fpath.stem
        tags = fm.get("tags", [])
        status = fm.get("status", "seed")
        related = fm.get("related", [])
        aliases = fm.get("aliases", [])

        # 提取 wiki-links（从 frontmatter related + body）
        link_targets = set()
        for r in related:
            m = _WIKI_LINK_RE.search(str(r))
            if m:
                link_targets.add(m.group(1))
        for m in _WIKI_LINK_RE.finditer(body):
            link_targets.add(m.group(1))

        # 记录关系提示
        for target in link_targets:
            hints.append(RelationHint(source_title=title, target_title=target))

        # meta 页只提取关系，不创建节点
        if skip_meta and page_type == "meta":
            report.meta_skipped += 1
            continue

        # 相对路径作为 source
        rel_path = str(fpath.relative_to(root))

        # 元数据合并 tags/status/type/aliases
        metadata: dict = {
            "page_type": page_type,
            "tags": tags,
            "status": status,
            "aliases": aliases,
            "domain": fm.get("domain", ""),
            "complexity": fm.get("complexity", ""),
        }
        # 清理空值
        metadata = {k: v for k, v in metadata.items() if v}

        node = KnowledgeNode.create(
            title=title,
            content=body,
            source=f"wiki:{rel_path}",
            summary=_extract_summary(body),
            metadata=metadata,
        )
        nodes.append(node)
        report.nodes_created += 1

    logger.info(
        "Wiki adapter: %d files, %d nodes, %d hints, %d meta-skipped, %d errors",
        report.files_scanned,
        report.nodes_created,
        len(hints),
        report.meta_skipped,
        len(report.errors),
    )
    return nodes, hints, report


def _split_frontmatter(text: str) -> tuple[dict | None, str]:
    """分离 YAML frontmatter 和 body。"""
    if not text.startswith("---"):
        return None, text

    parts = text.split("---", 2)
    if len(parts) < 3:
        return None, text

    try:
        fm = yaml.safe_load(parts[1])
    except yaml.YAMLError:
        return None, text

    if not isinstance(fm, dict):
        return None, text

    return fm, parts[2].strip()


def _extract_summary(body: str, max_chars: int = 200) -> str:
    """从 body 提取第一段非标题文本作为摘要。"""
    for line in body.split("\n"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        return line[:max_chars]
    return ""
