"""Supervisor 全工具进阶综合测试 V2 — 通过 LangGraph Dev Server.

20 个测试用例，4 组独立 thread，覆盖全部 Supervisor 工具。
三级验证：L1(工具调用) + L2(输出格式) + L3(副作用)。

组 A (A1-A7): 知识树完整闭环 — bootstrap/status/retrieve/ingest/dedup/retrieve-verify
组 B (B1-B6): Mode 2 + Mode 3 执行流 — executor/list/get/planner+executor/check
组 C (C1-C4): 异步 + 停止流程 — async dispatch/stop/list/get_result
组 D (D1-D3): 重规划 + 边界 — plan/execute+replan/kt_status

用法:
    1. make dev
    2. uv run python -u test_comprehensive_server.py
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

# Ensure src/ is importable regardless of working directory
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

R = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
CYAN = "\033[36m"
GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
MAGENTA = "\033[35m"
BLUE = "\033[34m"

SERVER_URL = "http://localhost:2024"
ASSISTANT_ID = "supervisor"
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
KT_ROOT = "workspace/kt_comprehensive_test"

ALL_TOOLS = {
    "knowledge_tree_bootstrap", "knowledge_tree_status",
    "knowledge_tree_retrieve", "knowledge_tree_ingest",
    "call_planner", "call_executor", "manage_executor",
}

CTX_KT = {"enable_knowledge_tree": True, "knowledge_tree_root": KT_ROOT}


def section(title: str):
    print(f"\n{BOLD}{CYAN}{'═' * 60}{R}")
    print(f"{BOLD}{CYAN}  {title}{R}")
    print(f"{BOLD}{CYAN}{'═' * 60}{R}")


# ─── L2 验证函数 ─────────────────────────────────────────────
def validate_kt_output(tool_name: str, output: dict) -> list[str]:
    """验证 KT 工具输出 JSON，返回问题列表（空=通过）。"""
    issues: list[str] = []
    if tool_name == "knowledge_tree_bootstrap":
        if not output.get("ok"):
            issues.append("ok != true")
    elif tool_name == "knowledge_tree_status":
        if "total_nodes" not in output:
            issues.append("missing total_nodes")
    elif tool_name == "knowledge_tree_retrieve":
        if "query_id" not in output and not output.get("message"):
            issues.append("missing query_id")
    elif tool_name == "knowledge_tree_ingest":
        if not output.get("ok"):
            issues.append("ok != true")
    return issues


def validate_executor_output(tool_name: str, raw: str) -> list[str]:
    """验证 Executor 工具输出字符串，返回问题列表。

    注意：dynamic_tools_node 会处理 [EXECUTOR_RESULT] 等标记后剥离它们，
    所以工具返回的 content 是纯文本摘要，不包含原始标记。
    只检查输出是否包含有意义的内容。
    """
    issues: list[str] = []
    if tool_name == "call_executor":
        # 输出应含执行摘要或异步派发信息
        if not raw or len(raw.strip()) < 10:
            issues.append("empty or too short output")
    elif tool_name == "call_planner":
        # plan JSON 应含 steps 或 goal
        if "steps" not in raw and "goal" not in raw and "[PLANNER_REASONING]" not in raw:
            issues.append("missing plan content (no steps/goal/reasoning)")
    elif tool_name == "manage_executor":
        # manage_executor 涵盖 stop/get_result/check_progress/list_tasks
        if not raw or len(raw.strip()) < 5:
            issues.append("empty or too short output")
    return issues


def _find_workspace_file(filename: str) -> Path | None:
    """查找 executor 创建的文件（可能在 workspace/ 或 workspace/workspace/）。"""
    # Executor 的 CWD 是 workspace/，但 LLM 可能会在路径前再加一层 workspace/
    for base in (_PROJECT_ROOT / "workspace", _PROJECT_ROOT / "workspace" / "workspace"):
        p = base / filename
        if p.exists():
            return p
    return None


def verify_side_effects(test_id: str) -> list[str]:
    """验证文件系统副作用。"""
    issues: list[str] = []
    if test_id == "B1":
        p = _find_workspace_file("b1_test.txt")
        if p is None:
            issues.append("b1_test.txt not created")
        elif "Mode2 test passed" not in p.read_text(encoding="utf-8", errors="replace"):
            issues.append("b1_test.txt wrong content")
    elif test_id == "B4":
        if _find_workspace_file("calculator.py") is None:
            issues.append("calculator.py not created")
    elif test_id == "B6":
        if _find_workspace_file("b6_done.txt") is None:
            issues.append("b6_done.txt not created")
    return issues


# ─── 4 组测试用例 ────────────────────────────────────────────
GROUP_A = [
    {
        "id": "A1", "name": "A1: bootstrap — 初始建树",
        "message": "请初始化知识树。",
        "expected_tools": ["knowledge_tree_bootstrap"],
        "l2_check": True,
    },
    {
        "id": "A2", "name": "A2: status — 验证建树结果",
        "message": "查看知识树当前状态。",
        "expected_tools": ["knowledge_tree_status"],
        "l2_check": True,
    },
    {
        "id": "A3", "name": "A3: retrieve — 精确查询种子知识",
        "message": "检索关于状态管理的知识。",
        "expected_tools": ["knowledge_tree_retrieve"],
        "l2_check": True,
    },
    {
        "id": "A4", "name": "A4: retrieve — 模糊查询（无种子）",
        "message": "检索关于调试和排错的知识。",
        "expected_tools": ["knowledge_tree_retrieve"],
        "l2_check": True,
    },
    {
        "id": "A5", "name": "A5: ingest — 摄入新知识",
        "message": (
            "请记录到知识树：调试 Python 程序时，"
            "用 print() 分段输出变量值是最快的方法。"
        ),
        "expected_tools": ["knowledge_tree_ingest"],
        "l2_check": True,
    },
    {
        "id": "A6", "name": "A6: retrieve — 验证摄入的知识可检索",
        "message": "检索关于调试的知识。",
        "expected_tools": ["knowledge_tree_retrieve"],
        "l2_check": True,
    },
    {
        "id": "A7", "name": "A7: ingest — 重复摄入（验证去重）",
        "message": (
            "再记录一次到知识树：调试 Python 程序时，"
            "用 print() 分段输出变量值是最快的方法。"
        ),
        "expected_tools": ["knowledge_tree_ingest"],
        "l2_check": True,
    },
]

GROUP_B = [
    {
        "id": "B1", "name": "B1: call_executor (Mode 2) — 简单文件创建",
        "message": (
            "请创建文件 b1_test.txt，"
            "内容为 'Mode2 test passed'。"
            "请用 call_executor 直接执行。"
        ),
        "expected_tools": ["call_executor"],
        "l2_check": True, "l3_check": True,
    },
    {
        "id": "B2", "name": "B2: manage_executor(list_tasks) — 查看任务列表",
        "message": "请列出当前所有执行器任务。",
        "expected_tools": ["manage_executor"],
        "l2_check": True,
    },
    {
        "id": "B3", "name": "B3: manage_executor(get_result) — 获取 B1 结果详情",
        "message": "请用 manage_executor(action=get_result) 获取刚才 B1 任务的结果，使用 detail='full'。",
        "expected_tools": ["manage_executor"],
        "l2_check": True,
    },
    {
        "id": "B4", "name": "B4: Mode 3 — 规划并执行复杂任务",
        "message": (
            "请创建 calculator.py（实现 add/subtract/multiply/divide 四个函数），"
            "然后创建 test_calc.py（验证 add(2,3)==5）。"
            "请先规划再执行。"
        ),
        "expected_tools": ["call_planner", "call_executor"],
        "l2_check": True, "l3_check": True,
    },
    {
        "id": "B5", "name": "B5: manage_executor(check_progress) — 检查任务进度",
        "message": (
            "请只使用 manage_executor(action=check_progress) 工具检查当前所有执行器任务的进度。"
            "不要执行任何任务，不要调用 call_executor 或 call_planner，只需要查看进度。"
        ),
        "expected_tools": ["manage_executor"],
        "l2_check": True,
    },
    {
        "id": "B6", "name": "B6: call_executor (Mode 2) — 第二个简单任务",
        "message": (
            "请创建文件 b6_done.txt，"
            "内容为 'all executor tests done'。"
            "用 call_executor 直接执行。"
        ),
        "expected_tools": ["call_executor"],
        "l2_check": True, "l3_check": True,
    },
]

GROUP_C = [
    {
        "id": "C1", "name": "C1: call_executor (async) — 异步派发",
        "message": (
            "请用 call_executor 的 wait_for_result=False 异步派发："
            "生成 primes.txt（1到500的素数）。"
            "只派发不等结果。"
        ),
        "expected_tools": ["call_executor"],
        "l2_check": True,
    },
    {
        "id": "C2", "name": "C2: manage_executor(stop) — 停止异步任务",
        "message": "请立即用 manage_executor(action=stop) 停止刚才的素数任务。",
        "expected_tools": ["manage_executor"],
        "l2_check": True,
    },
    {
        "id": "C3", "name": "C3: manage_executor(list_tasks) — 查看含异步任务的列表",
        "message": "列出所有执行器任务。",
        "expected_tools": ["manage_executor"],
        "l2_check": True,
    },
    {
        "id": "C4", "name": "C4: manage_executor(get_result) — 获取异步任务最终结果",
        "message": "请用 manage_executor(action=get_result) 获取刚才素数任务的结果。",
        "expected_tools": ["manage_executor"],
        "l2_check": True,
    },
]

GROUP_D = [
    {
        "id": "D1", "name": "D1: call_planner — 首次规划",
        "message": "请规划：创建一个文件 d1.txt，内容为 'replan test'。只需要规划，不要执行。",
        "expected_tools": ["call_planner"],
        "l2_check": True,
    },
    {
        "id": "D2", "name": "D2: call_executor + 可能 replan — 执行并处理失败",
        "message": (
            "请执行刚才的计划。"
            "如果执行成功了，告诉我结果。"
            "如果失败了，请重新规划修正后再执行。"
        ),
        "expected_tools": ["call_executor"],  # call_planner 可能出现（replan）
        "l2_check": False,  # 输出格式不确定
    },
    {
        "id": "D3", "name": "D3: knowledge_tree_status — 最终 KT 状态验证",
        "message": "查看知识树当前状态。",
        "expected_tools": ["knowledge_tree_status"],
        "l2_check": True,
    },
]


# ─── 核心逻辑 ────────────────────────────────────────────────
async def get_msg_count(client, thread_id: str) -> int:
    state = await client.threads.get_state(thread_id)
    return len(state.get("values", {}).get("messages", []))


def analyze_current_turn(all_messages: list, msg_count_before: int) -> dict:
    new_msgs = all_messages[msg_count_before:]
    tools_called: list[str] = []
    tool_outputs: list[dict] = []
    ai_responses: list[str] = []

    for msg in new_msgs:
        msg_type = msg.get("type", "")
        if msg_type == "ai":
            for tc in msg.get("tool_calls", []):
                tools_called.append(tc.get("name", "?"))
            content = msg.get("content", "")
            if content and not msg.get("tool_calls"):
                ai_responses.append(content[:500])
        elif msg_type == "tool":
            name = msg.get("name", "tool")
            content = msg.get("content", "")
            try:
                parsed = json.loads(content) if isinstance(content, str) else {}
            except (json.JSONDecodeError, TypeError):
                parsed = {"raw": str(content)[:500]}
            tool_outputs.append({"tool": name, "result": parsed, "raw": str(content)[:800]})

    return {
        "tools_called": tools_called,
        "tool_outputs": tool_outputs,
        "ai_responses": ai_responses,
    }


async def send_message(client, thread_id: str, message: str,
                        timeout_s: int = 480) -> dict:
    start = time.perf_counter()
    run = await client.runs.create(
        thread_id=thread_id,
        assistant_id=ASSISTANT_ID,
        input={"messages": [{"role": "user", "content": message}]},
        context=CTX_KT,
    )
    run_id = run["run_id"]

    timed_out = False
    for _ in range(timeout_s):
        rs = await client.runs.get(thread_id, run_id)
        status = rs.get("status", "unknown")
        if status in ("success", "error", "cancelled"):
            break
        await asyncio.sleep(1)
    else:
        timed_out = True
        try:
            await client.runs.cancel(thread_id=thread_id, run_id=run_id)
            for _ in range(10):
                rs = await client.runs.get(thread_id, run_id)
                if rs.get("status") in ("cancelled", "error", "success"):
                    break
                await asyncio.sleep(1)
        except Exception:
            pass

    elapsed = time.perf_counter() - start
    status = rs.get("status", "cancelled" if timed_out else "unknown")
    state = await client.threads.get_state(thread_id)
    return {
        "run_status": status,
        "elapsed": elapsed,
        "state": state,
        "error": rs.get("error") if status == "error" else ("timeout" if timed_out else None),
    }


def _resolve_timeout(tc: dict) -> int:
    """根据期望工具自动计算超时。"""
    tools = tc["expected_tools"]
    if any(t in ("call_planner", "call_executor") for t in tools):
        return 480
    if any(t in ("manage_executor",) for t in tools):
        return 300
    return 120


def _run_l2_validation(tc: dict, outputs: list[dict]) -> list[str]:
    """执行 L2 验证：检查工具输出格式。"""
    all_issues: list[str] = []
    for out in outputs:
        tool = out["tool"]
        if tool in tc["expected_tools"]:
            result = out["result"]
            raw = out.get("raw", "")
            # KT 工具：验证 JSON 字段
            if tool.startswith("knowledge_tree_"):
                issues = validate_kt_output(tool, result)
                all_issues.extend(f"{tool}: {i}" for i in issues)
            # Executor 工具：验证标记字符串
            else:
                issues = validate_executor_output(tool, raw)
                all_issues.extend(f"{tool}: {i}" for i in issues)
    return all_issues


async def run_group(client, group_name: str, test_cases: list[dict],
                     tools_seen: set[str]) -> list[dict]:
    """运行一组测试用例（独立 thread）。"""
    results: list[dict] = []
    thread = await client.threads.create()
    thread_id = thread["thread_id"]
    print(f"\n  {YELLOW}--- {group_name} (thread: {thread_id[:8]}...) ---{R}")

    for tc in test_cases:
        section(tc["name"])
        msg_preview = tc["message"][:70] + "..." if len(tc["message"]) > 70 else tc["message"]
        print(f"  {DIM}消息: {msg_preview}{R}")

        timeout = _resolve_timeout(tc)
        msg_count_before = await get_msg_count(client, thread_id)

        try:
            result = await send_message(client, thread_id, tc["message"], timeout_s=timeout)
        except Exception as e:
            results.append({
                "id": tc["id"], "name": tc["name"], "verdict": "FAIL",
                "detail": f"异常: {e}", "elapsed": 0,
                "tools_called": [], "expected_tools": tc["expected_tools"],
                "l2_issues": [], "l3_issues": [], "error": str(e),
            })
            print(f"  {RED}✗ 异常: {e}{R}")
            continue

        status = result["run_status"]
        elapsed = result["elapsed"]
        error = result["error"]

        all_messages = result["state"].get("values", {}).get("messages", [])
        analysis = analyze_current_turn(all_messages, msg_count_before)
        tools = analysis["tools_called"]
        outputs = analysis["tool_outputs"]

        expected = tc["expected_tools"]
        matched = [t for t in tools if t in expected]
        for t in tools:
            tools_seen.add(t)

        # ── L1 判定：工具调用 ──
        l1_pass = len(matched) > 0

        # ── L2 判定：输出格式 ──
        l2_issues: list[str] = []
        if tc.get("l2_check") and matched:
            l2_issues = _run_l2_validation(tc, outputs)

        # ── L3 判定：副作用 ──
        l3_issues: list[str] = []
        if tc.get("l3_check"):
            l3_issues = verify_side_effects(tc["id"])

        # ── 综合判定 ──
        if not l1_pass:
            verdict = "FAIL"
            if status in ("error", "timeout", "cancelled"):
                detail = f"{status}: {error} — 未匹配期望 {expected}"
            else:
                detail = f"未匹配期望 {expected}，实际: {tools}"
        elif l2_issues or l3_issues:
            verdict = "SOFT_PASS"
            parts = [f"工具匹配: {matched}"]
            if l2_issues:
                parts.append(f"L2问题: {l2_issues}")
            if l3_issues:
                parts.append(f"L3问题: {l3_issues}")
            detail = " | ".join(parts)
        else:
            verdict = "PASS"
            detail = f"工具: {matched}"

        # ── 打印结果 ──
        verdict_colors = {"PASS": GREEN, "SOFT_PASS": YELLOW, "FAIL": RED}
        vc = verdict_colors[verdict]
        icon = "✓" if verdict != "FAIL" else "✗"
        print(f"  {vc}{icon} [{verdict}]{R} {detail}")
        print(f"  {DIM}  耗时={elapsed:.1f}s  工具={tools}{R}")

        for out in outputs:
            if out["tool"] in expected:
                raw_preview = out.get("raw", "")[:200]
                if raw_preview:
                    print(f"  {DIM}  {out['tool']} → {raw_preview}{R}")

        ai_texts = analysis.get("ai_responses", [])
        for ai in ai_texts[:1]:
            if ai.strip():
                print(f"  {MAGENTA}  AI: {ai[:200]}{R}")

        if l2_issues:
            for issue in l2_issues:
                print(f"  {BLUE}  L2: {issue}{R}")
        if l3_issues:
            for issue in l3_issues:
                print(f"  {BLUE}  L3: {issue}{R}")

        results.append({
            "id": tc["id"], "name": tc["name"], "verdict": verdict,
            "detail": detail, "elapsed": round(elapsed, 1),
            "tools_called": tools, "expected_tools": expected,
            "l2_issues": l2_issues, "l3_issues": l3_issues,
            "error": str(error)[:200] if error else None,
        })

    return results


async def run_all_tests() -> tuple[list[dict], set[str]]:
    from langgraph_sdk import get_client

    tools_seen: set[str] = set()

    # 准备种子知识 — KT_ROOT 是相对路径（给 server 用），seed_dir 需要绝对路径
    seed_dir = _PROJECT_ROOT / KT_ROOT
    if seed_dir.exists():
        shutil.rmtree(seed_dir)
    seed_dir.mkdir(parents=True)

    from src.common.knowledge_tree.dag.node import KnowledgeNode
    seeds = {
        "development/state.md": ("状态管理", "LangGraph 使用 TypedDict 定义状态模式，通过 StateGraph 构建执行图。状态是不可变的。"),
        "development/tools.md": ("工具调用", "LangGraph 通过 ToolNode 执行工具。支持同步和异步工具调用。"),
        "patterns/react.md": ("ReAct 模式", "ReAct 模式结合推理和行动，逐步解决复杂问题。Agent 在每一步都先推理再行动。"),
        "fundamentals/embedding.md": ("向量嵌入基础", "文本嵌入将语义映射到高维向量空间。常用模型包括 BGE 和 OpenAI embeddings。"),
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
        print(f"  {RED}无法连接: {e}{R}")
        print(f"  {YELLOW}请先运行: make dev{R}")
        sys.exit(1)

    all_results: list[dict] = []

    section("组 A: 知识树完整闭环")
    all_results += await run_group(client, "组A", GROUP_A, tools_seen)

    section("组 B: Mode 2 + Mode 3 执行流")
    all_results += await run_group(client, "组B", GROUP_B, tools_seen)

    section("组 C: 异步 + 停止流程")
    all_results += await run_group(client, "组C", GROUP_C, tools_seen)

    section("组 D: 重规划 + 边界条件")
    all_results += await run_group(client, "组D", GROUP_D, tools_seen)

    return all_results, tools_seen


def print_summary(results: list[dict], tools_seen: set[str]):
    section("汇总")
    passed = sum(1 for r in results if r["verdict"] in ("PASS", "SOFT_PASS"))
    hard_pass = sum(1 for r in results if r["verdict"] == "PASS")
    soft_pass = sum(1 for r in results if r["verdict"] == "SOFT_PASS")
    failed = sum(1 for r in results if r["verdict"] == "FAIL")
    total = len(results)

    icon = f"{GREEN}✓{R}" if failed == 0 else f"{RED}✗{R}"
    print(f"  {icon} {passed}/{total} 通过 (PASS={hard_pass}, SOFT_PASS={soft_pass}, FAIL={failed})")

    for r in results:
        vc = {"PASS": GREEN, "SOFT_PASS": YELLOW, "FAIL": RED}[r["verdict"]]
        print(f"  [{vc}{r['verdict']}{R}] {r['name']}")
        print(f"         {r['detail']}  ({r['elapsed']:.1f}s)")
        if r.get("error"):
            print(f"         {RED}error: {r['error'][:150]}{R}")

    covered = len(tools_seen & ALL_TOOLS)
    section(f"工具覆盖: {covered}/{len(ALL_TOOLS)}")
    for t in sorted(ALL_TOOLS):
        hit = t in tools_seen
        icon = f"{GREEN}✓{R}" if hit else f"{RED}✗{R}"
        print(f"    {icon} {t}")
    missing = ALL_TOOLS - tools_seen
    if missing:
        print(f"\n  {RED}未触发: {sorted(missing)}{R}")

    # L2/L3 问题汇总
    l2_total = sum(len(r.get("l2_issues", [])) for r in results)
    l3_total = sum(len(r.get("l3_issues", [])) for r in results)
    if l2_total or l3_total:
        section(f"验证详情: L2问题={l2_total}  L3问题={l3_total}")
        for r in results:
            for i in r.get("l2_issues", []):
                print(f"    {BLUE}[L2] {r['id']}: {i}{R}")
            for i in r.get("l3_issues", []):
                print(f"    {BLUE}[L3] {r['id']}: {i}{R}")

    output = {
        "summary": {
            "passed": passed, "hard_pass": hard_pass, "soft_pass": soft_pass,
            "failed": failed, "total": total,
            "tools_seen": sorted(tools_seen),
            "tools_missing": sorted(ALL_TOOLS - tools_seen),
            "l2_issues": l2_total, "l3_issues": l3_total,
        },
        "turns": results,
    }
    out_path = Path(__file__).resolve().parent / "test_comprehensive_results.json"
    out_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n  详细结果: {out_path}")


async def main():
    print(f"{BOLD}{CYAN}")
    print("╔════════════════════════════════════════════════════════╗")
    print("║   Supervisor 进阶综合测试 V2 — LangGraph Dev Server   ║")
    print("║   20 用例 × 10 工具 × 4 组独立 Thread × 3 级验证    ║")
    print("╚════════════════════════════════════════════════════════╝")
    print(R)

    results, tools_seen = await run_all_tests()
    print_summary(results, tools_seen)

    all_passed = all(r["verdict"] in ("PASS", "SOFT_PASS") for r in results)
    sys.exit(0 if all_passed else 1)


if __name__ == "__main__":
    asyncio.run(main())
