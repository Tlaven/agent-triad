"""一次性 KT 垃圾节点清理脚本。

DELETE 白名单（spec §3 已人工核实）：10 个目录共 65 节点。
不靠关键词扫描——避免误删。脚本调 KnowledgeTree.delete_node 一站式删除，
删完 kt.save(force=True) 刷新 .vector_index.json。

Usage:
  uv run python scripts/cleanup_kt.py --dry-run       # 仅列出待删
  uv run python scripts/cleanup_kt.py --diff           # 与预期清单对比
  uv run python scripts/cleanup_kt.py --yes            # 真删
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# 允许脚本直接 uv run python scripts/xxx.py 时 import src 包
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# ruff: noqa: T201 — 脚本使用 print 输出进度

# DELETE 白名单：10 个目录（spec §3 已人工核实）
DELETE_DIRECTORIES = {
    "these_blocking_operations",
    "executor_executor_blockin",
    "executor_executor_typeerr",
    "executor_echo_executor_te",
    "created_file_hello_py_wit",
    "python_open_tmp_test_txt_",
    "1_data_2_data_users_j",
    "step_1",
    "step_1_nonexistent_file_y",
    "2026-07-02-001_t3_-_2026-",
}

# KEEP 白名单（spec §3）：以下目录**不**删，含真实知识
KEEP_DIRECTORIES = {
    "architecture",
    "conventions",
    "patterns",
    "setup",
    "meta_rules",
    "misc",
    "ingest",
    "knowledge_tree_ingest",
    "knowledge_tree_retrieve",
}

# 预期 DELETE 节点总数（spec §3 核实：91 → 26）
EXPECTED_DELETE_COUNT = 65


def list_nodes_in_delete_dirs(kt: object) -> list[str]:
    """收集 DELETE 白名单目录下所有 node_id。"""
    nodes: list[str] = []
    all_dirs = kt.md_store.list_directories()  # type: ignore[attr-defined]
    for d in all_dirs:
        if d in DELETE_DIRECTORIES:
            nodes.extend(kt.md_store.get_directory_files(d))  # type: ignore[attr-defined]
    return nodes


def cmd_dry_run(kt: object) -> int:
    nodes = list_nodes_in_delete_dirs(kt)
    print(f"[dry-run] 待删 {len(nodes)} 个节点，分布在以下目录:")
    for d in sorted(DELETE_DIRECTORIES):
        files = kt.md_store.get_directory_files(d)  # type: ignore[attr-defined]
        if files:
            print(f"  {d}/ ({len(files)})")
            for f in files:
                print(f"    - {f}")
    keep_count = len(kt.md_store.list_node_ids()) - len(nodes)  # type: ignore[attr-defined]
    print(f"\n[KEEP] {keep_count} 个节点将保留")
    return 0


def cmd_diff(kt: object) -> int:
    """与硬编码预期集合对比。返回 0 表示零偏差。"""
    nodes = list_nodes_in_delete_dirs(kt)
    actual_dirs: set[str] = set()
    for n in nodes:
        d = n.rsplit("/", 1)[0] if "/" in n else ""
        if d:
            actual_dirs.add(d)
    expected_dirs = set(DELETE_DIRECTORIES)

    extra = actual_dirs - expected_dirs
    missing = expected_dirs - actual_dirs
    actual_count = len(nodes)
    count_diff = actual_count - EXPECTED_DELETE_COUNT

    print(f"期望目录: {sorted(expected_dirs)}")
    print(f"实际命中: {sorted(actual_dirs)}")
    print(f"额外目录（白名单应补）: {sorted(extra) or '<none>'}")
    print(f"预期缺失（白名单已空）: {sorted(missing) or '<none>'}")
    print(f"期望节点数: {EXPECTED_DELETE_COUNT}，实际: {actual_count}，差: {count_diff:+d}")

    if extra or missing or count_diff != 0:
        print("偏差非零，请检查白名单或 KT 状态。")
        return 1
    print("零偏差。")
    return 0


def cmd_yes(kt: object) -> int:
    nodes = list_nodes_in_delete_dirs(kt)
    if not nodes:
        print("无节点可删。")
        return 0
    print(f"开始删除 {len(nodes)} 个节点...")
    ok_count = 0
    err_count = 0
    for i, node_id in enumerate(nodes, 1):
        result = kt.delete_node(node_id)  # type: ignore[attr-defined]
        if result.get("ok"):
            ok_count += 1
            print(f"  [{i}/{len(nodes)}] deleted: {node_id}")
        else:
            err_count += 1
            print(f"  [{i}/{len(nodes)}] FAILED: {node_id} -- {result.get('errors')}")
    saved = kt.save(force=True)  # type: ignore[attr-defined]
    remaining = len(kt.md_store.list_node_ids())  # type: ignore[attr-defined]
    print(f"\n清理完成：删除 {ok_count} 个，失败 {err_count} 个。")
    print(f"向量索引保存: {'ok' if saved else 'FAILED'}")
    print(f"剩余节点: {remaining}")
    return 0 if err_count == 0 else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="KT 垃圾节点清理脚本")
    g = parser.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true", help="仅列出待删节点")
    g.add_argument("--diff", action="store_true", help="与硬编码预期清单对比")
    g.add_argument("--yes", action="store_true", help="真删")
    args = parser.parse_args()

    from src.common.knowledge_tree import get_or_create_kt
    from src.common.knowledge_tree.config import KnowledgeTreeConfig

    config = KnowledgeTreeConfig()
    kt = get_or_create_kt(config)
    # bootstrap 是幂等的，确保锚点 / 索引已就绪
    kt.bootstrap()

    if args.dry_run:
        return cmd_dry_run(kt)
    if args.diff:
        return cmd_diff(kt)
    if args.yes:
        return cmd_yes(kt)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())