"""批量摄入历史 known issues 为失败教训节点。

数据源：logs/probes/state.json:known_issues_found
摄入路径：get_or_create_kt() + kt.ingest(metadata 含 node_type=experience, executor_status=failed)
检索侧：失败教训节点 inject 时自动加 [失败教训] 前缀（决策32）。

Usage: uv run python scripts/ingest_known_issues.py [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# 允许脚本直接 uv run python scripts/xxx.py 时 import src 包
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# ruff: noqa: T201 — 脚本使用 print 输出进度

STATE_PATH = Path("logs/probes/state.json")


def load_known_issues() -> list[dict]:
    if not STATE_PATH.exists():
        print(f"数据源不存在: {STATE_PATH}", file=sys.stderr)
        return []
    data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    issues = data.get("known_issues_found", [])
    return issues if isinstance(issues, list) else []


def _make_title(issue: dict) -> str:
    signal = issue.get("signal", "unknown")
    desc = issue.get("description", "")
    head = desc[:50].replace("\n", " ")
    return f"[{signal}] {head}"


def _make_text(issue: dict) -> str:
    session = issue.get("session", "?")
    turn = issue.get("turn", "?")
    ts = issue.get("ts", "")
    signal = issue.get("signal", "unknown")
    desc = issue.get("description", "")
    return (
        f"## 失败教训（{session} t{turn}）\n\n"
        f"- 时间: {ts}\n"
        f"- 信号: {signal}\n\n"
        f"### 描述\n\n{desc}\n\n"
        f"### 教训\n\n此为探测中发现的 Agent 行为缺陷，记录为失败教训，"
        f"供后续会话检索时以 [失败教训] 前缀注入，避免重蹈覆辙。"
    )


def _make_trigger(issue: dict) -> str:
    return f"known_issue:{issue.get('session', '?')}:t{issue.get('turn', '?')}"


def ingest_one(kt: object, issue: dict, dry_run: bool) -> int:
    if dry_run:
        print(f"[dry-run] would ingest: {_make_title(issue)}")
        return 0
    report = kt.ingest(  # type: ignore[attr-defined]
        _make_text(issue),
        trigger=_make_trigger(issue),
        source="auto:known_issues_batch",
        metadata={
            "node_type": "experience",
            "executor_status": "failed",
            "batch_source": "known_issues_2026_07",
            "signal": issue.get("signal", ""),
            "session": issue.get("session", ""),
            "turn": issue.get("turn", ""),
        },
    )
    return getattr(report, "nodes_ingested", 0)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    issues = load_known_issues()
    if not issues:
        print("无 known issues 可摄入。")
        return 0

    if args.dry_run:
        total = 0
        for issue in issues:
            total += ingest_one(None, issue, dry_run=True)  # type: ignore[arg-type]
        print(f"[dry-run] 共 {len(issues)} 条 known issues 待摄入。")
        return 0

    from src.common.knowledge_tree import get_or_create_kt
    from src.common.knowledge_tree.config import KnowledgeTreeConfig

    kt = get_or_create_kt(KnowledgeTreeConfig())
    ingested = 0
    for issue in issues:
        try:
            ingested += ingest_one(kt, issue, dry_run=False)
        except Exception as e:
            print(
                f"摄入失败 {issue.get('session', '?')}:t{issue.get('turn', '?')}: {e}",
                file=sys.stderr,
            )
    print(f"完成：摄入 {ingested} 个节点（来自 {len(issues)} 条 known issues）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
