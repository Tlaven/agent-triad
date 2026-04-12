"""Supervisor Agent 工具定义。"""

import asyncio
import json
import logging
import uuid
from typing import Annotated, Any, Callable, List

from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState

from src.common.context import Context
from src.executor_agent.graph import run_executor
from src.planner_agent.graph import run_planner
from src.supervisor_agent.state import PlannerSession, State

logger = logging.getLogger(__name__)


def _normalize_plan_id_arg(plan_id: str | None) -> str | None:
    if plan_id is None:
        return None
    s = str(plan_id).strip()
    return s if s else None


def _resolve_planner_input_for_call_planner(
    task_core: str,
    plan_id: str | None,
    planner_session: PlannerSession | None,
) -> tuple[str | None, str | None]:
    """校验 call_planner 参数，返回 (错误信息, 重规划用的 plan_json)。

    若无需重规划，第二项为 None。
    """
    pid = _normalize_plan_id_arg(plan_id)
    tc = (task_core or "").strip()

    if pid is not None:
        if not planner_session or not (planner_session.plan_json or "").strip():
            return (
                "错误：已指定 plan_id，但当前没有可修订的计划（无 PlannerSession 或 plan_json）。请先完成首次规划或检查会话状态。",
                None,
            )
        try:
            data = json.loads(planner_session.plan_json or "")
        except json.JSONDecodeError:
            return "错误：当前 session 中的 plan_json 无法解析为 JSON。", None
        if not isinstance(data, dict):
            return "错误：当前 plan_json 顶层必须是 JSON 对象。", None
        current_id = data.get("plan_id")
        if current_id != pid:
            return (
                f"错误：plan_id 不匹配。当前计划中的 plan_id 为 {current_id!r}，收到 {pid!r}。",
                None,
            )
        return None, planner_session.plan_json

    if not tc:
        return "错误：首次规划必须提供非空的 task_core（任务核心描述）。", None
    return None, None


def _normalize_plan_json(plan_json: str, previous_plan_json: str | None = None) -> str:
    """Normalize planner output to V1 schema with stable plan_id/version."""
    if not plan_json or not plan_json.strip():
        return plan_json
    try:
        parsed = json.loads(plan_json)
    except json.JSONDecodeError:
        return plan_json
    if not isinstance(parsed, dict):
        return plan_json

    prev: dict[str, Any] = {}
    if previous_plan_json and previous_plan_json.strip():
        try:
            prev_loaded = json.loads(previous_plan_json)
            if isinstance(prev_loaded, dict):
                prev = prev_loaded
        except json.JSONDecodeError:
            prev = {}

    prev_plan_id = prev.get("plan_id")
    # plan_id/version 属于系统字段：忽略 LLM 输出，始终由历史或本地生成决定。
    plan_id = prev_plan_id or f"plan_{uuid.uuid4().hex[:8]}"
    parsed["plan_id"] = plan_id

    prev_version = prev.get("version")
    prev_version = prev_version if isinstance(prev_version, int) else 0
    parsed["version"] = prev_version + 1 if prev_plan_id else 1

    steps = parsed.get("steps", [])
    if isinstance(steps, list):
        for idx, step in enumerate(steps, start=1):
            if not isinstance(step, dict):
                continue
            step["step_id"] = str(step.get("step_id") or f"step_{idx}")
            step["status"] = step.get("status") or "pending"
            step["result_summary"] = step.get("result_summary", None)
            step["failure_reason"] = step.get("failure_reason", None)

    return json.dumps(parsed, ensure_ascii=False, indent=2)


