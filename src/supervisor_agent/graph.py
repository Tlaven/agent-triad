"""Define a custom Reasoning and Action agent.

Works with a chat model with tool calling support.
"""

import asyncio
import json
import logging
import re
import uuid
from datetime import datetime
from typing import Any, Literal, cast

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.graph import StateGraph
from langgraph.prebuilt import ToolNode
from langgraph.runtime import Runtime

from src.common.context import Context
from src.common.utils import extract_reasoning_text, invoke_chat_model, load_chat_model
from src.supervisor_agent.prompts import get_supervisor_system_prompt
from src.supervisor_agent.state import (
    ActiveExecutorTask,
    ExecutorTaskRecord,
    InputState,
    PlannerSession,
    State,
    SupervisorDecision,
)
from src.supervisor_agent.tools import get_tools

logger = logging.getLogger(__name__)


async def _build_executor_status_brief(state: State, ctx: Context) -> str:
    """Build concise executor task summary for Supervisor context injection.

    Queries the Executor server's /tasks endpoint (single HTTP call).
    Also reads Mailbox for completed-but-not-yet-consumed results.
    Returns empty string if nothing to report (zero token overhead).
    """
    import httpx

    from src.supervisor_agent.v3_lifecycle import v3_manager

    try:
        infra = await v3_manager.ensure_started(ctx)
        pm = infra.process_manager
        base_urls = pm.iter_active_base_urls()
    except Exception:
        if state.active_executor_tasks:
            lines = [
                f"- {pid}: {t.status}（本地记录）"
                for pid, t in state.active_executor_tasks.items()
            ]
            return "Executor 服务不可达，本地记录的任务：\n" + "\n".join(lines)
        return ""

    server_tasks: dict = {}
    any_tasks_response = False
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            for base_url in base_urls:
                try:
                    r = await client.get(f"{base_url}/tasks")
                    if r.status_code != 200:
                        continue
                    any_tasks_response = True
                    chunk = r.json().get("tasks", {})
                    for k, v in chunk.items():
                        if k not in server_tasks:
                            server_tasks[k] = v
                except (httpx.ConnectError, httpx.TimeoutException):
                    continue
    except Exception:
        if state.active_executor_tasks:
            lines = [
                f"- {pid}: {t.status}（本地记录）"
                for pid, t in state.active_executor_tasks.items()
            ]
            return "Executor 服务不可达，本地记录的任务：\n" + "\n".join(lines)
        return ""

    if not server_tasks and not any_tasks_response and base_urls:
        if state.active_executor_tasks:
            lines = [
                f"- {pid}: {t.status}（本地记录）"
                for pid, t in state.active_executor_tasks.items()
            ]
            return "Executor 服务不可达，本地记录的任务：\n" + "\n".join(lines)
        return ""

    # Also check Mailbox for completed results not yet consumed
    from src.common.mailbox import get_mailbox

    mailbox_lines: list[str] = []
    try:
        mb = get_mailbox()
        for pid in list(state.active_executor_tasks.keys()):
            comp = await mb.get_completion(pid)
            if comp is not None:
                status = comp.payload.get("status", "completed")
                summary_preview = (comp.payload.get("summary", "") or "")[:100]
                suffix = f"：{summary_preview}..." if summary_preview else ""
                mailbox_lines.append(f"- {pid}: {status}（结果已就绪{suffix}）")
    except RuntimeError:
        pass

    if not server_tasks and not mailbox_lines:
        return ""

    lines: list[str] = []
    for pid, info in server_tasks.items():
        status = info.get("status", "?")
        step = info.get("current_step") or ""
        rounds = info.get("tool_rounds", 0)
        line = f"- {pid}: {status}"
        if step:
            line += f"，步骤={step}"
        if rounds:
            line += f"，轮次={rounds}"
        lines.append(line)

    all_lines = lines + mailbox_lines
    return f"当前 Executor 任务（{len(all_lines)}）：\n" + "\n".join(all_lines)


async def _force_poll_active_tasks(ctx: Context) -> None:
    """Trigger an immediate sweep of all active plan IDs via the unified poller.

    Replaces the old per-call _poll_executor_results scattered HTTP requests.
    """
    from src.supervisor_agent.v3_lifecycle import v3_manager

    try:
        infra = await v3_manager.ensure_started(ctx)
        if infra.poller:
            await infra.poller.force_poll_once()
    except asyncio.CancelledError:
        raise
    except Exception:
        pass


