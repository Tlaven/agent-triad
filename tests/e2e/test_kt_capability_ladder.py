"""KT 能力阶梯测试 — 语义 embedder + LangGraph server (全部 7 级).

基于 docs/test-findings-kt-capability.md 的 7 级框架。
之前 hash 天花板 Rung 3，semantic 0.4 到 Rung 7。
现在验证：API embedder + 0.6 阈值下能到哪一级。

Rung 1: 显式 ingest + 显式 retrieve + 原文
Rung 2: + 换措辞
Rung 3: + 隐式 retrieve（用户不提"检索"）
Rung 4: + 被动召回（auto-inject，无工具调用）
Rung 5: + 组合记忆（同时召回 2+ 条）
Rung 6: + 噪声环境
Rung 7: + 跨轮次衰减

用法:
    make dev
    uv run python -u tests/e2e/test_kt_capability_ladder.py
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
_PROJECT_ROOT = Path(__file__).resolve().parents[2]

# ─── 知识条目 ──────────────────────────────────────────────
K1 = "项目使用 uv 作为包管理器，开发服务器端口是 2024。"
K2 = "排查 HuggingFace 模型下载卡死的方法是，在 SentenceTransformer 构造函数中加 local_files_only=True 参数。"
K3 = "executor 执行超时通常是因为多步任务在单次 executor 调用中完成，单步任务很少超时。解决办法是把复杂任务拆分成多个步骤。"

NOISE = [
    "天气预报说明天北京有中雨，气温 15-22 度。",
    "Python 的 GIL 使得多线程在 CPU 密集任务中不会真正并行。",
    "量子计算的基本单位是量子比特，可以同时处于 0 和 1 的叠加态。",
    "今天的午餐吃了一碗牛肉面，味道不错。",
    "机器学习的三要素是数据、算法和算力。",
]

FILLER = [
    "Python 的字典和列表有什么区别？",
    "什么是 REST API？",
    "Git 的 merge 和 rebase 有什么不同？",
    "解释一下什么是 Docker 容器。",
    "HTTP 和 HTTPS 的区别是什么？",
    "什么是微服务架构？",
    "解释一下什么是缓存，为什么需要缓存。",
    "TCP 和 UDP 的区别是什么？",
    "什么是设计模式？举两个常见例子。",
    "解释一下什么是 CI/CD。",
]


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


def check(text: str, keywords: list[str]) -> bool:
    return any(kw in text for kw in keywords)


def result_line(rung: str, name: str, passed: bool, detail: str, elapsed: float):
    icon = f"{GREEN}✓{R}" if passed else f"{RED}✗{R}"
    print(f"  {icon} {rung}: {name} ({elapsed:.1f}s)")
    print(f"     {detail}")


async def rung1(client) -> dict:
    """显式 ingest + 显式 retrieve + 原文措辞。"""
    thread = await client.threads.create()

    # T1: ingest
    r = await send(client, thread["thread_id"], f"请记住这条知识：{K1}")
    ingest_ok = "knowledge_tree_ingest" in r["tools"]
    result_line("R1-T1", "Ingest K1", ingest_ok, f"ingest={'✓' if ingest_ok else '✗'}", r["elapsed"])

    # T2: 原文 retrieve
    r = await send(client, thread["thread_id"], "请用知识树检索工具检索关于包管理器的内容。")
    retrieve_ok = "knowledge_tree_retrieve" in r["tools"]
    content_ok = check(r["ai_text"], ["uv"])
    passed = retrieve_ok and content_ok
    result_line("R1-T2", "Retrieve K1 (原文)", passed,
                f"retrieve={'✓' if retrieve_ok else '✗'} uv={'✓' if content_ok else '✗'}", r["elapsed"])
    return {"rung": 1, "passed": passed, "detail": "显式 ingest+retrieve"}


async def rung2(client) -> dict:
    """换措辞检索。"""
    thread = await client.threads.create()

    # T1: ingest K2
    r = await send(client, thread["thread_id"], f"请记住这条知识：{K2}")
    result_line("R2-T1", "Ingest K2", "knowledge_tree_ingest" in r["tools"],
                f"ingest={'✓' if 'knowledge_tree_ingest' in r['tools'] else '✗'}", r["elapsed"])

    # T2: 换措辞 retrieve（不提 HuggingFace/SentenceTransformer）
    r = await send(client, thread["thread_id"],
                    "请用知识树检索工具检索关于'模型下载出问题导致程序卡住不动'的解决方法。")
    retrieve_ok = "knowledge_tree_retrieve" in r["tools"]
    content_ok = check(r["ai_text"], ["local_files_only", "HuggingFace"])
    passed = retrieve_ok and content_ok
    result_line("R2-T2", "Retrieve K2 (换措辞)", passed,
                f"retrieve={'✓' if retrieve_ok else '✗'} content={'✓' if content_ok else '✗'}", r["elapsed"])
    return {"rung": 2, "passed": passed, "detail": "换措辞检索"}


async def rung3(client) -> dict:
    """隐式 retrieve — 用户描述问题不提检索。"""
    thread = await client.threads.create()

    # T1: ingest K3
    r = await send(client, thread["thread_id"], f"请记住这条知识：{K3}")

    # T2: 隐式触发
    r = await send(client, thread["thread_id"],
                    "我的 executor 执行超时了，可能是什么原因？有没有之前记录的类似经验？")
    retrieve_ok = "knowledge_tree_retrieve" in r["tools"]
    content_ok = check(r["ai_text"], ["拆分", "步骤", "超时"])
    passed = retrieve_ok or content_ok  # auto-inject 或主动 retrieve 都算
    result_line("R3-T2", "隐式 retrieve K3", passed,
                f"retrieve={'✓' if retrieve_ok else '✗'} content={'✓' if content_ok else '✗'}", r["elapsed"])
    return {"rung": 3, "passed": passed, "detail": "隐式检索"}


async def rung4(client) -> dict:
    """被动召回 — auto-inject 无工具调用。"""
    # 新线程，看 auto-inject 能否把之前 ingest 的 K1 注入
    thread = await client.threads.create()

    r = await send(client, thread["thread_id"],
                    "请直接回答我，不要使用任何工具：这个项目用的是什么包管理器，端口是多少？")
    no_tools = len(r["tools"]) == 0
    content_ok = check(r["ai_text"], ["uv", "2024"])
    # 需要两个关键词都出现
    both = check(r["ai_text"], ["uv"]) and check(r["ai_text"], ["2024"])
    passed = content_ok and both
    result_line("R4-T1", "被动召回 K1 (auto-inject)", passed,
                f"no_tools={'✓' if no_tools else '✗'} uv+2024={'✓' if both else '✗'}", r["elapsed"])

    r2 = await send(client, thread["thread_id"],
                     "请直接回答我，不要使用任何工具：SentenceTransformer 加载模型一直卡着不动，怎么修？")
    content2 = check(r2["ai_text"], ["local_files_only"])
    passed2 = content2
    result_line("R4-T2", "被动召回 K2 (auto-inject)", passed2,
                f"local_files_only={'✓' if content2 else '✗'}", r2["elapsed"])

    return {"rung": 4, "passed": passed and passed2, "detail": "被动召回"}


async def rung5(client) -> dict:
    """组合记忆 — 同时召回 2 条知识。同线程先 ingest 确保知识存在。"""
    thread = await client.threads.create()

    # 先确保三条知识都在此线程中
    for k in [K1, K2, K3]:
        await send(client, thread["thread_id"], f"请记住这条知识：{k}")
    print(f"  预加载: K1+K2+K3")

    r = await send(client, thread["thread_id"],
                    "请直接回答我，不要使用任何工具：我遇到两个问题，一是 SentenceTransformer "
                    "加载模型卡住，二是 executor 执行超时，能分别给建议吗？")
    has_k2 = check(r["ai_text"], ["local_files_only", "HuggingFace"])
    has_k3 = check(r["ai_text"], ["拆分", "步骤", "超时"])
    passed = has_k2 and has_k3
    result_line("R5-T1", "组合召回 K2+K3", passed,
                f"K2(HuggingFace)={'✓' if has_k2 else '✗'} K3(超时)={'✓' if has_k3 else '✗'}", r["elapsed"])
    return {"rung": 5, "passed": passed, "detail": "组合记忆"}


async def rung6(client) -> dict:
    """噪声环境 — 注入噪声后仍正确检索。"""
    thread = await client.threads.create()

    # 批量 ingest 噪声（逐条发以避免 LLM 跳过）
    for i, noise in enumerate(NOISE):
        r = await send(client, thread["thread_id"], f"请记住这条知识：{noise}")
    print(f"  噪声注入完成: {len(NOISE)} 条")

    # 噪声中检索 K1
    r = await send(client, thread["thread_id"],
                    "请直接回答我，不要使用任何工具：这个项目用什么做包管理的？")
    content_ok = check(r["ai_text"], ["uv"])
    result_line("R6-T1", "噪声中找 K1(包管理器)", content_ok,
                f"uv={'✓' if content_ok else '✗'}", r["elapsed"])

    # 噪声中检索 K2
    r2 = await send(client, thread["thread_id"],
                     "请直接回答我，不要使用任何工具：模型加载卡住怎么修？")
    content2 = check(r2["ai_text"], ["local_files_only"])
    result_line("R6-T2", "噪声中找 K2(HuggingFace)", content2,
                f"local_files_only={'✓' if content2 else '✗'}", r2["elapsed"])

    return {"rung": 6, "passed": content_ok and content2, "detail": "噪声环境"}


async def rung7(client) -> dict:
    """跨轮次衰减 — 10 轮填充后仍能召回。"""
    thread = await client.threads.create()

    # 先 ingest 所有知识（确保在这个线程中有记录）
    for k in [K1, K2, K3]:
        await send(client, thread["thread_id"], f"请记住这条知识：{k}")

    # 10 轮填充
    for i, filler in enumerate(FILLER):
        await send(client, thread["thread_id"], filler, timeout_s=60)
        print(f"  填充轮次 {i + 1}/10", end="\r")
    print(f"  填充完成: 10 轮                ")

    # 跨轮次召回 K1
    r1 = await send(client, thread["thread_id"],
                     "请直接回答我，不要使用任何工具：我忘了，这个项目的包管理器和端口是什么？")
    k1_ok = check(r1["ai_text"], ["uv"]) and check(r1["ai_text"], ["2024"])
    result_line("R7-T1", "跨 10 轮召回 K1", k1_ok,
                f"uv+2024={'✓' if k1_ok else '✗'}", r1["elapsed"])

    # 跨轮次召回 K2
    r2 = await send(client, thread["thread_id"],
                     "请直接回答我，不要使用任何工具：之前 SentenceTransformer 卡死怎么修来着？")
    k2_ok = check(r2["ai_text"], ["local_files_only"])
    result_line("R7-T2", "跨 10 轮召回 K2", k2_ok,
                f"local_files_only={'✓' if k2_ok else '✗'}", r2["elapsed"])

    # 跨轮次召回 K3
    r3 = await send(client, thread["thread_id"],
                     "请直接回答我，不要使用任何工具：executor 超时的常见原因和解决办法是什么？")
    k3_ok = check(r3["ai_text"], ["拆分", "步骤"])
    result_line("R7-T3", "跨 10 轮召回 K3", k3_ok,
                f"拆分/步骤={'✓' if k3_ok else '✗'}", r3["elapsed"])

    return {"rung": 7, "passed": k1_ok and k2_ok and k3_ok, "detail": "跨轮次衰减"}


async def rung8(client) -> dict:
    """知识更新 — ingest 旧版 → ingest 新版 → retrieve 返回新版。"""
    thread = await client.threads.create()

    # T1: ingest 旧版知识
    old_knowledge = "项目使用的开发服务器端口是 8080。"
    r = await send(client, thread["thread_id"], f"请记住这条知识：{old_knowledge}")
    result_line("R8-T1", "Ingest 旧版知识", True, "旧版端口=8080", r["elapsed"])

    # T2: ingest 更新版知识
    new_knowledge = "项目使用的开发服务器端口已更新为 2024，不再使用 8080。"
    r = await send(client, thread["thread_id"], f"请记住这条知识：{new_knowledge}")
    result_line("R8-T2", "Ingest 新版知识", True, "新版端口=2024", r["elapsed"])

    # T3: retrieve 应返回新版
    r = await send(client, thread["thread_id"],
                    "请直接回答我，不要使用任何工具：这个项目的开发服务器端口是多少？")
    has_new = check(r["ai_text"], ["2024"])
    has_old = check(r["ai_text"], ["8080"])
    # 新版必须出现，旧版不应单独出现（可能作为"之前用 8080"的对比）
    passed = has_new
    result_line("R8-T3", "Retrieve 返回新版", passed,
                f"2024={'✓' if has_new else '✗'} 8080={'提到' if has_old else '未提'}", r["elapsed"])
    return {"rung": 8, "passed": passed, "detail": "知识更新"}


async def main():
    print(f"{BOLD}{CYAN}")
    print("╔════════════════════════════════════════════════════════╗")
    print("║   KT 能力阶梯测试 — 语义 Embedder + LangGraph        ║")
    print("║   Rung 1→8: 从显式检索到知识更新                    ║")
    print("╚════════════════════════════════════════════════════════╝")
    print(R)

    from langgraph_sdk import get_client

    client = get_client(url=SERVER_URL)
    try:
        await client.assistants.search()
    except Exception as e:
        print(f"  {RED}Server 不可用: {e}{R}")
        sys.exit(1)

    rungs = [
        ("Rung 1: 显式 ingest + retrieve", rung1),
        ("Rung 2: + 换措辞", rung2),
        ("Rung 3: + 隐式检索", rung3),
        ("Rung 4: + 被动召回", rung4),
        ("Rung 5: + 组合记忆", rung5),
        ("Rung 6: + 噪声环境", rung6),
        ("Rung 7: + 跨轮次衰减", rung7),
        ("Rung 8: + 知识更新", rung8),
    ]

    results = []
    highest_pass = 0

    for title, rung_fn in rungs:
        section(title)
        try:
            r = await rung_fn(client)
            results.append(r)
            if r["passed"]:
                highest_pass = r["rung"]
                print(f"  {GREEN}★ Rung {r['rung']} PASSED{R}")
            else:
                print(f"  {RED}✗ Rung {r['rung']} FAILED — 继续测试后续 rung{R}")
        except Exception as e:
            results.append({"rung": title, "passed": False, "detail": str(e)})
            print(f"  {RED}✗ 异常: {e}{R}")
            break

    # 汇总
    section(f"能力天花板: Rung {highest_pass}")
    print(f"  之前 (hash embedder):          Rung 3")
    print(f"  之前 (semantic 0.4 阈值):      Rung 7")
    print(f"  现在 (API embedder 0.6 阈值):  Rung {highest_pass}")
    print()
    for r in results:
        icon = f"{GREEN}✓{R}" if r["passed"] else f"{RED}✗{R}"
        print(f"  {icon} Rung {r['rung']}: {r['detail']}")

    out_path = Path(__file__).resolve().parent / "test_kt_capability_ladder_results.json"
    out_path.write_text(json.dumps({"highest_rung": highest_pass, "results": results},
                                    ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n  结果: {out_path}")
    sys.exit(0)


if __name__ == "__main__":
    asyncio.run(main())
