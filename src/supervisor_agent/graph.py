"""Define a custom Reasoning and Action agent.

Works with a chat model with tool calling support.
"""

import json
import logging
import uuid
from typing import Dict, List, Literal, cast

from langchain_core.messages import AIMessage, ToolMessage
from langgraph.graph import StateGraph
from langgraph.prebuilt import ToolNode
from langgraph.runtime import Runtime

from src.common.context import Context
from src.common.utils import load_chat_model
from src.supervisor_agent.prompts import get_supervisor_system_prompt
from src.supervisor_agent.tools import get_tools
from src.supervisor_agent.state import (
    InputState,
    PlannerSession,
    State,
    SupervisorDecision,
)

logger = logging.getLogger(__name__)


async def call_model(
    state: State, runtime: Runtime[Context]
) -> Dict[str, List[AIMessage]]:
    """调用 LLM 支持 Agent。
    负责准备提示、初始化模型并处理响应。
    """
    # 达到最大重规划次数后，停止工具循环，直接给出失败说明。
    if (
        state.planner_session is not None
        and state.planner_session.last_executor_status == "failed"
        and state.replan_count >= runtime.context.max_replan
    ):
        decision = SupervisorDecision(
            mode=1,
            reason=f"已达到最大重规划次数（{runtime.context.max_replan}）",
            confidence=0.95,
        )
        return {
            "messages": [
                AIMessage(
                    content=(
                        "执行已多次失败，且达到最大重规划次数。"
                        "请基于当前失败信息汇报用户并给出可执行的下一步建议。"
                    )
                )
            ],
            "supervisor_decision": decision,
        }

    # Mode2 失败且语义上需要计划层重构时，显式升级到 Mode3（由 Supervisor 决策）。
    if (
        state.planner_session is not None
        and state.planner_session.last_executor_status == "failed"
        and state.replan_count < runtime.context.max_replan
        and not (state.planner_session.plan_json or "").strip()
        and _needs_mode3_upgrade(
            state.planner_session.last_executor_summary,
            state.planner_session.last_executor_error,
        )
    ):
        task_core = (
            state.planner_session.last_executor_summary
            or state.planner_session.last_executor_error
            or "执行失败，当前路径无法推进，请重建可执行计划。"
        )
        return {
            "messages": [
                AIMessage(
                    content="检测到失败且需要计划层重构，切换到 Mode3：先规划再执行。",
                    tool_calls=[
                        {
                            "id": f"call_{uuid.uuid4().hex[:8]}",
                            "name": "generate_plan",
                            "args": {"task_core": task_core},
                            "type": "tool_call",
                        }
                    ],
                )
            ],
            "supervisor_decision": SupervisorDecision(
                mode=3,
                reason="Mode2 失败且 summary 表示需计划层重构，显式升级 Mode3",
                confidence=0.95,
            ),
        }

    available_tools = await get_tools(runtime.context)
    model = load_chat_model(runtime.context.supervisor_model).bind_tools(available_tools)
    system_message = get_supervisor_system_prompt()
    response = cast(
        AIMessage,
        await model.ainvoke(
            [{"role": "system", "content": system_message}, *state.messages]
        ),
    )

    decision = _infer_supervisor_decision(response)

    # 达到最大步数时，若模型仍想调用工具，强制终止并返回说明
    if state.is_last_step and response.tool_calls:
        return {
            "messages": [
                AIMessage(
                    id=response.id,
                    content="已达到最大执行步数限制，无法继续调用工具。请根据已有信息给出最终答复。",
                )
            ],
            "supervisor_decision": SupervisorDecision(
                mode=1,
                reason="已达到最大步数，强制结束工具调用",
                confidence=0.99,
            ),
        }

    return {"messages": [response], "supervisor_decision": decision}