def _resolve_meta_rule_conflicts(
    meta_rules: list[Any],
    kt: Any | None = None,
) -> tuple[list[Any], int, list[str]]:
    """Resolve contradictory meta-rules by alias and semantic mutual exclusion.

    Mutual exclusion edges are formed by:
    - Shared alias (decision 28): explicit user-declared conflict group
    - Semantic contradiction (title_sim > 0.5 and content_sim < 0.3): catches
      contradictions that lack shared aliases

    Per group: different priority keeps highest; same top priority picks the
    rule with the latest created_at as winner. Rules without any edge are kept.

    Returns (resolved_rules, suppressed_count, unresolved_notes).
    """
    if len(meta_rules) <= 1:
        return meta_rules, 0, []

    # Build adjacency: alias-shared edges + semantic-contradiction edges
    edges: dict[int, set[int]] = {i: set() for i in range(len(meta_rules))}

    alias_to_indices: dict[str, set[int]] = {}
    for i, rule in enumerate(meta_rules):
        for alias in rule.metadata.get("aliases", []):
            alias_to_indices.setdefault(alias, set()).add(i)
    for indices in alias_to_indices.values():
        idx_list = sorted(indices)
        for a in range(len(idx_list)):
            for b in range(a + 1, len(idx_list)):
                edges[idx_list[a]].add(idx_list[b])
                edges[idx_list[b]].add(idx_list[a])

    # Semantic conflict detection: O(n^2), bounded by MAX_META_RULES=15 → 105 pairs
    if kt is not None:
        try:
            from src.common.knowledge_tree.storage.vector_store import cosine_similarity
            title_embs = [kt.embedder(r.title) for r in meta_rules]
            content_embs = [
                r.embedding if r.embedding is not None else kt.embedder(r.content)
                for r in meta_rules
            ]
            for i in range(len(meta_rules)):
                for j in range(i + 1, len(meta_rules)):
                    if j in edges[i]:
                        continue
                    t_sim = cosine_similarity(title_embs[i], title_embs[j])
                    c_sim = cosine_similarity(content_embs[i], content_embs[j])
                    if t_sim > 0.5 and c_sim < 0.3:
                        edges[i].add(j)
                        edges[j].add(i)
        except Exception:
            pass

    # Connected components via BFS over the unified edge set
    visited: set[int] = set()
    groups: list[list[int]] = []
    for i in range(len(meta_rules)):
        if i in visited:
            continue
        component: set[int] = set()
        queue = [i]
        while queue:
            idx = queue.pop()
            if idx in component:
                continue
            component.add(idx)
            for neighbor in edges[idx]:
                if neighbor not in component:
                    queue.append(neighbor)
        visited.update(component)
        groups.append(sorted(component))

    # Resolve each group
    resolved: list[Any] = []
    suppressed = 0
    unresolved_notes: list[str] = []
    for group in groups:
        by_priority = sorted(
            group,
            key=lambda i: meta_rules[i].metadata.get("priority", 0),
            reverse=True,
        )
        top_priority = meta_rules[by_priority[0]].metadata.get("priority", 0)
        top_count = sum(
            1 for i in by_priority
            if meta_rules[i].metadata.get("priority", 0) == top_priority
        )
        if len(group) == 1:
            resolved.append(meta_rules[group[0]])
        elif top_count == 1:
            resolved.append(meta_rules[by_priority[0]])
            suppressed += len(group) - 1
        else:
            tied = [meta_rules[i] for i in by_priority[:top_count]]
            tied.sort(key=lambda r: r.created_at or "", reverse=True)
            resolved.append(tied[0])
            suppressed += top_count - 1
            remaining = by_priority[top_count:]
            if remaining:
                resolved.append(meta_rules[remaining[0]])
                suppressed += len(remaining) - 1

    resolved.sort(key=lambda r: r.metadata.get("priority", 0), reverse=True)
    return resolved, suppressed, unresolved_notes


def _detect_rag_contradictions(
    kt: Any, results: list[tuple[Any, float]]
) -> set[int]:
    """检测 RAG 结果间的潜在矛盾：同主题但内容分歧。
    使用 title 相似度高 + content 相似度低作为矛盾信号。
    """
    if len(results) < 2:
        return set()
    try:
        from src.common.knowledge_tree.storage.vector_store import cosine_similarity
        contradiction_indices: set[int] = set()
        for i in range(len(results)):
            for j in range(i + 1, len(results)):
                ni, _ = results[i]
                nj, _ = results[j]
                if ni.embedding is None or nj.embedding is None:
                    continue
                title_sim = cosine_similarity(kt.embedder(ni.title), kt.embedder(nj.title))
                content_sim = cosine_similarity(ni.embedding, nj.embedding)
                if title_sim > 0.5 and content_sim < 0.3:
                    contradiction_indices.add(i)
                    contradiction_indices.add(j)
        return contradiction_indices
    except Exception:
        return set()


