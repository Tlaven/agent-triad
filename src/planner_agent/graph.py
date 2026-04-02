# planner_agent/graph.py
"""Planner Agent - 单次 LLM 调用产出意图层 Plan JSON。

消息约定：第一条为完整 Planner 系统提示（`_PLANNER_SYSTEM_PROMPT_TEMPLATE`）；第二条为 Supervisor 提供的 **task_core**（须足够详细）；重规划时第三条为带执行状态的 plan JSON。
"""


import re

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langgraph.graph import StateGraph, START, END
from langgraph.runtime import Runtime

from src.common.context import Context
from src.common.utils import load_chat_model
from src.executor_agent.tools import get_executor_capabilities_docs
from .state import PlannerState
from .prompts import get_planner_system_prompt


def build_planner_messages(
    task_core: str,
    replan_plan_json: str | None,
) -> list[BaseMessage]:
    """组装 Planner LLM 输入：首条为完整系统提示词，第二条为 task_core，重规划时第三条为 plan JSON。"""
    capabilities = get_executor_capabilities_docs()
    system_text = get_planner_system_prompt(capabilities)
    messages: list[BaseMessage] = [SystemMessage(content=system_text)]

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


async def call_planner(state: PlannerState, runtime: Runtime[Context]) -> dict[str, list[BaseMessage]]:
    """Planner 核心节点：消息已由 `build_planner_messages` 就绪，此处仅过滤并调用模型。"""
    model = load_chat_model(runtime.context.planner_model)

    messages = [
        m for m in state.messages
        if not (isinstance(m, AIMessage) and m.tool_calls)
    ]

    # 调用模型（Planner 负责输出 JSON 文本）
    response = await model.ainvoke(messages)

    content = response.content.strip() if isinstance(response.content, str) else ""

    # 防御：若 LLM 误将输出写入 tool_calls 而非 content，提前报错而非静默返回空
    if not content:
        raise RuntimeError(
            "Planner 未返回文本内容。"
        )

    return {
        "messages": [AIMessage(content=content, name="planner")]
    }


# ==================== 构建 Graph ====================
builder = StateGraph(PlannerState, context_schema=Context)

builder.add_node("call_planner", call_planner)

builder.add_edge(START, "call_planner")
builder.add_edge("call_planner", END)

# 单次规划无需 checkpoint；避免同 thread 多次调用时消息累加。
planner_graph = builder.compile(name="Planner Agent")


# ==================== 对外暴露的运行函数 ====================
async def run_planner(
    task_core: str,
    *,
    replan_plan_json: str | None = None,
    context: Context | None = None,
) -> str:
    """
    运行 planner，返回生成的 JSON 计划字符串。

    与架构约定一致：Planner 只接收 **task_core** 与（重规划时）**session 中的完整 plan**，
    不接收 Supervisor 全量对话。

    Args:
        task_core: Supervisor 提供的任务核心（**须足够详细**；新规划必填；重规划时建议补充修订说明）。
        replan_plan_json: 重规划时由上层根据 plan_id 从 ``PlannerSession.plan_json`` 取出。
        context: 运行时上下文（模型等）；默认 ``Context()``，会从环境变量填充

    Returns:
        str: 干净的 JSON 字符串
    """
    ctx = context if context is not None else Context()
    planner_messages = build_planner_messages(task_core, replan_plan_json)
    input_state = PlannerState(messages=planner_messages)
    result = await planner_graph.ainvoke(
        input_state,
        context=ctx,
    )
    
    final_message = result["messages"][-1]
    content = final_message.content.strip()
    return _extract_plan_json_from_planner_content(content)


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