def _build_call_planner_tool(runtime_context: Context):
    @tool
    async def call_planner(
        state: Annotated[State, InjectedState],
        task_core: str = "",
        plan_id: str | None = None,
    ) -> str:
        """调用 Planner Agent 生成或更新意图层 Plan（JSON）。

        Planner 只接收 **task_core** 与（重规划时）由 **plan_id** 从状态中解析出的完整计划。

        - **首次规划**：必须提供非空且**有用的上下文与核心目标**的 ``task_core``，不要传 ``plan_id``。
        - **重规划**：传入当前计划中的 ``plan_id``（与 ``PlannerSession`` 内 JSON 一致）；**强烈建议**同时提供详尽的 ``task_core`` 说明修订方向。完整带执行状态的计划由工具从状态中读取，无需在参数里粘贴 JSON。
        """
        if state.planner_session is None:
            session_id = f"plan_{uuid.uuid4().hex[:8]}"
        else:
            session_id = state.planner_session.session_id

        err, replan_plan_json = _resolve_planner_input_for_call_planner(
            task_core,
            plan_id,
            state.planner_session,
        )
        if err:
            return err

        previous_plan_json = state.planner_session.plan_json if state.planner_session else None
        normalized_pid = _normalize_plan_id_arg(plan_id)
        planner_history_messages = None
        if normalized_pid and state.planner_session is not None:
            planner_history_messages = state.planner_session.planner_history_by_plan_id.get(normalized_pid)

        plan_json = await run_planner(
            task_core,
            plan_id=plan_id,
            replan_plan_json=replan_plan_json,
            planner_history_messages=planner_history_messages,
            context=runtime_context,
        )
        normalized = _normalize_plan_json(plan_json, previous_plan_json=previous_plan_json)

        logger.info("Planner 生成计划，session_id=%s，长度=%d", session_id, len(normalized))
        return normalized

    return call_planner


def _build_get_executor_full_output_tool():
    @tool
    def get_executor_full_output(
        state: Annotated[State, InjectedState],
    ) -> str:
        """查看最近一次 Executor 执行的完整详情（含每个步骤的 result_summary / failure_reason 等）。

        仅在 call_executor 的摘要不足以做出判断时调用。
        """
        if state.planner_session is None or not state.planner_session.last_executor_full_output:
            return "当前没有可查看的 Executor 完整输出。"
        return state.planner_session.last_executor_full_output

    return get_executor_full_output


