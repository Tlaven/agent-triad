"""通过 LangGraph dev server 测试知识树工具（真实 ASGI 环境）.

用法:
    1. 先启动 dev server: make dev
    2. 再运行本脚本: uv run python test_kt_via_server.py

会经过完整的 ASGI 中间件，能捕获阻塞调用等问题。
"""

from __future__ import annotations

import asyncio
import json
import shutil
import sys
import time
from pathlib import Path

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)

# ─── 颜色 ────────────────────────────────────────────────────
R = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
CYAN = "\033[36m"
GREEN = "\033[32m"
RED = "\033[31m"
MAGENTA = "\033[35m"
YELLOW = "\033[33m"

SERVER_URL = "http://localhost:2024"
ASSISTANT_ID = "supervisor"

KT_ROOT = "workspace/kt_server_test"


def section(title: str):
    print(f"\n{BOLD}{CYAN}{'═' * 55}{R}")
    print(f"{BOLD}{CYAN}  {title}{R}")
    print(f"{BOLD}{CYAN}{'═' * 55}{R}")


# ─── 测试用例 ────────────────────────────────────────────────
TEST_CASES = [
    {
        "name": "T1: 查看知识树状态（空树→自动bootstrap）",
        "message": "请查看知识树当前状态。",
    },
    {
        "name": "T2: 检索知识（状态管理）",
        "message": "帮我检索关于状态管理的知识。",
    },
    {
        "name": "T3: 摄入新知识",
        "message": "请记录这条知识：发现向量搜索的相似度阈值最佳实践是0.7，过低会引入噪声，过高会漏检。",
    },
    {
        "name": "T4: 检索新摄入的知识",
        "message": "检索关于向量搜索阈值最佳实践的知识。",
    },
    {
        "name": "T5: 再次查看状态",
        "message": "查看知识树状态。",
    },
]


async def send_message(client, thread_id: str, message: str) -> dict:
    """通过 LangGraph server 发送消息，等待完成，返回最终状态。"""
    start = time.perf_counter()

    # 创建 run 并等待完成
    run = await client.runs.create(
        thread_id=thread_id,
        assistant_id=ASSISTANT_ID,
        input={"messages": [{"role": "user", "content": message}]},
        context={
            "enable_knowledge_tree": True,
            "knowledge_tree_root": KT_ROOT,
        },
    )

    # 轮询等待 run 完成
    while True:
        run_status = await client.runs.get(thread_id, run["run_id"])
        status = run_status.get("status", "unknown")
        if status in ("success", "error", "cancelled"):
            break
        await asyncio.sleep(1)

    elapsed = time.perf_counter() - start

    # 获取最终状态
    state = await client.threads.get_state(thread_id)

    return {
        "run_status": status,
        "elapsed": elapsed,
        "state": state,
        "error": run_status.get("error") if status == "error" else None,
    }


def analyze_state(state: dict) -> dict:
    """从图状态中提取工具调用信息。"""
    messages = state.get("values", {}).get("messages", [])

    tools_called: list[str] = []
    tool_outputs: list[dict] = []
    ai_responses: list[str] = []

    for msg in messages:
        msg_type = msg.get("type", "")

        if msg_type == "ai":
            # AI 消息可能有 tool_calls
            tcs = msg.get("tool_calls", [])
            for tc in tcs:
                tools_called.append(tc.get("name", "?"))
            content = msg.get("content", "")
            if content and not tcs:
                ai_responses.append(content[:400])

        elif msg_type == "tool":
            name = msg.get("name", "tool")
            content = msg.get("content", "")
            try:
                parsed = json.loads(content) if isinstance(content, str) else {}
            except (json.JSONDecodeError, TypeError):
                parsed = {"raw": str(content)[:200]}
            tool_outputs.append({"tool": name, "result": parsed})

    return {
        "tools_called": tools_called,
        "tool_outputs": tool_outputs,
        "ai_responses": ai_responses,
    }


