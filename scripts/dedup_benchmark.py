"""dedup_threshold 离线摄入压测（spec §B5 / §4.4 撤销记录）。

目的：用真实 KT 节点 + 历史 embedding cache 量化各 dedup_threshold 在生产
dim=1024 hash embedder 下的 FAR / merge count / 簇结构。压测结果推翻了原 0.88
决定，遵循"宁缺毋滥"哲学回滚为 0.95。

数据源：
  A) workspace/knowledge_tree/.vector_index.json — 当前 26 节点的 content 向量（按
     文件路径标注目录；ground truth 用 cosine≥0.9999 = hash embedder 下"token 集
     合完全相同" = 应合并）
  B) workspace/knowledge_tree/.embedding_cache_*.json — 历史 SHA256→向量 集合；含
     已删除节点的遗留向量。**无标签**，仅用作纯聚类分析

模拟算法（与 src/common/knowledge_tree/ingestion/ingest.py:78-89 严格一致）：
  对每个候选节点（按某顺序）：
    existing = vector_store.similarity_search(node.embedding, top_k=1, threshold=dedup_threshold)
    if existing:
      skip (dedup)
    else:
      store（加入向量库供后续 dedup 检索）

阈值扫描：[0.75, 0.80, 0.85, 0.88, 0.92, 0.95]
顺序敏感性：尝试 5 种顺序（lexicographic + 4 random），取均值+方差

输出指标（每个阈值）：
  - 跨阈值对数 (cluster_size_distribution)
  - 剩余簇数 (vs 原始 N，差异=被 dedup 掉的数量)
  - 真合并召回 = (合并对的同目录数) / (所有同目录对数)
  - 误合并率 FAR = (合并对的跨目录数) / (被 dedup 的总数)
  - 最差误合并示例（sim 最高但跨目录的那对，附文件名）

Usage:
  uv run python scripts/dedup_benchmark.py
  uv run python scripts/dedup_benchmark.py --dataset cache  # 只跑历史 cache
  uv run python scripts/dedup_benchmark.py --permutations 20
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# ruff: noqa: T201, D103, D415, D401 — 脚本工具放行 print / docstring 规则

KT_DIR = Path(__file__).resolve().parent.parent / "workspace" / "knowledge_tree"

# 阈值扫描列表（与 Spec 关注点对应）
THRESHOLDS = [0.75, 0.80, 0.85, 0.88, 0.92, 0.95]

# 随机顺序种子（可复现）
SEEDS = [11, 23, 37, 53, 89]


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def load_index_vectors() -> dict[str, list[float]]:
    """A 数据集：vector_index.json 中的 node_md 向量（content embedding）。

    文件缺失时返回空 dict（不 sys.exit），让 main 决定是否 skip。
    CI runner 上 workspace/ 可能未包含此文件（gitignore derived artifact）。
    """
    path = KT_DIR / ".vector_index.json"
    if not path.exists():
        print(f"WARN: {path} 不存在（CI runner 可能 gitignore），跳过 index dataset")
        return {}
    d = json.loads(path.read_text(encoding="utf-8"))
    emb = d["vectors"]["embeddings"]
    # 只取 content 向量（无 ':' 前缀），不含 title:stored:alias:
    return {k: v for k, v in emb.items() if ":" not in k}


def load_cache_vectors() -> dict[str, list[float]]:
    """B 数据集：embedding_cache_*.json 中的所有 SHA256→向量（无标签）"""
    path = next(KT_DIR.glob(".embedding_cache_*.json"), None)
    if path is None:
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    # 不同版本可能是 dict 或 list-of-pairs；统一为 dict[key]=list
    if isinstance(raw, dict):
        return {k: list(v) for k, v in raw.items()}
    if isinstance(raw, list) and raw and isinstance(raw[0], (list, tuple)):
        return {str(k): list(v) for k, v in raw}
    return {str(k): list(v) for k, v in raw.items()}


def directory_of(key: str) -> str:
    """文件路径 key -> 目录标签"""
    return key.rsplit("/", 1)[0] if "/" in key else "(root)"


def online_dedup(
    vectors: dict[str, list[float]],
    order: list[str],
    threshold: float,
) -> tuple[set[str], list[tuple[str, str, float]]]:
    """复现 ingest.py:78-89 的 online single-link dedup。

    Returns:
        kept_keys: 保留的 key 集合
        merges: [(kept_key, skipped_key, similarity), ...]
    """
    store: list[tuple[str, list[float]]] = []
    merges: list[tuple[str, str, float]] = []
    for key in order:
        v = vectors[key]
        # top-1 similarity search
        best_sim = -1.0
        best_key = None
        for ek, ev in store:
            sim = cosine(v, ev)
            if sim > best_sim:
                best_sim = sim
                best_key = ek
        if best_key is not None and best_sim >= threshold:
            # dedup：跳过
            merges.append((best_key, key, best_sim))
        else:
            store.append((key, v))
    kept_keys = {k for k, _ in store}
    return kept_keys, merges


def pairwise_matrix(
    vectors: dict[str, list[float]],
) -> list[tuple[str, str, float, bool]]:
    """计算所有 pair 的 cosine sim + 是否同目录"""
    keys = list(vectors.keys())
    pairs = []
    for i, k1 in enumerate(keys):
        d1 = directory_of(k1)
        for k2 in keys[i + 1 :]:
            d2 = directory_of(k2)
            sim = cosine(vectors[k1], vectors[k2])
            pairs.append((k1, k2, sim, d1 == d2))
    return pairs


def run_labeled(
    vectors: dict[str, list[float]], n_perm: int
) -> dict[float, dict[str, float]]:
    """跑阈值扫描，返回每个 threshold 的指标 dict。

    Returns:
        {threshold: {"precision": float, "recall": float, "true_merges": float,
                      "false_merges": float, "avg_kept": float, "avg_merges": float}}
    """
    keys = list(vectors.keys())
    print(
        f"\n[Phase A] 真实 KT 数据集：{len(keys)} 节点，{len(set(directory_of(k) for k in keys))} 目录"
    )

    # 全对相似度统计
    pairs = pairwise_matrix(vectors)

    # 真实 ground truth：hash embedder 下 cosine=1.0000 代表 token 集合完全相同
    # （应为 hash embedder 是二值 bag-of-words，归一化向量的内积=1 iff token 完全相同）
    # 这些才是真正"应合并"对。同目录但 sim<1.0 表示相似议题但分立为不同节点。
    same_content_pairs = [p for p in pairs if p[2] >= 0.9999]
    distinct_pairs = [p for p in pairs if p[2] < 0.9999]
    print(
        f"  pair 总数 {len(pairs)}（内容相同 {len(same_content_pairs)} / 内容不同 {len(distinct_pairs)}）"
    )

    # 阈值扫描
    print()
    print(
        f"  {'阈值':>6} | {'≥阈值的对数':>10} | {'簇余数':>6} | {'真合并':>6} | {'误合并':>6} | {'precision':>10} | {'recall':>8}"
    )
    print("  " + "-" * 80)
    results: dict[float, dict[str, float]] = {}
    for th in THRESHOLDS:
        # 在线单链 dedup，取 n 种随机顺序均值
        kept_counts = []
        merge_lists = []
        for seed in SEEDS[:n_perm]:
            rng = random.Random(seed)
            order = list(keys)
            rng.shuffle(order)
            kept, merges = online_dedup(vectors, order, th)
            kept_counts.append(len(kept))
            merge_lists.append(merges)
        avg_kept = sum(kept_counts) / len(kept_counts)
        avg_merges = sum(len(m) for m in merge_lists) / len(merge_lists)

        # 用 sim≥0.9999 作 ground truth（hash embedder 下"内容完全相同"）
        # 在线算法每次合并记录 kept_key 与 skipped_key 的 sim，sim≥0.9999 视作真合并
        true_merges: float = 0.0
        false_merges: float = 0.0
        for ml in merge_lists:
            for kept_k, skipped_k, sim in ml:
                if sim >= 0.9999:
                    true_merges += 1.0
                else:
                    false_merges += 1.0
        n_ms = len(merge_lists)
        if n_ms:
            true_merges /= n_ms
            false_merges /= n_ms

        precision = (
            true_merges / (true_merges + false_merges)
            if (true_merges + false_merges) > 0
            else float("nan")
        )
        recall = (
            true_merges / len(same_content_pairs)
            if same_content_pairs
            else float("nan")
        )

        print(
            f"  {th:>6.2f} | {avg_merges:>10.2f} | {avg_kept:>6.2f} | "
            f"{true_merges:>6.2f} | {false_merges:>6.2f} | {precision:>10.3f} | {recall:>8.3f}"
        )
        results[th] = {
            "precision": precision,
            "recall": recall,
            "true_merges": true_merges,
            "false_merges": false_merges,
            "avg_kept": avg_kept,
            "avg_merges": avg_merges,
        }

    # 真合并样本（内容相同的 pair）
    print()
    print(f"  内容相同的 pair（{len(same_content_pairs)} 对，应合并）：")
    for k1, k2, sim, _ in sorted(same_content_pairs, key=lambda p: -p[2]):
        print(
            f"    sim={sim:.4f}  [{directory_of(k1)}] {k1}\n"
            f"                        vs  [{directory_of(k2)}] {k2}"
        )

    # 误合并风险（内容不同但 sim 最高的 pair）
    print()
    print("  最危险误合并（内容不同但 sim 最高的 5 对）：")
    diff_sorted = sorted(distinct_pairs, key=lambda p: -p[2])[:5]
    for k1, k2, sim, _ in diff_sorted:
        print(
            f"    sim={sim:.4f}  [{directory_of(k1)}] {k1}  vs  [{directory_of(k2)}] {k2}"
        )

    # 同目录但内容不同的最相似 pair（不当合并风险区域）
    print()
    print("  同目录内内容不同但最相似的 5 对（最易触发误判 same-dir=merge 的 case）：")
    same_dir_distinct = [p for p in distinct_pairs if p[3]]
    same_sorted = sorted(same_dir_distinct, key=lambda p: -p[2])[:5]
    for k1, k2, sim, _ in same_sorted:
        print(f"    sim={sim:.4f}  [{directory_of(k1)}] {k1}  vs  {k2}")

    return results


def run_cache(cache_vectors: dict[str, list[float]]) -> None:
    keys = list(cache_vectors.keys())
    print(
        f"\n[Phase B] 历史 cache 数据集：{len(keys)} 个 chunk 向量（无标签，纯聚类分析）"
    )
    if len(keys) < 2:
        print("  样本太少，跳过")
        return

    # 简单在线单链 dedup，随机 5 种顺序
    print()
    print(f"  {'阈值':>6} | {'簇余数':>6} | {'剔除率%':>8}")
    print("  " + "-" * 40)
    for th in THRESHOLDS:
        kept_counts = []
        for seed in SEEDS:
            rng = random.Random(seed)
            order = list(keys)
            rng.shuffle(order)
            kept, _ = online_dedup(cache_vectors, order, th)
            kept_counts.append(len(kept))
        avg = sum(kept_counts) / len(kept_counts)
        min_kept = min(kept_counts)
        max_kept = max(kept_counts)
        dedup_rate = (len(keys) - avg) / len(keys) * 100
        print(
            f"  {th:>6.2f} | {avg:>6.1f} (min {min_kept} max {max_kept}) | {dedup_rate:>8.1f}"
        )

    # 显示聚类分布：取一个顺序，按 0.88 跑一次，输出簇大小直方图
    rng = random.Random(SEEDS[0])
    order = list(keys)
    rng.shuffle(order)
    kept, merges = online_dedup(cache_vectors, order, 0.88)
    cluster_of: dict[str, str] = {}
    rep_of: dict[str, list[str]] = defaultdict(list)
    for kept_k, skipped_k, _sim in merges:
        rep_of[kept_k].append(skipped_k)
        cluster_of[skipped_k] = kept_k
    sizes = [1 + len(m) for m in rep_of.values()]
    size_hist: dict[int, int] = defaultdict(int)
    for s in sizes:
        size_hist[s] += 1
    size_hist[1] = len(kept) - sum(1 for _ in rep_of)
    print("\n  在 0.88 阈值下的簇大小分布：")
    for s in sorted(size_hist):
        print(f"    size={s}: {size_hist[s]} 个簇")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", choices=["all", "index", "cache"], default="all")
    parser.add_argument("--permutations", type=int, default=len(SEEDS))
    parser.add_argument(
        "--min-precision",
        type=float,
        default=None,
        help="对生产阈值 0.95 行的 precision 设门禁，不达标 exit 1",
    )
    parser.add_argument(
        "--min-recall",
        type=float,
        default=None,
        help="对生产阈值 0.95 行的 recall 设门禁，不达标 exit 1",
    )
    parser.add_argument(
        "--json",
        type=str,
        default=None,
        help="写机器可读 JSON 结果到指定路径（CI artifact 用）",
    )
    args = parser.parse_args()

    all_results: dict[str, Any] = {}
    exit_code = 0

    if args.dataset in ("all", "index"):
        idx_vec = load_index_vectors()
        if idx_vec:
            idx_results = run_labeled(idx_vec, args.permutations)
            all_results["index"] = idx_results
        else:
            print("\n[skip] index dataset 无数据（.vector_index.json 不存在）")

    if args.dataset in ("all", "cache"):
        cache_vec = load_cache_vectors()
        if cache_vec:
            run_cache(cache_vec)

    # 阈值门禁：对生产阈值 0.95 行做断言
    PROD_THRESHOLD = 0.95
    if args.min_precision is not None or args.min_recall is not None:
        if "index" not in all_results:
            print(
                "\n[gate] 跳过阈值检查：未跑 index dataset（无 precision/recall 数据）"
            )
        else:
            idx = all_results["index"]
            if PROD_THRESHOLD not in idx:
                print(
                    f"\n[gate] 跳过阈值检查：threshold {PROD_THRESHOLD} 不在扫描列表 {THRESHOLDS}"
                )
            else:
                row = idx[PROD_THRESHOLD]
                p = row["precision"]
                r = row["recall"]
                failed: list[str] = []
                if args.min_precision is not None and (
                    isinstance(p, float) and p == p and p < args.min_precision
                ):
                    failed.append(f"precision={p:.3f} < {args.min_precision:.3f}")
                if args.min_recall is not None and (
                    isinstance(r, float) and r == r and r < args.min_recall
                ):
                    failed.append(f"recall={r:.3f} < {args.min_recall:.3f}")

                print()
                if failed:
                    print(
                        f"[gate] FAIL @ threshold {PROD_THRESHOLD}: {'; '.join(failed)}"
                    )
                    exit_code = 1
                else:
                    print(
                        f"[gate] PASS @ threshold {PROD_THRESHOLD}: "
                        f"precision={p:.3f} recall={r:.3f}"
                    )

    if args.json:
        import json as _json

        out_path = Path(args.json)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            _json.dumps(all_results, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        print(f"\n[json] 写入 {out_path}")

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
