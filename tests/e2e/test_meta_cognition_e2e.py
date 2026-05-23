"""元认知 E2E 验证 — 真实 LLM 任务中观察经验节点创建/检索 + 聪明提问元规则生效.

验证三件事：
  1. 经验节点在 Executor 完成后被自动创建（Entry A 自动摄入）
  2. 经验节点在后续相关查询中被检索到
  3. "聪明提问"元规则在模糊任务时生效（Agent 主动提出澄清问题）

用法:
    make dev
    uv run python -u tests/e2e/test_meta_cognition_e2e.py
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
MAGENTA = "\033[35m"

SERVER_URL = "http://localhost:2024"
ASSISTANT_ID = "supervisor"
CTX_KT = {"enable_knowledge_tree": True}

TIMEOUT_S = 180  # 每个场景的超时


def section(title: str):
    print(f"\n{BOLD}{CYAN}{'═' * 60}{R}")
    print(f"{BOLD}{CYAN}  {title}{R}")
    print(f"{BOLD}{CYAN}{'═' * 60}{R}")


async def send(client, thread_id: str, message: str, timeout_s: float = TIMEOUT_S) -> dict:
    """发送消息并等待完成，返回结构化结果。"""
    start = time.perf_counter()
    run = await client.runs.create(
        thread_id=thread_id,
        assistant_id=ASSISTANT_ID,
        input={"messages": [{"role": "user", "content": message}]},
        context=CTX_KT,
    )
    status = "timeout"
    for _ in range(int(timeout_s)):
        rs = await client.runs.get(thread_id, run["run_id"])
        if rs.get("status") in ("success", "error", "cancelled"):
            status = rs.get("status")
            break
        await asyncio.sleep(1)
    else:
        await client.runs.cancel(thread_id, run["run_id"])

    elapsed = time.perf_counter() - start
    state = await client.threads.get_state(thread_id)
    messages = state.get("values", {}).get("messages", [])

    ai_text = ""
    tools = []
    for msg in reversed(messages):
        if msg.get("type") == "ai":
            if not msg.get("tool_calls") and not ai_text:
                ai_text = msg.get("content", "")
            if msg.get("tool_calls"):
                for tc in msg["tool_calls"]:
                    tools.append(tc.get("name", ""))

    return {
        "status": status,
        "elapsed": elapsed,
        "ai_text": ai_text,
        "tools": tools,
        "messages": messages,
    }


def result_line(name: str, passed: bool, detail: str, elapsed: float):
    icon = f"{GREEN}✓{R}" if passed else f"{RED}✗{R}"
    print(f"  {icon} {name} ({elapsed:.1f}s)")
    print(f"     {detail}")


def show_ai_text(text: str, max_len: int = 200):
    if not text:
        return
    preview = text[:max_len].replace("\n", " ")
    print(f"     {DIM}AI: {preview}{'...' if len(text) > max_len else ''}{R}")


async def main():
    print(f"{BOLD}{MAGENTA}")
    print("╔════════════════════════════════════════════════════════╗")
    print("║   元认知 E2E 真实 LLM 验证                           ║")
    print("║   经验创建 · 经验检索 · 聪明提问元规则              ║")
    print("╚════════════════════════════════════════════════════════╝")
    print(R)

    from langgraph_sdk import get_client

    client = get_client(url=SERVER_URL)
    try:
        await client.assistants.search()
    except Exception as e:
        print(f"  {RED}Server 不可用: {e}{R}")
        print(f"  {DIM}请先运行 make dev{R}")
        sys.exit(1)

    results = []

    # ════════════════════════════════════════════════════════
    # Phase 0: 前置检查 — 元规则是否已种子（含"聪明提问"）
    # ════════════════════════════════════════════════════════
    section("P0: 前置检查 — 元规则种子 + 聪明提问")
    thread0 = await client.threads.create()

    r = await send(client, thread0["thread_id"],
                   "请使用 knowledge_tree_list_meta_rules 查看当前所有元规则。"
                   "列出每条规则的名称和优先级。")
    has_smart_questioning = "聪明提问" in r["ai_text"] or "澄清问题" in r["ai_text"]
    meta_rules_listed = "knowledge_tree_list_meta_rules" in r["tools"]

    result_line("P0: 元规则种子", meta_rules_listed,
                f"list_tool={'✓' if meta_rules_listed else '✗'} | "
                f"聪明提问={'✓' if has_smart_questioning else '?'}",
                r["elapsed"])
    show_ai_text(r["ai_text"], 300)
    results.append({
        "id": "P0", "name": "元规则种子(含聪明提问)",
        "passed": meta_rules_listed,
    })

    # ════════════════════════════════════════════════════════
    # Phase 1: 执行一个会产生"发现"的任务 → 观察经验节点
    # ════════════════════════════════════════════════════════
    section("P1: 执行真实任务 — 应触发经验节点创建")

    thread1 = await client.threads.create()

    # 选一个会产生发现性内容的任务
    # 要求 Executor 在 workspace 中操作并返回发现
    task_prompt = (
        "请在 workspace 目录下创建一个名为 meta_cognition_test.py 的文件，"
        "实现一个简单的 Fibonacci 函数。"
        "创建完成后，用 read_workspace_text_file 读回文件内容确认。"
        "在总结中说明你发现的关键模式或方法。"
    )

    r = await send(client, thread1["thread_id"], task_prompt, timeout_s=300)
    executor_used = "call_executor" in r["tools"]
    task_completed = r["status"] == "success" and len(r["ai_text"]) > 20

    result_line("P1: 任务执行", task_completed,
                f"executor={'✓' if executor_used else '✗'} | "
                f"completed={'✓' if task_completed else '✗'} | "
                f"status={r['status']}",
                r["elapsed"])
    show_ai_text(r["ai_text"], 300)
    results.append({
        "id": "P1", "name": "执行真实任务",
        "passed": task_completed,
    })

    # ════════════════════════════════════════════════════════
    # Phase 2: 检查经验节点是否被创建
    # ════════════════════════════════════════════════════════
    section("P2: 验证经验节点 — 检查是否被自动创建")

    thread2 = await client.threads.create()

    r = await send(client, thread2["thread_id"],
                   "请使用 knowledge_tree_status 查看知识树当前状态，"
                   "包括总节点数和各类型节点数量。"
                   "然后用 knowledge_tree_list 列出所有节点，"
                   "特别关注是否有 node_type 为 experience 的节点。")
    has_experience = "experience" in r["ai_text"].lower() or "经验" in r["ai_text"]
    kt_checked = "knowledge_tree_status" in r["tools"] or "knowledge_tree_list" in r["tools"]

    result_line("P2: 经验节点存在", has_experience,
                f"kt_tools={'✓' if kt_checked else '✗'} | "
                f"experience_found={'✓' if has_experience else '✗'}",
                r["elapsed"])
    show_ai_text(r["ai_text"], 400)
    results.append({
        "id": "P2", "name": "经验节点被创建",
        "passed": has_experience,
    })

    # ════════════════════════════════════════════════════════
    # Phase 3: 经验检索 — 后续相关查询能否命中经验
    # ════════════════════════════════════════════════════════
    section("P3: 经验检索 — 相关查询是否命中经验节点")

    thread3 = await client.threads.create()

    r = await send(client, thread3["thread_id"],
                   "请用 knowledge_tree_retrieve 搜索关于 Fibonacci、"
                   "递归模式、Python 函数实现的内容。"
                   "查看搜索结果中是否包含之前执行任务时沉淀的经验。")
    retrieve_used = "knowledge_tree_retrieve" in r["tools"]
    found_relevant = "fibonacci" in r["ai_text"].lower() or "递归" in r["ai_text"] or "经验" in r["ai_text"]

    result_line("P3: 经验检索命中", retrieve_used and found_relevant,
                f"retrieve={'✓' if retrieve_used else '✗'} | "
                f"relevant={'✓' if found_relevant else '✗'}",
                r["elapsed"])
    show_ai_text(r["ai_text"], 300)
    results.append({
        "id": "P3", "name": "经验检索命中",
        "passed": retrieve_used and found_relevant,
    })

    # ════════════════════════════════════════════════════════
    # Phase 4: 聪明提问元规则 — 模糊任务时 Agent 是否主动澄清
    # ════════════════════════════════════════════════════════
    section("P4: 聪明提问 — 模糊任务是否触发澄清问题")

    thread4 = await client.threads.create()

    # 给一个明确模糊的任务，期望 Agent 在执行同时提出澄清问题
    ambiguous_prompt = (
        "帮我优化一下性能。"
    )

    r = await send(client, thread4["thread_id"], ambiguous_prompt)
    # 聪明提问元规则：在执行的同时提出 1-2 个澄清问题
    asks_clarification = (
        "什么" in r["ai_text"] or "哪个" in r["ai_text"]
        or "请问" in r["ai_text"] or "具体" in r["ai_text"]
        or "澄清" in r["ai_text"] or "确认" in r["ai_text"]
        or "哪些方面" in r["ai_text"] or "是指" in r["ai_text"]
        or "想要" in r["ai_text"] and "优化" in r["ai_text"]
    )

    result_line("P4: 聪明提问生效", asks_clarification,
                f"clarification={'✓' if asks_clarification else '✗'}",
                r["elapsed"])
    show_ai_text(r["ai_text"], 400)
    results.append({
        "id": "P4", "name": "聪明提问元规则生效",
        "passed": asks_clarification,
    })

    # ════════════════════════════════════════════════════════
    # 汇总
    # ════════════════════════════════════════════════════════
    section("汇总")
    passed = sum(1 for r in results if r["passed"])
    total = len(results)
    icon = f"{GREEN}✓{R}" if passed == total else f"{YELLOW}○{R}"
    print(f"  {icon} {passed}/{total} 通过")
    print()
    for r in results:
        status = f"{GREEN}PASS{R}" if r["passed"] else f"{RED}FAIL{R}"
        print(f"    [{status}] {r['id']}: {r['name']}")

    # 关键判断
    print()
    experience_ok = results[2]["passed"]  # P2
    retrieval_ok = results[3]["passed"]   # P3
    questioning_ok = results[4]["passed"] # P4

    if experience_ok and retrieval_ok:
        print(f"  {GREEN}★ 经验闭环验证通过：创建 ✓ → 检索 ✓{R}")
    elif experience_ok:
        print(f"  {YELLOW}○ 经验创建 ✓ 但检索未命中{R}")
    else:
        print(f"  {RED}✗ 经验节点未被创建{R}")

    if questioning_ok:
        print(f"  {GREEN}★ 聪明提问元规则生效：Agent 在模糊任务时主动澄清{R}")
    else:
        print(f"  {YELLOW}○ 聪明提问元规则未明显生效（可能受 LLM 判断影响）{R}")

    # 保存结果
    out_path = Path(__file__).resolve().parent / "test_meta_cognition_e2e_results.json"
    out_path.write_text(
        json.dumps({"results": results, "timestamp": time.strftime("%Y-%m-%d %H:%M:%S")},
                    ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\n  结果: {out_path}")
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    asyncio.run(main())
