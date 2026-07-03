"""N4 路径判定探测脚本：绕过 LangChain 直调 GLM，看 content 与 tool_calls 是否解耦。

复现 s002-t15：用户"不调用任何工具" + 简单题。
- 若 GLM 直接返回 content="1024" + tool_calls 非空 → A 路径（模型层解耦）
- 若 GLM 返回 content="1024" + tool_calls=[] → C 路径（LangChain bind_tools 注入）

Usage: uv run python scripts/n4_glm_probe.py [--model kimi-k2.6]
结果记录到 docs/n4-diagnosis-result.md §2。
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# ruff: noqa: T201, E402

S002_T15_USER = "你能否只回答不调用任何工具？问题：'2 的 10 次方是多少？'"


def _build_openai_client(model_id: str) -> object:
    from openai import OpenAI

    base_url = os.environ.get("OPENAI_BASE_URL", "https://opencode.ai/zen/go/v1")
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        print("错误：OPENAI_API_KEY 未设置", file=sys.stderr)
        raise SystemExit(2)
    return OpenAI(api_key=api_key, base_url=base_url), model_id


async def _get_supervisor_tools_openai() -> list[dict]:
    """取 Supervisor 真实工具集并转 OpenAI function schema。"""
    from langchain_core.utils.function_calling import convert_to_openai_tool

    from src.supervisor_agent.tools import get_tools

    tools = await get_tools(None)
    return [convert_to_openai_tool(t) for t in tools]


def _get_system_prompt() -> str:
    from src.supervisor_agent.prompts import get_supervisor_system_prompt

    return get_supervisor_system_prompt(None)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="kimi-k2.6", help="GLM 模型名")
    args = parser.parse_args()

    print(f"[N4 probe] 直调 GLM（绕过 LangChain），model={args.model}")
    print(f"[N4 probe] user message: {S002_T15_USER!r}")

    client, model_id = _build_openai_client(args.model)
    system_prompt = _get_system_prompt()
    print(f"[N4 probe] system_prompt length: {len(system_prompt)} 字符")

    try:
        tools_schema = asyncio.run(_get_supervisor_tools_openai())
        print(f"[N4 probe] 工具数: {len(tools_schema)} (names={ [t.get('function',{}).get('name') for t in tools_schema[:5]] }...)")
    except Exception as e:
        print(f"[N4 probe] 警告：取真实工具失败 ({e})，回退到单 noop 工具测试解耦性。")
        tools_schema = [{
            "type": "function",
            "function": {
                "name": "noop",
                "description": "noop placeholder tool",
                "parameters": {"type": "object", "properties": {}},
            },
        }]

    # 模拟 Supervisor bind_tools 后调用：传 tools + tool_choice="auto"
    resp = client.chat.completions.create(
        model=model_id,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": S002_T15_USER},
        ],
        tools=tools_schema,
        tool_choice="auto",
    )

    msg = resp.choices[0].message
    content = msg.content or ""
    tc_names = [tc.function.name for tc in (msg.tool_calls or [])]

    print("\n=== GLM 直接 API 返回 ===")
    print(f"content: {content!r}")
    print(f"tool_calls ({len(tc_names)}): {tc_names}")

    print("\n=== 路径判定 ===")
    if tc_names and content.strip():
        print("判定: A 路径 — GLM 模型层 content/tool_calls 解耦（content={!r} 同时 tool_calls={}）".format(content[:80], tc_names))
        print("  → bind_tools 未注入；LLM 直发 tool_calls。修复方向：user message 语义 strip。")
    elif not tc_names and content.strip():
        print("判定: C 路径 — GLM 直调无 tool_calls，说明 LangChain bind_tools 包装层注入了它们。")
        print("  → 修复方向：查 load_chat_model 的 bind_tools；升级 langchain-openai。")
    elif tc_names and not content.strip():
        print("判定: 正常委派 mode（content 空 + tool_calls 非空）— 非 N4 场景，需重新设计触发句。")
    else:
        print("判定: 均空 — 异常，需检查 API 返回。")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())