async def kt_retrieve(state: State, runtime: Runtime[Context]) -> dict:
    """用户消息入口：高阈值 RAG 检索，自动注入相关知识。

    仅在 __start__ 入口执行，工具循环不重复注入。
    """
    if not runtime.context.enable_knowledge_tree:
        return {"kt_context": "", "kt_meta_rules": "", "kt_optimization_suggestions": "", "kt_snapshot_data": {}}

    from langchain_core.messages import HumanMessage

    # 提取用户最新消息作为查询
    query = ""
    for msg in reversed(state.messages):
        if isinstance(msg, HumanMessage):
            query = msg.content if isinstance(msg.content, str) else str(msg.content)
            break
    if not query:
        return {"kt_context": "", "kt_meta_rules": "", "kt_optimization_suggestions": "", "kt_snapshot_data": {}}

    # 获取 KT 实例并检索
    from src.common.knowledge_tree import get_or_create_kt
    from src.common.knowledge_tree.config import KnowledgeTreeConfig

    config = KnowledgeTreeConfig.from_context(runtime.context)

    try:
        kt = get_or_create_kt(config)
        results, _log = await asyncio.to_thread(kt.retrieve, query)
    except Exception as e:
        logger.warning("KT auto-retrieve failed: %s", e)
        return {"kt_context": "", "kt_meta_rules": "", "kt_optimization_suggestions": "", "kt_snapshot_data": {}}

    # 获取持久元规则（每次请求都注入，绕过相似度阈值）
    kt_meta_rules_str = ""
    try:
        meta_rules = await asyncio.to_thread(kt.get_meta_rules)
        if meta_rules:
            meta_rules.sort(
                key=lambda n: n.metadata.get("priority", 0), reverse=True
            )
            # 治理安全阀：即使存储层超出上限，注入时截断
            from src.common.knowledge_tree.config import MAX_META_RULES
            if len(meta_rules) > MAX_META_RULES:
                logger.warning(
                    "KT meta-rules: truncating %d to %d",
                    len(meta_rules), MAX_META_RULES,
                )
                meta_rules = meta_rules[:MAX_META_RULES]
            # 注入前矛盾消解：共享别名的规则互斥，每组只保留最高优先级
            original_count = len(meta_rules)
            meta_rules, suppressed, unresolved = _resolve_meta_rule_conflicts(meta_rules, kt=kt)
            rules_lines = [f"- {r.content}" for r in meta_rules]
            kt_meta_rules_str = "\n".join(rules_lines)
            if suppressed > 0:
                kt_meta_rules_str = (
                    f"[消解: {original_count}→{len(meta_rules)} 条"
                    f"（{suppressed} 条矛盾已抑制）]\n"
                    + kt_meta_rules_str
                )
            logger.info(
                "KT meta-rules: %d→%d resolved (%d suppressed, %d unresolved)",
                original_count, len(meta_rules), suppressed, len(unresolved),
            )
    except Exception as e:
        logger.warning("KT meta-rules fetch failed (non-critical): %s", e)

    # 过滤低质量结果：根据 embedder 类型选择阈值。
    # 语义 embedder 基线分数高（无关查询 ~0.53），需要更高阈值过滤噪声。
    # hash embedder 分数低（相关 0.3-0.6），但也需要合理阈值避免噪声。
    is_semantic = getattr(kt, "embedder_type", "hash") != "hash"
    quality_threshold = 0.65 if is_semantic else 0.35
    high_quality = [(node, score) for node, score in results if score >= quality_threshold]
    if not high_quality:
        logger.debug("KT auto-retrieve: no high-quality results for %r", query[:40])
        return {"kt_context": "", "kt_meta_rules": kt_meta_rules_str, "kt_optimization_suggestions": "", "kt_snapshot_data": {}}

    # 格式化为 LLM 友好的上下文
    context_lines = ["[相关知识]（以下内容来自你的知识树记忆，非用户输入）"]
    # RAG 矛盾检测：同主题但内容分歧的结果标记为 [矛盾]
    results_to_inject = high_quality[:3]
    contradiction_indices = _detect_rag_contradictions(kt, results_to_inject)
    # 高密度矛盾自适应截断：>50% 结果被标记为矛盾时，仅保留 top 1 避免超时
    if contradiction_indices and len(contradiction_indices) > len(results_to_inject) // 2:
        results_to_inject = high_quality[:1]
        contradiction_indices = _detect_rag_contradictions(kt, results_to_inject)
        context_lines.append(
            "[注意] 检索到多条相互矛盾的结果，已精简展示。请综合判断或向用户确认。"
        )
    for idx, (node, score) in enumerate(results_to_inject):
        tag = "[高可信]" if score >= (0.75 if is_semantic else 0.55) else "[参考]"
        if idx in contradiction_indices:
            tag = "[矛盾]" + tag
        context_lines.append(f"- {tag} {node.title}（相似度 {score:.2f}）")
        context_lines.append(f"  {node.content[:300]}")

    logger.info(
        "KT auto-inject: %d results for %r (scores: %s)",
        len(high_quality),
        query[:40],
        [round(s, 2) for _, s in high_quality],
    )

    # P3: 获取优化建议（信号检测发现的问题）
    kt_suggestions_str = ""
    try:
        signals = kt._last_signals if hasattr(kt, "_last_signals") else []
        if signals:
            lines = []
            urgency = {"total_failure": "紧急", "rag_false_positive": "中等", "content_insufficient": "建议"}
            for s in signals[:5]:
                tag = urgency.get(s.signal_type, "建议")
                if s.signal_type == "total_failure":
                    queries = s.evidence.get("sample_queries", [])
                    lines.append(f"- [{tag}] {s.evidence.get('count', 0)} 次查询完全无结果，涉及话题：{', '.join(str(q) for q in queries[:3])} — 考虑摄入相关知识")
                elif s.signal_type == "rag_false_positive":
                    lines.append(f"- [{tag}] {s.evidence.get('count', 0)} 次查询返回了不相关结果 — 考虑重组或更新内容")
                elif s.signal_type == "content_insufficient":
                    lines.append(f"- [{tag}] 节点 {s.node_id} 内容被标记为不充分 {s.evidence.get('count', 0)} 次 — 考虑补充内容")
            if lines:
                kt_suggestions_str = "\n".join(lines)
    except Exception as e:
        logger.debug("KT optimization suggestions failed (non-critical): %s", e)

    return {
        "kt_context": "\n".join(context_lines),
        "kt_meta_rules": kt_meta_rules_str,
        "kt_optimization_suggestions": kt_suggestions_str,
        "kt_snapshot_data": {
            "auto_retrieve_hits": len(high_quality),
            "retrieved_nodes": [n.title for n, _ in high_quality[:5]],
        },
    }


