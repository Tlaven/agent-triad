# planner_agent/graph.py
"""Planner Agent - ReAct 循环产出意图层 Plan JSON（V2-b 可挂载规划辅助工具 + 只读 MCP）。

消息约定：第一条为完整 Planner 系统提示（`_PLANNER_SYSTEM_PROMPT_TEMPLATE`）；第二条为 Supervisor 提供的 **task_core**（须足够详细）；重规划时第三条为带执行状态的 plan JSON。
"""


import asyncio
import json
import os
import re
from dataclasses import replace
from datetime import UTC, datetime
from typing import Literal
from uuid import uuid4

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode
from langgraph.runtime import Runtime

from src.common.context import Context
from src.common.observation import normalize_tool_message_content
from src.common.utils import invoke_chat_model, load_chat_model
from src.executor_agent.tools import get_executor_capabilities_docs

from src.planner_agent.prompts import get_planner_system_prompt
from src.planner_agent.state import PlannerState
from src.planner_agent.tools import get_planner_tools


def build_planner_messages(
    task_core: str,
    replan_plan_json: str | None,
    planner_history_messages: list[dict[str, str]] | None = None,
) -> list[BaseMessage]:
    """组装 Planner LLM 输入：首条为完整系统提示词，第二条为 task_core，重规划时第三条为 plan JSON。"""
    capabilities = get_executor_capabilities_docs()
    system_text = get_planner_system_prompt(capabilities)
    messages: list[BaseMessage] = [SystemMessage(content=system_text)]
    for msg in planner_history_messages or []:
        role = (msg.get("role") or "").strip().lower()
        content = (msg.get("content") or "").strip()
        if not content:
            continue
        if role == "assistant":
            messages.append(AIMessage(content=content, name="planner"))
        else:
            messages.append(HumanMessage(content=content))

    tc = (task_core or "").strip()
    has_replan = bool(replan_plan_json and replan_plan_json.strip())

    if has_replan:
        if tc:
            messages.append(HumanMessage(content=tc))
        else:
            messages.append(
                HumanMessage(
                    content=(
                        "（Supervisor 未单独补充 task_core；请根据下一条中的当前计划与执行状态修订。）"
                    )
                )
            )
        messages.append(HumanMessage(content=replan_plan_json.strip()))
    else:
        if not tc:
            messages.append(HumanMessage(content="（未提供任务描述。）"))
        else:
            messages.append(HumanMessage(content=tc))
    return messages


async def _load_planner_tools(ctx: Context) -> list[object]:
    """Planner 仅绑定规划辅助工具 + 只读 MCP（不含 Executor 副作用工具）。"""
    tools: list[object] = list(get_planner_tools())
    if ctx.enable_deepwiki:
        from src.common.mcp import get_readonly_mcp_tools

        tools.extend(await get_readonly_mcp_tools())
    return tools


async def call_planner(state: PlannerState, runtime: Runtime[Context]) -> dict[str, list[BaseMessage]]:
    """Planner 核心节点：ReAct 循环；最终应产出无 tool_calls 的 Plan JSON 文本。"""
    ctx = replace(runtime.context, readonly_tools_only=True)
    tools = await _load_planner_tools(ctx)
    model = load_chat_model(ctx.planner_model).bind_tools(tools)

    response = await invoke_chat_model(
        model,
        state.messages,
        enable_streaming=ctx.enable_llm_streaming,
    )
    if not isinstance(response, AIMessage):
        raise RuntimeError("Planner 模型返回类型异常")

    if not (response.content or "").strip() and not response.tool_calls:
        raise RuntimeError("Planner 未返回文本内容且无工具调用。")

    return {"messages": [response]}


async def planner_tools_node(state: PlannerState, runtime: Runtime[Context]) -> dict[str, list[BaseMessage]]:
    """执行 Planner 侧工具调用，并对 Observation 做 V2-a 规范化。"""
    ctx = replace(runtime.context, readonly_tools_only=True)
    tools = await _load_planner_tools(ctx)
    tool_node = ToolNode(tools)
    result = await tool_node.ainvoke(state)
    messages = result.get("messages", [])
    cwd = await asyncio.to_thread(os.getcwd)
    out: list[BaseMessage] = []
    for m in messages:
        if isinstance(m, ToolMessage):
            text = normalize_tool_message_content(m.content, context=ctx, cwd=cwd)
            out.append(m.model_copy(update={"content": text}))
        else:
            out.append(m)
    return {"messages": out}


def route_planner_output(state: PlannerState) -> Literal["tools", "__end__"]:
    last = state.messages[-1]
    if isinstance(last, AIMessage) and last.tool_calls:
        return "tools"
    return "__end__"


# ==================== 构建 Graph ====================

builder = StateGraph(PlannerState, context_schema=Context)

builder.add_node("call_planner", call_planner)
builder.add_node("tools", planner_tools_node)