def _build_call_executor_tool(runtime_context: Context):
    @tool
    async def call_executor(
        state: Annotated[State, InjectedState],
        task_description: str = "",
        plan_id: str | None = None,
    ) -> str:
        """调用 Executor Agent 执行任务或执行已有计划。

        参数约定下列方式任选其一：
        - 仅传 ``task_description``（简短、明确、可执行）
        - 仅传 ``plan_id``（从 session 中读取对应计划）
        """
        td = (task_description or "").strip()
        pid = _normalize_plan_id_arg(plan_id)

        if td and pid:
            return "错误：call_executor 不能同时传 task_description 和 plan_id。Mode2/Mode3 二选一。"

        if pid is not None:
            if state.planner_session is None or not (state.planner_session.plan_json or "").strip():
                return "错误：已指定 plan_id，但当前没有可执行的计划。请先调用 call_planner。"
            try:
                plan_obj = json.loads(state.planner_session.plan_json or "")
            except json.JSONDecodeError:
                return "错误：当前 session 中的 plan_json 无法解析为 JSON。"
            if not isinstance(plan_obj, dict):
                return "错误：当前 plan_json 顶层必须是 JSON 对象。"
            current_id = plan_obj.get("plan_id")
            if current_id != pid:
                return f"错误：plan_id 不匹配。当前计划中的 plan_id 为 {current_id!r}，收到 {pid!r}。"
            plan_json = state.planner_session.plan_json or ""
        else:
            if not td:
                return "错误：Mode2 需提供非空 task_description；Mode3 需提供 plan_id。"
            mode2_plan = {
                "plan_id": f"plan_{uuid.uuid4().hex[:8]}",
                "version": 1,
                "goal": td,
                "steps": [
                    {
                        "step_id": "step_1",
                        "intent": td,
                        "expected_output": "完成任务并给出结果",
                        "status": "pending",
                        "result_summary": None,
                        "failure_reason": None,
                    }
                ],
            }
            plan_json = json.dumps(mode2_plan, ensure_ascii=False, indent=2)

        executor_session_id = f"exec_{uuid.uuid4().hex[:8]}"
        planner_session_id = state.planner_session.session_id if state.planner_session else None

        # ---- 决定同步 / 异步执行路径 ----
        if not runtime_context.enable_v3_parallel:
            # V2 路径：进程内同步执行
            logger.info(
                "Executor 开始执行（同步），executor_session_id=%s，planner_session_id=%s",
                executor_session_id,
                planner_session_id,
            )
            snapshot_json = ""
            try:
                executor_result = await run_executor(
                    plan_json,
                    context=runtime_context,
                )
                status = executor_result.status
                summary = executor_result.summary
                updated_plan_json = executor_result.updated_plan_json
                snapshot_json = getattr(executor_result, "snapshot_json", "") or ""
                error_detail: str | None = None
                if (
                    pid is not None
                    and status == "failed"
                    and not (updated_plan_json or "").strip()
                ):
                    fallback_reason = "Executor 失败且未返回 updated_plan_json，已由 Supervisor 侧兜底补全。"
                    updated_plan_json = _mark_plan_steps_failed(plan_json, fallback_reason)
                    error_detail = fallback_reason
                logger.info(
                    "Executor 执行完成，status=%s，executor_session_id=%s",
                    status,
                    executor_session_id,
                )
            except Exception as e:
                import traceback

                error_detail = f"{type(e).__name__}: {str(e)}"
                full_tb = traceback.format_exc()
                summary = f"Executor 执行过程中发生异常：\n{error_detail}\n\n{full_tb[:800]}"
                status = "failed"
                updated_plan_json = _mark_plan_steps_failed(plan_json, error_detail)
                logger.error(
                    "Executor 执行失败，executor_session_id=%s，错误：%s",
                    executor_session_id,
                    error_detail,
                )
        else:
            # V3 路径：进程分离异步执行（POST 派发，fire-and-forget）
            import httpx

            logger.info(
                "Executor 开始执行（V3 异步），executor_session_id=%s，planner_session_id=%s",
                executor_session_id,
                planner_session_id,
            )

            actual_plan_id = pid or f"plan_{uuid.uuid4().hex[:8]}"
            callback_url = f"http://localhost:{runtime_context.supervisor_callback_port}"
            base_url = f"http://{runtime_context.executor_host}:{runtime_context.executor_port}"

            error_detail = None
            summary = ""
            updated_plan_json = ""

            # 1) POST 派发
            try:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    resp = await client.post(
                        f"{base_url}/execute",
                        json={
                            "plan_json": plan_json,
                            "plan_id": actual_plan_id,
                            "executor_session_id": executor_session_id,
                            "callback_url": callback_url,
                            "config": {
                                "snapshot_interval": runtime_context.snapshot_interval,
                            },
                        },
                    )
                    if resp.status_code == 409:
                        return f"错误：Executor 已在执行 plan_id={actual_plan_id}。请先等待完成或停止。"
                    if resp.status_code != 200:
                        error_detail = f"Executor 服务返回 {resp.status_code}：{resp.text}"
                        updated_plan_json = _mark_plan_steps_failed(plan_json, error_detail)
                        summary = f"Executor 派发失败：{error_detail}"
            except httpx.ConnectError:
                error_detail = "无法连接到 Executor 服务。请确认 Executor 进程已启动。"
                updated_plan_json = _mark_plan_steps_failed(plan_json, error_detail)
                summary = f"Executor 派发失败：{error_detail}"
            except Exception as e:
                error_detail = f"Executor 派发异常：{type(e).__name__}: {e}"
                updated_plan_json = _mark_plan_steps_failed(plan_json, error_detail)
                summary = f"Executor 派发失败：{error_detail}"

            # 2) 派发失败 → 返回 [EXECUTOR_RESULT] with status=failed
            if error_detail is not None:
                meta = {
                    "status": "failed",
                    "error_detail": error_detail,
                    "updated_plan_json": updated_plan_json,
                    "snapshot_json": "",
                }
                meta_line = f"[EXECUTOR_RESULT] {json.dumps(meta, ensure_ascii=False)}"
                return f"{summary}\n\n{meta_line}"

            # 3) 派发成功 → fire-and-forget 返回（无 [EXECUTOR_RESULT]）
            logger.info(
                "Executor 异步派发成功，plan_id=%s",
                actual_plan_id,
            )
            dispatch_meta = json.dumps(
                {"plan_id": actual_plan_id, "status": "accepted"},
                ensure_ascii=False,
            )
            return (
                f"Executor 已异步派发，plan_id={actual_plan_id}，状态：accepted。\n"
                f'使用 get_executor_result(plan_id="{actual_plan_id}") 查询执行结果。\n\n'
                f"[EXECUTOR_DISPATCH] {dispatch_meta}"
            )

        # 结构化返回（仅 V2 路径使用），供 dynamic_tools_node 解析 updated_plan_json 写回 State。
        # 格式约定：[EXECUTOR_RESULT] 标记行后接 JSON
        meta = {
            "status": status,
            "error_detail": error_detail,
            "updated_plan_json": updated_plan_json,
            "snapshot_json": snapshot_json,
        }
        meta_line = f"[EXECUTOR_RESULT] {json.dumps(meta, ensure_ascii=False)}"

        return f"{summary}\n\n{meta_line}"

    return call_executor