async def dynamic_tools_node(
    state: State, runtime: Runtime[Context]
) -> Dict:
    """动态执行工具，并同步更新 PlannerSession 状态。

    - generate_plan 执行后：将新 plan_json 写入 planner_session
    - execute_plan 执行后：将 updated_plan_json（带执行状态）写回 planner_session
    """
    available_tools = await get_tools(runtime.context)
    tool_node = ToolNode(available_tools)
    result = await tool_node.ainvoke(state)

    tool_messages: List[ToolMessage] = result.get("messages", [])
    id_to_name = _build_id_to_name(state)
    id_to_call = _build_id_to_call(state)

    sanitized_tool_messages: List[ToolMessage] = []
    updates: Dict = {"messages": sanitized_tool_messages}

    for tm in tool_messages:
        if not isinstance(tm, ToolMessage):
            continue
        tool_name = id_to_name.get(tm.tool_call_id, "")
        content = tm.content if isinstance(tm.content, str) else str(tm.content)

        if tool_name == "generate_plan" and content.strip():
            sanitized_tool_messages.append(tm)
            session_id = (
                state.planner_session.session_id
                if state.planner_session is not None
                else f"plan_{uuid.uuid4().hex[:8]}"
            )
            existing_history = (
                dict(state.planner_session.planner_history_by_plan_id)
                if state.planner_session is not None
                else {}
            )
            existing_last_version = (
                dict(state.planner_session.planner_last_version_by_plan_id)
                if state.planner_session is not None
                else {}
            )
            existing_last_output = (
                dict(state.planner_session.planner_last_output_by_plan_id)
                if state.planner_session is not None
                else {}
            )
            existing_archive = (
                dict(state.planner_session.plan_archive_by_plan_id)
                if state.planner_session is not None
                else {}
            )

            new_plan_json = content.strip()
            new_plan_id, new_version = _parse_plan_meta(new_plan_json)
            if new_plan_id:
                tool_call = id_to_call.get(tm.tool_call_id, {})
                args = tool_call.get("args", {}) if isinstance(tool_call, dict) else {}
                task_core = str(args.get("task_core", "")).strip() if isinstance(args, dict) else ""
                pid_history = list(existing_history.get(new_plan_id, []))
                pid_history.append({"role": "user", "content": task_core or "（空 task_core）"})
                pid_history.append({"role": "assistant", "content": new_plan_json})
                existing_history[new_plan_id] = pid_history
                existing_last_output[new_plan_id] = new_plan_json
                if isinstance(new_version, int):
                    existing_last_version[new_plan_id] = new_version

                if state.planner_session is not None and (state.planner_session.plan_json or "").strip():
                    old_plan_json = state.planner_session.plan_json.strip()
                    old_plan_id, old_version = _parse_plan_meta(old_plan_json)
                    if (
                        old_plan_id == new_plan_id
                        and isinstance(old_version, int)
                        and isinstance(new_version, int)
                        and new_version > old_version
                    ):
                        versions = list(existing_archive.get(new_plan_id, []))
                        if not versions or versions[-1] != old_plan_json:
                            versions.append(old_plan_json)
                        existing_archive[new_plan_id] = versions

            updates["planner_session"] = PlannerSession(
                session_id=session_id,
                plan_json=new_plan_json,
                last_executor_status=(
                    state.planner_session.last_executor_status if state.planner_session else None
                ),
                last_executor_error=(
                    state.planner_session.last_executor_error if state.planner_session else None
                ),
                last_executor_summary=(
                    state.planner_session.last_executor_summary if state.planner_session else None
                ),
                planner_history_by_plan_id=existing_history,
                planner_last_version_by_plan_id=existing_last_version,
                planner_last_output_by_plan_id=existing_last_output,
                plan_archive_by_plan_id=existing_archive,
            )
            logger.info("PlannerSession 已更新（generate_plan），session_id=%s", session_id)

        elif tool_name == "execute_plan":
            updated_plan = _extract_updated_plan_from_executor(content)
            exec_status, exec_error = _extract_executor_status(content)
            exec_summary = _extract_executor_summary(content)
            public_feedback = _build_executor_feedback_for_llm(content, exec_status, exec_error)
            sanitized_tool_messages.append(tm.model_copy(update={"content": public_feedback}))
            next_replan_count = state.replan_count
            if exec_status == "failed":
                next_replan_count = state.replan_count + 1
            elif exec_status == "completed":
                next_replan_count = 0
            if state.planner_session is not None:
                session_id = state.planner_session.session_id
                next_plan_json = updated_plan if updated_plan else state.planner_session.plan_json
            else:
                session_id = f"plan_{uuid.uuid4().hex[:8]}"
                next_plan_json = updated_plan

            existing_history = (
                dict(state.planner_session.planner_history_by_plan_id)
                if state.planner_session is not None
                else {}
            )
            existing_last_version = (
                dict(state.planner_session.planner_last_version_by_plan_id)
                if state.planner_session is not None
                else {}
            )
            existing_last_output = (
                dict(state.planner_session.planner_last_output_by_plan_id)
                if state.planner_session is not None
                else {}
            )
            existing_archive = (
                dict(state.planner_session.plan_archive_by_plan_id)
                if state.planner_session is not None
                else {}
            )
            updates["planner_session"] = PlannerSession(
                session_id=session_id,
                plan_json=next_plan_json,
                last_executor_status=exec_status,
                last_executor_error=exec_error,
                last_executor_summary=exec_summary,
                planner_history_by_plan_id=existing_history,
                planner_last_version_by_plan_id=existing_last_version,
                planner_last_output_by_plan_id=existing_last_output,
                plan_archive_by_plan_id=existing_archive,
            )
            logger.info(
                "PlannerSession 已更新（execute_plan 回填），session_id=%s，status=%s，replan_count=%s",
                session_id,
                exec_status,
                next_replan_count,
            )
            updates["replan_count"] = next_replan_count
        else:
            sanitized_tool_messages.append(tm)

    return updates


def _build_id_to_name(state: State) -> Dict[str, str]:
    """从最后一条 AIMessage 中构建 tool_call_id → tool_name 的映射。"""
    if not state.messages:
        return {}
    last_ai = state.messages[-1]
    if not isinstance(last_ai, AIMessage) or not last_ai.tool_calls:
        return {}
    return {
        tc["id"]: tc["name"]
        for tc in last_ai.tool_calls
        if "id" in tc and "name" in tc
    }


