"""KT ingest 可靠性测试 — 4 种指令风格触发 knowledge_tree_ingest。

验证不同表达方式下，LLM 是否能可靠地调用 knowledge_tree_ingest 工具。

测试项:
  E1: 隐式 "请记住" — 不提工具名，靠 LLM 自主判断
  E2: 显式工具名 — 用户直接说 "请用 knowledge_tree_ingest"
  E3: 自然分享 — 知识嵌入日常对话，无任何指令词
  E4: ingest + verify — 先记忆再检索验证

用法:
    make dev
    uv run python -u tests/e2e/test_kt_ingest_reliability.py
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


# ─── E1: 隐式 "请记住" ────────────────────────────────────

async def test_e1(client) -> dict:
    """Implicit 'remember' — no tool name mentioned."""
    thread = await client.threads.create()
    msg = "请记住：Zeta 分布是一种重尾概率分布，常用于自然语言处理中的词频建模。"
    r = await send(client, thread["thread_id"], msg)
    ingest_called = "knowledge_tree_ingest" in r["tools"]
    icon = f"{GREEN}PASS{R}" if ingest_called else f"{RED}FAIL{R}"
    print(f"  E1 隐式 '请记住'")
    print(f"    tools: {r['tools']}")
    print(f"    ai_text: {r['ai_text'][:120]}...")
    print(f"    {icon} — knowledge_tree_ingest {'was' if ingest_called else 'was NOT'} called")
    return {
        "id": "E1",
        "name": "Implicit 'remember'",
        "passed": ingest_called,
        "tools": r["tools"],
        "elapsed": round(r["elapsed"], 1),
        "status": r["status"],
    }


# ─── E2: 显式工具名 ────────────────────────────────────────

async def test_e2(client) -> dict:
    """Explicit tool name — user names the tool directly."""
    thread = await client.threads.create()
    msg = (
        "请用 knowledge_tree_ingest 记录以下知识："
        "Kolmogorov-Smirnov 检验用于比较样本分布与参考分布是否不同。"
    )
    r = await send(client, thread["thread_id"], msg)
    ingest_called = "knowledge_tree_ingest" in r["tools"]
    icon = f"{GREEN}PASS{R}" if ingest_called else f"{RED}FAIL{R}"
    print(f"  E2 显式工具名")
    print(f"    tools: {r['tools']}")
    print(f"    ai_text: {r['ai_text'][:120]}...")
    print(f"    {icon} — knowledge_tree_ingest {'was' if ingest_called else 'was NOT'} called")
    return {
        "id": "E2",
        "name": "Explicit tool name",
        "passed": ingest_called,
        "tools": r["tools"],
        "elapsed": round(r["elapsed"], 1),
        "status": r["status"],
    }


# ─── E3: 自然分享 ──────────────────────────────────────────

async def test_e3(client) -> dict:
    """Natural sharing — knowledge embedded in casual conversation."""
    thread = await client.threads.create()
    msg = (
        "对了，我发现 Bayes 估计在样本量小时比最大似然估计更稳定，"
        "因为先验可以防止过拟合。以后可以参考这个经验。"
    )
    r = await send(client, thread["thread_id"], msg)
    ingest_called = "knowledge_tree_ingest" in r["tools"]
    icon = f"{GREEN}PASS{R}" if ingest_called else f"{RED}FAIL{R}"
    print(f"  E3 自然分享")
    print(f"    tools: {r['tools']}")
    print(f"    ai_text: {r['ai_text'][:120]}...")
    print(f"    {icon} — knowledge_tree_ingest {'was' if ingest_called else 'was NOT'} called")
    return {
        "id": "E3",
        "name": "Natural sharing",
        "passed": ingest_called,
        "tools": r["tools"],
        "elapsed": round(r["elapsed"], 1),
        "status": r["status"],
    }


# ─── E4: ingest + verify ───────────────────────────────────

async def test_e4(client) -> dict:
    """Ingest then retrieve — verify knowledge was stored."""
    thread = await client.threads.create()
    msg = (
        "记住这条知识：Laplace 平滑通过加 1 解决零概率问题，"
        "公式是 P(w|c) = (count(w,c)+1)/(count(c)+V)。"
        "然后用 knowledge_tree_retrieve 验证已经存入了。"
    )
    r = await send(client, thread["thread_id"], msg, timeout_s=180)
    ingest_called = "knowledge_tree_ingest" in r["tools"]
    retrieve_called = "knowledge_tree_retrieve" in r["tools"]
    # Check that AI response mentions the stored content
    laplace_mentioned = "laplace" in r["ai_text"].lower() or "平滑" in r["ai_text"]
    all_ok = ingest_called and retrieve_called and laplace_mentioned
    icon = f"{GREEN}PASS{R}" if all_ok else f"{RED}FAIL{R}"
    print(f"  E4 ingest + verify")
    print(f"    tools: {r['tools']}")
    print(f"    ingest: {ingest_called}, retrieve: {retrieve_called}, content_found: {laplace_mentioned}")
    print(f"    ai_text: {r['ai_text'][:200]}...")
    print(f"    {icon} — all checks {'passed' if all_ok else 'FAILED'}")
    return {
        "id": "E4",
        "name": "Ingest + verify",
        "passed": all_ok,
        "ingest_called": ingest_called,
        "retrieve_called": retrieve_called,
        "content_found": laplace_mentioned,
        "tools": r["tools"],
        "elapsed": round(r["elapsed"], 1),
        "status": r["status"],
    }


# ─── main ──────────────────────────────────────────────────

async def main():
    print(f"\n{BOLD}KT Ingest Reliability — 4 Instruction Styles{R}")
    print(f"  Server: {SERVER_URL}")

    from langgraph_sdk import get_client

    client = get_client(url=SERVER_URL)
    try:
        await client.assistants.search()
    except Exception as e:
        print(f"  {RED}Server unavailable: {e}{R}")
        sys.exit(1)

    tests = [
        ("E1: Implicit 'remember'", test_e1),
        ("E2: Explicit tool name", test_e2),
        ("E3: Natural sharing", test_e3),
        ("E4: Ingest + verify", test_e4),
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

    out_path = Path(__file__).resolve().parent / "test_kt_ingest_reliability_results.json"
    out_path.write_text(
        json.dumps({"passed": passed, "total": len(tests), "results": results},
                    ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\n  Results saved to: {out_path}")

    if passed == len(tests):
        print(f"  {GREEN}All tests passed!{R}")
        sys.exit(0)
    else:
        print(f"  {YELLOW}{passed}/{len(tests)} tests passed{R}")
        sys.exit(0)


if __name__ == "__main__":
    asyncio.run(main())