def _build_stop_executor_tool(runtime_context: Context):
    @tool
    async def stop_executor(
        state: Annotated[State, InjectedState],
        plan_id: str,
        reason: str = "",
    ) -> str:
        """请求 Executor 优雅停止执行指定计划。

        发送软中断信号，Executor 会在下一次 LLM 调用前检查并退出。
        """
        import httpx

        base_url = f"http://{runtime_context.executor_host}:{runtime_context.executor_port}"
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(
                    f"{base_url}/stop/{plan_id}",
                    json={"reason": reason},
                )
                if resp.status_code == 404:
                    return f"plan_id={plan_id} 未找到（可能已完成或不存在）。"
                if resp.status_code != 200:
                    return f"停止请求失败：{resp.status_code} {resp.text}"
                return f"已发送停止信号给 plan_id={plan_id}。Executor 将在下次循环中优雅退出。"
        except httpx.ConnectError:
            return "错误：无法连接到 Executor 服务。"

    return stop_executor


def _build_get_executor_result_tool(runtime_context: Context):
    @tool
    async def get_executor_result(
        state: Annotated[State, InjectedState],
        plan_id: str,
    ) -> str:
        """查询已派发的 Executor 异步任务的执行结果（阻塞等待完成）。

        在 V3 模式下，call_executor 仅派发任务并立即返回。使用此工具
        查询指定 plan_id 的执行结果。如果任务仍在运行，此工具会阻塞等待
        直到完成或超时（300秒）。
        """
        pid = _normalize_plan_id_arg(plan_id)
        if pid is None:
            return "错误：必须提供有效的 plan_id。"

        if pid not in state.active_executor_tasks:
            return f"错误：plan_id={pid} 不在活跃任务列表中。请先调用 call_executor 派发任务。"

        task = state.active_executor_tasks[pid]

        from src.supervisor_agent.callback_server import get_mailbox

        try:
            mb = get_mailbox()
        except RuntimeError:
            error_detail = "回调邮箱未初始化。请确认 V3 模式已启用。"
            meta = {
                "status": "failed",
                "error_detail": error_detail,
                "updated_plan_json": "",
                "snapshot_json": "",
                "plan_id": pid,
            }
            meta_line = f"[EXECUTOR_RESULT] {json.dumps(meta, ensure_ascii=False)}"
            return f"Executor 执行失败：{error_detail}\n\n{meta_line}"

        result_item = await mb.wait_for_completion(pid, timeout=300.0, poll_interval=1.0)

        if result_item is None:
            error_detail = "等待 Executor 完成超时（300秒）"
            updated_plan_json = _mark_plan_steps_failed(task.plan_json, error_detail)
            meta = {
                "status": "failed",
                "error_detail": error_detail,
                "updated_plan_json": updated_plan_json,
                "snapshot_json": "",
                "plan_id": pid,
            }
            meta_line = f"[EXECUTOR_RESULT] {json.dumps(meta, ensure_ascii=False)}"
            return f"Executor 执行超时\n\n{meta_line}"

        payload = result_item.payload
        status = payload.get("status", "failed")
        summary = payload.get("summary", "")
        updated_plan_json = payload.get("updated_plan_json", "")
        snapshot_json = payload.get("snapshot_json", "")
        error_detail = None

        if status == "failed" and not (updated_plan_json or "").strip():
            fallback_reason = "Executor 失败且未返回 updated_plan_json，已由 Supervisor 侧兜底补全。"
            updated_plan_json = _mark_plan_steps_failed(task.plan_json, fallback_reason)
            error_detail = fallback_reason

        logger.info("get_executor_result 完成，plan_id=%s，status=%s", pid, status)

        meta = {
            "status": status,
            "error_detail": error_detail,
            "updated_plan_json": updated_plan_json,
            "snapshot_json": snapshot_json,
            "plan_id": pid,
        }
        meta_line = f"[EXECUTOR_RESULT] {json.dumps(meta, ensure_ascii=False)}"
        return f"{summary}\n\n{meta_line}"

    return get_executor_result


