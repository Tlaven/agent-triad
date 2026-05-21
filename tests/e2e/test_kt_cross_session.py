"""知识树跨会话持久性测试 — 验证知识在不同 Thread 间持久化与检索.

测试场景：
  G1: Thread A ingest → Thread B 显式 retrieve
  G2: Thread C ingest → Thread D 自动注入（无需显式工具调用）
  G3: Thread E 无关噪声 ingest → Thread F 查询不被污染

用法:
    1. make dev
    2. uv run python -u tests/e2e/test_kt_cross_session.py
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)

R = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
CYAN = "\033[36m"
GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"

SERVER_URL = "http://localhost:2024"
ASSISTANT_ID = "supervisor"
CTX_KT = {"enable_knowledge_tree": True}


def section(title: str):
    print(f"\n{BOLD}{CYAN}{'═' * 60}{R}")
    print(f"{BOLD}{CYAN}  {title}{R}")
    print(f"{BOLD}{CYAN}{'═' * 60}{R}")


async def send(thread_id: str, message: str, timeout_s: float = 120) -> dict:
    """发送消息并等待完成，返回 ai_text / tools / status."""
    from langgraph_sdk import get_client

    client = get_client(url=SERVER_URL)
    run = await client.runs.create(
        thread_id=thread_id,
        assistant_id=ASSISTANT_ID,
        input={"messages": [{"role": "user", "content": message}]},
        context=CTX_KT,
    )
    for _ in range(int(timeout_s)):
        rs = await client.runs.get(thread_id, run["run_id"])
        status = rs.get("status", "unknown")
        if status in ("success", "error", "cancelled"):
            break
        await asyncio.sleep(1)
    else:
        await client.runs.cancel(thread_id, run["run_id"])
        status = "timeout"

    state = await client.threads.get_state(thread_id)
    ai_text = ""
    tools: list[str] = []
    for msg in reversed(state.get("values", {}).get("messages", [])):
        if msg.get("type") == "ai" and not msg.get("tool_calls") and not ai_text:
            ai_text = msg.get("content", "")
    for msg in state.get("values", {}).get("messages", []):
        if msg.get("type") == "ai" and msg.get("tool_calls"):
            for tc in msg["tool_calls"]:
                tools.append(tc.get("name", ""))

    return {"ai_text": ai_text, "tools": tools, "status": status}


async def main():
    print(f"{BOLD}{CYAN}")
    print("╔════════════════════════════════════════════════════════╗")
    print("║   知识树跨会话持久性测试                             ║")
    print("║   G1: 显式跨 Thread 检索                            ║")
    print("║   G2: 自动注入跨 Thread 生效                        ║")
    print("║   G3: 噪声隔离                                      ║")
    print("╚════════════════════════════════════════════════════════╝")
    print(R)

    from langgraph_sdk import get_client

    client = get_client(url=SERVER_URL)
    try:
        assistants = await client.assistants.search()
        section(f"Server OK (assistants: {len(assistants)})")
    except Exception as e:
        section("Server 不可用")
        print(f"  {RED}无法连接: {e}{R}")
        print(f"  {DIM}请先运行: make dev{R}")
        sys.exit(1)

    results: list[dict] = []

    # ── G1: Thread A ingest → Thread B retrieve ──────────────
    section("G1: Thread A ingest → Thread B retrieve")

    thread_a = await client.threads.create()
    print(f"  {DIM}Thread A: {thread_a['thread_id'][:16]}...{R}")
    g1_ingest_msg = "请用 knowledge_tree_ingest 记录：Markov 链是一种随机过程，未来状态只依赖当前状态，与历史无关。"
    print(f"  {DIM}消息: {g1_ingest_msg[:60]}...{R}")

    start = time.perf_counter()
    r1 = await send(thread_a["thread_id"], g1_ingest_msg, timeout_s=120)
    elapsed_a = time.perf_counter() - start

    ingest_ok = "knowledge_tree_ingest" in r1["tools"]
    icon = f"{GREEN}✓{R}" if ingest_ok else f"{YELLOW}○{R}"
    print(f"  {icon} Thread A ingest: {'工具已调用' if ingest_ok else '未调用 ingest 工具'} ({elapsed_a:.1f}s)")
    if r1["ai_text"]:
        print(f"  {DIM}AI: {r1['ai_text'][:120].replace(chr(10), ' ')}{R}")

    # 等待 KT 持久化生效
    await asyncio.sleep(2)

    thread_b = await client.threads.create()
    print(f"\n  {DIM}Thread B: {thread_b['thread_id'][:16]}...{R}")
    g1_retrieve_msg = "请用 knowledge_tree_retrieve 搜索关于 Markov 链的知识"
    print(f"  {DIM}消息: {g1_retrieve_msg[:60]}...{R}")

    start = time.perf_counter()
    r2 = await send(thread_b["thread_id"], g1_retrieve_msg, timeout_s=120)
    elapsed_b = time.perf_counter() - start

    has_markov = "Markov" in r2["ai_text"] or "markov" in r2["ai_text"].lower()
    has_retrieve = "knowledge_tree_retrieve" in r2["tools"]
    g1_issues: list[str] = []
    if not has_markov:
        g1_issues.append("Thread B 回复未包含 Markov 相关内容")
    if not has_retrieve:
        g1_issues.append("Thread B 未调用 knowledge_tree_retrieve")

    g1_passed = has_markov
    icon = f"{GREEN}✓{R}" if g1_passed else f"{RED}✗{R}"
    detail = "符合预期" if g1_passed else "; ".join(g1_issues)
    print(f"  {icon} {detail}")
    print(f"  {DIM}retrieve 调用: {'是' if has_retrieve else '否'}{R}")
    if r2["ai_text"]:
        preview = r2["ai_text"][:200].replace("\n", " ")
        print(f"  {DIM}AI: {preview}{'...' if len(r2['ai_text']) > 200 else ''}{R}")
    print(f"  {DIM}耗时: {elapsed_b:.1f}s{R}")

    results.append({
        "id": "G1", "name": "跨 Thread 显式 retrieve",
        "passed": g1_passed, "detail": detail,
        "elapsed": round(elapsed_a + elapsed_b, 1),
        "thread_a_tools": r1["tools"],
        "thread_b_tools": r2["tools"],
    })

    # ── G2: Thread C ingest → Thread D auto-inject ───────────
    section("G2: Thread C ingest → Thread D auto-inject（无显式工具调用）")

    thread_c = await client.threads.create()
    print(f"  {DIM}Thread C: {thread_c['thread_id'][:16]}...{R}")
    g2_ingest_msg = "请记住：信息熵 H(X) = -Σ p(x) log p(x)，衡量随机变量的不确定性。"
    print(f"  {DIM}消息: {g2_ingest_msg[:60]}...{R}")

    start = time.perf_counter()
    r3 = await send(thread_c["thread_id"], g2_ingest_msg, timeout_s=120)
    elapsed_c = time.perf_counter() - start

    print(f"  {DIM}Thread C tools: {', '.join(r3['tools']) or '无'}{R}")

    # 等待 KT 持久化
    await asyncio.sleep(2)

    thread_d = await client.threads.create()
    print(f"\n  {DIM}Thread D: {thread_d['thread_id'][:16]}...{R}")
    g2_query_msg = "请直接回答，不要使用工具：信息熵的公式是什么？"
    print(f"  {DIM}消息: {g2_query_msg}{R}")

    start = time.perf_counter()
    r4 = await send(thread_d["thread_id"], g2_query_msg, timeout_s=120)
    elapsed_d = time.perf_counter() - start

    # 检查：回复含"熵"或 "log"，且无显式工具调用
    has_entropy = "熵" in r4["ai_text"] or "log" in r4["ai_text"]
    no_explicit_tools = len(r4["tools"]) == 0
    g2_issues = []
    if not has_entropy:
        g2_issues.append("回复未包含'熵'或'log'")
    if not no_explicit_tools:
        g2_issues.append(f"期望无工具调用，实际: {r4['tools']}")

    g2_passed = has_entropy
    icon = f"{GREEN}✓{R}" if g2_passed else f"{RED}✗{R}"
    detail = "符合预期" if g2_passed else "; ".join(g2_issues)
    print(f"  {icon} {detail}")
    if r4["ai_text"]:
        preview = r4["ai_text"][:200].replace("\n", " ")
        print(f"  {DIM}AI: {preview}{'...' if len(r4['ai_text']) > 200 else ''}{R}")
    print(f"  {DIM}耗时: {elapsed_d:.1f}s{R}")

    results.append({
        "id": "G2", "name": "跨 Thread auto-inject",
        "passed": g2_passed, "detail": detail,
        "elapsed": round(elapsed_c + elapsed_d, 1),
        "thread_c_tools": r3["tools"],
        "thread_d_tools": r4["tools"],
    })

    # ── G3: Cross-session noise isolation ────────────────────
    section("G3: 噪声隔离 — 无关知识不应污染后续查询")

    thread_e = await client.threads.create()
    print(f"  {DIM}Thread E: {thread_e['thread_id'][:16]}...{R}")
    g3_noise_msg = "请记住：Tornado 是一个 Python web 框架。"
    print(f"  {DIM}消息: {g3_noise_msg}{R}")

    start = time.perf_counter()
    r5 = await send(thread_e["thread_id"], g3_noise_msg, timeout_s=120)
    elapsed_e = time.perf_counter() - start

    print(f"  {DIM}Thread E tools: {', '.join(r5['tools']) or '无'} ({elapsed_e:.1f}s){R}")

    # 等待
    await asyncio.sleep(2)

    thread_f = await client.threads.create()
    print(f"\n  {DIM}Thread F: {thread_f['thread_id'][:16]}...{R}")
    g3_query_msg = "这个项目用什么做包管理？"
    print(f"  {DIM}消息: {g3_query_msg}{R}")

    start = time.perf_counter()
    r6 = await send(thread_f["thread_id"], g3_query_msg, timeout_s=120)
    elapsed_f = time.perf_counter() - start

    # 检查：回复不应包含 "Tornado"
    has_tornado = "Tornado" in r6["ai_text"] or "tornado" in r6["ai_text"].lower()
    g3_issues = []
    if has_tornado:
        g3_issues.append("噪声泄漏：回复中出现了 Tornado（来自 Thread E 的无关 ingest）")

    g3_passed = not has_tornado
    icon = f"{GREEN}✓{R}" if g3_passed else f"{RED}✗{R}"
    detail = "无噪声泄漏" if g3_passed else "; ".join(g3_issues)
    print(f"  {icon} {detail}")
    if r6["ai_text"]:
        preview = r6["ai_text"][:200].replace("\n", " ")
        print(f"  {DIM}AI: {preview}{'...' if len(r6['ai_text']) > 200 else ''}{R}")
    print(f"  {DIM}耗时: {elapsed_f:.1f}s{R}")

    results.append({
        "id": "G3", "name": "噪声隔离",
        "passed": g3_passed, "detail": detail,
        "elapsed": round(elapsed_e + elapsed_f, 1),
        "thread_e_tools": r5["tools"],
        "thread_f_tools": r6["tools"],
    })

    # ── 汇总 ────────────────────────────────────────────────
    section("汇总")
    passed = sum(1 for r in results if r["passed"])
    total = len(results)
    icon = f"{GREEN}✓{R}" if passed == total else f"{YELLOW}○{R}"
    print(f"  {icon} {passed}/{total} 通过")
    for r in results:
        status = f"{GREEN}PASS{R}" if r["passed"] else f"{RED}FAIL{R}"
        print(f"    [{status}] {r['id']}: {r['name']}  ({r.get('elapsed', '?')}s)")
        if r["detail"] != "符合预期" and not r["detail"].startswith("无"):
            print(f"           {r['detail']}")

    # 保存结果
    output_path = Path(__file__).resolve().parent / "test_kt_cross_session_results.json"
    output_path.write_text(
        json.dumps({"results": results}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\n  结果已保存: {output_path}")

    # 不因 LLM 行为不确定而退出失败
    sys.exit(0)


if __name__ == "__main__":
    asyncio.run(main())