def _build_id_to_call(state: State) -> Dict[str, dict]:
    """从最后一条 AIMessage 中构建 tool_call_id -> tool_call 映射。"""
    if not state.messages:
        return {}
    last_ai = state.messages[-1]
    if not isinstance(last_ai, AIMessage) or not last_ai.tool_calls:
        return {}
    out: Dict[str, dict] = {}
    for tc in last_ai.tool_calls:
        if "id" in tc:
            out[tc["id"]] = tc
    return out


def _parse_plan_meta(plan_json: str) -> tuple[str | None, int | None]:
    try:
        data = json.loads(plan_json)
    except json.JSONDecodeError:
        return None, None
    if not isinstance(data, dict):
        return None, None
    plan_id = data.get("plan_id") if isinstance(data.get("plan_id"), str) else None
    version = data.get("version") if isinstance(data.get("version"), int) else None
    return plan_id, version


def _extract_updated_plan_from_executor(content: str) -> str | None:
    """从 execute_plan 返回的 ToolMessage 内容中提取 updated_plan_json。

    约定格式：内容末尾有一行 `[EXECUTOR_RESULT] {...json...}`
    """
    import json as _json
    import re as _re
    match = _re.search(r'\[EXECUTOR_RESULT\]\s*(\{.*\})', content, _re.DOTALL)
    if not match:
        return None
    try:
        meta = _json.loads(match.group(1))
        updated = meta.get("updated_plan_json", "")
        return updated if updated else None
    except _json.JSONDecodeError:
        logger.warning("execute_plan 返回的 EXECUTOR_RESULT 解析失败")
        return None


def _extract_executor_status(content: str) -> tuple[str | None, str | None]:
    """从 execute_plan 返回的 ToolMessage 内容中提取 status 和 error_detail。

    返回 (status, error_detail)，解析失败时返回 (None, None)。
    """
    import json as _json
    import re as _re
    match = _re.search(r'\[EXECUTOR_RESULT\]\s*(\{.*\})', content, _re.DOTALL)
    if not match:
        return None, None
    try:
        meta = _json.loads(match.group(1))
        return meta.get("status"), meta.get("error_detail")
    except _json.JSONDecodeError:
        return None, None


def _extract_executor_summary(content: str) -> str:
    """从 execute_plan 返回中提取 summary（[EXECUTOR_RESULT] 前正文）。"""
    marker = "[EXECUTOR_RESULT]"
    return content.split(marker, 1)[0].strip() if marker in content else content.strip()


def _needs_mode3_upgrade(summary: str | None, error_detail: str | None) -> bool:
    """基于失败语义信号判断是否应从 Mode2 升级到 Mode3。"""
    text = f"{summary or ''}\n{error_detail or ''}".lower()
    signals = (
        "需要计划",
        "重规划",
        "无法继续",
        "无法推进",
        "无法完成",
        "需要重新拆解",
        "需要重构",
        "no reusable plan",
        "replan",
        "cannot proceed",
    )
    return any(sig in text for sig in signals)


def _build_executor_feedback_for_llm(
    content: str,
    status: str | None,
    error_detail: str | None,
) -> str:
    """构造给 Supervisor LLM 的精简反馈，避免注入大体量 updated_plan_json。"""
    marker = "[EXECUTOR_RESULT]"
    summary_text = content.split(marker, 1)[0].strip() if marker in content else content.strip()
    if status == "completed":
        # 成功分支仅保留 summary，避免给 Supervisor LLM 注入冗余结构化负载。
        return summary_text
    if status == "failed":
        detail = error_detail or "未知错误"
        return f"Executor 执行结果：failed\n失败原因：{detail}\n摘要：{summary_text}"
    return summary_text


def _infer_supervisor_decision(response: AIMessage) -> SupervisorDecision:
    """根据本轮输出推断结构化决策（mode/reason/confidence）。"""
    tool_names = [tc.get("name", "") for tc in response.tool_calls] if response.tool_calls else []
    if not tool_names:
        return SupervisorDecision(mode=1, reason="无需工具即可回答", confidence=0.85)
    if "generate_plan" in tool_names:
        return SupervisorDecision(mode=3, reason="检测到多步规划需求，先规划后执行", confidence=0.8)
    if "execute_plan" in tool_names:
        return SupervisorDecision(mode=2, reason="目标明确，直接工具执行", confidence=0.75)
    return SupervisorDecision(mode=2, reason="存在工具调用", confidence=0.6)


# ==================== 图定义 ====================

builder = StateGraph(State, input_schema=InputState, context_schema=Context)

builder.add_node(call_model)
builder.add_node("tools", dynamic_tools_node)

builder.add_edge("__start__", "call_model")


def route_model_output(state: State) -> Literal["__end__", "tools"]:
    """根据模型输出决定下一个节点。"""
    last_message = state.messages[-1]
    if not isinstance(last_message, AIMessage):
        raise ValueError(
            f"路由时期望 AIMessage，但收到 {type(last_message).__name__}"
        )
    if not last_message.tool_calls:
        return "__end__"
    return "tools"


builder.add_conditional_edges("call_model", route_model_output)
builder.add_edge("tools", "call_model")

graph = builder.compile(name="ReAct Agent")