def _mark_plan_steps_failed(plan_json: str, error_detail: str) -> str:
    """将 plan_json 中 pending/running 步骤标记为 failed，并写入 failure_reason。"""
    if not plan_json or not plan_json.strip():
        return plan_json
    try:
        data = json.loads(plan_json)
    except json.JSONDecodeError:
        return plan_json

    steps = data if isinstance(data, list) else data.get("steps", [])
    for step in steps:
        if isinstance(step, dict) and step.get("status") in ("pending", "running", None):
            step["status"] = "failed"
            step["failure_reason"] = f"Executor 异常中断：{error_detail}"

    return json.dumps(data, ensure_ascii=False, indent=2)


def _build_check_executor_progress_tool():
    @tool
    async def check_executor_progress(
        plan_id: str,
    ) -> str:
        """查看 Executor 异步任务的实时执行进度（快照）。

        返回最新快照信息，包括已完成步骤数、当前步骤、工具调用轮数等。
        不会阻塞等待——如果任务仍在运行，只返回当前进度。
        如果任务已完成，返回完成状态。
        """
        from src.supervisor_agent.callback_server import get_mailbox

        try:
            mb = get_mailbox()
        except RuntimeError:
            return "邮箱未初始化。请确认 V3 模式已启用。"

        # Check if completed
        completion = await mb.get_completion(plan_id)
        if completion is not None:
            payload = completion.payload
            return (
                f"任务已完成，status={payload.get('status')}。\n"
                f"摘要：{payload.get('summary', '')}"
            )

        # Get latest snapshot
        snapshot = await mb.get_latest_snapshot(plan_id)
        if snapshot is not None:
            p = snapshot.payload
            return (
                f"任务运行中，最新进度：\n"
                f"- 已完成步骤：{p.get('completed_steps', '?')}/{p.get('total_steps', '?')}\n"
                f"- 当前步骤：{p.get('current_step', '未知')}\n"
                f"- 工具调用轮数：{p.get('tool_rounds', 0)}"
            )

        return f"plan_id={plan_id} 暂无进度数据。任务可能尚未开始发送快照。"

    return check_executor_progress


async def get_tools(runtime_context: Context | None = None) -> List[Callable[..., Any]]:
    """主 ReAct 循环返回的工具集。"""
    if runtime_context is None:
        runtime_context = Context()

    tools = [
        _build_call_planner_tool(runtime_context),
        _build_get_executor_full_output_tool(),
        _build_call_executor_tool(runtime_context),
    ]

    if runtime_context.enable_v3_parallel:
        tools.append(_build_stop_executor_tool(runtime_context))
        tools.append(_build_get_executor_result_tool(runtime_context))
        tools.append(_build_check_executor_progress_tool())

    return tools
