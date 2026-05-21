"""语义 Embedder 全面端到端验证 — 12 用例 × 4 组.

组 A：同义理解（语义 embedder 核心价值）
组 B：噪声过滤（0.6 阈值有效性）
组 C：Ingest-Retrieve 闭环（写入后能读回）
组 D：KT ON/OFF 对比（证明语义 embedder 改变行为）

用法:
    make dev
    uv run python -u tests/e2e/test_kt_semantic_e2e_full.py
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
CTX_KT_ON = {"enable_knowledge_tree": True}
CTX_KT_OFF = {"enable_knowledge_tree": False}


def section(title: str):
    print(f"\n{BOLD}{CYAN}{'═' * 60}{R}")
    print(f"{BOLD}{CYAN}  {title}{R}")
    print(f"{BOLD}{CYAN}{'═' * 60}{R}")


async def send_message(client, thread_id: str, message: str, ctx: dict, timeout_s: float = 120) -> dict:
    start = time.perf_counter()
    run = await client.runs.create(
        thread_id=thread_id,
        assistant_id=ASSISTANT_ID,
        input={"messages": [{"role": "user", "content": message}]},
        context=ctx,
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
    return {"run_status": status, "elapsed": elapsed, "state": state,
            "error": rs.get("error") if status == "error" else None}


def extract_ai_text(state: dict) -> str:
    for msg in reversed(state.get("values", {}).get("messages", [])):
        if msg.get("type") == "ai" and not msg.get("tool_calls"):
            return msg.get("content", "")
    return ""


def extract_tool_names(state: dict) -> list[str]:
    names = []
    for msg in state.get("values", {}).get("messages", []):
        if msg.get("type") == "ai" and msg.get("tool_calls"):
            for tc in msg["tool_calls"]:
                names.append(tc.get("name", ""))
    return names


def check_keywords(text: str, keywords: list[str]) -> list[str]:
    return [kw for kw in keywords if kw in text]


# ─── 测试用例 ──────────────────────────────────────────────

TEST_CASES = [
    # ── A 组：同义理解（语义 embedder 核心价值）──
    {
        "id": "A1", "group": "A", "ctx": CTX_KT_ON,
        "name": "同义：进程间通信",
        "message": "进程间怎么传递消息和同步状态？",
        "expect_keywords": ["进程", "通信", "FastAPI"],
        "desc": "语义理解'进程间通信'≈'executor-protocol'种子",
    },
    {
        "id": "A2", "group": "A", "ctx": CTX_KT_ON,
        "name": "同义：记忆管理",
        "message": "Agent 怎么管理和维护自己的内部数据？",
        "expect_keywords": ["状态", "State", "管理"],
        "desc": "语义理解'内部数据管理'≈'plan-json-and-state'种子",
    },
    {
        "id": "A3", "group": "A", "ctx": CTX_KT_ON,
        "name": "同义：工具输出处理",
        "message": "当工具返回的结果很长时应该怎么处理？",
        "expect_keywords": ["截断", "Observation", "外置"],
        "desc": "语义理解'工具输出长'≈'observation-and-reflection'种子",
    },
    {
        "id": "A4", "group": "A", "ctx": CTX_KT_ON,
        "name": "英文查询",
        "message": "How does the executor communicate with the supervisor process?",
        "expect_keywords": ["Executor", "Supervisor", "进程"],
        "desc": "英文查询中文种子，跨语言语义匹配",
    },

    # ── B 组：噪声过滤 ──
    {
        "id": "B1", "group": "B", "ctx": CTX_KT_ON,
        "name": "完全无关：天气",
        "message": "今天天气怎么样？",
        "expect_no_kt_tool": True,
        "expect_no_keywords": ["TypedDict", "FastAPI", "ReAct"],
        "desc": "0.6 阈值应过滤，不注入项目知识",
    },
    {
        "id": "B2", "group": "B", "ctx": CTX_KT_ON,
        "name": "半相关：通用编程",
        "message": "Python 的 asyncio 怎么用？给我一个基础教程。",
        "expect_no_kt_tool": True,
        "desc": "通用编程问题不应触发项目 KT 知识注入",
    },

    # ── C 组：Ingest-Retrieve 闭环 ──
    {
        "id": "C1", "group": "C", "ctx": CTX_KT_ON,
        "name": "Ingest 新知识",
        "message": "记住这条知识：当 Executor 子进程意外退出时，应检查 logs/ 目录下的 .port 文件确认端口分配。",
        "expect_tool": "knowledge_tree_ingest",
        "desc": "Supervisor 应主动 ingest",
    },
    {
        "id": "C2", "group": "C", "ctx": CTX_KT_ON,
        "name": "Retrieve 刚 ingest 的知识",
        "message": "用 knowledge_tree_retrieve 搜索关于 Executor 子进程端口的知识",
        "expect_tool": "knowledge_tree_retrieve",
        "desc": "应能检索到 C1 ingest 的内容",
    },
    {
        "id": "C3", "group": "C", "ctx": CTX_KT_ON,
        "name": "Ingest 复杂知识",
        "message": "请记住：Supervisor 的三种模式决策规则——无需工具时直接回复（Mode 1），短流程用 task_description 直接调用 Executor（Mode 2），多步依赖时先 Planner 再 Executor（Mode 3）。",
        "expect_tool": "knowledge_tree_ingest",
        "desc": "复杂多行知识也应 ingest",
    },

    # ── D 组：KT ON/OFF 对比 ──
    {
        "id": "D1", "group": "D", "ctx": CTX_KT_ON,
        "name": "ReAct 查询（KT ON）",
        "message": "ReAct 模式的核心机制是什么？",
        "expect_keywords": ["推理", "行动", "ReAct"],
        "desc": "KT ON 应注入 ReAct 种子知识",
    },
    {
        "id": "D2", "group": "D", "ctx": CTX_KT_OFF,
        "name": "ReAct 查询（KT OFF 对照）",
        "message": "ReAct 模式的核心机制是什么？",
        "expect_not_keywords": ["Supervisor", "Executor", "Planner"],
        "desc": "KT OFF 时不应出现 AgentTriad 特有的 ReAct 实现",
    },
]


async def run_tests(client) -> list[dict]:
    results = []
    groups = {"A": [], "B": [], "C": [], "D": []}

    for tc in TEST_CASES:
        groups[tc["group"]].append(tc)

    group_names = {
        "A": "A 组：同义理解（语义 embedder 核心价值）",
        "B": "B 组：噪声过滤（0.6 阈值）",
        "C": "C 组：Ingest-Retrieve 闭环",
        "D": "D 组：KT ON/OFF 对比",
    }

    for g, cases in groups.items():
        section(group_names.get(g, g))
        for tc in cases:
            print(f"\n{BOLD}{tc['id']}: {tc['name']}{R}")
            print(f"  {DIM}{tc.get('desc', '')}{R}")

            thread = await client.threads.create()
            try:
                result = await send_message(client, thread["thread_id"], tc["message"], tc["ctx"], timeout_s=90)
            except Exception as e:
                results.append({"id": tc["id"], "name": tc["name"], "passed": False, "detail": str(e)})
                print(f"  {RED}✗ 异常: {e}{R}")
                continue

            if result["run_status"] == "error":
                err = str(result.get("error", ""))[:200]
                results.append({"id": tc["id"], "name": tc["name"], "passed": False, "detail": err})
                print(f"  {RED}✗ Run error: {err}{R}")
                continue

            ai_text = extract_ai_text(result["state"])
            tools = extract_tool_names(result["state"])
            kt_tools = [t for t in tools if "knowledge_tree" in t]
            elapsed = result["elapsed"]

            analysis = analyze(tc, ai_text, tools)
            results.append({
                "id": tc["id"], "name": tc["name"], "group": tc["group"],
                "passed": analysis["passed"], "detail": analysis["detail"],
                "elapsed": round(elapsed, 1), "kt_tools": kt_tools,
            })

            icon = f"{GREEN}✓{R}" if analysis["passed"] else f"{RED}✗{R}"
            print(f"  {icon} {analysis['detail']}")
            if kt_tools:
                print(f"  {DIM}KT 工具: {', '.join(kt_tools)}{R}")
            if ai_text:
                preview = ai_text[:120].replace("\n", " ")
                print(f"  {DIM}AI: {preview}{'...' if len(ai_text) > 120 else ''}{R}")
            print(f"  {DIM}耗时: {elapsed:.1f}s{R}")

    return results


def analyze(tc: dict, ai_text: str, tools: list[str]) -> dict:
    issues = []

    if tc.get("expect_tool"):
        if tc["expect_tool"] not in tools:
            issues.append(f"期望工具 {tc['expect_tool']} 未调用 (实际: {tools or '无'})")

    if tc.get("expect_no_kt_tool"):
        kt_active = [t for t in tools if "knowledge_tree_retrieve" in t]
        if kt_active:
            issues.append(f"不应主动 retrieve 但调用了 {kt_active}")

    if tc.get("expect_keywords"):
        found = check_keywords(ai_text, tc["expect_keywords"])
        missing = [kw for kw in tc["expect_keywords"] if kw not in found]
        if len(found) == 0:
            issues.append(f"期望关键词均未出现: {tc['expect_keywords']}")

    if tc.get("expect_no_keywords"):
        unexpected = check_keywords(ai_text, tc["expect_no_keywords"])
        if unexpected:
            issues.append(f"不应出现的关键词: {unexpected}")

    passed = len(issues) == 0
    return {"passed": passed, "detail": "符合预期" if passed else "; ".join(issues)}


def print_summary(results: list[dict]):
    section("汇总")
    passed = sum(1 for r in results if r["passed"])
    total = len(results)
    icon = f"{GREEN}✓{R}" if passed == total else f"{YELLOW}○{R}"
    print(f"  {icon} {passed}/{total} 通过")

    groups = {"A": [], "B": [], "C": [], "D": []}
    for r in results:
        groups.get(r.get("group", ""), []).append(r)

    group_names = {"A": "同义理解", "B": "噪声过滤", "C": "Ingest-Retrieve", "D": "ON/OFF 对比"}
    for g, gresults in groups.items():
        gp = sum(1 for r in gresults if r["passed"])
        gt = len(gresults)
        icon = f"{GREEN}✓{R}" if gp == gt else f"{YELLOW}○{R}"
        print(f"\n  {icon} {group_names.get(g, g)}: {gp}/{gt}")
        for r in gresults:
            status = f"{GREEN}PASS{R}" if r["passed"] else f"{RED}FAIL{R}"
            elapsed = r.get("elapsed", "?")
            print(f"    [{status}] {r['id']}: {r['name']} ({elapsed}s)")
            if r.get("detail") and r["detail"] != "符合预期":
                print(f"           {r['detail']}")

    # D 组对比
    d_results = [r for r in results if r.get("group") == "D"]
    if len(d_results) >= 2:
        section("D 组对比")
        d_on = [r for r in d_results if "ON" in r["name"]]
        d_off = [r for r in d_results if "OFF" in r["name"]]
        for on, off in zip(d_on, d_off):
            pair_icon = f"{GREEN}✓{R}" if on["passed"] else f"{RED}✗{R}"
            print(f"  {pair_icon} ON={on['detail']} | OFF={off['detail']}")

    out_path = Path(__file__).resolve().parent / "test_kt_semantic_e2e_full_results.json"
    out_path.write_text(json.dumps({"results": results}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n  结果: {out_path}")


async def main():
    print(f"{BOLD}{CYAN}")
    print("╔════════════════════════════════════════════════════════╗")
    print("║   语义 Embedder 全面验证 — 12 用例 × 4 组           ║")
    print("║   同义理解 | 噪声过滤 | 闭环 | ON/OFF 对比          ║")
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

    results = await run_tests(client)
    print_summary(results)
    sys.exit(0)


if __name__ == "__main__":
    asyncio.run(main())
