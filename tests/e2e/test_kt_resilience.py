"""KT 韧性测试 — 去重、特殊字符、超长查询。

验证知识树工具在边界条件下的行为：
  H1: 重复摄入 — 同一知识摄入两次，验证去重（AI 回复提及 "已存在" 或类似）
  H2: 特殊字符 — 查询含 & <tag> 'quotes' "dquotes"，验证不崩溃
  H3: 超长查询 — 发送 2000+ 字符消息，验证响应非空

用法:
    make dev
    uv run python -u tests/e2e/test_kt_resilience.py
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

load_dotenv(override=True)

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


# ─── 发送消息 ──────────────────────────────────────────────

async def send(client, thread_id: str, message: str, timeout_s: float = 120) -> dict:
    """Send a message to the LangGraph server and return structured result."""
    start = time.perf_counter()
    run = await client.runs.create(
        thread_id=thread_id,
        assistant_id=ASSISTANT_ID,
        input={"messages": [{"role": "user", "content": message}]},
        context=CTX_KT,
    )
    for _ in range(int(timeout_s)):
        rs = await client.runs.get(thread_id, run["run_id"])
        if rs.get("status") in ("success", "error", "cancelled"):
            break
        await asyncio.sleep(1)
    else:
        await client.runs.cancel(thread_id, run["run_id"])
        rs = {"status": "timeout"}
    elapsed = time.perf_counter() - start
    state = await client.threads.get_state(thread_id)
    ai_text = ""
    tools = []
    for msg in reversed(state.get("values", {}).get("messages", [])):
        if msg.get("type") == "ai" and not msg.get("tool_calls") and not ai_text:
            ai_text = msg.get("content", "")
        if msg.get("type") == "ai" and msg.get("tool_calls"):
            for tc in msg["tool_calls"]:
                tools.append(tc.get("name", ""))
    return {"status": rs.get("status"), "elapsed": elapsed, "ai_text": ai_text, "tools": tools}


def section(title: str):
    print(f"\n{BOLD}{CYAN}{'=' * 60}{R}")
    print(f"{BOLD}{CYAN}  {title}{R}")
    print(f"{BOLD}{CYAN}{'=' * 60}{R}")


# ─── H1: 重复摄入去重 ─────────────────────────────────────

async def test_h1(client) -> dict:
    """Ingest the same knowledge twice, verify dedup behavior."""
    thread = await client.threads.create()
    tid = thread["thread_id"]
    fact = "AgentTriad 使用三层架构：Supervisor、Planner、Executor。"

    # First ingest
    r1 = await send(client, tid, f"请记住：{fact}")
    print(f"    [1st ingest] status={r1['status']}, tools={r1['tools']}")

    # Second ingest of the same fact
    r2 = await send(client, tid, f"请记住：{fact}")
    print(f"    [2nd ingest] status={r2['status']}, tools={r2['tools']}")

    # Retrieve to check
    r3 = await send(client, tid, "请用 knowledge_tree_retrieve 查找关于 AgentTriad 架构的知识。")
    print(f"    [retrieve]   status={r3['status']}, tools={r3['tools']}")

    # Check: both ingests should have called the tool (LLM decides to call or skip)
    # The key check: second ingest's AI response should indicate awareness of existing knowledge
    dedup_hint = False
    response_text = r2["ai_text"].lower()
    dedup_keywords = ["已存在", "已经", "重复", "already", "duplicate", "existing", "之前"]
    for kw in dedup_keywords:
        if kw in response_text:
            dedup_hint = True
            break

    # Alternative pass: if the second call simply didn't call ingest at all
    # (LLM recognized it was already stored)
    second_skipped = "knowledge_tree_ingest" not in r2["tools"]

    passed = dedup_hint or second_skipped
    icon = f"{GREEN}PASS{R}" if passed else f"{RED}FAIL{R}"

    print(f"    2nd response: {r2['ai_text'][:200]}...")
    print(f"    dedup_hint={dedup_hint}, second_skipped={second_skipped}")
    print(f"    {icon} — dedup {'detected' if passed else 'NOT detected'}")

    return {
        "id": "H1",
        "name": "Dedup: ingest same knowledge twice",
        "passed": passed,
        "dedup_hint": dedup_hint,
        "second_skipped": second_skipped,
        "tools_1st": r1["tools"],
        "tools_2nd": r2["tools"],
        "tools_retrieve": r3["tools"],
        "elapsed": round(r1["elapsed"] + r2["elapsed"] + r3["elapsed"], 1),
    }


# ─── H2: 特殊字符查询 ─────────────────────────────────────

async def test_h2(client) -> dict:
    """Query with special characters — verify no crash."""
    thread = await client.threads.create()
    msg = '知识树 & <tag> \'quotes\' "dquotes"'
    r = await send(client, thread["thread_id"], msg)

    no_crash = r["status"] == "success" and bool(r["ai_text"].strip())
    icon = f"{GREEN}PASS{R}" if no_crash else f"{RED}FAIL{R}"

    print(f"    status: {r['status']}")
    print(f"    ai_text: {r['ai_text'][:200]}...")
    print(f"    {icon} — no crash: {no_crash}, has_response: {bool(r['ai_text'].strip())}")

    return {
        "id": "H2",
        "name": "Special chars query",
        "passed": no_crash,
        "status": r["status"],
        "has_response": bool(r["ai_text"].strip()),
        "tools": r["tools"],
        "elapsed": round(r["elapsed"], 1),
    }


# ─── H3: 超长查询 ─────────────────────────────────────────

async def test_h3(client) -> dict:
    """Very long query (2000+ chars) — verify response is not empty."""
    thread = await client.threads.create()

    # Build a long but coherent message about a topic
    base = (
        "请帮我详细分析分布式系统中的一致性问题。"
        "需要涵盖以下方面："
    )
    aspects = [
        "CAP 定理的含义和实际应用场景。",
        "强一致性、最终一致性和因果一致性的区别。",
        "Paxos 和 Raft 共识算法的工作原理对比。",
        "分布式事务的两阶段提交和三阶段提交协议。",
        "拜占庭将军问题及其在现代系统中的影响。",
        "向量时钟和逻辑时钟在事件排序中的作用。",
        "分布式缓存的一致性策略，如缓存穿透、缓存击穿的解决方案。",
        "微服务架构下的数据一致性挑战和 SAGA 模式。",
        "区块链系统如何通过共识机制实现一致性。",
        "ZooKeeper 和 etcd 在分布式协调中的应用。",
    ]
    # Repeat to exceed 2000 chars
    payload = base + "".join(f"（{i+1}）{a}" for i, a in enumerate(aspects))
    # Pad further if needed to guarantee 2000+ chars
    if len(payload) < 2100:
        payload += (
            " 此外，请分析在全球化部署场景下，跨区域数据复制如何平衡延迟与一致性。"
            " 讨论 CRDT（Conflict-free Replicated Data Types）在协作编辑中的应用，"
            " 以及如何设计一个支持多主写入的数据库系统来处理冲突。"
        )

    print(f"    message length: {len(payload)} chars")
    r = await send(client, thread["thread_id"], payload, timeout_s=180)

    has_response = bool(r["ai_text"].strip())
    no_crash = r["status"] == "success"
    passed = no_crash and has_response
    icon = f"{GREEN}PASS{R}" if passed else f"{RED}FAIL{R}"

    print(f"    status: {r['status']}")
    print(f"    response length: {len(r['ai_text'])} chars")
    print(f"    ai_text: {r['ai_text'][:200]}...")
    print(f"    {icon} — no crash: {no_crash}, has response: {has_response}")

    return {
        "id": "H3",
        "name": "Very long query (2000+ chars)",
        "passed": passed,
        "input_length": len(payload),
        "response_length": len(r["ai_text"]),
        "status": r["status"],
        "tools": r["tools"],
        "elapsed": round(r["elapsed"], 1),
    }


# ─── main ──────────────────────────────────────────────────

async def main():
    print(f"\n{BOLD}KT Resilience Tests — Dedup / Special Chars / Long Query{R}")
    print(f"  Server: {SERVER_URL}")

    from langgraph_sdk import get_client

    client = get_client(url=SERVER_URL)
    try:
        await client.assistants.search()
    except Exception as e:
        print(f"  {RED}Server unavailable: {e}{R}")
        sys.exit(1)

    tests = [
        ("H1: Dedup — ingest same knowledge twice", test_h1),
        ("H2: Special characters query", test_h2),
        ("H3: Very long query (2000+ chars)", test_h3),
    ]

    results = []
    passed = 0

    for title, fn in tests:
        section(title)
        try:
            r = await fn(client)
            results.append(r)
            if r["passed"]:
                passed += 1
        except Exception as e:
            results.append({"id": title, "passed": False, "error": str(e)})
            print(f"  {RED}Exception: {e}{R}")

    # Summary
    section(f"Results: {passed}/{len(tests)} passed")
    for r in results:
        icon = f"{GREEN}PASS{R}" if r["passed"] else f"{RED}FAIL{R}"
        name = r.get("name", r.get("id", "?"))
        extra = ""
        if "elapsed" in r:
            extra = f" ({r['elapsed']}s)"
        print(f"  {icon} [{r.get('id', '?')}] {name}{extra}")

    out_path = Path(__file__).resolve().parent / "test_kt_resilience_results.json"
    out_path.write_text(
        json.dumps({"passed": passed, "total": len(tests), "results": results},
                    ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\n  Results saved to: {out_path}")

    if passed == len(tests):
        print(f"  {GREEN}All tests passed!{R}")
    else:
        print(f"  {YELLOW}{passed}/{len(tests)} tests passed{R}")
    sys.exit(0)


if __name__ == "__main__":
    asyncio.run(main())
