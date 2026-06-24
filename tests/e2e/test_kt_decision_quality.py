"""知识树决策质量测试 — 验证 KT 是否改善 Supervisor 的任务决策.

测试场景：
  I1: 项目特定问题 → 直接回答（不派发 Executor/Planner）
  I2: KT 增强准确性 — 超时处理应提到重规划
  I3: 部分信息 + KT 补全 — Observation 两种处理策略

用法:
    1. make dev
    2. uv run python -u tests/e2e/test_kt_decision_quality.py
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
    print("║   知识树决策质量测试                                 ║")
    print("║   I1: 项目问答 → 直接回答（不派发子 Agent）          ║")
    print("║   I2: KT 增强准确性 — 超时处理                      ║")
    print("║   I3: 部分信息 + KT 补全 — Observation 策略          ║")
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

    # ── I1: 项目特定问题 → 直接回答 ──────────────────────────
    section("I1: 项目问答 → 直接回答（不应派发 call_executor/call_planner）")

    thread_i1 = await client.threads.create()
    print(f"  {DIM}Thread: {thread_i1['thread_id'][:16]}...{R}")
    i1_msg = "请直接回答：这个项目的 ReAct 模式和普通 ReAct 有什么区别？"
    print(f"  {DIM}消息: {i1_msg}{R}")

    start = time.perf_counter()
    r1 = await send(thread_i1["thread_id"], i1_msg, timeout_s=120)
    elapsed_i1 = time.perf_counter() - start

    # 检查：不应调用 call_executor 或 call_planner
    has_executor = "call_executor" in r1["tools"]
    has_planner = "call_planner" in r1["tools"]
    # 回复应包含项目特定关键词
    project_keywords = ["Supervisor", "Executor", "Planner", "supervisor", "executor", "planner"]
    has_project_content = any(kw in r1["ai_text"] for kw in project_keywords)

    i1_issues: list[str] = []
    if has_executor:
        i1_issues.append("不应调用 call_executor")
    if has_planner:
        i1_issues.append("不应调用 call_planner")
    if not has_project_content:
        i1_issues.append("回复未包含 AgentTriad 特定信息（Supervisor/Executor/Planner）")

    i1_passed = (not has_executor) and (not has_planner) and has_project_content
    icon = f"{GREEN}✓{R}" if i1_passed else f"{RED}✗{R}"
    detail = "符合预期" if i1_passed else "; ".join(i1_issues)
    print(f"  {icon} {detail}")
    if r1["tools"]:
        print(f"  {DIM}工具调用: {', '.join(r1['tools'])}{R}")
    else:
        print(f"  {DIM}工具调用: 无（直接回答）{R}")
    if r1["ai_text"]:
        preview = r1["ai_text"][:200].replace("\n", " ")
        print(f"  {DIM}AI: {preview}{'...' if len(r1['ai_text']) > 200 else ''}{R}")
    print(f"  {DIM}耗时: {elapsed_i1:.1f}s{R}")

    results.append({
        "id": "I1", "name": "项目问答直接回答",
        "passed": i1_passed, "detail": detail,
        "elapsed": round(elapsed_i1, 1),
        "tools": r1["tools"],
    })

    # ── I2: KT 增强准确性 — 超时处理 ─────────────────────────
    section("I2: 超时处理决策 — KT 应提供'重规划'知识")

    thread_i2 = await client.threads.create()
    print(f"  {DIM}Thread: {thread_i2['thread_id'][:16]}...{R}")
    i2_msg = "请直接回答，不要使用工具：当 Executor 超时了，Supervisor 应该怎么做？"
    print(f"  {DIM}消息: {i2_msg}{R}")

    start = time.perf_counter()
    r2 = await send(thread_i2["thread_id"], i2_msg, timeout_s=120)
    elapsed_i2 = time.perf_counter() - start

    # 检查：回复应包含"重规划"或"replan"
    has_replan = "重规划" in r2["ai_text"] or "replan" in r2["ai_text"].lower()
    i2_issues = []
    if not has_replan:
        i2_issues.append("回复未提到'重规划'或'replan'（项目特有的失败处理策略）")

    i2_passed = has_replan
    icon = f"{GREEN}✓{R}" if i2_passed else f"{RED}✗{R}"
    detail = "符合预期" if i2_passed else "; ".join(i2_issues)
    print(f"  {icon} {detail}")
    if r2["ai_text"]:
        preview = r2["ai_text"][:200].replace("\n", " ")
        print(f"  {DIM}AI: {preview}{'...' if len(r2['ai_text']) > 200 else ''}{R}")
    print(f"  {DIM}耗时: {elapsed_i2:.1f}s{R}")

    results.append({
        "id": "I2", "name": "超时处理决策质量",
        "passed": i2_passed, "detail": detail,
        "elapsed": round(elapsed_i2, 1),
        "tools": r2["tools"],
    })

    # ── I3: 部分信息 + KT 补全 — Observation 策略 ────────────
    section("I3: Observation 策略 — KT 应提供'截断'和'外置'知识")

    thread_i3 = await client.threads.create()
    print(f"  {DIM}Thread: {thread_i3['thread_id'][:16]}...{R}")
    i3_msg = "请直接回答：Observation 机制的两种处理策略是什么？"
    print(f"  {DIM}消息: {i3_msg}{R}")

    start = time.perf_counter()
    r3 = await send(thread_i3["thread_id"], i3_msg, timeout_s=120)
    elapsed_i3 = time.perf_counter() - start

    # 检查：回复应同时包含"截断"和"外置"
    has_truncate = "截断" in r3["ai_text"]
    has_offload = "外置" in r3["ai_text"]
    i3_issues = []
    if not has_truncate:
        i3_issues.append("回复未提到'截断'（Observation 处理策略之一）")
    if not has_offload:
        i3_issues.append("回复未提到'外置'（Observation 处理策略之二）")

    i3_passed = has_truncate and has_offload
    icon = f"{GREEN}✓{R}" if i3_passed else f"{RED}✗{R}"
    detail = "符合预期" if i3_passed else "; ".join(i3_issues)
    print(f"  {icon} {detail}")
    if r3["ai_text"]:
        preview = r3["ai_text"][:200].replace("\n", " ")
        print(f"  {DIM}AI: {preview}{'...' if len(r3['ai_text']) > 200 else ''}{R}")
    print(f"  {DIM}耗时: {elapsed_i3:.1f}s{R}")

    results.append({
        "id": "I3", "name": "Observation 策略补全",
        "passed": i3_passed, "detail": detail,
        "elapsed": round(elapsed_i3, 1),
        "tools": r3["tools"],
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
        if r["detail"] != "符合预期":
            print(f"           {r['detail']}")

    # 保存结果
    output_path = Path(__file__).resolve().parent / "test_kt_decision_quality_results.json"
    output_path.write_text(
        json.dumps({"results": results}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\n  结果已保存: {output_path}")

    # 不因 LLM 行为不确定而退出失败
    sys.exit(0)


if __name__ == "__main__":
    asyncio.run(main())