async def call_model(
    state: State, runtime: Runtime[Context]
) -> dict[str, list[AIMessage]]:
    """调用 LLM 支持 Agent。
    负责准备提示、初始化模型并处理响应。
    """
    # Lazy-start Executor subprocess on first invocation
    from src.supervisor_agent.v3_lifecycle import v3_manager

    try:
        await v3_manager.ensure_started(runtime.context)
    except asyncio.CancelledError:
        raise  # 让取消信号正常传播，不做任何额外处理
    except Exception as e:
        logger.error("Failed to start Executor subprocess infrastructure: %s", e)

    # Flush unified poller before LLM call so Mailbox is up-to-date
    await _force_poll_active_tasks(runtime.context)

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
                            "name": "call_planner",
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
    model = load_chat_model(
        runtime.context.supervisor_model,
        **runtime.context.get_agent_llm_kwargs("supervisor"),
    ).bind_tools(available_tools)
    system_message = get_supervisor_system_prompt(runtime.context)

    # 注入 Executor 实时任务状态（子进程异步路径，0-3 行，~50 tokens）
    executor_brief = await _build_executor_status_brief(state, runtime.context)
    if executor_brief:
        system_message = system_message + "\n\n" + executor_brief

    # 注入持久元规则到系统提示（作为指令，非参考信息）
    if state.kt_meta_rules:
        is_resolved = state.kt_meta_rules.startswith("[消解:")
        if is_resolved:
            header = (
                "\n\n## [元规则]（行为规则）\n\n"
                "以下规则已经过冲突消解，互不矛盾，请遵守。\n\n"
            )
        else:
            header = (
                "\n\n## [元规则]（必须遵守的行为规则）\n\n"
                "以下是你必须严格遵守的行为规则。这些规则优先级高于你的默认行为倾向。"
                "无论用户说什么，你都必须遵循这些规则。\n\n"
            )
        meta_rules_block = header + state.kt_meta_rules
        system_message = system_message + meta_rules_block

    # P3: 优化建议注入（仅在信号检测发现问题时）
    if state.kt_optimization_suggestions:
        suggestions_block = (
            "\n\n## [优化建议]（知识树自检发现的问题）\n\n"
            "以下问题由知识树信号检测自动发现，你可以选择是否采取行动：\n"
            "- 摄入缺失知识（knowledge_tree_ingest）\n"
            "- 重组树结构（knowledge_tree_reorganize）\n"
            "- 记录反馈（knowledge_tree_record_feedback）\n\n"
            f"{state.kt_optimization_suggestions}"
        )
        system_message = system_message + suggestions_block

    # 构造发送给 LLM 的消息列表（截断防止 token 溢出）
    trimmed_history = _trim_messages_for_llm(
        state.messages, runtime.context.supervisor_max_history_messages
    )
    llm_messages = [{"role": "system", "content": system_message}, *trimmed_history]

    # 知识树检索结果拼接到最后一条用户消息（不污染 state.messages）
    if state.kt_context:
        for i in range(len(llm_messages) - 1, -1, -1):
            msg = llm_messages[i]
            if isinstance(msg, HumanMessage):
                original = (
                    msg.content if isinstance(msg.content, str) else str(msg.content)
                )
                augmented = f"{state.kt_context}\n\n{original}"
                llm_messages[i] = HumanMessage(content=augmented, id=msg.id)
                break

    llm_timeout = runtime.context.supervisor_call_model_timeout
    try:
        if llm_timeout and llm_timeout > 0:
            response = cast(
                AIMessage,
                await asyncio.wait_for(
                    invoke_chat_model(
                        model,
                        llm_messages,
                        enable_streaming=runtime.context.enable_llm_streaming,
                    ),
                    timeout=llm_timeout,
                ),
            )
        else:
            response = cast(
                AIMessage,
                await invoke_chat_model(
                    model,
                    llm_messages,
                    enable_streaming=runtime.context.enable_llm_streaming,
                ),
            )
    except asyncio.TimeoutError:
        logger.error("Supervisor LLM call timed out after %.0fs", llm_timeout)
        return {
            "messages": [
                AIMessage(
                    content=(
                        f"模型响应超时（{llm_timeout:.0f}s），无法完成本次推理。"
                        "请稍后重试，或换一种方式描述你的需求。"
                    )
                )
            ],
            "supervisor_decision": SupervisorDecision(
                mode=1,
                reason=f"Supervisor LLM call timed out ({llm_timeout:.0f}s)",
                confidence=0.99,
            ),
        }

    if _is_thinking_visible(runtime.context) and not response.tool_calls:
        response = _inject_reasoning_for_visible_mode(response)

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


