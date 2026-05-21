"""知识树 Auto-Inject 有效性测试 — 验证 Supervisor 真实利用 KT 上下文.

核心问题：auto-inject 的 [相关知识] 是否真正改变了 Supervisor 的行为？

测试设计：
  A. 对比测试：同一问题，KT 开/关，看回答是否不同
  B. 主动性测试：给 Supervisor 新信息，观察是否主动 ingest
  C. 决策质量测试：问需要 KT 知识才能正确回答的问题

用法:
    1. make dev
    2. uv run python -u tests/e2e/test_kt_auto_inject_effectiveness.py
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
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
KT_ROOT = "workspace/kt_inject_test"
CTX_KT_ON = {"enable_knowledge_tree": True, "knowledge_tree_root": KT_ROOT}
CTX_KT_OFF = {"enable_knowledge_tree": False}


def section(title: str):
    print(f"\n{BOLD}{CYAN}{'═' * 60}{R}")
    print(f"{BOLD}{CYAN}  {title}{R}")
    print(f"{BOLD}{CYAN}{'═' * 60}{R}")


async def send_message(
    client, thread_id: str, message: str, ctx: dict, timeout_s: float = 120
) -> dict:
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


def extract_tool_calls(state: dict) -> list[dict]:
    messages = state.get("values", {}).get("messages", [])
    calls = []
    for msg in messages:
        if msg.get("type") == "ai" and msg.get("tool_calls"):
            for tc in msg["tool_calls"]:
                calls.append({"name": tc.get("name", ""), "args": tc.get("args", {})})
    return calls


def extract_tool_outputs(state: dict) -> list[dict]:
    messages = state.get("values", {}).get("messages", [])
    outputs = []
    for msg in messages:
        if msg.get("type") == "tool":
            outputs.append({
                "name": msg.get("name", ""),
                "content": msg.get("content", "")[:500],
            })
    return outputs


def check_response_mentions(response: str, keywords: list[str]) -> list[str]:
    found = []
    for kw in keywords:
        if kw in response:
            found.append(kw)
    return found


# ─── 测试种子知识 ──────────────────────────────────────────
# 植入一段不在系统提示词中、只有通过 KT 才能知道的知识

SEED_KNOWLEDGE = {
    "operations/turtle-protocol.md": (
        "乌龟协议",
        (
            "当用户提到'慢'、'乌龟'或'爬行'时，Supervisor 应回复："
            "我注意到你提到了速度相关的话题。根据乌龟协议，我建议采用渐进式方法，"
            "先确认当前状态，再逐步优化。乌龟虽慢，但从不后退。"
            "\n\n关键规则：\n"
            "1. 必须在回复中引用'乌龟虽慢，但从不后退'这句话\n"
            "2. 建议采用渐进式方法\n"
            "3. 这段知识只存在于知识树中，不在任何代码或提示词里"
        ),
    ),
    "operations/emergency-code.md": (
        "紧急代码 Omega-7",
        (
            "紧急代码 Omega-7 的含义是：系统检测到知识树与实际行为不一致，"
            "需要立即审计提示词与后端的对齐状态。\n\n"
            "触发条件：用户明确说出 Omega-7\n"
            "响应要求：Supervisor 必须在回复中包含以下内容：\n"
            "1. 确认收到紧急代码\n"
            "2. 说明将进行提示词-后端对齐审计\n"
            "3. 这段知识只存在于知识树中"
        ),
    ),
}


def plant_seeds():
    from src.common.knowledge_tree.dag.node import KnowledgeNode

    seed_dir = _PROJECT_ROOT / KT_ROOT
    if seed_dir.exists():
        shutil.rmtree(seed_dir)
    seed_dir.mkdir(parents=True)

    for rel, (title, content) in SEED_KNOWLEDGE.items():
        node = KnowledgeNode.create(
            node_id=rel, title=title, content=content, source="test_seed"
        )
        p = seed_dir / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(node.to_frontmatter_md(), encoding="utf-8")

    print(f"  种子: {len(SEED_KNOWLEDGE)} 篇 → {seed_dir}")


# ─── 测试用例 ──────────────────────────────────────────────

TEST_CASES = [
    # ── A 组：Auto-Inject 对比测试 ──
    {
        "id": "A1",
        "group": "auto-inject",
        "name": "乌龟协议触发（KT ON）",
        "message": "我的系统跑得好慢，像乌龟一样爬，怎么办？",
        "ctx": CTX_KT_ON,
        "expect_keywords": ["乌龟", "渐进式"],
        "description": "auto-inject 应将乌龟协议知识注入，Supervisor 应在回复中引用",
    },
    {
        "id": "A2",
        "group": "auto-inject",
        "name": "乌龟协议触发（KT OFF 对照）",
        "message": "我的系统跑得好慢，像乌龟一样爬，怎么办？",
        "ctx": CTX_KT_OFF,
        "expect_keywords": [],
        "expect_not_keywords": ["乌龟协议", "乌龟虽慢"],
        "description": "无 KT 时不应出现乌龟协议相关内容",
    },
    {
        "id": "A3",
        "group": "auto-inject",
        "name": "紧急代码 Omega-7（KT ON）",
        "message": "报告紧急代码 Omega-7",
        "ctx": CTX_KT_ON,
        "expect_keywords": ["Omega-7", "对齐"],
        "description": "auto-inject 应将紧急代码知识注入",
    },
    {
        "id": "A4",
        "group": "auto-inject",
        "name": "紧急代码 Omega-7（KT OFF 对照）",
        "message": "报告紧急代码 Omega-7",
        "ctx": CTX_KT_OFF,
        "expect_keywords": [],
        "expect_not_keywords": ["紧急代码 Omega-7", "提示词-后端对齐审计"],
        "description": "无 KT 时不应知道 Omega-7 的含义",
    },
    # ── B 组：主动 Ingest 测试 ──
    {
        "id": "B1",
        "group": "proactive-ingest",
        "name": "主动 ingest 触发",
        "message": (
            "请记住一条重要知识：当 LangGraph dev server 端口 2024 被占用时，"
            "可以用 --port 参数指定其他端口。这个信息对我以后的项目开发很有用。"
        ),
        "ctx": CTX_KT_ON,
        "expect_tool": "knowledge_tree_ingest",
        "description": "Supervisor 应主动调用 knowledge_tree_ingest 记录用户认为重要的知识",
    },
    {
        "id": "B2",
        "group": "proactive-ingest",
        "name": "主动 retrieve 触发（知识不足时）",
        "message": "告诉我关于本项目 Executor 子进程超时处理的所有细节",
        "ctx": CTX_KT_ON,
        "expect_tool": "knowledge_tree_retrieve",
        "description": "当用户问需要深层知识的问题时，Supervisor 应主动 retrieve",
    },
    # ── C 组：种子知识检索验证 ──
    {
        "id": "C1",
        "group": "seed-retrieval",
        "name": "精确查询种子（架构）",
        "message": "用 knowledge_tree_retrieve 搜索关于状态管理的知识",
        "ctx": CTX_KT_ON,
        "expect_tool": "knowledge_tree_retrieve",
        "description": "直接要求 retrieve，验证种子知识可被检索",
    },
]


async def run_tests() -> list[dict]:
    from langgraph_sdk import get_client

    all_results: list[dict] = []
    plant_seeds()

    client = get_client(url=SERVER_URL)
    try:
        assistants = await client.assistants.search()
        section(f"Server: {SERVER_URL}  (assistants: {len(assistants)})")
    except Exception as e:
        section("Server 不可用")
        print(f"  {RED}无法连接: {e}{R}")
        print(f"  {DIM}请先运行: make dev{R}")
        sys.exit(1)

    # 按组运行，每组用不同 thread
    groups = {}
    for tc in TEST_CASES:
        g = tc["group"]
        if g not in groups:
            groups[g] = []
        groups[g].append(tc)

    group_names = {
        "auto-inject": "A 组：Auto-Inject 对比（KT ON vs OFF）",
        "proactive-ingest": "B 组：主动 Ingest/Retrieve",
        "seed-retrieval": "C 组：种子知识检索",
    }

    for g, cases in groups.items():
        section(group_names.get(g, g))

        # 每个测试用独立 thread，避免上下文污染
        for tc in cases:
            print(f"\n{BOLD}{tc['id']}: {tc['name']}{R}")
            print(f"  {DIM}消息: {tc['message'][:80]}{'...' if len(tc['message']) > 80 else ''}{R}")

            thread = await client.threads.create()

            try:
                result = await send_message(
                    client, thread["thread_id"], tc["message"], tc["ctx"]
                )
            except Exception as e:
                all_results.append({
                    "id": tc["id"], "name": tc["name"],
                    "error": str(e), "passed": False,
                })
                print(f"  {RED}✗ 异常: {e}{R}")
                continue

            if result["run_status"] == "error":
                err = str(result.get("error", ""))[:200]
                all_results.append({
                    "id": tc["id"], "name": tc["name"],
                    "error": err, "passed": False,
                })
                print(f"  {RED}✗ Run error: {err}{R}")
                continue

            elapsed = result["elapsed"]
            ai_text = extract_ai_text(result["state"])
            tool_calls = extract_tool_calls(result["state"])
            tool_outputs = extract_tool_outputs(result["state"])

            # 分析
            analysis = analyze_result(tc, ai_text, tool_calls, tool_outputs)
            all_results.append({
                "id": tc["id"],
                "name": tc["name"],
                "elapsed": round(elapsed, 1),
                "passed": analysis["passed"],
                "detail": analysis["detail"],
                "ai_text_length": len(ai_text),
                "tool_calls": [{"name": tc["name"]} for tc in tool_calls],
            })

            # 打印结果
            icon = f"{GREEN}✓{R}" if analysis["passed"] else f"{RED}✗{R}"
            print(f"  {icon} {analysis['detail']}")
            if tool_calls:
                tc_names = [t["name"] for t in tool_calls]
                print(f"  {DIM}工具调用: {', '.join(tc_names)}{R}")
            if ai_text:
                preview = ai_text[:200].replace("\n", " ")
                print(f"  {DIM}AI 回复: {preview}{'...' if len(ai_text) > 200 else ''}{R}")
            print(f"  {DIM}耗时: {elapsed:.1f}s{R}")

    return all_results


def analyze_result(
    tc: dict, ai_text: str, tool_calls: list[dict], tool_outputs: list[dict]
) -> dict:
    issues: list[str] = []
    group = tc["group"]

    if group == "auto-inject":
        # 检查期望关键词是否出现在回复中
        expect_kw = tc.get("expect_keywords", [])
        found = check_response_mentions(ai_text, expect_kw)
        missing = [kw for kw in expect_kw if kw not in found]

        if missing:
            issues.append(f"期望关键词未出现: {missing}")

        # 检查不应出现的关键词（排除用户消息中也出现的词，避免回退误判）
        expect_not = tc.get("expect_not_keywords", [])
        user_msg = tc["message"]
        unexpected = []
        for kw in expect_not:
            if kw in ai_text and kw not in user_msg:
                unexpected.append(kw)
        if unexpected:
            issues.append(f"不应出现的关键词: {unexpected}")

        # KT ON 时不应直接调用 KT 工具（应该是 auto-inject 隐式生效）
        # 但如果调用了也不算失败，只要关键词命中
        kt_tools = [t for t in tool_calls if "knowledge_tree" in t.get("name", "")]
        if kt_tools and not missing:
            pass  # 主动调用 + 关键词命中，更好

    elif group == "proactive-ingest":
        expect_tool = tc.get("expect_tool", "")
        if expect_tool:
            called = [t["name"] for t in tool_calls]
            if expect_tool not in called:
                issues.append(f"期望工具 {expect_tool} 未被调用（实际: {called or '无'}）")

    elif group == "seed-retrieval":
        expect_tool = tc.get("expect_tool", "")
        if expect_tool:
            called = [t["name"] for t in tool_calls]
            if expect_tool not in called:
                issues.append(f"期望工具 {expect_tool} 未被调用")

    passed = len(issues) == 0
    detail = "符合预期" if passed else "; ".join(issues)
    return {"passed": passed, "detail": detail}


def print_summary(results: list[dict]):
    section("汇总")
    passed = sum(1 for r in results if r["passed"])
    total = len(results)
    icon = f"{GREEN}✓{R}" if passed == total else f"{YELLOW}○{R}"
    print(f"  {icon} {passed}/{total} 通过")

    # 按组统计
    groups = {}
    for r in results:
        rid = r["id"]
        g = rid[0]
        if g == "A":
            groups.setdefault("A: Auto-Inject 对比", []).append(r)
        elif g == "B":
            groups.setdefault("B: 主动 Ingest", []).append(r)
        elif g == "C":
            groups.setdefault("C: 种子检索", []).append(r)

    for cat, cat_results in groups.items():
        cat_passed = sum(1 for r in cat_results if r["passed"])
        cat_total = len(cat_results)
        icon = f"{GREEN}✓{R}" if cat_passed == cat_total else f"{YELLOW}○{R}"
        print(f"\n  {icon} {cat}: {cat_passed}/{cat_total}")
        for r in cat_results:
            status = f"{GREEN}PASS{R}" if r["passed"] else f"{RED}FAIL{R}"
            elapsed = r.get("elapsed", "?")
            print(f"    [{status}] {r['id']}: {r['name']}  ({elapsed}s)")
            print(f"           {r.get('detail', '')}")
            if r.get("error"):
                print(f"           {RED}error: {r['error'][:200]}{R}")

    # A 组对比分析
    a_results = [r for r in results if r["id"].startswith("A")]
    if len(a_results) >= 2:
        section("A 组对比分析")
        a_on = [r for r in a_results if "ON" in r["name"]]
        a_off = [r for r in a_results if "OFF" in r["name"]]
        for on, off in zip(a_on, a_off):
            on_detail = on.get("detail", "")
            off_detail = off.get("detail", "")
            pair_icon = f"{GREEN}✓{R}" if on["passed"] and not off["passed"] else f"{YELLOW}○{R}"
            print(f"  {pair_icon} {on['id']}/{off['id']}: KT ON={on_detail} | KT OFF={off_detail}")
            if on["passed"] and off.get("expect_not_keywords"):
                print(f"     {DIM}→ auto-inject 有效性已验证：知识注入改变了行为{R}")

    # 保存结果
    output = {"summary": {"passed": passed, "total": total}, "results": results}
    out_path = Path(__file__).resolve().parent / "test_kt_auto_inject_results.json"
    out_path.write_text(
        json.dumps(output, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    print(f"\n  详细结果: {out_path}")


async def main():
    print(f"{BOLD}{CYAN}")
    print("╔════════════════════════════════════════════════════════╗")
    print("║   知识树 Auto-Inject 有效性测试                      ║")
    print("║   验证 Supervisor 是否真实利用 KT 自动注入的上下文    ║")
    print("║   7 用例 × 3 组 × 对比/主动/检索                    ║")
    print("╚════════════════════════════════════════════════════════╝")
    print(R)

    results = await run_tests()
    print_summary(results)

    # 不因 LLM 行为不确定而退出失败
    sys.exit(0)


if __name__ == "__main__":
    asyncio.run(main())
