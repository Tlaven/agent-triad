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

def _normalize_executor_status_token(s: str) -> Literal["completed", "failed"] | None:
    """Map a single status token (no composite placeholders)."""
    if not s:
        return None
    sl = s.strip().lower()
    if sl in (
        "completed",
        "complete",
        "success",
        "succeeded",
        "done",
        "ok",
        "finished",
        "pass",
        "passed",
    ):
        return "completed"
    if sl in ("failed", "fail", "failure", "error", "errors"):
        return "failed"
    # 中文常见写法（大小写不敏感不适用）
    st = s.strip()
    if st in ("成功", "完成", "已完成"):
        return "completed"
    if st in ("失败", "未完成", "错误"):
        return "failed"
    return None


def _normalize_executor_status(raw: Any) -> Literal["completed", "failed"] | None:
    """Map model status strings to completed/failed, or None if unrecognized."""
    if raw is True:
        return "completed"
    if raw is False:
        return "failed"
    if raw is None:
        return None
    if not isinstance(raw, str):
        raw = str(raw)
    s = raw.strip()
    if not s:
        return None

    # 模型照抄提示词占位符 "completed | failed" / "completed / failed" 时整串无法匹配
    if "|" in s:
        for part in s.split("|"):
            tok = part.strip()
            if tok:
                n = _normalize_executor_status_token(tok)
                if n is not None:
                    return n
    if " / " in s:
        for part in s.split(" / "):
            tok = part.strip()
            if tok:
                n = _normalize_executor_status_token(tok)
                if n is not None:
                    return n

    return _normalize_executor_status_token(s)


def _validate_executor_payload(data: Any) -> dict[str, Any] | None:
    """Validate whether a JSON object is a plausible executor payload."""
    if not isinstance(data, dict):
        return None

    norm = _normalize_executor_status(data.get("status"))
    if norm is None:
        return None

    out = dict(data)
    out["status"] = norm

    # 标准形态：含 updated_plan / summary（summary 可为 null）
    if "updated_plan" in data or "summary" in data:
        return out

    # 极少数模型只输出 {"status": "completed"}，summary 在 _parse_executor_output 中回退为全文
    if list(data.keys()) == ["status"]:
        return {"status": norm}

    # 将 plan 视作 updated_plan（常见别名）
    if "updated_plan" not in out and isinstance(out.get("plan"), dict):
        out["updated_plan"] = out["plan"]
        return out

    if "updated_plan" in out or "summary" in out:
        return out

    # 用 message/result 等代替 summary
    for alt in ("message", "result", "output", "answer", "details"):
        if alt in data and data[alt] is not None:
            if "summary" not in out:
                out["summary"] = str(data[alt])
            return out

    # status 合法且含其它任意字段时仍视为终端结构化结果（summary 可由 _parse_executor_output 回退为全文）
    return out


def _iter_json_objects(text: str):
    """从任意文本中扫描顶层 JSON 对象（用于处理「前后有说明文字」的模型输出）。"""
    decoder = json.JSONDecoder()
    i = 0
    n = len(text)
    while i < n:
        if text[i] != "{":
            i += 1
            continue
        try:
            obj, end = decoder.raw_decode(text, i)
        except json.JSONDecodeError:
            i += 1
            continue
        yield obj
        i = end


def _executor_payload_sort_key(p: dict[str, Any]) -> tuple[int, int, int]:
    """Sort key: prefer completed, then richer updated_plan, then larger payload."""
    st = p.get("status")
    status_rank = 2 if st == "completed" else 1
    up = p.get("updated_plan")
    plan_len = 0
    if isinstance(up, dict):
        plan_len = len(json.dumps(up, ensure_ascii=False))
    total_len = len(json.dumps(p, ensure_ascii=False))
    return (status_rank, plan_len, total_len)