async def dynamic_tools_node(state: State, runtime: Runtime[Context]) -> dict:
    """动态执行工具，并同步更新 PlannerSession 状态。

    - call_planner 执行后：将新 plan_json 写入 planner_session
    - call_executor 执行后：将 updated_plan_json（带执行状态）写回 planner_session
    """
    available_tools = await get_tools(runtime.context)
    tool_node = ToolNode(available_tools)

    try:
        result = await tool_node.ainvoke(state)
    except asyncio.CancelledError:
        logger.info("dynamic_tools_node 被取消，工具执行中断")
        raise  # CancelledError 必须传播，不做吞没

    # Flush unified poller after tool execution -> write completions to Mailbox
    await _force_poll_active_tasks(runtime.context)

    tool_messages: list[ToolMessage] = result.get("messages", [])
    id_to_name = _build_id_to_name(state)
    id_to_call = _build_id_to_call(state)


    sanitized_tool_messages: list[ToolMessage] = []
    updates: dict = {"messages": sanitized_tool_messages}

    for tm in tool_messages:
        if not isinstance(tm, ToolMessage):
            continue
        tool_name = id_to_name.get(tm.tool_call_id, "")
        content = tm.content if isinstance(tm.content, str) else str(tm.content)

        if tool_name == "call_planner" and content.strip():
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

            planner_reasoning, new_plan_json = _split_planner_output(content.strip())
            new_plan_id, new_version = _parse_plan_meta(new_plan_json)
            if new_plan_id:
                tool_call = id_to_call.get(tm.tool_call_id, {})
                args = tool_call.get("args", {}) if isinstance(tool_call, dict) else {}
                task_core = (
                    str(args.get("task_core", "")).strip()
                    if isinstance(args, dict)
                    else ""
                )
                pid_history = list(existing_history.get(new_plan_id, []))
                pid_history.append(
                    {"role": "user", "content": task_core or "（空 task_core）"}
                )
                pid_history.append({"role": "assistant", "content": new_plan_json})
                existing_history[new_plan_id] = pid_history
                existing_last_output[new_plan_id] = new_plan_json
                if isinstance(new_version, int):
                    existing_last_version[new_plan_id] = new_version

                if (
                    state.planner_session is not None
                    and (state.planner_session.plan_json or "").strip()
                ):
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
                planner_reasoning=planner_reasoning,
                last_executor_status=(
                    state.planner_session.last_executor_status
                    if state.planner_session
                    else None
                ),
                last_executor_error=(
                    state.planner_session.last_executor_error
                    if state.planner_session
                    else None
                ),
                last_executor_summary=(
                    state.planner_session.last_executor_summary
                    if state.planner_session
                    else None
                ),
                planner_history_by_plan_id=existing_history,
                planner_last_version_by_plan_id=existing_last_version,
                planner_last_output_by_plan_id=existing_last_output,
                plan_archive_by_plan_id=existing_archive,
            )
            logger.info(
                "PlannerSession 已更新（call_planner），session_id=%s", session_id
            )

        elif tool_name == "call_executor":
            if "[EXECUTOR_RESULT]" in content:
                # 同步完成或异步派发失败 → 完整状态更新
                updates.update(
                    _process_executor_completion(
                        state, content, tm, sanitized_tool_messages
                    )
                )
                # Record in executor_task_history
                meta_plan_id = _extract_plan_id_from_meta(content)
                exec_status, _ = _extract_executor_status(content)
                if meta_plan_id:
                    history = dict(state.executor_task_history)
                    history[meta_plan_id] = ExecutorTaskRecord(
                        plan_id=meta_plan_id,
                        status=exec_status or "unknown",
                        queryable=True,
                        last_updated=datetime.now().isoformat(timespec="seconds"),
                    )
                    updates["executor_task_history"] = _trim_task_history(history)
                # Entry A: 自动从 Executor 结果提取知识
                if exec_status in ("completed", "failed") and runtime.context.enable_knowledge_tree:
                    await asyncio.to_thread(
                        _try_auto_ingest_executor_result, content, runtime.context, exec_status
                    )
            elif "[EXECUTOR_DISPATCH]" in content:
                # 异步派发成功 → 存储 ActiveExecutorTask，透传消息（去除内部标记）
                clean_content = re.sub(
                    r"\n?\[EXECUTOR_DISPATCH\]\s*\{.*?\}", "", content, flags=re.DOTALL
                ).strip()
                sanitized_tool_messages.append(
                    tm.model_copy(
                        update={"content": clean_content or "Executor 已异步派发。"}
                    )
                )
                dispatched_pid = _extract_dispatched_plan_id(content)
                if dispatched_pid:
                    new_tasks = dict(
                        updates.get(
                            "active_executor_tasks", state.active_executor_tasks
                        )
                    )
                    new_tasks[dispatched_pid] = ActiveExecutorTask(
                        plan_id=dispatched_pid,
                        status="dispatched",
                    )
                    updates["active_executor_tasks"] = new_tasks
                    logger.info(
                        "Async executor dispatch recorded, plan_id=%s", dispatched_pid
                    )
                # Record in executor_task_history
                if dispatched_pid:
                    history = dict(state.executor_task_history)
                    history[dispatched_pid] = ExecutorTaskRecord(
                        plan_id=dispatched_pid,
                        status="dispatched",
                        queryable=False,
                        last_updated=datetime.now().isoformat(timespec="seconds"),
                    )
                    updates["executor_task_history"] = _trim_task_history(history)
            else:
                sanitized_tool_messages.append(tm)
        elif tool_name == "manage_executor":
            tool_call = id_to_call.get(tm.tool_call_id, {})
            args = tool_call.get("args", {}) if isinstance(tool_call, dict) else {}
            action_arg = str(args.get("action", "")).strip()

            if action_arg == "get_result" and "[EXECUTOR_RESULT]" in content:
                # get_result 与同步 call_executor 完成路径共用处理逻辑
                updates.update(
                    _process_executor_completion(
                        state, content, tm, sanitized_tool_messages
                    )
                )
                detail_arg = str(args.get("detail", "overview")).strip().lower()
                if detail_arg == "full":
                    ps = updates.get("planner_session")
                    if ps and (ps.last_executor_full_output or "").strip():
                        _append_full_executor_detail_to_last_tool_message(
                            sanitized_tool_messages, ps.last_executor_full_output or ""
                        )
                # 更新 ActiveExecutorTask 状态，并清理已终态的任务
                meta_plan_id = _extract_plan_id_from_meta(content)
                if meta_plan_id and meta_plan_id in state.active_executor_tasks:
                    exec_status, _ = _extract_executor_status(content)
                    new_tasks = dict(state.active_executor_tasks)
                    if exec_status in ("completed", "failed", "stopped"):
                        del new_tasks[meta_plan_id]
                        history = dict(state.executor_task_history)
                        history[meta_plan_id] = ExecutorTaskRecord(
                            plan_id=meta_plan_id,
                            status=exec_status,
                            queryable=True,
                            last_updated=datetime.now().isoformat(timespec="seconds"),
                        )
                        updates["executor_task_history"] = _trim_task_history(history)
                    else:
                        new_tasks[meta_plan_id] = ActiveExecutorTask(
                            plan_id=meta_plan_id,
                            status=exec_status or "unknown",
                        )
                    updates["active_executor_tasks"] = new_tasks
            elif action_arg == "check_progress":
                # 进度查看：若任务在运行，将 dispatched 升级为 running
                sanitized_tool_messages.append(tm)
                if "任务运行中" in content:
                    target_pid = str(args.get("plan_id", "")).strip()
                    if target_pid:
                        base_tasks = updates.get(
                            "active_executor_tasks", state.active_executor_tasks
                        )
                        task = base_tasks.get(target_pid)
                        if task and task.status == "dispatched":
                            new_tasks = dict(base_tasks)
                            new_tasks[target_pid] = ActiveExecutorTask(
                                plan_id=target_pid,
                                status="running",
                            )
                            updates["active_executor_tasks"] = new_tasks
            elif action_arg == "list_tasks":
                # 任务注册表同步 → 更新 executor_task_history
                sanitized_tool_messages.append(tm)
                registry_updates = _extract_registry_updates(content)
                if registry_updates:
                    history = dict(state.executor_task_history)
                    history.update(registry_updates)
                    updates["executor_task_history"] = _trim_task_history(history)
            else:
                sanitized_tool_messages.append(tm)
        else:
            sanitized_tool_messages.append(tm)

    return updates