async def run_all_tests() -> list[dict]:
    from langgraph_sdk import get_client

    all_results: list[dict] = []

    # 准备种子知识
    seed_dir = Path(KT_ROOT)
    if seed_dir.exists():
        shutil.rmtree(seed_dir)
    seed_dir.mkdir(parents=True)

    from src.common.knowledge_tree.dag.node import KnowledgeNode

    seeds = {
        "development/state.md": ("状态管理", "LangGraph 使用 TypedDict 定义状态模式，通过 StateGraph 构建执行图。状态是不可变的。"),
        "development/tools.md": ("工具调用", "LangGraph 通过 ToolNode 执行工具。支持同步和异步工具调用。"),
        "patterns/react.md": ("ReAct 模式", "ReAct 模式结合推理和行动，逐步解决复杂问题。Agent 在每一步都先推理再行动。"),
    }
    for rel, (title, content) in seeds.items():
        node = KnowledgeNode.create(node_id=rel, title=title, content=content, source="seed")
        p = seed_dir / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(node.to_frontmatter_md(), encoding="utf-8")

    section(f"种子: {len(seeds)} 篇 → {seed_dir}")

    # 连接 server
    client = get_client(url=SERVER_URL)

    # 检查 server 是否可用
    try:
        assistants = await client.assistants.search()
        section(f"Server: {SERVER_URL}  (assistants: {len(assistants)})")
    except Exception as e:
        section("Server 不可用")
        print(f"  {RED}无法连接 {SERVER_URL}: {e}{R}")
        print(f"  {YELLOW}请先运行: make dev{R}")
        sys.exit(1)

    for tc in TEST_CASES:
        section(tc["name"])
        print(f"  {DIM}消息: {tc['message']}{R}")

        # 每个测试用独立 thread，避免上下文干扰
        thread = await client.threads.create()

        try:
            result = await send_message(client, thread["thread_id"], tc["message"])
        except Exception as e:
            all_results.append({
                "name": tc["name"],
                "passed": False,
                "detail": f"异常: {e}",
                "elapsed": 0,
                "tools_called": [],
                "tool_outputs": [],
                "error": str(e),
            })
            print(f"  {RED}✗ 异常: {e}{R}")
            continue

        status = result["run_status"]
        elapsed = result["elapsed"]
        error = result["error"]

        if status == "error":
            all_results.append({
                "name": tc["name"],
                "passed": False,
                "detail": f"run error: {error}",
                "elapsed": elapsed,
                "tools_called": [],
                "tool_outputs": [],
                "error": str(error)[:500],
            })
            print(f"  {RED}✗ Run 错误: {str(error)[:300]}{R}")
            continue

        # 分析状态
        analysis = analyze_state(result["state"])
        tools = analysis["tools_called"]
        outputs = analysis["tool_outputs"]
        ai_texts = analysis["ai_responses"]

        # 简单判定：知识树工具是否被调用
        kt_tools = [t for t in tools if t.startswith("knowledge_tree_")]
        passed = len(kt_tools) > 0
        detail = f"KT 工具: {kt_tools}" if kt_tools else "未调用 KT 工具"

        icon = f"{GREEN}✓{R}" if passed else f"{RED}✗{R}"
        print(f"  {icon} {detail}")
        print(f"  {DIM}  耗时={elapsed:.1f}s  所有工具={tools}{R}")

        for out in outputs:
            name = out["tool"]
            r = out["result"]
            summary = json.dumps(r, ensure_ascii=False)[:200]
            print(f"  {DIM}  {name} → {summary}{R}")

        for ai in ai_texts[:1]:
            if ai.strip():
                print(f"  {MAGENTA}  AI: {ai[:200]}{R}")

        all_results.append({
            "name": tc["name"],
            "passed": passed,
            "detail": detail,
            "elapsed": round(elapsed, 1),
            "tools_called": tools,
            "tool_outputs": outputs,
        })

    return all_results


def print_summary(results: list[dict]):
    section("汇总")
    passed = sum(1 for r in results if r["passed"])
    total = len(results)
    icon = f"{GREEN}✓{R}" if passed == total else f"{RED}✗{R}"
    print(f"  {icon} {passed}/{total} 通过")

    for r in results:
        status = f"{GREEN}PASS{R}" if r["passed"] else f"{RED}FAIL{R}"
        print(f"  [{status}] {r['name']}")
        print(f"         {r['detail']}  ({r['elapsed']:.1f}s)")
        if r.get("error"):
            print(f"         {RED}error: {r['error'][:200]}{R}")

    output = {"summary": {"passed": passed, "total": total}, "turns": results}
    out_path = Path("test_kt_server_results.json")
    out_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n  详细结果: {out_path}")


async def main():
    print(f"{BOLD}{CYAN}")
    print("╔════════════════════════════════════════════════╗")
    print("║   V4 知识树 — LangGraph Dev Server 真实测试    ║")
    print("╚════════════════════════════════════════════════╝")
    print(R)

    results = await run_all_tests()
    print_summary(results)

    all_passed = all(r["passed"] for r in results)
    sys.exit(0 if all_passed else 1)


if __name__ == "__main__":
    asyncio.run(main())
