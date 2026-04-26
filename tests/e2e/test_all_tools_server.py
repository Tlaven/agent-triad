"""通过 LangGraph dev server 测试 Supervisor 全部工具.

用法:
    1. 先启动 dev server: make dev
    2. 运行: uv run python -u test_all_tools_server.py

覆盖工具:
    - call_planner          T1
    - call_executor         T2 (Mode 2: task_description)
    - manage_executor       T3 (get_result / list_tasks / check_progress / stop)
    - knowledge_tree_status T4
    - knowledge_tree_retrieve T5
    - knowledge_tree_ingest  T6
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
KT_ROOT = "workspace/kt_full_test"


def section(title: str):
    print(f"\n{BOLD}{CYAN}{'═' * 60}{R}")
    print(f"{BOLD}{CYAN}  {title}{R}")
    print(f"{BOLD}{CYAN}{'═' * 60}{R}")


# ─── 测试用例 ────────────────────────────────────────────────
TEST_CASES = [
    # ═══ Planner & Executor (Mode 2/3) ═══
    {
        "name": "T1: call_planner — 让 Supervisor 规划任务",
        "message": "我需要你帮我完成以下任务：请规划如何创建一个简单的 Python 脚本来计算斐波那契数列的前10个数。只需要规划，不需要执行。",
        "expected_tools": ["call_planner"],
    },
    {
        "name": "T2: call_executor (Mode 2) — 执行简单任务",
        "message": "请用 call_executor 执行以下任务：在 workspace 目录下创建一个文件 fib.py，内容是打印斐波那契数列前10个数字的 Python 脚本。",
        "expected_tools": ["call_executor"],
    },
    {
        "name": "T3: manage_executor(list_tasks) — 列出所有执行任务",
        "message": "请列出当前所有执行器任务的状态。",
        "expected_tools": ["manage_executor"],
    },
    # ═══ Knowledge Tree ═══
    {
        "name": "T4: knowledge_tree_status — 查看知识树",
        "message": "请查看知识树当前状态。",
        "expected_tools": ["knowledge_tree_status"],
    },
    {
        "name": "T5: knowledge_tree_retrieve — 检索知识",
        "message": "请检索关于状态管理的知识。",
        "expected_tools": ["knowledge_tree_retrieve"],
    },
    {
        "name": "T6: knowledge_tree_ingest — 摄入新知识",
        "message": "请把这条知识记录到知识树：Agent 在多步任务中应使用 Plan-Execute 模式，先规划再执行，遇到失败时重规划。",
        "expected_tools": ["knowledge_tree_ingest"],
    },
    # ═══ 再次执行 ═══
    {
        "name": "T7: call_executor (Mode 2) — 执行第二个任务",
        "message": "请执行这个任务：读取 workspace/fib.py 文件的内容并告诉我脚本写了什么。",
        "expected_tools": ["call_executor"],
    },
    {
        "name": "T8: manage_executor(check_progress) — 检查进度",
        "message": "请检查一下刚才的执行器任务进度如何。",
        "expected_tools": ["manage_executor"],
    },
    # ═══ Knowledge Tree 再次验证 ═══
    {
        "name": "T9: knowledge_tree_retrieve — 检索新摄入的知识",
        "message": "检索关于 Plan-Execute 模式的知识。",
        "expected_tools": ["knowledge_tree_retrieve"],
    },
    {
        "name": "T10: knowledge_tree_status — 最终状态",
        "message": "最后再查看一下知识树状态。",
        "expected_tools": ["knowledge_tree_status"],
    },
]


async def send_message(client, thread_id: str, message: str) -> dict:
    """通过 LangGraph server 发送消息，等待完成，返回最终状态。"""
    start = time.perf_counter()

    run = await client.runs.create(
        thread_id=thread_id,
        assistant_id=ASSISTANT_ID,
        input={"messages": [{"role": "user", "content": message}]},
        context={
            "enable_knowledge_tree": True,
            "knowledge_tree_root": KT_ROOT,
        },
    )

    # 等待完成（最长 5 分钟）
    for _ in range(300):
        rs = await client.runs.get(thread_id, run["run_id"])
        status = rs.get("status", "unknown")
        if status in ("success", "error", "cancelled"):
            break
        await asyncio.sleep(1)

    elapsed = time.perf_counter() - start
    state = await client.threads.get_state(thread_id)

    return {
        "run_status": status,
        "elapsed": elapsed,
        "state": state,
        "error": rs.get("error") if status == "error" else None,
    }


def analyze_messages(state: dict) -> dict:
    """从图状态中提取工具调用信息。"""
    messages = state.get("values", {}).get("messages", [])

    tools_called: list[str] = []
    tool_outputs: list[dict] = []
    ai_responses: list[str] = []
    errors: list[str] = []

    for msg in messages:
        msg_type = msg.get("type", "")

        if msg_type == "ai":
            tcs = msg.get("tool_calls", [])
            for tc in tcs:
                tools_called.append(tc.get("name", "?"))
            content = msg.get("content", "")
            if content and not tcs:
                ai_responses.append(content[:500])

        elif msg_type == "tool":
            name = msg.get("name", "tool")
            content = msg.get("content", "")
            status_field = msg.get("status", "")
            try:
                parsed = json.loads(content) if isinstance(content, str) else {}
            except (json.JSONDecodeError, TypeError):
                parsed = {"raw": str(content)[:300]}
            tool_outputs.append({"tool": name, "result": parsed})
            if status_field == "error":
                errors.append(f"{name}: {str(content)[:200]}")

    return {
        "tools_called": tools_called,
        "tool_outputs": tool_outputs,
        "ai_responses": ai_responses,
        "errors": errors,
    }


async def run_all_tests() -> list[dict]:
    from langgraph_sdk import get_client

    all_results: list[dict] = []
    tools_seen: set[str] = set()

    # 准备种子知识
    seed_dir = Path(KT_ROOT)
    if seed_dir.exists():
        shutil.rmtree(seed_dir)
    seed_dir.mkdir(parents=True)

    from src.common.knowledge_tree.dag.node import KnowledgeNode
    seeds = {
        "development/state.md": ("状态管理", "LangGraph 使用 TypedDict 定义状态模式，通过 StateGraph 构建执行图。状态是不可变的。"),
        "patterns/react.md": ("ReAct 模式", "ReAct 模式结合推理和行动，逐步解决复杂问题。Agent 在每一步都先推理再行动。"),
    }
    for rel, (title, content) in seeds.items():
        node = KnowledgeNode.create(node_id=rel, title=title, content=content, source="seed")
        p = seed_dir / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(node.to_frontmatter_md(), encoding="utf-8")

    section(f"种子: {len(seeds)} 篇 → {seed_dir}")

    client = get_client(url=SERVER_URL)
    try:
        assistants = await client.assistants.search()
        section(f"Server: {SERVER_URL}  (assistants: {len(assistants)})")
    except Exception as e:
        section("Server 不可用")
        print(f"  {RED}无法连接 {SERVER_URL}: {e}{R}")
        print(f"  {YELLOW}请先运行: make dev{R}")
        sys.exit(1)

    # 使用单一 thread，模拟真实对话
    thread = await client.threads.create()
    thread_id = thread["thread_id"]
    print(f"  Thread: {thread_id}")

    for i, tc in enumerate(TEST_CASES):
        section(tc["name"])
        print(f"  {DIM}消息: {tc['message'][:80]}...{R}" if len(tc["message"]) > 80 else f"  {DIM}消息: {tc['message']}{R}")

        try:
            result = await send_message(client, thread_id, tc["message"])
        except Exception as e:
            all_results.append({
                "name": tc["name"],
                "passed": False,
                "detail": f"异常: {e}",
                "elapsed": 0,
                "tools_called": [],
                "expected_tools": tc["expected_tools"],
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
                "expected_tools": tc["expected_tools"],
                "error": str(error)[:500],
            })
            print(f"  {RED}✗ Run 错误: {str(error)[:300]}{R}")
            continue

        analysis = analyze_messages(result["state"])
        tools = analysis["tools_called"]
        outputs = analysis["tool_outputs"]
        ai_texts = analysis["ai_responses"]

        # 判定：期望的工具是否被调用
        expected = tc["expected_tools"]
        matched = [t for t in tools if t in expected]
        passed = len(matched) > 0
        for t in matched:
            tools_seen.add(t)

        detail = f"匹配工具: {matched}" if matched else f"未匹配期望工具 {expected}，实际: {tools}"
        icon = f"{GREEN}✓{R}" if passed else f"{RED}✗{R}"
        print(f"  {icon} {detail}")
        print(f"  {DIM}  耗时={elapsed:.1f}s  全部工具={tools}{R}")

        for out in outputs:
            name = out["tool"]
            r = out["result"]
            summary = json.dumps(r, ensure_ascii=False)[:250]
            print(f"  {DIM}  {name} → {summary}{R}")

        for ai in ai_texts[:1]:
            if ai.strip():
                print(f"  {MAGENTA}  AI: {ai[:250]}{R}")

        all_results.append({
            "name": tc["name"],
            "passed": passed,
            "detail": detail,
            "elapsed": round(elapsed, 1),
            "tools_called": tools,
            "expected_tools": expected,
            "tool_outputs": outputs,
        })

    return all_results, tools_seen


def print_summary(results: list[dict], tools_seen: set[str]):
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

    # 工具覆盖报告
    all_tools = {
        "call_planner", "call_executor", "manage_executor",
        "knowledge_tree_retrieve", "knowledge_tree_status",
        "knowledge_tree_ingest", "knowledge_tree_bootstrap",
    }
    section("工具覆盖")
    covered = len(tools_seen & all_tools)
    print(f"  已触发: {covered}/{len(all_tools)}")
    for t in sorted(all_tools):
        hit = t in tools_seen
        icon = f"{GREEN}✓{R}" if hit else f"{YELLOW}○{R}"
        print(f"    {icon} {t}")
    missing = all_tools - tools_seen
    if missing:
        print(f"\n  {YELLOW}未触发工具: {sorted(missing)}{R}")

    output = {
        "summary": {
            "passed": passed,
            "total": total,
            "tools_seen": sorted(tools_seen),
            "tools_missing": sorted(all_tools - tools_seen),
        },
        "turns": results,
    }
    out_path = Path("test_all_tools_results.json")
    out_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n  详细结果: {out_path}")


async def main():
    print(f"{BOLD}{CYAN}")
    print("╔════════════════════════════════════════════════════╗")
    print("║   Supervisor 全工具 — LangGraph Dev Server 测试   ║")
    print("║   覆盖: 核心工具 × 真实 LLM × ASGI 环境           ║")
    print("╚════════════════════════════════════════════════════╝")
    print(R)

    results, tools_seen = await run_all_tests()
    print_summary(results, tools_seen)

    all_passed = all(r["passed"] for r in results)
    sys.exit(0 if all_passed else 1)


if __name__ == "__main__":
    asyncio.run(main())