builder.add_edge(START, "call_planner")
builder.add_conditional_edges(
    "call_planner",
    route_planner_output,
    {"tools": "tools", "__end__": END},
)
builder.add_edge("tools", "call_planner")

planner_graph = builder.compile(name="Planner Agent")


def _final_planner_text_from_messages(messages: list[BaseMessage]) -> str:
    """取 Planner 最终一轮（无 tool_calls）的 AIMessage 文本作为 Plan JSON 来源。"""
    for m in reversed(messages):
        if not isinstance(m, AIMessage) or m.tool_calls:
            continue
        if not m.content:
            continue
        c = m.content
        if isinstance(c, str):
            return c.strip()
        if isinstance(c, list):
            parts: list[str] = []
            for block in c:
                if isinstance(block, str):
                    parts.append(block)
                elif isinstance(block, dict) and block.get("type") == "text" and "text" in block:
                    parts.append(str(block["text"]))
            return "\n".join(parts).strip()
    raise RuntimeError("Planner 未产生最终文本输出（缺少无 tool_calls 的 AIMessage）")


# ==================== 对外暴露的运行函数 ====================
async def run_planner(
    task_core: str,
    *,
    plan_id: str | None = None,
    replan_plan_json: str | None = None,
    planner_history_messages: list[dict[str, str]] | None = None,
    context: Context | None = None,
) -> str:
    """运行 planner，返回生成的 JSON 计划字符串。

    与架构约定一致：Planner 只接收 **task_core** 与（重规划时）**session 中的完整 plan**，
    不接收 Supervisor 全量对话。

    Args:
        task_core: Supervisor 提供的任务核心（**须足够详细**；新规划必填；重规划时建议补充修订说明）。
        replan_plan_json: 重规划时由上层根据 plan_id 从 ``PlannerSession.plan_json`` 取出。
        planner_history_messages: 同一 plan_id 下历史 Planner 对话（user/assistant），用于会话复用。
        context: 运行时上下文（模型等）；默认 ``Context()``，会从环境变量填充

    Returns:
        str: 干净且已规范化（含稳定 plan_id/version）的 JSON 字符串
    """
    ctx = context if context is not None else Context()
    planner_messages = build_planner_messages(
        task_core,
        replan_plan_json,
        planner_history_messages=planner_history_messages,
    )
    input_state = PlannerState(messages=planner_messages)
    result = await planner_graph.ainvoke(
        input_state,
        config={"recursion_limit": ctx.max_planner_iterations},
        context=ctx,
    )

    content = _final_planner_text_from_messages(result["messages"])
    extracted = _extract_plan_json_from_planner_content(content)
    return _normalize_planner_output_plan_json(
        extracted,
        plan_id=plan_id,
        previous_plan_json=replan_plan_json,
    )


def _extract_plan_json_from_planner_content(content: str) -> str:
    """从 Planner 输出文本中提取唯一 JSON 代码块内容。

    若未命中或命中多个代码块，则返回原始文本，交由上层决定后续处理。
    """
    matches = re.findall(
        r"```(?:json)?\s*([\s\S]*?)```",
        content,
        re.IGNORECASE | re.DOTALL,
    )
    if len(matches) != 1:
        return content
    return matches[0].strip()


def _generate_plan_id() -> str:
    """生成默认 plan_id：plan_vYYYYMMDD_xxxx。"""
    date_tag = datetime.now(UTC).strftime("%Y%m%d")
    return f"plan_v{date_tag}_{uuid4().hex[:4]}"


def _normalize_planner_output_plan_json(
    plan_json: str,
    *,
    plan_id: str | None,
    previous_plan_json: str | None,
) -> str:
    """确保 Planner 输出具备稳定 plan_id/version（系统字段强制托管）。"""
    if not plan_json or not plan_json.strip():
        return plan_json

    try:
        parsed = json.loads(plan_json)
    except json.JSONDecodeError:
        return plan_json

    if not isinstance(parsed, dict):
        return plan_json

    previous: dict = {}
    if previous_plan_json and previous_plan_json.strip():
        try:
            loaded_previous = json.loads(previous_plan_json)
            if isinstance(loaded_previous, dict):
                previous = loaded_previous
        except json.JSONDecodeError:
            previous = {}

    normalized_arg_plan_id = (plan_id or "").strip() or None
    previous_plan_id = previous.get("plan_id") if isinstance(previous.get("plan_id"), str) else None

    # plan_id 为系统字段：忽略 LLM 输出，始终由上层/历史或本地生成决定。
    normalized_plan_id = normalized_arg_plan_id or previous_plan_id or _generate_plan_id()
    parsed["plan_id"] = normalized_plan_id

    previous_version = previous.get("version")
    previous_version = previous_version if isinstance(previous_version, int) and previous_version > 0 else 0

    # version 为系统字段：忽略 LLM 输出，首次=1，重规划=上一版+1。
    parsed["version"] = previous_version + 1 if previous_plan_id else 1

    return json.dumps(parsed, ensure_ascii=False, indent=2)
