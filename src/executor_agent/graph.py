# executor_agent/graph.py
import asyncio
import json
import logging
import operator
import os
import re
from dataclasses import dataclass, field
from typing import Annotated, Any, List, Literal, cast

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langgraph.graph import END, START, StateGraph
from langgraph.managed import IsLastStep
from langgraph.prebuilt import ToolNode
from langgraph.runtime import Runtime

from src.common.context import Context
from src.common.mcp import get_readonly_mcp_tools
from src.common.observation import normalize_tool_message_content
from src.common.tools import apply_context_workspace_root
from src.common.utils import invoke_chat_model, load_chat_model

from src.executor_agent.prompts import get_executor_system_prompt, get_reflection_system_prompt
from src.executor_agent.tools import get_executor_capabilities_docs, get_executor_tools
from src.executor_agent.interrupt import (
    set_current_plan_id,
    clear_current_plan_id,
    INTERRUPT_PROMPT,
)

logger = logging.getLogger(__name__)


# ==================== State ====================

@dataclass
class ExecutorState:
    messages: Annotated[List[BaseMessage], lambda x, y: x + y] = field(default_factory=list)
    is_last_step: IsLastStep = field(default=False)  # type: ignore[assignment]
    tool_rounds: Annotated[int, operator.add] = 0
    reflection_interval: int = 0
    confidence_threshold: float = 0.6


# ==================== 返回值结构体 ====================

@dataclass
class ExecutorResult:
    """run_executor 的结构化返回值"""

    status: Literal["completed", "failed", "paused"]
    updated_plan_json: str  # 带执行状态的完整 plan JSON 字符串
    summary: str  # 给 Supervisor LLM 读的文字摘要
    snapshot_json: str = ""  # status=paused 时的结构化快照 JSON（Reflection 检查点）


# ==================== 工具加载（本地 + 可选 MCP） ====================


async def _load_executor_tools(ctx: Context) -> list[object]:
    apply_context_workspace_root(ctx)
    tools: list[object] = list(get_executor_tools())
    mcp_tools = list(await get_readonly_mcp_tools(ctx))
    tools.extend(mcp_tools)
    if ctx.enable_deepwiki and not mcp_tools:
        logger.warning(
            "Executor 已启用 DeepWiki MCP，但未加载到任何工具（enable_deepwiki=%s）",
            ctx.enable_deepwiki,
        )
    return tools


# ==================== 节点 ====================

async def call_executor(state: ExecutorState, runtime: Runtime[Context]) -> dict[str, Any]:
    """Executor 核心节点：ReAct 循环的 LLM 调用。

    在子进程服务模式下：每次调用 LLM 前检查停止标志；若已设置则返回
    无 tool_calls 的 AIMessage，以便路由到 __end__ 正常结束。
    """
    # 停止标志检查（Supervisor 可通过 HTTP 触发）
    plan_id = _extract_plan_id_from_messages(state.messages)
    if plan_id:
        try:
            from src.executor_agent.server import _stop_events
            stop_event = _stop_events.get(plan_id)
            if stop_event and stop_event.is_set():
                logger.info("Stop flag detected for plan_id=%s, exiting gracefully", plan_id)
                return {
                    "messages": [
                        AIMessage(
                            content=json.dumps({
                                "status": "failed",
                                "summary": "Executor stopped by Supervisor",
                                "updated_plan": {},
                            })
                        )
                    ]
                }
        except ImportError:
            pass  # 非子进程服务模式（无 server 侧 stop 事件表）

    available_tools = await _load_executor_tools(runtime.context)
    capabilities = get_executor_capabilities_docs()
    executor_system_prompt = get_executor_system_prompt(capabilities)
    model = load_chat_model(
        runtime.context.executor_model,
        **runtime.context.get_agent_llm_kwargs("executor"),
    ).bind_tools(available_tools)

    # Wall-clock timeout for LLM call — if exceeded, process is unrecoverable
    llm_timeout = runtime.context.executor_call_model_timeout
    try:
        if llm_timeout and llm_timeout > 0:
            response = await asyncio.wait_for(
                invoke_chat_model(
                    model,
                    [{"role": "system", "content": executor_system_prompt}, *state.messages],
                    enable_streaming=runtime.context.enable_llm_streaming,
                ),
                timeout=llm_timeout,
            )
        else:
            response = await invoke_chat_model(
                model,
                [{"role": "system", "content": executor_system_prompt}, *state.messages],
                enable_streaming=runtime.context.enable_llm_streaming,
            )
        response = cast(AIMessage, response)
    except asyncio.TimeoutError:
        logger.error("Executor LLM call timed out (%.0fs), aborting", llm_timeout)
        raise RuntimeError(f"Executor LLM 调用超时（{llm_timeout:.0f}秒），进程将被终止")

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


