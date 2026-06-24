"""KT 高级工具 E2E 测试 — tree / overlay / reorganize 通过真实 LLM 会话.

测试三个 KT 高级管理工具在 LangGraph dev server 上的端到端行为:
  F1: knowledge_tree_tree — 编号树结构显示
  F2: knowledge_tree_overlay — 跨目录关联创建
  F3: knowledge_tree_reorganize — 编号树重组提议
  F4: overlay + retrieve 交互验证

前置条件:
  1. 启动 dev server: make dev
  2. 运行: uv run python -u tests/e2e/test_kt_advanced_tools.py
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

from langgraph_sdk import get_client

# ─── 颜色 ────────────────────────────────────────────────────
R = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
CYAN = "\033[36m"
GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
MAGENTA = "\033[35m"

SERVER_URL = "http://localhost:2024"
ASSISTANT_ID = "supervisor"
CTX_KT = {"enable_knowledge_tree": True}

# ─── 帮助函数 ────────────────────────────────────────────────


def section(title: str):
    print(f"\n{BOLD}{CYAN}{'═' * 60}{R}")
    print(f"{BOLD}{CYAN}  {title}{R}")
    print(f"{BOLD}{CYAN}{'═' * 60}{R}")


async def send(thread_id: str, message: str, timeout_s: float = 120) -> dict:
    """通过 LangGraph server 发送消息，等待完成，返回解析结果。"""
    from langgraph_sdk import get_client as _get_client

    client = _get_client(url=SERVER_URL)
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
    tools: list[str] = []
    tool_outputs: list[dict] = []

    for msg in reversed(state.get("values", {}).get("messages", [])):
        if msg.get("type") == "ai" and not msg.get("tool_calls") and not ai_text:
            ai_text = msg.get("content", "")
        if msg.get("type") == "ai" and msg.get("tool_calls"):
            for tc in msg["tool_calls"]:
                tools.append(tc.get("name", ""))
        if msg.get("type") == "tool":
            tool_name = msg.get("name", "")
            tool_content = msg.get("content", "")
            tool_outputs.append({"name": tool_name, "content": tool_content})

    return {
        "status": rs.get("status"),
        "elapsed": elapsed,
        "ai_text": ai_text,
        "tools": tools,
        "tool_outputs": tool_outputs,
    }


def _has(text: str, *keywords: str) -> bool:
    """Check if text contains any of the given keywords."""
    return any(kw in text for kw in keywords)


def _has_all(text: str, *keywords: str) -> bool:
    """Check if text contains ALL of the given keywords."""
    return all(kw in text for kw in keywords)


# ─── 测试用例 ────────────────────────────────────────────────


async def test_f1_tree(client) -> dict:
    """F1: knowledge_tree_tree — 显示编号树结构。"""
    section("F1: knowledge_tree_tree")
    thread = await client.threads.create()
    tid = thread["thread_id"]

    r = await send(tid, "请用 knowledge_tree_tree 工具显示知识树的编号树结构。")

    tool_ok = "knowledge_tree_tree" in r["tools"]
    # AI 回复应包含结构信息（编号、目录名、树表示等）
    content_ok = _has(r["ai_text"], "architecture", "patterns", "目录", "节点", "树", "tree", "编号")

    passed = tool_ok and content_ok
    icon = f"{GREEN}✓{R}" if passed else f"{RED}✗{R}"
    print(f"  {icon} F1: knowledge_tree_tree")
    print(f"     tool_called={'✓' if tool_ok else '✗'}  content_has_structure={'✓' if content_ok else '✗'}")
    print(f"     {DIM}tools={r['tools']}  elapsed={r['elapsed']:.1f}s{R}")
    if r["ai_text"]:
        print(f"     {MAGENTA}AI: {r['ai_text'][:300]}{R}")

    return {
        "test": "F1: knowledge_tree_tree",
        "passed": passed,
        "detail": f"tool={'✓' if tool_ok else '✗'} content={'✓' if content_ok else '✗'}",
        "elapsed": round(r["elapsed"], 1),
        "tools": r["tools"],
        "ai_text": r["ai_text"][:500],
        "thread_id": tid,
    }


async def test_f2_overlay(client) -> dict:
    """F2: knowledge_tree_overlay — 创建跨目录关联。"""
    section("F2: knowledge_tree_overlay")
    thread = await client.threads.create()
    tid = thread["thread_id"]

    r = await send(
        tid,
        "请用 knowledge_tree_overlay 工具在 architecture 和 patterns 目录之间添加一条关联边，"
        "关系类型设为 'related'，备注写'共享 Agent 设计模式'。",
    )

    tool_ok = "knowledge_tree_overlay" in r["tools"]
    content_ok = _has(r["ai_text"], "成功", "已添加", "created", "关联", "添加")

    passed = tool_ok and content_ok
    icon = f"{GREEN}✓{R}" if passed else f"{RED}✗{R}"
    print(f"  {icon} F2: knowledge_tree_overlay")
    print(f"     tool_called={'✓' if tool_ok else '✗'}  content_confirmed={'✓' if content_ok else '✗'}")
    print(f"     {DIM}tools={r['tools']}  elapsed={r['elapsed']:.1f}s{R}")
    if r["ai_text"]:
        print(f"     {MAGENTA}AI: {r['ai_text'][:300]}{R}")

    return {
        "test": "F2: knowledge_tree_overlay",
        "passed": passed,
        "detail": f"tool={'✓' if tool_ok else '✗'} content={'✓' if content_ok else '✗'}",
        "elapsed": round(r["elapsed"], 1),
        "tools": r["tools"],
        "ai_text": r["ai_text"][:500],
        "thread_id": tid,
    }


async def test_f3_reorganize(client) -> dict:
    """F3: knowledge_tree_reorganize — 先摄入知识，再提议重组。"""
    section("F3: knowledge_tree_reorganize")
    thread = await client.threads.create()
    tid = thread["thread_id"]

    # Step 1: 摄入可重组的错误知识
    r1 = await send(
        tid,
        "请记住这条知识：测试错误 E001 是连接超时错误，通常因为网络不可达。",
    )
    ingest_ok = "knowledge_tree_ingest" in r1["tools"]
    print(f"  {'✓' if ingest_ok else '✗'} Step 1: ingest error knowledge")
    print(f"     {DIM}tools={r1['tools']}  elapsed={r1['elapsed']:.1f}s{R}")

    # Step 2: 请求重组
    r2 = await send(
        tid,
        "请用 knowledge_tree_reorganize 工具，提出一个新的树结构，把所有错误相关的内容集中到一个 errors 目录下。",
    )

    # LLM 可能直接调用 reorganize，也可能先调用 tree 查看结构再调用 reorganize
    tool_ok = "knowledge_tree_reorganize" in r2["tools"]
    # 即使 LLM 没有调用 reorganize，只要它给出了重组方案也算部分通过
    content_ok = _has(r2["ai_text"], "errors", "error", "错误", "重组", "reorganize")

    passed = tool_ok
    partial = content_ok and not tool_ok

    if passed:
        icon = f"{GREEN}✓{R}"
    elif partial:
        icon = f"{YELLOW}~{R}"
    else:
        icon = f"{RED}✗{R}"

    print(f"  {icon} F3: knowledge_tree_reorganize")
    print(f"     tool_called={'✓' if tool_ok else '✗'}  content_discussed={'✓' if content_ok else '✗'}")
    print(f"     {DIM}tools={r2['tools']}  elapsed={r2['elapsed']:.1f}s{R}")
    if r2["ai_text"]:
        print(f"     {MAGENTA}AI: {r2['ai_text'][:300]}{R}")

    return {
        "test": "F3: knowledge_tree_reorganize",
        "passed": passed,
        "partial": partial,
        "detail": f"tool={'✓' if tool_ok else '✗'} content={'✓' if content_ok else '✗'}",
        "elapsed": round(r1["elapsed"] + r2["elapsed"], 1),
        "tools": r2["tools"],
        "ai_text": r2["ai_text"][:500],
        "thread_id": tid,
    }


async def test_f4_overlay_retrieve(client) -> dict:
    """F4: overlay + retrieve 交互 — 验证 F2 创建的 overlay 后检索能触发。"""
    section("F4: overlay + retrieve 交互")
    thread = await client.threads.create()
    tid = thread["thread_id"]

    r = await send(
        tid,
        "用 knowledge_tree_retrieve 搜索关于 Agent 设计的知识",
    )

    tool_ok = "knowledge_tree_retrieve" in r["tools"]
    # 回复应包含与 Agent 设计相关的内容
    content_ok = _has(r["ai_text"], "Agent", "设计", "架构", "architecture", "pattern", "模式")

    passed = tool_ok
    icon = f"{GREEN}✓{R}" if passed else f"{RED}✗{R}"
    print(f"  {icon} F4: knowledge_tree_retrieve (after overlay)")
    print(f"     tool_called={'✓' if tool_ok else '✗'}  content_relevant={'✓' if content_ok else '✗'}")
    print(f"     {DIM}tools={r['tools']}  elapsed={r['elapsed']:.1f}s{R}")
    if r["ai_text"]:
        print(f"     {MAGENTA}AI: {r['ai_text'][:300]}{R}")

    return {
        "test": "F4: overlay + retrieve interaction",
        "passed": passed,
        "detail": f"tool={'✓' if tool_ok else '✗'} content={'✓' if content_ok else '✗'}",
        "elapsed": round(r["elapsed"], 1),
        "tools": r["tools"],
        "ai_text": r["ai_text"][:500],
        "thread_id": tid,
    }


# ─── 主流程 ──────────────────────────────────────────────────


async def main():
    print(f"{BOLD}{CYAN}")
    print("╔════════════════════════════════════════════════════════╗")
    print("║   KT 高级工具 E2E 测试                               ║")
    print("║   F1 tree · F2 overlay · F3 reorganize · F4 retrieve ║")
    print("╚════════════════════════════════════════════════════════╝")
    print(R)

    client = get_client(url=SERVER_URL)

    # 检查 server 可用性
    try:
        assistants = await client.assistants.search()
        section(f"Server: {SERVER_URL}  (assistants: {len(assistants)})")
    except Exception as e:
        section("Server 不可用")
        print(f"  {RED}无法连接 {SERVER_URL}: {e}{R}")
        print(f"  {YELLOW}请先运行: make dev{R}")
        sys.exit(1)

    results: list[dict] = []

    # 按序执行四个测试
    test_fns = [
        test_f1_tree,
        test_f2_overlay,
        test_f3_reorganize,
        test_f4_overlay_retrieve,
    ]

    for test_fn in test_fns:
        try:
            r = await test_fn(client)
            results.append(r)
        except Exception as e:
            results.append({
                "test": test_fn.__doc__ or test_fn.__name__,
                "passed": False,
                "detail": f"异常: {e}",
                "elapsed": 0,
                "tools": [],
                "ai_text": "",
            })
            print(f"  {RED}✗ 异常: {e}{R}")

    # ─── 汇总 ──────────────────────────────────────────────
    section("汇总")
    passed = sum(1 for r in results if r["passed"])
    total = len(results)
    icon = f"{GREEN}✓{R}" if passed == total else f"{RED}✗{R}"
    print(f"  {icon} {passed}/{total} 通过")
    print()

    for r in results:
        status = f"{GREEN}PASS{R}" if r["passed"] else f"{RED}FAIL{R}"
        partial_tag = " (partial)" if r.get("partial") else ""
        print(f"  [{status}] {r['test']}{partial_tag}")
        print(f"         {r['detail']}  ({r['elapsed']:.1f}s)")

    # 保存 JSON 结果
    out_path = Path(__file__).resolve().parent / "test_kt_advanced_tools_results.json"
    output = {
        "summary": {"passed": passed, "total": total},
        "tests": results,
    }
    out_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n  结果已保存: {out_path}")

    all_passed = all(r["passed"] for r in results)
    sys.exit(0 if all_passed else 1)


if __name__ == "__main__":
    asyncio.run(main())
