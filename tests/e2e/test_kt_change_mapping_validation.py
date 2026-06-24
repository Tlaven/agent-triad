"""Change Mapping 验证实验 — 语义 embedder 下闭环效果测量.

实验目的：
  1. 用 SiliconFlow API embedder 对种子知识做语义 embedding
  2. 跑完 Change Mapping 闭环（content → anchor → stored_vector）
  3. 对比三种检索模式的质量：
     A. 纯 content_embedding（无结构信号）
     B. stored_vector（α·content + β·structural，结构校准后）
     C. hash embedder（当前基线）
  4. 验证锚点是否形成有意义的语义区域

用法:
    确保 .env 中有 SILICONFLOW_API_KEY
    uv run python -u tests/e2e/test_kt_change_mapping_validation.py
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dotenv import load_dotenv

load_dotenv(override=True)

R = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
CYAN = "\033[36m"
GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"

_PROJECT_ROOT = Path(__file__).resolve().parents[2]


def section(title: str):
    print(f"\n{BOLD}{CYAN}{'═' * 60}{R}")
    print(f"{BOLD}{CYAN}  {title}{R}")
    print(f"{BOLD}{CYAN}{'═' * 60}{R}")


# ─── 查询集：同主题/跨主题/同义/模糊 ──────────────────────────

QUERIES = [
    # 精确查询（应命中对应目录的种子）
    {"id": "Q1", "query": "状态管理 State TypedDict", "expect_dir": "architecture", "desc": "精确：状态管理"},
    {"id": "Q2", "query": "Executor 子进程 通信协议", "expect_dir": "architecture", "desc": "精确：执行器协议"},
    {"id": "Q3", "query": "ReAct 模式 推理和行动", "expect_dir": "patterns", "desc": "精确：ReAct 模式"},
    {"id": "Q4", "query": "Observation 截断 外置", "expect_dir": "patterns", "desc": "精确：Observation"},
    # 语义同义查询（措辞不同但含义相同）
    {"id": "Q5", "query": "怎么管理 Agent 的记忆和数据", "expect_dir": "architecture", "desc": "同义：状态管理"},
    {"id": "Q6", "query": "进程间怎么传递消息", "expect_dir": "architecture", "desc": "同义：通信协议"},
    {"id": "Q7", "query": "Agent 怎么思考和执行的", "expect_dir": "patterns", "desc": "同义：ReAct"},
    {"id": "Q8", "query": "工具输出太长怎么办", "expect_dir": "patterns", "desc": "同义：Observation"},
    # 跨主题查询
    {"id": "Q9", "query": "Python 异常处理最佳实践", "expect_dir": None, "desc": "无关：Python 异常"},
    {"id": "Q10", "query": "React hooks useEffect", "expect_dir": None, "desc": "无关：React"},
]


def setup_kt_with_api_embedder():
    """用 API embedder 创建 KT 并 bootstrap 种子知识。"""
    from src.common.knowledge_tree import KnowledgeTree
    from src.common.knowledge_tree.config import KnowledgeTreeConfig

    # 直接用种子目录作为 root，这样 md_store 能扫描到文件
    kt_root = _PROJECT_ROOT / "workspace" / "knowledge_tree"

    config = KnowledgeTreeConfig(
        markdown_root=kt_root,
        embedder_type="api",
        embedding_model="BAAI/bge-large-zh-v1.5",
        embedding_dimension=1024,
        rag_similarity_threshold=0.6,
    )

    kt = KnowledgeTree(config=config)
    print(f"  Embedder type: {kt.embedder_type}")
    if kt.embedder_type != "api":
        print(f"  {RED}API embedder 未能加载，跳过实验{R}")
        sys.exit(1)

    # Bootstrap：读取文件系统 → 向量索引
    from src.common.knowledge_tree.bootstrap import bootstrap_from_directory

    report = bootstrap_from_directory(
        kt_root, kt.md_store, kt.vector_store, kt.overlay_store, kt.embedder,
    )
    print(f"  Bootstrap: {report.nodes_created} nodes, {report.anchors_computed} anchors")

    # 刷新锚点 + 计算 stored_vectors
    from src.common.knowledge_tree.storage.sync import _refresh_anchor
    from src.common.knowledge_tree.editing.stored_vector import compute_all_stored_vectors

    dirs = kt.md_store.list_directories()
    for d in dirs:
        _refresh_anchor(d, kt.md_store, kt.vector_store)
    print(f"  Directories from md_store: {len(dirs)}")

    sv_count = compute_all_stored_vectors(kt.md_store, kt.vector_store, 0.8, 0.2)
    print(f"  Stored vectors: {sv_count}")

    return kt


def setup_kt_with_hash_embedder():
    """用 hash embedder 创建 KT（对照组）。"""
    from src.common.knowledge_tree import KnowledgeTree
    from src.common.knowledge_tree.config import KnowledgeTreeConfig
    from src.common.knowledge_tree.bootstrap import bootstrap_from_directory
    from src.common.knowledge_tree.storage.sync import _refresh_anchor
    from src.common.knowledge_tree.editing.stored_vector import compute_all_stored_vectors

    # 同样用种子目录作为 root
    kt_root = _PROJECT_ROOT / "workspace" / "knowledge_tree"

    config = KnowledgeTreeConfig(
        markdown_root=kt_root,
        embedder_type="hash",
        embedding_dimension=512,
        rag_similarity_threshold=0.15,
    )

    kt = KnowledgeTree(config=config)
    print(f"  Hash embedder, dim=512")

    report = bootstrap_from_directory(
        kt_root, kt.md_store, kt.vector_store, kt.overlay_store, kt.embedder,
    )
    print(f"  Bootstrap: {report.nodes_created} nodes, {report.anchors_computed} anchors")

    dirs = kt.md_store.list_directories()
    for d in dirs:
        _refresh_anchor(d, kt.md_store, kt.vector_store)

    sv_count = compute_all_stored_vectors(kt.md_store, kt.vector_store, 0.8, 0.2)
    print(f"  Stored vectors: {sv_count}")

    return kt


def run_retrieval_experiment(kt, mode: str) -> list[dict]:
    """在 KT 上跑检索实验。

    mode:
      "content" — 用 content_embedding 检索（similarity_search）
      "stored"  — 用 stored_vector 检索（similarity_search_stored）
    """
    results = []
    for q in QUERIES:
        query_vec = kt.embedder(q["query"])
        start = time.perf_counter()

        if mode == "content":
            hits = kt.vector_store.similarity_search(query_vec, top_k=3, threshold=0.4)
        elif mode == "stored":
            hits = kt.vector_store.similarity_search_stored(query_vec, top_k=3, threshold=0.4)
        else:
            hits = []

        elapsed = time.perf_counter() - start

        # 分析命中目录
        hit_dirs = set()
        top_score = 0.0
        for node_id, score in hits:
            parts = node_id.rsplit("/", 1)
            hit_dirs.add(parts[0] if len(parts) > 1 else "root")
            top_score = max(top_score, score)

        expect_dir = q.get("expect_dir")
        dir_match = expect_dir in hit_dirs if expect_dir else None

        results.append({
            "id": q["id"],
            "query": q["query"],
            "desc": q["desc"],
            "top_score": round(top_score, 4),
            "hit_dirs": sorted(hit_dirs),
            "num_hits": len(hits),
            "dir_match": dir_match,
            "elapsed_ms": round(elapsed * 1000, 1),
        })

    return results


def analyze_anchors(kt) -> dict:
    """分析锚点质量：每个目录的锚点是否有区分度。"""
    anchors = kt.vector_store.get_all_anchors()
    if not anchors:
        return {"total": 0}

    # 计算锚点间相似度矩阵
    from src.common.knowledge_tree.storage.vector_store import cosine_similarity

    anchor_sims = []
    for i, a1 in enumerate(anchors):
        for a2 in anchors[i + 1:]:
            sim = cosine_similarity(a1.anchor_vector, a2.anchor_vector)
            anchor_sims.append({
                "dir_a": a1.directory,
                "dir_b": a2.directory,
                "similarity": round(sim, 4),
            })

    anchor_sims.sort(key=lambda x: x["similarity"], reverse=True)

    avg_sim = sum(s["similarity"] for s in anchor_sims) / len(anchor_sims) if anchor_sims else 0
    max_sim = anchor_sims[0]["similarity"] if anchor_sims else 0
    min_sim = anchor_sims[-1]["similarity"] if anchor_sims else 0

    return {
        "total": len(anchors),
        "directories": [a.directory for a in anchors],
        "file_counts": {a.directory: a.file_count for a in anchors},
        "avg_inter_anchor_sim": round(avg_sim, 4),
        "max_inter_anchor_sim": round(max_sim, 4),
        "min_inter_anchor_sim": round(min_sim, 4),
        "most_similar_pair": anchor_sims[0] if anchor_sims else None,
        "least_similar_pair": anchor_sims[-1] if anchor_sims else None,
    }


def print_retrieval_comparison(content_results, stored_results, hash_results, label: str):
    """打印三种模式的检索对比。"""
    section(f"检索对比 — {label}")

    header = f"{'ID':<4} {'描述':<16} {'Content':>8} {'Stored':>8} {'Hash':>8} {'目录命中':>8}"
    print(f"  {DIM}{header}{R}")

    content_wins = 0
    stored_wins = 0

    for cr, sr in zip(content_results, stored_results):
        hr = next((h for h in hash_results if h["id"] == cr["id"]), None)
        hash_score = hr["top_score"] if hr else 0.0
        hash_match = hr["dir_match"] if hr else None

        # 哪个分数更高
        best = max(cr["top_score"], sr["top_score"])
        c_marker = " ★" if cr["top_score"] == best and best > 0 else ""
        s_marker = " ★" if sr["top_score"] == best and best > 0 else ""

        # 目录命中标记
        cm = "✓" if cr["dir_match"] else ("✗" if cr["dir_match"] is not None else "—")
        sm = "✓" if sr["dir_match"] else ("✗" if sr["dir_match"] is not None else "—")
        hm = "✓" if hash_match else ("✗" if hash_match is not None else "—")
        dir_str = f"C:{cm} S:{sm} H:{hm}"

        print(f"  {cr['id']:<4} {cr['desc']:<16} {cr['top_score']:>8.4f}{c_marker} {sr['top_score']:>8.4f}{s_marker} {hash_score:>8.4f} {dir_str}")

        if cr["dir_match"] and not sr["dir_match"]:
            content_wins += 1
        elif sr["dir_match"] and not cr["dir_match"]:
            stored_wins += 1

    # 汇总
    expect_queries = [q for q in QUERIES if q["expect_dir"] is not None]

    def dir_match_rate(results):
        matched = sum(1 for r in results if r["dir_match"] is True)
        return f"{matched}/{len(expect_queries)}"

    def avg_score(results):
        scores = [r["top_score"] for r in results if r["top_score"] > 0]
        return f"{sum(scores)/len(scores):.4f}" if scores else "N/A"

    print(f"\n  目录命中率: Content={dir_match_rate(content_results)} Stored={dir_match_rate(stored_results)} Hash={dir_match_rate(hash_results)}")
    print(f"  平均分数:   Content={avg_score(content_results)} Stored={avg_score(stored_results)} Hash={avg_score(hash_results)}")
    print(f"  Stored > Content 胜出: {stored_wins} | Content > Stored 胜出: {content_wins}")


def main():
    print(f"{BOLD}{CYAN}")
    print("╔════════════════════════════════════════════════════════╗")
    print("║   Change Mapping 验证实验                            ║")
    print("║   语义 embedder + 锚点闭环 + stored_vector 对比      ║")
    print("╚════════════════════════════════════════════════════════╝")
    print(R)

    # Phase 1: 语义 embedder
    section("Phase 1: 初始化语义 embedder (API)")
    try:
        kt_semantic = setup_kt_with_api_embedder()
    except Exception as e:
        print(f"  {RED}语义 embedder 初始化失败: {e}{R}")
        print(f"  {DIM}请检查 .env 中的 SILICONFLOW_API_KEY{R}")
        sys.exit(1)

    # Phase 2: Hash embedder 对照
    section("Phase 2: 初始化 hash embedder (对照)")
    kt_hash = setup_kt_with_hash_embedder()

    # Phase 3: 锚点质量分析
    section("Phase 3: 锚点质量分析")
    anchor_info = analyze_anchors(kt_semantic)
    print(f"  目录数: {anchor_info['total']}")
    print(f"  目录列表: {anchor_info.get('directories', [])}")
    print(f"  各目录文件数: {json.dumps(anchor_info.get('file_counts', {}), ensure_ascii=False)}")
    print(f"  锚点间平均相似度: {anchor_info.get('avg_inter_anchor_sim', 'N/A')}")
    print(f"  锚点间最高相似度: {anchor_info.get('max_inter_anchor_sim', 'N/A')}")
    print(f"  锚点间最低相似度: {anchor_info.get('min_inter_anchor_sim', 'N/A')}")
    if anchor_info.get("most_similar_pair"):
        p = anchor_info["most_similar_pair"]
        print(f"  最相似目录对: {p['dir_a']} ↔ {p['dir_b']} = {p['similarity']}")
    if anchor_info.get("least_similar_pair"):
        p = anchor_info["least_similar_pair"]
        print(f"  最不同目录对: {p['dir_a']} ↔ {p['dir_b']} = {p['similarity']}")

    # Phase 4: 检索对比
    section("Phase 4: 语义检索 — content vs stored_vector")
    content_results = run_retrieval_experiment(kt_semantic, "content")
    stored_results = run_retrieval_experiment(kt_semantic, "stored")

    section("Phase 5: Hash 检索 (对照组)")
    hash_content_results = run_retrieval_experiment(kt_hash, "content")

    # Phase 6: 打印对比
    print_retrieval_comparison(content_results, stored_results, hash_content_results, "语义 API vs Hash")

    # Phase 7: 结论
    section("结论")

    expect_queries = [q for q in QUERIES if q["expect_dir"] is not None]
    n_expect = len(expect_queries)

    content_hits = sum(1 for r in content_results if r["dir_match"] is True)
    stored_hits = sum(1 for r in stored_results if r["dir_match"] is True)
    hash_hits = sum(1 for r in hash_content_results if r["dir_match"] is True)

    print(f"  精确+同义查询目录命中率 ({n_expect} queries):")
    print(f"    Content (语义):  {content_hits}/{n_expect}")
    print(f"    Stored  (语义+锚点): {stored_hits}/{n_expect}")
    print(f"    Hash (对照):     {hash_hits}/{n_expect}")

    # Change Mapping 是否有效？
    if stored_hits > content_hits:
        print(f"\n  {GREEN}★ Change Mapping 有效：stored_vector 比纯 content 命中更多{R}")
    elif stored_hits == content_hits:
        # 进一步看分数
        c_scores = [r["top_score"] for r in content_results if r["top_score"] > 0]
        s_scores = [r["top_score"] for r in stored_results if r["top_score"] > 0]
        c_avg = sum(c_scores) / len(c_scores) if c_scores else 0
        s_avg = sum(s_scores) / len(s_scores) if s_scores else 0
        if s_avg > c_avg:
            print(f"\n  {GREEN}★ Change Mapping 有提升：stored_vector 分数更高 (avg {s_avg:.4f} > {c_avg:.4f}){R}")
        else:
            print(f"\n  {YELLOW}○ Change Mapping 效果不明显：stored_vector 未超过 content{R}")
    else:
        print(f"\n  {RED}✗ Change Mapping 未显示正效果{R}")

    # 语义 vs hash
    if content_hits > hash_hits:
        print(f"  {GREEN}★ 语义 embedder 明显优于 hash ({content_hits} vs {hash_hits} hits){R}")
    elif content_hits == hash_hits:
        print(f"  {YELLOW}○ 语义和 hash 命中率持平{R}")
    else:
        print(f"  {YELLOW}○ 语义 embedder 未优于 hash{R}")

    # 保存结果
    output = {
        "anchor_analysis": anchor_info,
        "semantic_content": content_results,
        "semantic_stored": stored_results,
        "hash_content": hash_content_results,
    }
    out_path = Path(__file__).resolve().parent / "test_kt_change_mapping_results.json"
    out_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n  详细结果: {out_path}")

    sys.exit(0)


if __name__ == "__main__":
    main()