_MAX_TASK_HISTORY = 50


def _trim_task_history(history: dict) -> dict:
    """Keep at most _MAX_TASK_HISTORY entries, dropping the oldest by insertion order."""
    if len(history) <= _MAX_TASK_HISTORY:
        return history
    keys = list(history.keys())
    for k in keys[: len(keys) - _MAX_TASK_HISTORY]:
        del history[k]
    return history


def _build_id_to_name(state: State) -> dict[str, str]:
    """从最后一条 AIMessage 中构建 tool_call_id → tool_name 的映射。"""
    if not state.messages:
        return {}
    last_ai = state.messages[-1]
    if not isinstance(last_ai, AIMessage) or not last_ai.tool_calls:
        return {}
    return {
        tc["id"]: tc["name"] for tc in last_ai.tool_calls if "id" in tc and "name" in tc
    }


def _build_id_to_call(state: State) -> dict[str, dict]:
    """从最后一条 AIMessage 中构建 tool_call_id -> tool_call 映射。"""
    if not state.messages:
        return {}
    last_ai = state.messages[-1]
    if not isinstance(last_ai, AIMessage) or not last_ai.tool_calls:
        return {}
    out: dict[str, dict] = {}
    for tc in last_ai.tool_calls:
        if "id" in tc:
            out[tc["id"]] = tc
    return out


def _split_planner_output(content: str) -> tuple[str, str]:
    """从 call_planner 返回中拆分 reasoning 和 plan_json。

    约定格式：[PLANNER_REASONING]...[/PLANNER_REASONING] 后跟 JSON。
    若无标记则整个内容视为 plan_json。
    """
    m = re.search(
        r"\[PLANNER_REASONING\]\s*([\s\S]*?)\s*\[/PLANNER_REASONING\]",
        content,
    )
    if m:
        reasoning = m.group(1).strip()
        remaining = content[: m.start()] + content[m.end() :]
        return reasoning, remaining.strip()
    return "", content.strip()


def _trim_messages_for_llm(
    messages: list[Any],
    max_messages: int,
) -> list[Any]:
    """截断消息历史，保持工具调用序列完整性。

    策略：从末尾保留 max_messages 条消息，然后移除所有孤立的
    ToolMessage（其 tool_call_id 在窗口内没有任何 AI message 的
    tool_calls 引用）。

    Args:
        messages: 原始消息列表。
        max_messages: 最大保留条数。<= 0 表示不截断。

    Returns:
        截断后的消息列表（浅拷贝）。
    """
    if max_messages <= 0:
        return messages

    if len(messages) <= max_messages:
        trimmed = messages
    else:
        trimmed = messages[-max_messages:]

    claimed_ids: set[str] = set()
    has_tool_messages = False
    for msg in trimmed:
        if isinstance(msg, AIMessage):
            for tc in getattr(msg, "tool_calls", []) or []:
                tc_id = tc.get("id")
                if tc_id:
                    claimed_ids.add(tc_id)
        elif isinstance(msg, ToolMessage):
            has_tool_messages = True

    if not has_tool_messages:
        return trimmed

    result: list[Any] = []
    for msg in trimmed:
        if isinstance(msg, ToolMessage):
            if getattr(msg, "tool_call_id", None) in claimed_ids:
                result.append(msg)
        else:
            result.append(msg)

    return result


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
    """从 call_executor 返回的 ToolMessage 内容中提取 updated_plan_json。

    约定格式：内容末尾有一行 `[EXECUTOR_RESULT] {...json...}`
    """
    import json as _json
    import re as _re

    match = _re.search(r"\[EXECUTOR_RESULT\]\s*(\{.*\})", content, _re.DOTALL)
    if not match:
        return None
    try:
        meta = _json.loads(match.group(1))
        updated = meta.get("updated_plan_json", "")
        return updated if updated else None
    except _json.JSONDecodeError:
        logger.warning("call_executor 返回的 EXECUTOR_RESULT 解析失败")
        return None


def _extract_executor_status(content: str) -> tuple[str | None, str | None]:
    """从 call_executor 返回的 ToolMessage 内容中提取 status 和 error_detail。

    返回 (status, error_detail)，解析失败时返回 (None, None)。
    """
    import json as _json
    import re as _re

    match = _re.search(r"\[EXECUTOR_RESULT\]\s*(\{.*\})", content, _re.DOTALL)
    if not match:
        return None, None
    try:
        meta = _json.loads(match.group(1))
        return meta.get("status"), meta.get("error_detail")
    except _json.JSONDecodeError:
        return None, None


def _extract_snapshot_json(content: str) -> str | None:
    """从 call_executor 返回中提取 snapshot_json。"""
    import json as _json
    import re as _re

    match = _re.search(r"\[EXECUTOR_RESULT\]\s*(\{.*\})", content, _re.DOTALL)
    if not match:
        return None
    try:
        meta = _json.loads(match.group(1))
        snap = meta.get("snapshot_json", "")
        return snap if snap else None
    except _json.JSONDecodeError:
        return None


def _extract_executor_summary(content: str) -> str:
    """从 call_executor 返回中提取 summary（[EXECUTOR_RESULT] 前正文）。"""
    marker = "[EXECUTOR_RESULT]"
    return content.split(marker, 1)[0].strip() if marker in content else content.strip()


