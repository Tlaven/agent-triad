"""元知识自举 E2E 测试 — 验证持久元规则改变 Agent 行为.

重复 F7-F9 的测试场景，但这次元规则通过系统提示通道注入（而非 RAG [相关知识]）。

场景 1: 用户分享信息 → Agent 主动 ingest（元规则触发检测）
场景 2: 元规则指导工具使用模式（操作改进）
场景 3: 元规则列表和删除

用法:
    make dev
    uv run python -u tests/e2e/test_kt_meta_rules_e2e.py
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

# 元规则内容：触发用户信息分享时主动 ingest
META_RULE_INGEST = (
    "当用户在对话中主动分享项目信息、技术细节、配置参数或个人偏好时，"
    "必须主动使用 knowledge_tree_ingest 工具将这些信息存储到知识树。"
    "不要仅口头确认，必须调用 ingest 工具。"
)


def section(title: str):
    print(f"\n{BOLD}{CYAN}{'═' * 60}{R}")
    print(f"{BOLD}{CYAN}  {title}{R}")
    print(f"{BOLD}{CYAN}{'═' * 60}{R}")


async def send(client, thread_id: str, message: str, timeout_s: float = 120) -> dict:
    start = time.perf_counter()
    run = await client.runs.create(
        thread_id=thread_id, assistant_id=ASSISTANT_ID,
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


def result_line(name: str, passed: bool, detail: str, elapsed: float):
    icon = f"{GREEN}✓{R}" if passed else f"{RED}✗{R}"
    print(f"  {icon} {name} ({elapsed:.1f}s)")
    print(f"     {detail}")


async def main():
    print(f"{BOLD}{CYAN}")
    print("╔════════════════════════════════════════════════════════╗")
    print("║   元知识自举 E2E 测试                                ║")
    print("║   验证持久元规则通过系统提示改变 Agent 行为          ║")
    print("╚════════════════════════════════════════════════════════╝")
    print(R)

    from langgraph_sdk import get_client

    client = get_client(url=SERVER_URL)
    try:
        await client.assistants.search()
    except Exception as e:
        print(f"  {RED}Server 不可用: {e}{R}")
        sys.exit(1)

    results = []

    # ── 场景 0: 创建元规则 ──
    section("S0: 创建持久元规则")
    thread0 = await client.threads.create()

    r = await send(client, thread0["thread_id"],
                    f"请使用 knowledge_tree_add_meta_rule 工具创建一条元规则："
                    f"标题'auto_ingest'，内容'{META_RULE_INGEST}'，优先级 10。")
    add_ok = "knowledge_tree_add_meta_rule" in r["tools"]
    result_line("S0: 创建元规则", add_ok,
                f"add_meta_rule={'✓' if add_ok else '✗'} tools={r['tools']}", r["elapsed"])
    results.append({"id": "S0", "name": "创建元规则", "passed": add_ok})

    if not add_ok:
        print(f"  {RED}元规则创建失败，后续测试可能无效{R}")

    # ── 场景 1: 触发检测 — 用户分享信息时 Agent 主动 ingest ──
    # 这是 F7 中失败的场景（RAG 注入无效），现在通过系统提示注入应有效
    section("S1: 触发检测 — 用户分享信息应触发 ingest")

    thread1 = await client.threads.create()

    r = await send(client, thread1["thread_id"],
                    "对了，我发现这个项目用的是 uv 包管理器，开发服务器端口是 2024。")
    ingest_triggered = "knowledge_tree_ingest" in r["tools"]
    result_line("S1: 用户分享信息 → 主动 ingest", ingest_triggered,
                f"ingest={'✓' if ingest_triggered else '✗'} tools={r['tools']}", r["elapsed"])
    if r["ai_text"]:
        preview = r["ai_text"][:120].replace("\n", " ")
        print(f"     {DIM}AI: {preview}{'...' if len(r['ai_text']) > 120 else ''}{R}")
    results.append({"id": "S1", "name": "触发检测：用户分享→ingest", "passed": ingest_triggered})

    # ── 场景 2: 另一种触发 — 技术细节分享 ──
    section("S2: 触发检测 — 技术细节分享")

    thread2 = await client.threads.create()

    r = await send(client, thread2["thread_id"],
                    "我刚才排查了一个问题，SentenceTransformer 加载模型卡死是因为网络问题，"
                    "解决方法是在构造函数里加 local_files_only=True 参数。")
    ingest2 = "knowledge_tree_ingest" in r["tools"]
    result_line("S2: 技术经验分享 → 主动 ingest", ingest2,
                f"ingest={'✓' if ingest2 else '✗'} tools={r['tools']}", r["elapsed"])
    results.append({"id": "S2", "name": "触发检测：技术细节→ingest", "passed": ingest2})

    # ── 场景 3: 元规则列表 ──
    section("S3: 元规则列表")

    thread3 = await client.threads.create()

    r = await send(client, thread3["thread_id"],
                    "请用 knowledge_tree_list_meta_rules 查看当前所有元规则。")
    list_ok = "knowledge_tree_list_meta_rules" in r["tools"]
    has_auto_ingest = "auto_ingest" in r["ai_text"] or "ingest" in r["ai_text"].lower()
    result_line("S3: 列出元规则", list_ok and has_auto_ingest,
                f"list_tool={'✓' if list_ok else '✗'} has_rule={'✓' if has_auto_ingest else '✗'}", r["elapsed"])
    results.append({"id": "S3", "name": "元规则列表", "passed": list_ok and has_auto_ingest})

    # ── 场景 4: 操作改进 — 元规则指导工具使用 ──
    section("S4: 操作改进 — 元规则在工具模式下增强行为")

    thread4 = await client.threads.create()

    r = await send(client, thread4["thread_id"],
                    "请用知识树检索工具搜索关于包管理器的内容。")
    # 期望：先 ingest（如果发现新信息），再 retrieve
    has_kt_tool = any("knowledge_tree" in t for t in r["tools"])
    result_line("S4: 工具模式下的元规则增强", has_kt_tool,
                f"kt_tools={r['tools']}", r["elapsed"])
    results.append({"id": "S4", "name": "操作改进", "passed": has_kt_tool})

    # ── 汇总 ──
    section("汇总")
    passed = sum(1 for r in results if r["passed"])
    total = len(results)
    icon = f"{GREEN}✓{R}" if passed == total else f"{YELLOW}○{R}"
    print(f"  {icon} {passed}/{total} 通过")
    print()
    for r in results:
        status = f"{GREEN}PASS{R}" if r["passed"] else f"{RED}FAIL{R}"
        print(f"    [{status}] {r['id']}: {r['name']}")

    print()
    if passed >= 2 and results[1]["passed"]:
        print(f"  {GREEN}★ F7 突破：元规则通过系统提示成功触发新行为（用户分享→主动 ingest）{R}")
    elif passed >= 3:
        print(f"  {YELLOW}○ 部分突破：元规则在工具模式下有效，但触发检测仍依赖 LLM 判断{R}")
    else:
        print(f"  {RED}✗ 元规则未能改变 Agent 行为{R}")

    out_path = Path(__file__).resolve().parent / "test_kt_meta_rules_e2e_results.json"
    out_path.write_text(json.dumps({"results": results}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n  结果: {out_path}")
    sys.exit(0)


if __name__ == "__main__":
    asyncio.run(main())
