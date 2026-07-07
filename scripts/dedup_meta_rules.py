"""一次性 meta_rule 重复镜像清理脚本。

背景：bootstrap 流程（src/common/knowledge_tree/bootstrap.py:seed_meta_rules）调
kt.ingest() 时 anchor 相似度把同一 meta_rule 写到了 meta_rules/ 之外的其他目录
（misc/、ingest/、knowledge_tree_retrieve/），产生 content 完全相同（hash embedder
下 sim=1.0）的重复节点。dedup_benchmark.py 离线压测发现 4 对。

策略：保留 meta_rules/ 下的种子版本（下次 bootstrap 仍能 seed），删除其他目录
下的镜像。auto_ingest 对的 priority 不一致（10 vs 5），保留 priority=10
（meta_rules/ 种子值）。镜像版本的 aliases 已手工同步到
meta_rules/auto_ingest.md（其他 3 对 aliases 原本就一致）。

Usage:
  uv run python scripts/dedup_meta_rules.py --dry-run       # 仅列出待删
  uv run python scripts/dedup_meta_rules.py --diff           # 与预期清单对比
  uv run python scripts/dedup_meta_rules.py --yes            # 真删
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# 允许脚本直接 uv run python scripts/xxx.py 时 import src 包
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# ruff: noqa: T201, D103, D415, D401 — 脚本工具放行 print / docstring 规则

# 4 个待删镜像 node_id（content 与 meta_rules/ 下种子版本完全一致）
DELETE_NODES = {
    "misc/当任务目标模糊或涉及不确定的关键假设时_在执行的同时向用户提出 1-2 个澄清问.md",
    "misc/执行失败后重规划前_先检索相关失败经验避免重复踩坑.md",
    "ingest/完成任务后如果发现新的可复用知识_工具用法_配置技巧_排错方法__主动 inge.md",
    "knowledge_tree_retrieve/遇到重复出现的错误模式时_先用 knowledge_tree_retrieve .md",
}

# 对应保留的 meta_rules/ 种子版本（diff 模式校验用）
KEEP_SEEDS = {
    "meta_rules/smart_questioning.md",
    "meta_rules/learn_from_failure.md",
    "meta_rules/auto_ingest.md",
    "meta_rules/check_before_retry.md",
}


def cmd_dry_run(kt: object) -> int:
    nodes = [n for n in DELETE_NODES if _node_exists(kt, n)]
    print(f"[dry-run] 待删 {len(nodes)} 个 meta_rule 镜像节点:")
    for n in nodes:
        print(f"  - {n}")
    print(f"\n[KEEP] meta_rules/ 下 {len(KEEP_SEEDS)} 个种子版本保留")
    return 0


def cmd_diff(kt: object) -> int:
    """与硬编码预期集合对比。返回 0 表示零偏差。"""
    actual = {n for n in DELETE_NODES if _node_exists(kt, n)}
    expected = DELETE_NODES

    missing = expected - actual
    extra = actual - expected

    print(f"期望删除: {len(expected)}")
    print(f"实际命中: {len(actual)}")
    print(f"缺失（已删或路径变）: {sorted(missing) or '<none>'}")
    print(f"额外（白名单应补）: {sorted(extra) or '<none>'}")

    # 验证保留的种子版本仍存在
    missing_seeds = [s for s in KEEP_SEEDS if not _node_exists(kt, s)]
    if missing_seeds:
        print(f"警告：种子版本缺失: {missing_seeds}")

    if missing or extra or missing_seeds:
        print("偏差非零，请检查白名单或 KT 状态。")
        return 1
    print("零偏差。")
    return 0


def cmd_yes(kt: object) -> int:
    nodes = [n for n in DELETE_NODES if _node_exists(kt, n)]
    if not nodes:
        print("无节点可删。")
        return 0
    print(f"开始删除 {len(nodes)} 个 meta_rule 镜像节点...")
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
    remaining_meta = [
        n for n in kt.md_store.list_nodes()  # type: ignore[attr-defined]
        if n.metadata.get("node_type") == "meta_rule"
    ]
    print(f"\n清理完成：删除 {ok_count} 个，失败 {err_count} 个。")
    print(f"向量索引保存: {'ok' if saved else 'FAILED'}")
    print(f"剩余 meta_rule 节点: {len(remaining_meta)}")
    return 0 if err_count == 0 else 1


def _node_exists(kt: object, node_id: str) -> bool:
    return kt.md_store.node_exists(node_id)  # type: ignore[attr-defined]


def main() -> int:
    parser = argparse.ArgumentParser(description="meta_rule 重复镜像清理脚本")
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