def _extract_dispatched_plan_id(content: str) -> str | None:
    """从 [EXECUTOR_DISPATCH] 标记中提取 plan_id。"""
    match = re.search(r"\[EXECUTOR_DISPATCH\]\s*(\{.*?\})", content, re.DOTALL)
    if not match:
        return None
    try:
        data = json.loads(match.group(1))
        return data.get("plan_id")
    except json.JSONDecodeError:
        return None


def _extract_plan_id_from_meta(content: str) -> str | None:
    """从 [EXECUTOR_RESULT] meta JSON 中提取 plan_id（含 manage_executor(action="get_result") 返回）。"""
    match = re.search(r"\[EXECUTOR_RESULT\]\s*(\{.*\})", content, re.DOTALL)
    if not match:
        return None
    try:
        meta = json.loads(match.group(1))
        return meta.get("plan_id")
    except json.JSONDecodeError:
        return None


def _extract_registry_updates(content: str) -> dict[str, ExecutorTaskRecord]:
    """从 [EXECUTOR_REGISTRY_UPDATE] 标记中提取任务记录更新。"""
    match = re.search(r"\[EXECUTOR_REGISTRY_UPDATE\]\s*(\[.*\])", content, re.DOTALL)
    if not match:
        return {}
    try:
        items = json.loads(match.group(1))
        result: dict[str, ExecutorTaskRecord] = {}
        for item in items:
            pid = item.get("plan_id")
            if pid:
                result[pid] = ExecutorTaskRecord(
                    plan_id=pid,
                    status=item.get("status", "unknown"),
                    queryable=item.get("queryable", False),
                    last_updated=item.get("last_updated", ""),
                )
        return result
    except json.JSONDecodeError:
        return {}


def _append_full_executor_detail_to_last_tool_message(
    sanitized_tool_messages: list[ToolMessage],
    full_output: str,
) -> None:
    """在 manage_executor(action="get_result", detail="full") 且含 [EXECUTOR_RESULT] 时，把步骤级详情拼入给 LLM 的 ToolMessage。"""
    if not sanitized_tool_messages or not (full_output or "").strip():
        return
    last = sanitized_tool_messages[-1]
    if not isinstance(last, ToolMessage):
        return
    old = last.content if isinstance(last.content, str) else str(last.content)
    hint_legacy = (
        "\n\n（如需查看完整的步骤级执行详情，请调用 get_executor_full_output）"
    )
    hint_new = '\n\n（如需步骤级执行详情，可调用 manage_executor(action="get_result", plan_id=…, detail="full")）'
    body = old.replace(hint_legacy, "").replace(hint_new, "").rstrip()
    new_content = f"{body}\n\n{(full_output or '').strip()}"
    sanitized_tool_messages[-1] = last.model_copy(update={"content": new_content})


def _try_auto_ingest_executor_result(content: str, ctx: Any, exec_status: str = "completed") -> None:
    """Entry A: 从 Executor 完成结果中自动提取知识并存入知识树。

    设计原则：全程 try/except 包裹，KT 失败不影响主图执行路径。
    同时处理 completed 和 failed 状态——失败结果的 failure_reason 是重要的教训知识。
    """
    try:
        from src.common.knowledge_tree import get_or_create_kt
        from src.common.knowledge_tree.config import KnowledgeTreeConfig
        from src.common.knowledge_tree.ingestion.extractor import (
            extract_experience_from_executor_result,
            extract_knowledge_from_executor_result,
        )

        summary = _extract_executor_summary(content)
        updated_plan = _extract_updated_plan_from_executor(content) or ""

        chunks = extract_knowledge_from_executor_result(
            summary, updated_plan, exec_status
        )

        # 即使无常规知识块，也继续提取经验
        config = KnowledgeTreeConfig.from_context(ctx)
        kt = get_or_create_kt(config)
        total_ingested = 0
        for chunk in chunks:
            report = kt.ingest(
                chunk,
                trigger="task_complete",
                source="auto:executor",
            )
            total_ingested += report.nodes_ingested

        # 元认知：提取结构化经验节点
        experiences = extract_experience_from_executor_result(
            summary, updated_plan, exec_status
        )
        for exp in experiences:
            exp_report = kt.ingest(
                exp,
                trigger="task_complete",
                source="auto:executor_experience",
                metadata={"node_type": "experience"},
            )
            total_ingested += exp_report.nodes_ingested

        if total_ingested > 0:
            logger.info(
                "Entry A: auto-ingested %d knowledge chunks (%d experiences) from executor result",
                total_ingested,
                len(experiences),
            )
    except Exception:
        logger.debug("Entry A: auto-ingest failed (non-critical)", exc_info=True)


def _process_executor_completion(
    state: State,
    content: str,
    tm: ToolMessage,
    sanitized_tool_messages: list[ToolMessage],
) -> dict:
    """处理 Executor 完成结果（call_executor 同步完成或 manage_executor(action="get_result")）。

    返回 updates dict 供 dynamic_tools_node 合并。
    """
    updated_plan = _extract_updated_plan_from_executor(content)
    exec_status, exec_error = _extract_executor_status(content)
    exec_summary = _extract_executor_summary(content)
    snapshot_json = _extract_snapshot_json(content)
    full_output = _build_executor_full_output(
        exec_summary, exec_status, exec_error, updated_plan, snapshot_json
    )
    public_feedback = _build_executor_feedback_for_llm(content, exec_status, exec_error)
    sanitized_tool_messages.append(tm.model_copy(update={"content": public_feedback}))
    return _build_executor_updates(
        state, updated_plan, exec_status, exec_error, exec_summary, full_output
    )


