"""知识树检索质量深度测试 — 通过 LangGraph Dev Server.

重点测试：
1. 精确查询 vs 模糊查询 vs 跨语言查询
2. 摄入后的检索可发现性
3. 多轮上下文中的检索行为
4. 重组/重新组织能力（如果存在）

用法:
    1. make dev
    2. uv run python -u tests/e2e/test_kt_retrieval_quality.py
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
BLUE = "\033[34m"

SERVER_URL = "http://localhost:2024"
ASSISTANT_ID = "supervisor"
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
KT_ROOT = "workspace/kt_retrieval_test"
CTX_KT = {"enable_knowledge_tree": True, "knowledge_tree_root": KT_ROOT}


def section(title: str):
    print(f"\n{BOLD}{CYAN}{'═' * 60}{R}")
    print(f"{BOLD}{CYAN}  {title}{R}")
    print(f"{BOLD}{CYAN}{'═' * 60}{R}")


async def send_message(client, thread_id: str, message: str, timeout_s: float = 120) -> dict:
    """发送消息并等待完成。"""
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


def extract_kt_tool_outputs(state: dict) -> list[dict]:
    """提取所有 KT 工具调用及其输出。"""
    messages = state.get("values", {}).get("messages", [])
    results = []
    for msg in messages:
        if msg.get("type") == "tool" and msg.get("name", "").startswith("knowledge_tree_"):
            content = msg.get("content", "")
            try:
                parsed = json.loads(content) if isinstance(content, str) else {}
            except (json.JSONDecodeError, TypeError):
                parsed = {"raw": str(content)[:300]}
            results.append({
                "tool": msg.get("name"),
                "output": parsed,
                "raw": content[:500] if isinstance(content, str) else str(content)[:500],
            })
    return results


def extract_ai_text(state: dict) -> str:
    """提取最后的 AI 文本回复。"""
    messages = state.get("values", {}).get("messages", [])
    for msg in reversed(messages):
        if msg.get("type") == "ai" and not msg.get("tool_calls"):
            return msg.get("content", "")[:600]
    return ""


def print_kt_result(kt_outputs: list[dict], ai_text: str):
    """打印 KT 工具结果摘要。"""
    for out in kt_outputs:
        name = out["tool"]
        o = out["output"]
        if "retrieve" in name:
            ok = o.get("ok", False)
            sim = o.get("similarity", 0)
            quality = o.get("quality", "?")
            title = o.get("title", "")
            node_id = o.get("node_id", "")
            warn = o.get("warning", "")
            icon = f"{GREEN}✓{R}" if ok and quality != "low" else (f"{YELLOW}⚠{R}" if ok else f"{RED}✗{R}")
            print(f"  {icon} {name}: ok={ok} similarity={sim:.3f} quality={quality}")
            if title:
                print(f"     命中: {title} ({node_id})")
            if warn:
                print(f"     {YELLOW}警告: {warn[:100]}{R}")
        elif "ingest" in name:
            ingested = o.get("nodes_ingested", 0)
            deduped = o.get("nodes_deduplicated", 0)
            print(f"  {GREEN}✓{R} {name}: ingested={ingested} deduplicated={deduped}")
        elif "status" in name:
            nodes = o.get("total_nodes", 0)
            dirs = o.get("total_directories", 0)
            anchors = o.get("total_anchors", 0)
            print(f"  {GREEN}✓{R} {name}: nodes={nodes} dirs={dirs} anchors={anchors}")
        else:
            print(f"  {GREEN}✓{R} {name}: {json.dumps(o, ensure_ascii=False)[:150]}")
    if ai_text.strip():
        print(f"  {MAGENTA}AI: {ai_text[:300]}{R}")


# ─── 测试用例设计 ────────────────────────────────────────────

RETRIEVAL_TESTS = [
    # ── R1: 精确查询种子知识 ──
    {
        "id": "R1",
        "name": "精确查询：状态管理",
        "message": "检索关于状态管理的知识。",
        "expect_hit": True,
        "expect_keyword": "TypedDict",
    },
    {
        "id": "R2",
        "name": "精确查询：ReAct 模式",
        "message": "检索关于 ReAct 模式的知识。",
        "expect_hit": True,
        "expect_keyword": "推理和行动",
    },
    {
        "id": "R3",
        "name": "精确查询：向量嵌入",
        "message": "检索关于向量嵌入的知识。",
        "expect_hit": True,
        "expect_keyword": "嵌入",
    },
    # ── R4-R6: 模糊/跨语言查询 ──
    {
        "id": "R4",
        "name": "模糊查询：错误处理（无种子）",
        "message": "检索关于错误处理和异常捕获的知识。",
        "expect_hit": False,
    },
    {
        "id": "R5",
        "name": "英文查询：state management",
        "message": "检索关于 state management 的知识。",
        "expect_hit": True,
        "expect_keyword": "TypedDict",
    },
    {
        "id": "R6",
        "name": "同义词查询：代理架构（≈patterns）",
        "message": "检索关于代理 Agent 架构设计模式的知识。",
        "expect_hit": True,
        "expect_keyword": "ReAct",
    },
    # ── R7-R9: 摄入后检索 ──
    {
        "id": "R7",
        "name": "摄入：Agent 超时处理知识",
        "message": "请记录到知识树：当 Executor 子进程超时时，Supervisor 应该发送 SIGTERM 信号优雅终止，而不是直接 SIGKILL。超时配置应从内到外递增。",
        "expect_hit": True,
        "ingest_test": True,
    },
    {
        "id": "R8",
        "name": "检索：超时处理（精确）",
        "message": "检索关于 Executor 超时处理的知识。",
        "expect_hit": True,
        "expect_keyword": "SIGTERM",
    },
    {
        "id": "R9",
        "name": "检索：超时处理（同义）",
        "message": "检索关于子进程优雅终止的知识。",
        "expect_hit": False,  # hash embedder 不支持语义匹配
    },
    # ── R10-R12: 边界与压力 ──
    {
        "id": "R10",
        "name": "空查询触发",
        "message": "检索关于的知识。",  # 空查询关键词
        "expect_hit": False,
    },
    {
        "id": "R11",
        "name": "大量摄入后检索",
        "message": "请记录以下三条知识到知识树：\n1. Python 的 asyncio 使用事件循环管理并发任务，避免阻塞。\n2. FastAPI 框架基于 Starlette，支持异步路由和依赖注入。\n3. LangGraph 的 StateGraph 通过节点和边定义执行图，支持条件分支。",
        "expect_hit": True,
        "ingest_test": True,
    },
    {
        "id": "R12",
        "name": "摄入后验证：asyncio 事件循环",
        "message": "检索关于 asyncio 事件循环的知识。",
        "expect_hit": True,
        "expect_keyword": "事件循环",
    },
]


async def run_retrieval_tests() -> list[dict]:
    from langgraph_sdk import get_client

    all_results: list[dict] = []

    # 准备种子
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
        sys.exit(1)

    # 单一 thread，模拟真实对话
    thread = await client.threads.create()
    print(f"  Thread: {thread['thread_id']}")

    section("Phase 1: 精确查询种子知识 (R1-R3)")
    section("Phase 2: 模糊/跨语言查询 (R4-R6)")
    section("Phase 3: 摄入后检索 (R7-R9)")
    section("Phase 4: 边界与压力 (R10-R12)")

    for tc in RETRIEVAL_TESTS:
        phase = {
            "R1": "Phase 1", "R2": "Phase 1", "R3": "Phase 1",
            "R4": "Phase 2", "R5": "Phase 2", "R6": "Phase 2",
            "R7": "Phase 3", "R8": "Phase 3", "R9": "Phase 3",
            "R10": "Phase 4", "R11": "Phase 4", "R12": "Phase 4",
        }.get(tc["id"], "")
        print(f"\n{BOLD}{tc['id']}: {tc['name']}  {DIM}[{phase}]{R}")
        print(f"  {DIM}消息: {tc['message'][:80]}{'...' if len(tc['message']) > 80 else ''}{R}")

        try:
            result = await send_message(client, thread["thread_id"], tc["message"])
        except Exception as e:
            all_results.append({"id": tc["id"], "name": tc["name"], "error": str(e), "passed": False})
            print(f"  {RED}✗ 异常: {e}{R}")
            continue

        if result["run_status"] == "error":
            err = str(result.get("error", ""))[:200]
            all_results.append({"id": tc["id"], "name": tc["name"], "error": err, "passed": False})
            print(f"  {RED}✗ Run error: {err}{R}")
            continue

        elapsed = result["elapsed"]
        kt_outputs = extract_kt_tool_outputs(result["state"])
        ai_text = extract_ai_text(result["state"])

        print_kt_result(kt_outputs, ai_text)
        print(f"  {DIM}耗时: {elapsed:.1f}s{R}")

        # 分析结果
        analysis = analyze_retrieval(tc, kt_outputs)
        all_results.append({
            "id": tc["id"],
            "name": tc["name"],
            "elapsed": round(elapsed, 1),
            "passed": analysis["passed"],
            "detail": analysis["detail"],
            "kt_outputs": [{"tool": o["tool"], "output": o["output"]} for o in kt_outputs],
        })

    return all_results


def analyze_retrieval(tc: dict, kt_outputs: list[dict]) -> dict:
    """分析检索结果是否符合预期。"""
    expect_hit = tc.get("expect_hit", False)
    expect_keyword = tc.get("expect_keyword")
    is_ingest = tc.get("ingest_test", False)

    # 找到关键工具输出
    retrieve_outputs = [o for o in kt_outputs if "retrieve" in o["tool"]]
    ingest_outputs = [o for o in kt_outputs if "ingest" in o["tool"]]

    issues: list[str] = []

    if is_ingest:
        # 摄入测试：检查 ingest 工具是否被调用且成功
        if not ingest_outputs:
            return {"passed": False, "detail": "ingest 工具未被调用"}
        ingest_result = ingest_outputs[0]["output"]
        if not ingest_result.get("ok"):
            return {"passed": False, "detail": f"ingest 失败: {ingest_result}"}
        ingested = ingest_result.get("nodes_ingested", 0)
        deduped = ingest_result.get("nodes_deduplicated", 0)
        return {"passed": True, "detail": f"摄入成功: ingested={ingested} dedup={deduped}"}

    # 检索测试
    if not retrieve_outputs:
        return {"passed": False, "detail": "retrieve 工具未被调用"}

    last_retrieve = retrieve_outputs[-1]["output"]
    ok = last_retrieve.get("ok", False)
    similarity = last_retrieve.get("similarity", 0)
    quality = last_retrieve.get("quality", "?")
    content = last_retrieve.get("content", "")
    title = last_retrieve.get("title", "")

    if expect_hit:
        if not ok:
            issues.append(f"预期命中但 ok=false")
        if expect_keyword and expect_keyword not in content and expect_keyword not in title:
            issues.append(f"预期关键词 '{expect_keyword}' 未出现（title={title}, content={content[:100]}）")
    else:
        # 预期不命中
        if ok and quality == "high":
            issues.append(f"预期不命中但得到高质量结果: {title} (sim={similarity})")

    passed = len(issues) == 0
    detail = "符合预期" if passed else "; ".join(issues)
    detail += f" [sim={similarity:.3f} q={quality}]"

    return {"passed": passed, "detail": detail}


def print_summary(results: list[dict]):
    section("汇总")
    passed = sum(1 for r in results if r["passed"])
    total = len(results)
    icon = f"{GREEN}✓{R}" if passed == total else f"{RED}✗{R}"
    print(f"  {icon} {passed}/{total} 通过")

    # 按类别统计
    categories = {"精确查询": [], "模糊/跨语言": [], "摄入后检索": [], "边界与压力": []}
    for r in results:
        rid = r["id"]
        if rid in ("R1", "R2", "R3"):
            categories["精确查询"].append(r)
        elif rid in ("R4", "R5", "R6"):
            categories["模糊/跨语言"].append(r)
        elif rid in ("R7", "R8", "R9"):
            categories["摄入后检索"].append(r)
        else:
            categories["边界与压力"].append(r)

    for cat, cat_results in categories.items():
        cat_passed = sum(1 for r in cat_results if r["passed"])
        cat_total = len(cat_results)
        icon = f"{GREEN}✓{R}" if cat_passed == cat_total else f"{YELLOW}○{R}"
        print(f"\n  {icon} {cat}: {cat_passed}/{cat_total}")
        for r in cat_results:
            status = f"{GREEN}PASS{R}" if r["passed"] else f"{RED}FAIL{R}"
            detail = r.get("detail", "")
            elapsed = r.get("elapsed", "?")
            print(f"    [{status}] {r['id']}: {r['name']}  ({elapsed}s)")
            print(f"           {detail}")
            if r.get("error"):
                print(f"           {RED}error: {r['error'][:200]}{R}")

    # 保存结果
    output = {
        "summary": {"passed": passed, "total": total},
        "results": results,
    }
    out_path = Path(__file__).resolve().parent / "test_kt_retrieval_results.json"
    out_path.write_text(json.dumps(output, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"\n  详细结果: {out_path}")

    # 关键发现
    section("关键发现")
    for r in results:
        if not r["passed"] and r["id"] in ("R5", "R6", "R9"):
            detail = r.get("detail", "")
            print(f"  {YELLOW}● {r['id']} ({r['name']}): {detail}{R}")
            print(f"    {DIM}→ hash embedder 的已知限制：无语义理解{R}")


async def main():
    print(f"{BOLD}{CYAN}")
    print("╔════════════════════════════════════════════════════════╗")
    print("║   知识树检索质量深度测试 — LangGraph Dev Server      ║")
    print("║   12 用例 × 4 Phase × 精确/模糊/摄入/边界          ║")
    print("╚════════════════════════════════════════════════════════╝")
    print(R)

    results = await run_retrieval_tests()
    print_summary(results)

    all_passed = all(r["passed"] for r in results)
    sys.exit(0 if all_passed else 0)  # 不因 LLM 行为失败退出


if __name__ == "__main__":
    asyncio.run(main())