async def tools_node(state: ExecutorState, runtime: Runtime[Context]) -> dict[str, Any]:
    """执行工具调用，并对 Observation 做统一规范化。

    Sets plan_id context so tools can check for interrupt signals.
    If any tool returns an interrupt marker, injects a stop prompt.
    Wall-clock timeout: if tools_node exceeds executor_tool_timeout, returns
    a timeout warning to the LLM so it can summarize with partial results.
    """
    ctx = runtime.context

    # Set plan_id context for interrupt checks during tool execution
    plan_id = _extract_plan_id_from_messages(state.messages)
    if plan_id:
        set_current_plan_id(plan_id)

    tool_timeout = ctx.executor_tool_timeout
    try:
        available_tools = await _load_executor_tools(ctx)
        tool_node = ToolNode(available_tools)
        if tool_timeout and tool_timeout > 0:
            try:
                result = await asyncio.wait_for(
                    tool_node.ainvoke(state),
                    timeout=tool_timeout,
                )
            except asyncio.TimeoutError:
                logger.warning("Executor tools_node timed out (%.0fs), returning partial", tool_timeout)
                # Return timeout warning as a ToolMessage so the LLM can summarize
                last_ai = None
                for m in reversed(state.messages):
                    if isinstance(m, AIMessage) and m.tool_calls:
                        last_ai = m
                        break
                out_msgs: list[BaseMessage] = []
                if last_ai and last_ai.tool_calls:
                    for tc in last_ai.tool_calls:
                        out_msgs.append(ToolMessage(
                            content=f"[工具执行超时] 工具 {tc.get('name', '?')} 执行超过 {tool_timeout:.0f} 秒被强制中断。"
                                    f"请根据已获取的部分信息输出执行摘要。",
                            tool_call_id=tc.get("id", ""),
                        ))
                if not out_msgs:
                    out_msgs.append(HumanMessage(
                        content=f"[系统超时] 工具执行总耗时超过 {tool_timeout:.0f} 秒。"
                                f"请立即停止调用工具，根据已有信息输出执行摘要。"
                    ))
                return {"messages": out_msgs, "tool_rounds": 1}
        else:
            result = await tool_node.ainvoke(state)
        messages = result.get("messages", [])
    finally:
        clear_current_plan_id()

    cwd = await asyncio.to_thread(os.getcwd)
    out: list[BaseMessage] = []
    has_interrupt = False
    for m in messages:
        if isinstance(m, ToolMessage):
            text = normalize_tool_message_content(m.content, context=ctx, cwd=cwd)
            if INTERRUPT_PROMPT in text:
                has_interrupt = True
            out.append(m.model_copy(update={"content": text}))
        else:
            out.append(m)

    # If a tool was interrupted, inject stop prompt so LLM terminates naturally
    if has_interrupt:
        out.append(HumanMessage(
            content="[系统中断] Supervisor 已发出停止指令。请立即停止调用任何工具，"
                    "根据已有信息输出执行摘要，包含 status 字段为 stopped。"
        ))

    return {"messages": out, "tool_rounds": 1}


def route_executor_output(state: ExecutorState) -> Literal["__end__", "tools"]:
    last_message = state.messages[-1]
    if not isinstance(last_message, AIMessage):
        raise ValueError(
            f"路由时期望 AIMessage，但收到 {type(last_message).__name__}"
        )
    if not last_message.tool_calls:
        return "__end__"
    return "tools"


def route_after_tools(state: ExecutorState) -> Literal["reflection", "call_executor"]:
    """工具执行后：按间隔触发 Reflection，否则回到主循环。"""
    if state.reflection_interval <= 0:
        return "call_executor"
    if state.tool_rounds > 0 and state.tool_rounds % state.reflection_interval == 0:
        return "reflection"
    return "call_executor"