def _build_executor_updates(
    state: State,
    updated_plan: str | None,
    exec_status: str | None,
    exec_error: str | None,
    exec_summary: str,
    full_output: str,
) -> dict:
    """从 Executor 完成结果构建 state updates dict。"""
    next_replan_count = state.replan_count
    if exec_status == "failed":
        next_replan_count = state.replan_count + 1
    elif exec_status == "completed":
        next_replan_count = 0
    elif exec_status == "paused":
        next_replan_count = state.replan_count

    if state.planner_session is not None:
        session_id = state.planner_session.session_id
        next_plan_json = (
            updated_plan if updated_plan else state.planner_session.plan_json
        )
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
    return {
        "planner_session": PlannerSession(
            session_id=session_id,
            plan_json=next_plan_json,
            planner_reasoning=(
                state.planner_session.planner_reasoning if state.planner_session else ""
            ),
            last_executor_status=exec_status,
            last_executor_error=exec_error,
            last_executor_summary=exec_summary,
            last_executor_full_output=full_output,
            planner_history_by_plan_id=existing_history,
            planner_last_version_by_plan_id=existing_last_version,
            planner_last_output_by_plan_id=existing_last_output,
            plan_archive_by_plan_id=existing_archive,
        ),
        "replan_count": next_replan_count,
    }


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


def _build_executor_full_output(
    summary_text: str,
    status: str | None,
    error_detail: str | None,
    updated_plan_json: str | None,
    snapshot_json: str | None,
) -> str:
    """构建 Supervisor 可按需查阅的完整执行详情（含步骤级结果）。"""
    import json as _json

    parts: list[str] = []
    parts.append(f"## Executor 执行详情\n\n状态：{status or '未知'}")
    if error_detail:
        parts.append(f"错误详情：{error_detail}")
    parts.append(f"\n### 执行摘要\n\n{summary_text}")

    if updated_plan_json:
        try:
            plan = _json.loads(updated_plan_json)
            steps = plan.get("steps", []) if isinstance(plan, dict) else []
            if steps:
                parts.append("\n### 步骤级执行结果\n")
                for s in steps:
                    if not isinstance(s, dict):
                        continue
                    sid = s.get("step_id", "?")
                    intent = s.get("intent", "")
                    st = s.get("status", "unknown")
                    rs = s.get("result_summary") or ""
                    fr = s.get("failure_reason") or ""
                    line = f"- **{sid}** [{st}] {intent}"
                    if rs:
                        line += f"\n  结果：{rs}"
                    if fr:
                        line += f"\n  失败原因：{fr}"
                    parts.append(line)
        except _json.JSONDecodeError:
            parts.append(f"\n### 原始 updated_plan_json\n\n{updated_plan_json}")

    if snapshot_json:
        parts.append(f"\n### Checkpoint 快照\n\n{snapshot_json}")

    return "\n".join(parts)


def _build_executor_feedback_for_llm(
    content: str,
    status: str | None,
    error_detail: str | None,
) -> str:
    """构造给 Supervisor LLM 的精简反馈，避免注入大体量 updated_plan_json。"""
    marker = "[EXECUTOR_RESULT]"
    summary_text = (
        content.split(marker, 1)[0].strip() if marker in content else content.strip()
    )
    hint = '\n\n（如需步骤级执行详情，可调用 manage_executor(action="get_result", plan_id=当前计划顶层 id, detail="full")）'
    if status == "completed":
        return summary_text + hint
    if status == "failed":
        detail = error_detail or "未知错误"
        return (
            f"Executor 执行结果：failed\n失败原因：{detail}\n摘要：{summary_text}"
            + hint
        )
    if status == "paused":
        return f"Executor 执行暂停（checkpoint）：\n{summary_text}" + hint
    return summary_text


def _infer_supervisor_decision(response: AIMessage) -> SupervisorDecision:
    """根据本轮输出推断结构化决策（mode/reason/confidence）。"""
    tool_names = (
        [tc.get("name", "") for tc in response.tool_calls]
        if response.tool_calls
        else []
    )
    if not tool_names:
        return SupervisorDecision(mode=1, reason="无需工具即可回答", confidence=0.85)
    if "call_planner" in tool_names:
        return SupervisorDecision(
            mode=3, reason="检测到多步规划需求，先规划后执行", confidence=0.8
        )
    if "call_executor" in tool_names or "manage_executor" in tool_names:
        return SupervisorDecision(
            mode=2, reason="目标明确，直接工具执行", confidence=0.75
        )
    return SupervisorDecision(mode=2, reason="存在工具调用", confidence=0.6)


def _is_thinking_visible(ctx: Context) -> bool:
    mode = (ctx.supervisor_thinking_visibility or "").strip().lower()
    return mode in ("visible", "show", "on", "1", "true", "display")


def _inject_reasoning_for_visible_mode(response: AIMessage) -> AIMessage:
    reasoning = extract_reasoning_text(response)
    if not reasoning:
        return response
    answer = (
        response.content if isinstance(response.content, str) else str(response.content)
    )
    decorated = f"[思考过程]\n{reasoning}\n\n[最终回答]\n{answer}".strip()
    return response.model_copy(update={"content": decorated})


# ==================== 图定义 ====================

builder = StateGraph(State, input_schema=InputState, context_schema=Context)

builder.add_node(call_model)
builder.add_node("kt_retrieve", kt_retrieve)
builder.add_node("tools", dynamic_tools_node)

builder.add_edge("__start__", "kt_retrieve")
builder.add_edge("kt_retrieve", "call_model")


def route_model_output(state: State) -> Literal["__end__", "tools"]:
    """根据模型输出决定下一个节点。"""
    last_message = state.messages[-1]
    if not isinstance(last_message, AIMessage):
        raise ValueError(f"路由时期望 AIMessage，但收到 {type(last_message).__name__}")
    if not last_message.tool_calls:
        return "__end__"
    return "tools"


builder.add_conditional_edges("call_model", route_model_output)
builder.add_edge("tools", "call_model")

graph = builder.compile(name="ReAct Agent")
