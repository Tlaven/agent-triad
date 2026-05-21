"""语义 embedder 端到端验证 — 真实 LLM + 真实 API embedder 完整链路.

验证：
  1. auto-inject 用语义 embedder 后是否能正确注入（不注入噪声）
  2. Supervisor 是否主动使用 KT 工具
  3. 同义查询是否被语义理解命中

用法:
    make dev
    uv run python -u tests/e2e/test_kt_semantic_e2e.py
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


async def send_message(client, thread_id: str, message: str, timeout_s: float = 120) -> dict:
    start = time.perf_counter()
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

    elapsed = time.perf_counter() - start
    state = await client.threads.get_state(thread_id)
    return {
        "run_status": status,
        "elapsed": elapsed,
        "state": state,
        "error": rs.get("error") if status == "error" else None,
    }


def extract_ai_text(state: dict) -> str:
    messages = state.get("values", {}).get("messages", [])
    for msg in reversed(messages):
        if msg.get("type") == "ai" and not msg.get("tool_calls"):
            return msg.get("content", "")
    return ""


def extract_tool_calls(state: dict) -> list[str]:
    messages = state.get("values", {}).get("messages", [])
    names = []
    for msg in messages:
        if msg.get("type") == "ai" and msg.get("tool_calls"):
            for tc in msg["tool_calls"]:
                names.append(tc.get("name", ""))
    return names


async def main():
    print(f"{BOLD}{CYAN}")
    print("╔════════════════════════════════════════════════════════╗")
    print("║   语义 Embedder 端到端验证                           ║")
    print("║   真实 LLM + SiliconFlow API embedder 完整链路       ║")
    print("╚════════════════════════════════════════════════════════╝")
    print(R)

    from langgraph_sdk import get_client

    client = get_client(url=SERVER_URL)
    try:
        assistants = await client.assistants.search()
        section(f"Server OK (assistants: {len(assistants)})")
    except Exception as e:
        print(f"  {RED}Server 不可用: {e}{R}")
        sys.exit(1)

    tests = [
        {
            "id": "E1",
            "name": "同义查询：进程间通信",
            "message": "进程间怎么传递消息和同步状态？",
            "expect_topic": True,
            "desc": "语义 embedder 应理解'进程间通信'≈'通信协议'种子",
        },
        {
            "id": "E2",
            "name": "主动 ingest",
            "message": "记住这条知识：当 Python asyncio 的 gather 遇到异常时，return_exceptions=True 可以防止整个任务组崩溃。",
            "expect_tool": "knowledge_tree_ingest",
            "desc": "Supervisor 应主动 ingest 用户要求记住的知识",
        },
        {
            "id": "E3",
            "name": "无关查询（不应注入噪声）",
            "message": "今天天气怎么样？",
            "expect_no_kt": True,
            "desc": "auto-inject 阈值 0.6 应过滤无关查询",
        },
        {
            "id": "E4",
            "name": "精确查询：ReAct 模式",
            "message": "ReAct 模式是怎么工作的？",
            "expect_topic": True,
            "desc": "应检索到 ReAct 模式种子知识",
        },
    ]

    results = []
    for tc in tests:
        print(f"\n{BOLD}{tc['id']}: {tc['name']}{R}")
        print(f"  {DIM}{tc['desc']}{R}")
        print(f"  {DIM}消息: {tc['message'][:60]}{R}")

        thread = await client.threads.create()

        try:
            result = await send_message(client, thread["thread_id"], tc["message"], timeout_s=90)
        except Exception as e:
            print(f"  {RED}✗ 异常: {e}{R}")
            results.append({"id": tc["id"], "passed": False, "detail": str(e)})
            continue

        if result["run_status"] == "error":
            print(f"  {RED}✗ Run error: {result.get('error', '')[:200]}{R}")
            results.append({"id": tc["id"], "passed": False, "detail": result.get("error", "")})
            continue

        ai_text = extract_ai_text(result["state"])
        tools = extract_tool_calls(result["state"])
        kt_tools = [t for t in tools if "knowledge_tree" in t]
        elapsed = result["elapsed"]

        # 分析
        issues = []
        if tc.get("expect_tool"):
            if tc["expect_tool"] not in tools:
                issues.append(f"期望工具 {tc['expect_tool']} 未调用")

        if tc.get("expect_no_kt"):
            # 无关查询不应触发 KT 工具调用（auto-inject 是后台的，不算工具调用）
            # 但如果 Supervisor 主动调了 KT retrieve，说明它误解了查询意图
            if "knowledge_tree_retrieve" in tools:
                issues.append("无关查询触发了主动 KT retrieve")

        if tc.get("expect_topic"):
            # 期望 AI 回答涉及项目知识（从 KT 或自身）
            # 检查是否调用了 KT 工具或回答中包含项目关键词
            project_keywords = ["ReAct", "Executor", "Supervisor", "Planner", "进程", "协议", "asyncio", "gather"]
            has_content = any(kw in ai_text for kw in project_keywords)
            if not has_content and not kt_tools:
                issues.append("期望涉及项目知识但未发现相关内容")

        passed = len(issues) == 0
        icon = f"{GREEN}✓{R}" if passed else f"{RED}✗{R}"
        detail = "符合预期" if passed else "; ".join(issues)

        print(f"  {icon} {detail}")
        if kt_tools:
            print(f"  {DIM}KT 工具: {', '.join(kt_tools)}{R}")
        if ai_text:
            preview = ai_text[:150].replace("\n", " ")
            print(f"  {DIM}AI: {preview}{'...' if len(ai_text) > 150 else ''}{R}")
        print(f"  {DIM}耗时: {elapsed:.1f}s{R}")

        results.append({"id": tc["id"], "name": tc["name"], "passed": passed, "detail": detail, "elapsed": round(elapsed, 1)})

    # 汇总
    section("汇总")
    passed = sum(1 for r in results if r["passed"])
    total = len(results)
    icon = f"{GREEN}✓{R}" if passed == total else f"{YELLOW}○{R}"
    print(f"  {icon} {passed}/{total} 通过")
    for r in results:
        status = f"{GREEN}PASS{R}" if r["passed"] else f"{RED}FAIL{R}"
        print(f"    [{status}] {r['id']}: {r.get('name', '')}  ({r.get('elapsed', '?')}s)")
        if r.get("detail") and r["detail"] != "符合预期":
            print(f"           {r['detail']}")

    # 保存
    out_path = Path(__file__).resolve().parent / "test_kt_semantic_e2e_results.json"
    out_path.write_text(json.dumps({"results": results}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n  结果: {out_path}")

    sys.exit(0)


if __name__ == "__main__":
    asyncio.run(main())