def _extract_executor_payload(content: str) -> dict[str, Any] | None:
    """Extract executor payload from fenced JSON or raw JSON body."""
    # 1) 所有 fenced 块中可解析的 payload 择优（避免「先完整结果、末尾又多一段小 JSON」被错误当成最终结果）。
    blocks = re.findall(
        r"```(?:json)?\s*([\s\S]*?)```",
        content,
        re.IGNORECASE | re.DOTALL,
    )
    fence_candidates: list[dict[str, Any]] = []
    decoder = json.JSONDecoder()
    for raw in blocks:
        stripped = raw.strip().lstrip("\ufeff")
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            try:
                parsed, _ = decoder.raw_decode(stripped)
            except json.JSONDecodeError:
                continue
        validated = _validate_executor_payload(parsed)
        if validated is not None:
            fence_candidates.append(validated)
    if fence_candidates:
        fence_candidates.sort(key=_executor_payload_sort_key, reverse=True)
        return fence_candidates[0]

    # 2) Try full content as JSON.
    try:
        parsed = json.loads(content.strip())
    except json.JSONDecodeError:
        pass
    else:
        validated = _validate_executor_payload(parsed)
        if validated is not None:
            return validated

    # 3) 正文内嵌 JSON（无 fence 或 fence 外还有说明）
    for obj in reversed(list(_iter_json_objects(content))):
        validated = _validate_executor_payload(obj)
        if validated is not None:
            return validated
    return None

def _parse_executor_output(content: str) -> ExecutorResult:
    """从 Executor 最终 AIMessage 中解析结构化结果。

    期望 content 中包含一个 ```json ``` 代码块，内容符合 EXECUTOR_SYSTEM_PROMPT 规定的格式。
    解析失败时降级处理：status=failed，summary=原始文本，updated_plan_json=""。
    """
    data = _extract_executor_payload(content)
    if data is None:
        logger.warning("Executor 输出中未找到可解析的结构化结果，降级处理")
        return ExecutorResult(
            status="failed",
            updated_plan_json="",
            summary=content.strip(),
        )

    status = _normalize_executor_status(data.get("status")) or "failed"

    updated_plan = data.get("updated_plan", {})
    updated_plan_json = json.dumps(updated_plan, ensure_ascii=False, indent=2) if updated_plan else ""

    raw_summary = data.get("summary")
    if raw_summary is None:
        summary_text = content.strip()
    elif isinstance(raw_summary, str):
        summary_text = raw_summary
    else:
        summary_text = str(raw_summary)

    return ExecutorResult(
        status=cast(Literal["completed", "failed"], status),
        updated_plan_json=updated_plan_json,
        summary=summary_text,
    )


def _executor_final_text_from_messages(messages: List[BaseMessage]) -> str | None:
    """取用于解析 Executor 最终结果的文本：优先无 tool_calls 的最后一条 AIMessage。

    避免把中间轮「我要调用工具」的短句当成最终结果，从而漏掉随后一轮的 ```json```。
    """
    for message in reversed(messages):
        if not isinstance(message, AIMessage):
            continue
        if not message.content:
            continue
        if message.tool_calls:
            continue
        return _flatten_ai_message_content(message.content)
    for message in reversed(messages):
        if isinstance(message, AIMessage) and message.content:
            return _flatten_ai_message_content(message.content)
    return None


def _flatten_ai_message_content(content: Any) -> str:
    """Turn AIMessage.content into plain text (str or list of content blocks)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                if block.get("type") == "text" and "text" in block:
                    parts.append(str(block["text"]))
                else:
                    parts.append(json.dumps(block, ensure_ascii=False))
            else:
                parts.append(str(block))
        return "\n".join(parts)
    return str(content)


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
    content = _executor_final_text_from_messages(messages)
    if content is not None:
        return _parse_executor_output(content)

    logger.error("Executor 未产生任何 AIMessage，消息列表长度=%d", len(messages))
    raise RuntimeError("Executor 执行完毕但未产生任何输出，请检查工具调用链。")
