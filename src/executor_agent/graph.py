# executor_agent/graph.py
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Annotated, Any, List, Literal, cast

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langgraph.graph import StateGraph, START
from langgraph.managed import IsLastStep
from langgraph.runtime import Runtime

from src.common.context import Context
from src.common.utils import load_chat_model
from .prompts import get_executor_system_prompt
from .tools import get_executor_capabilities_docs, get_executor_tools


logger = logging.getLogger(__name__)


# ==================== State ====================

@dataclass
class ExecutorState:
    messages: Annotated[List[BaseMessage], lambda x, y: x + y] = field(default_factory=list)
    is_last_step: IsLastStep = field(default=False)  # type: ignore[assignment]


# ==================== 返回值结构体 ====================

@dataclass
class ExecutorResult:
    """run_executor 的结构化返回值"""
    status: Literal["completed", "failed"]
    updated_plan_json: str   # 带执行状态的完整 plan JSON 字符串
    summary: str             # 给 Supervisor LLM 读的文字摘要


# ==================== 节点 ====================

async def call_executor(state: ExecutorState, runtime: Runtime[Context]) -> dict[str, Any]:
    """Executor 核心节点：ReAct 循环的 LLM 调用"""
    available_tools = get_executor_tools()
    capabilities = get_executor_capabilities_docs()
    executor_system_prompt = get_executor_system_prompt(capabilities)
    model = load_chat_model(runtime.context.executor_model).bind_tools(
        available_tools
    )

    response = cast(
        AIMessage,
        await model.ainvoke(
            [{"role": "system", "content": executor_system_prompt}, *state.messages]
        ),
    )


    # 达到最大步数时强制终止工具调用
    if state.is_last_step and response.tool_calls:
        return {
            "messages": [
                AIMessage(
                    id=response.id,
                    content="已达到最大执行步数限制，无法继续调用工具。根据已有信息输出执行摘要。",
                )
            ]
        }

    return {"messages": [response]}


async def tools_node(state: ExecutorState) -> dict[str, Any]:
    """执行工具调用"""
    from langgraph.prebuilt import ToolNode

    available_tools = get_executor_tools()
    tool_node = ToolNode(available_tools)
    result = await tool_node.ainvoke(state)
    return result  # type: ignore[return-value]


def route_executor_output(state: ExecutorState) -> Literal["__end__", "tools"]:
    last_message = state.messages[-1]
    if not isinstance(last_message, AIMessage):
        raise ValueError(
            f"路由时期望 AIMessage，但收到 {type(last_message).__name__}"
        )
    if not last_message.tool_calls:
        return "__end__"
    return "tools"


# ==================== 构建 Graph ====================

builder = StateGraph(ExecutorState, context_schema=Context)

builder.add_node("call_executor", call_executor)
builder.add_node("tools", tools_node)

builder.add_edge(START, "call_executor")
builder.add_conditional_edges("call_executor", route_executor_output)
builder.add_edge("tools", "call_executor")

executor_graph = builder.compile(name="Executor Agent")


# ==================== 辅助函数：解析最终输出 ====================

def _parse_executor_output(content: str) -> ExecutorResult:
    """从 Executor 最终 AIMessage 中解析结构化结果。

    期望 content 中包含一个 ```json ``` 代码块，内容符合 EXECUTOR_SYSTEM_PROMPT 规定的格式。
    解析失败时降级处理：status=failed，summary=原始文本，updated_plan_json=""。
    """
    matches = re.findall(r'```(?:json)?\s*([\s\S]*?)```', content, re.IGNORECASE | re.DOTALL)
    raw_json = matches[0].strip() if len(matches) == 1 else None

    if raw_json is None:
        logger.warning("Executor 输出中未找到唯一 JSON 代码块，降级处理")
        return ExecutorResult(
            status="failed",
            updated_plan_json="",
            summary=content.strip(),
        )

    try:
        data = json.loads(raw_json)
    except json.JSONDecodeError as e:
        logger.warning("Executor 输出 JSON 解析失败：%s", e)
        return ExecutorResult(
            status="failed",
            updated_plan_json=raw_json,
            summary=content.strip(),
        )

    status = data.get("status", "failed")
    if status not in ("completed", "failed"):
        status = "failed"

    updated_plan = data.get("updated_plan", {})
    updated_plan_json = json.dumps(updated_plan, ensure_ascii=False, indent=2) if updated_plan else ""

    return ExecutorResult(
        status=cast(Literal["completed", "failed"], status),
        updated_plan_json=updated_plan_json,
        summary=data.get("summary", content.strip()),
    )


# ==================== 对外暴露的运行函数 ====================

async def run_executor(
    plan_json: str,
    context: Context | None = None,
) -> ExecutorResult:
    """运行 Executor Agent，按 JSON 计划执行任务。

    Args:
        plan_json: 由 Planner 生成的 JSON 计划字符串（意图层，含步骤状态）。
        context: 运行时上下文（含 executor_model、max_executor_iterations）；默认 `Context()`。

    Returns:
        ExecutorResult: 包含执行状态、更新后的 plan JSON 和摘要。

    Raises:
        ValueError: plan_json 为空时抛出。
        RuntimeError: Executor 未产生任何输出时抛出。
    """
    if not plan_json or not plan_json.strip():
        raise ValueError("plan_json 不能为空")

    ctx = context if context is not None else Context()

    input_state = ExecutorState(
        messages=[HumanMessage(content=f"请按照以下计划执行：\n\n{plan_json}")],
    )

    result = await executor_graph.ainvoke(
        input_state,
        config={"recursion_limit": ctx.max_executor_iterations},
        context=ctx,
    )
    messages = result.get("messages", [])

    for message in reversed(messages):
        if isinstance(message, AIMessage) and message.content:
            content = message.content if isinstance(message.content, str) else str(message.content)
            return _parse_executor_output(content)

    logger.error("Executor 未产生任何 AIMessage，消息列表长度=%d", len(messages))
    raise RuntimeError("Executor 执行完毕但未产生任何输出，请检查工具调用链。")