async def reflection_node(state: ExecutorState, runtime: Runtime[Context]) -> dict[str, Any]:
    """中途 Reflection：产出 paused 结构化结果并结束本轮 Executor。"""
    prompt = get_reflection_system_prompt()
    model = load_chat_model(
        runtime.context.executor_model,
        **runtime.context.get_agent_llm_kwargs("executor"),
    )
    llm_timeout = runtime.context.executor_call_model_timeout
    try:
        if llm_timeout and llm_timeout > 0:
            raw_response = await asyncio.wait_for(
                invoke_chat_model(
                    model,
                    [{"role": "system", "content": prompt}, *state.messages],
                    enable_streaming=runtime.context.enable_llm_streaming,
                ),
                timeout=llm_timeout,
            )
        else:
            raw_response = await invoke_chat_model(
                model,
                [{"role": "system", "content": prompt}, *state.messages],
                enable_streaming=runtime.context.enable_llm_streaming,
            )
        response = cast(AIMessage, raw_response)
    except asyncio.TimeoutError:
        logger.error("Reflection LLM call timed out (%.0fs), aborting", llm_timeout)
        raise RuntimeError(
            f"Reflection LLM 调用超时（{llm_timeout:.0f}秒），进程将被终止"
        )
    return {"messages": [response]}


# ==================== 构建 Graph ====================

builder = StateGraph(ExecutorState, context_schema=Context)

builder.add_node("call_executor", call_executor)
builder.add_node("tools", tools_node)
builder.add_node("reflection", reflection_node)

builder.add_edge(START, "call_executor")
builder.add_conditional_edges("call_executor", route_executor_output)
builder.add_conditional_edges(
    "tools",
    route_after_tools,
    {
        "reflection": "reflection",
        "call_executor": "call_executor",
    },
)
builder.add_edge("reflection", END)

executor_graph = builder.compile(name="Executor Agent")


# ==================== 辅助函数：解析最终输出 ====================

def _extract_plan_id_from_messages(messages: list[BaseMessage]) -> str:
    """Extract plan_id from the first HumanMessage containing plan JSON."""
    for msg in messages:
        if not isinstance(msg, HumanMessage):
            continue
        content = msg.content if isinstance(msg.content, str) else ""
        try:
            data = json.loads(content)
            if isinstance(data, dict) and "plan_id" in data:
                return str(data["plan_id"])
        except (json.JSONDecodeError, AttributeError):
            continue
    return ""

def _normalize_executor_status_token(s: str) -> Literal["completed", "failed", "paused"] | None:
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
    if sl in ("paused", "pause", "checkpoint", "halt"):
        return "paused"
    # 中文常见写法（大小写不敏感不适用）
    st = s.strip()
    if st in ("成功", "完成", "已完成"):
        return "completed"
    if st in ("失败", "未完成", "错误"):
        return "failed"
    if st in ("暂停", "已暂停"):
        return "paused"
    return None


def _normalize_executor_status(raw: Any) -> Literal["completed", "failed", "paused"] | None:
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

    # 标准形态：含 updated_plan / summary（summary 可为 null）；paused 可含 snapshot
    if "updated_plan" in data or "summary" in data or "snapshot" in data:
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
    """Sort key: prefer completed, then paused, then failed; then richer updated_plan."""
    st = p.get("status")
    if st == "completed":
        status_rank = 3
    elif st == "paused":
        status_rank = 2
    else:
        status_rank = 1
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

    # 4) 文本兜底：当模型未输出 JSON，但给出了明确 status 行时，避免误判为 failed。
    status_match = re.search(
        r"(?:^|\n)\s*(?:status|状态)\s*[:：]\s*([^\n]+)",
        content,
        flags=re.IGNORECASE,
    )
    if status_match:
        normalized = _normalize_executor_status(status_match.group(1).strip())
        if normalized is not None:
            return {"status": normalized}
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
    updated_plan_json = (
        json.dumps(updated_plan, ensure_ascii=False, indent=2) if updated_plan else ""
    )

    raw_summary = data.get("summary")
    if raw_summary is None:
        summary_text = content.strip()
    elif isinstance(raw_summary, str):
        summary_text = raw_summary
    else:
        summary_text = str(raw_summary)

    snapshot_json = ""
    snap = data.get("snapshot")
    if snap is not None:
        try:
            snapshot_json = json.dumps(snap, ensure_ascii=False, indent=2)
        except TypeError:
            snapshot_json = str(snap)

    return ExecutorResult(
        status=cast(Literal["completed", "failed", "paused"], status),
        updated_plan_json=updated_plan_json,
        summary=summary_text,
        snapshot_json=snapshot_json,
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
        reflection_interval=ctx.reflection_interval,
        confidence_threshold=ctx.confidence_threshold,
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
