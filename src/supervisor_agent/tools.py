"""Supervisor Agent 工具定义。"""

import asyncio
import json
import logging
import uuid
from datetime import datetime
from typing import Annotated, Any, Callable, List

from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState

from src.common.context import Context
from src.planner_agent.graph import PlannerOutput, run_planner
from src.supervisor_agent.state import PlannerSession, State

logger = logging.getLogger(__name__)


def _normalize_plan_id_arg(plan_id: str | None) -> str | None:
    if plan_id is None:
        return None
    s = str(plan_id).strip()
    return s if s else None


def _relative_time_ago(dt: datetime) -> str:
    """将 datetime 转为 LLM 友好的相对时间描述。

    LLM 对时间的理解与人类类似：以"多久之前"判断先后和长短。
    粒度从秒到小时，超过 24 小时则显示具体日期。
    """
    now = datetime.now(dt.tzinfo)
    delta = now - dt
    seconds = int(delta.total_seconds())

    if seconds < 0:
        # 时钟偏移容忍：未来不超过 5 秒视为"刚刚"
        return "刚刚" if seconds > -5 else dt.strftime("%m-%d %H:%M")
    if seconds < 5:
        return "刚刚"
    if seconds < 60:
        return f"{seconds}秒前"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}分钟前"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}小时前"
    days = hours // 24
    if days < 7:
        return f"{days}天前"
    return dt.strftime("%m-%d %H:%M")


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
    """将 Planner 输出规范为意图层 Plan JSON，并托管稳定的 plan_id/version。"""
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
            # parallel_group: 可选字段，同值步骤可并行执行；null 表示顺序执行
            if "parallel_group" not in step:
                step["parallel_group"] = None

    return json.dumps(parsed, ensure_ascii=False, indent=2)


def _build_call_planner_tool(runtime_context: Context):
    @tool
    async def call_planner(
        state: Annotated[State, InjectedState],
        task_core: str = "",
        plan_id: str | None = None,
    ) -> str:
        """调用 Planner Agent 生成或更新意图层 Plan（JSON）。

        - **首次规划**：必须提供非空且**有用的上下文与核心目标**的 ``task_core``，不要传 ``plan_id``。
        - **重规划**：传入当前计划中的 ``plan_id``；**强烈建议**同时提供详尽的 ``task_core`` 说明修订方向。完整带执行状态的计划由工具内部读取，无需在参数里粘贴 JSON。
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

        planner_output: PlannerOutput = await run_planner(
            task_core,
            plan_id=plan_id,
            replan_plan_json=replan_plan_json,
            planner_history_messages=planner_history_messages,
            context=runtime_context,
        )
        normalized = _normalize_plan_json(planner_output.plan_json, previous_plan_json=previous_plan_json)

        logger.info("Planner 生成计划，session_id=%s，长度=%d", session_id, len(normalized))

        # 组装返回：reasoning + plan_json，让 Supervisor 能看到 Planner 的分析推理
        parts = []
        if planner_output.reasoning.strip():
            parts.append(f"[PLANNER_REASONING]\n{planner_output.reasoning.strip()}\n[/PLANNER_REASONING]")
        parts.append(normalized)
        return "\n\n".join(parts)

    return call_planner


def _session_plan_id_for_detail_read(planner_session: PlannerSession | None) -> str | None:
    """从 session.plan_json 读取顶层 plan_id，供 detail=full 缓存读取校验。"""
    if planner_session is None or not (planner_session.plan_json or "").strip():
        return None
    try:
        data = json.loads(planner_session.plan_json or "")
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    pid = data.get("plan_id")
    if isinstance(pid, str) and pid.strip():
        return pid.strip()
    return None


def _build_call_executor_tool(runtime_context: Context):
    @tool
    async def call_executor(
        state: Annotated[State, InjectedState],
        task_description: str = "",
        plan_id: str | None = None,
        wait_for_result: bool = True,
    ) -> str:
        """调用 Executor Agent 执行任务或执行已有计划（per-task 进程）。

        子进程调度约定（与 ``CLAUDE.md``「plan_id 与 Executor 载体」一致）：
        - 传 ``plan_id``（Mode 3）：以该 id 为键 ``start_for_task``；同一 id 且子进程仍在跑则复用，否则新建。
        - 仅传 ``task_description``（Mode 2）：每次调用在计划 JSON 内生成**新** ``plan_id`` 并新建子进程（新 executor）。

        参数下列方式二选一：
        - 仅传 ``task_description``（简短、明确、可执行）
        - 仅传 ``plan_id``（与当前 ``session.plan_json`` 顶层 ``plan_id`` 一致）

        ``wait_for_result`` 控制是否阻塞等待执行结果：
        - ``True``（默认）：派发后自动等待结果并直接返回执行摘要，无需再调用 ``get_executor_result``。
        - ``False``：仅异步派发，立即返回，需后续用 ``get_executor_result(plan_id)`` 获取结果（可选 ``detail``）。适用于并行派发多个独立任务的场景。
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
            actual_plan_id = pid
        else:
            if not td:
                return "错误：Mode2 需提供非空 task_description；Mode3 需提供 plan_id。"
            # 与 plan_json 内 plan_id 必须一致：子进程键、/execute body、计划正文同一标识
            actual_plan_id = f"plan_{uuid.uuid4().hex[:8]}"
            mode2_plan = {
                "plan_id": actual_plan_id,
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

        import httpx

        from src.supervisor_agent.v3_lifecycle import v3_manager

        # Get infrastructure (mailbox server + process manager)
        try:
            infra = await v3_manager.ensure_started(runtime_context)
            mailbox_url = infra.mailbox_server.base_url
            pm = infra.process_manager
        except Exception as e:
            error_detail = f"V3 基础设施启动失败：{e}"
            updated_plan_json = _mark_plan_steps_failed(plan_json, error_detail)
            meta = {"status": "failed", "error_detail": error_detail,
                    "updated_plan_json": updated_plan_json, "snapshot_json": ""}
            meta_line = f"[EXECUTOR_RESULT] {json.dumps(meta, ensure_ascii=False)}"
            return f"Executor 基础设施错误：{error_detail}\n\n{meta_line}"

        # Start per-task Executor process
        try:
            handle = await pm.start_for_task(actual_plan_id, runtime_context, mailbox_url=mailbox_url)
            base_url = handle.base_url
        except Exception as e:
            error_detail = f"Executor 进程启动失败：{type(e).__name__}: {e}"
            updated_plan_json = _mark_plan_steps_failed(plan_json, error_detail)
            meta = {"status": "failed", "error_detail": error_detail,
                    "updated_plan_json": updated_plan_json, "snapshot_json": ""}
            meta_line = f"[EXECUTOR_RESULT] {json.dumps(meta, ensure_ascii=False)}"
            return f"Executor 启动失败：{error_detail}\n\n{meta_line}"

        # Build LangSmith distributed trace headers so Executor sub-process
        # nodes appear nested under this Supervisor trace in LangSmith UI.
        trace_headers: dict[str, str] = {}
        try:
            from langsmith.run_helpers import get_current_run_tree
            run_tree = get_current_run_tree()
            if run_tree is not None:
                trace_headers.update(run_tree.to_headers())
        except Exception:
            pass  # LangSmith not installed or not in a traced context

        # POST dispatch to the per-task Executor
        error_detail = None
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    f"{base_url}/execute",
                    json={
                        "plan_json": plan_json,
                        "plan_id": actual_plan_id,
                        "executor_session_id": executor_session_id,
                        "config": {},
                    },
                    headers=trace_headers,
                )
                if resp.status_code == 409:
                    return f"错误：Executor 已在执行 plan_id={actual_plan_id}。请先等待完成或停止。"
                if resp.status_code != 200:
                    error_detail = f"Executor 服务返回 {resp.status_code}：{resp.text}"
        except httpx.ConnectError:
            error_detail = "无法连接到 Executor 服务。"
        except Exception as e:
            error_detail = f"Executor 派发异常：{type(e).__name__}: {e}"

        if error_detail is not None:
            updated_plan_json = _mark_plan_steps_failed(plan_json, error_detail)
            meta = {
                "status": "failed",
                "error_detail": error_detail,
                "updated_plan_json": updated_plan_json,
                "snapshot_json": "",
            }
            meta_line = f"[EXECUTOR_RESULT] {json.dumps(meta, ensure_ascii=False)}"
            return f"Executor 派发失败：{error_detail}\n\n{meta_line}"

        # Fire-and-forget: result will be pushed to mailbox by Executor
        logger.info("Executor 异步派发成功，plan_id=%s", actual_plan_id)

        # Register with unified poller so it starts tracking this task
        if infra.poller:
            infra.poller.register(actual_plan_id, plan_json, executor_base_url=base_url)

        if wait_for_result:
            # 默认路径：阻塞等待结果，直接返回 [EXECUTOR_RESULT]，省去额外工具调用
            logger.info("call_executor wait_for_result=True，等待 plan_id=%s 完成", actual_plan_id)
            return await _wait_for_executor_result(
                actual_plan_id, plan_json, runtime_context,
                timeout=runtime_context.executor_wait_timeout,
            )

        # wait_for_result=False：异步派发，返回 [EXECUTOR_DISPATCH]
        dispatch_meta = json.dumps(
            {"plan_id": actual_plan_id, "status": "accepted"},
            ensure_ascii=False,
        )
        return (
            f"Executor 已异步派发，plan_id={actual_plan_id}，状态：accepted。"
            f"\n[EXECUTOR_DISPATCH] {dispatch_meta}"
        )


    return call_executor


def _build_stop_executor_tool(runtime_context: Context):
    @tool
    async def stop_executor(
        state: Annotated[State, InjectedState],
        plan_id: str,
        reason: str = "",
    ) -> str:
        """请求 Executor 停止执行指定计划（优雅退出，非强制终止）。

        仅在确认任务方向错误或需要提前结束时使用。
        """
        import httpx

        from src.supervisor_agent.v3_lifecycle import v3_manager

        try:
            infra = await v3_manager.ensure_started(runtime_context)
            base_url = infra.process_manager.get_task_base_url(plan_id)
            if not base_url:
                return f"plan_id={plan_id} 对应的 Executor 进程未运行（可能已完成或不存在）。"
        except Exception as e:
            return f"错误：无法获取 Executor 信息：{e}"

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
                return f"已发送停止信号给 plan_id={plan_id}。Executor 将在工具执行期间优雅退出。"
        except httpx.ConnectError:
            return "错误：无法连接到 Executor 服务。"

    return stop_executor


async def _ordered_executor_bases(pm: Any, plan_id: str) -> list[str]:
    bases: list[str] = []
    b = pm.get_task_base_url(plan_id)
    if b:
        bases.append(b)
    for u in pm.iter_active_base_urls():
        if u not in bases:
            bases.append(u)
    return bases


async def _cleanup_dead_executor(plan_id: str, ctx: Context) -> None:
    """尝试终止并清理已死或卡住的 Executor 进程。

    用于 executor 崩溃（不可达）或超时（卡住）场景。
    """
    from src.supervisor_agent.v3_lifecycle import v3_manager

    try:
        infra = await v3_manager.ensure_started(ctx)
        await infra.process_manager.stop_task(plan_id)
        logger.info("已清理 Executor 进程，plan_id=%s", plan_id)
    except Exception:
        logger.warning("清理 Executor 进程失败，plan_id=%s", plan_id, exc_info=True)


async def _probe_executor_task(plan_id: str, ctx: Context) -> str:
    """快速探测 Executor 服务中任务状态（非阻塞，3秒超时）。

    Returns: 'running' | 'completed' | 'failed' | 'stopped' | 'not_found' | 'unreachable'
    """
    import httpx

    from src.supervisor_agent.v3_lifecycle import v3_manager

    try:
        infra = await v3_manager.ensure_started(ctx)
        pm = infra.process_manager
    except Exception:
        return "unreachable"

    bases = await _ordered_executor_bases(pm, plan_id)
    if not bases:
        return "unreachable"

    any_connected = False
    for base_url in bases:
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                try:
                    resp = await client.get(f"{base_url}/status/{plan_id}")
                except (httpx.ConnectError, httpx.TimeoutException):
                    continue
                any_connected = True
                if resp.status_code == 200:
                    return resp.json().get("status", "unknown")
                if resp.status_code == 404:
                    try:
                        resp2 = await client.get(f"{base_url}/result/{plan_id}")
                    except (httpx.ConnectError, httpx.TimeoutException):
                        continue
                    if resp2.status_code == 200:
                        return resp2.json().get("status", "completed")
        except (httpx.ConnectError, httpx.TimeoutException):
            continue
        except Exception:
            continue

    if not any_connected:
        return "unreachable"
    return "not_found"


async def _fetch_executor_result_directly(
    plan_id: str, task_plan_json: str, ctx: Context
) -> str | None:
    """Fetch result directly from Executor /result endpoint.

    Returns formatted [EXECUTOR_RESULT] string if task is in terminal state,
    or None if not available / not terminal.
    """
    import httpx

    from src.supervisor_agent.v3_lifecycle import v3_manager

    try:
        infra = await v3_manager.ensure_started(ctx)
        pm = infra.process_manager
    except Exception:
        return None

    bases = await _ordered_executor_bases(pm, plan_id)
    if not bases:
        return None

    for base_url in bases:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                try:
                    resp = await client.get(f"{base_url}/result/{plan_id}")
                except (httpx.ConnectError, httpx.TimeoutException):
                    continue
                if resp.status_code != 200:
                    continue
                data = resp.json()
                status = data.get("status", "unknown")
                if status not in ("completed", "failed", "stopped"):
                    continue
                summary = data.get("summary", "")
                updated_plan_json = data.get("updated_plan_json", "")
                snapshot_json = data.get("snapshot_json", "")
                error_detail = None
                if status == "failed" and not (updated_plan_json or "").strip():
                    fallback_reason = "Executor 失败且未返回 updated_plan_json，已由 Supervisor 侧兜底补全。"
                    updated_plan_json = _mark_plan_steps_failed(task_plan_json, fallback_reason)
                    error_detail = fallback_reason
                meta = {
                    "status": status,
                    "error_detail": error_detail,
                    "updated_plan_json": updated_plan_json,
                    "snapshot_json": snapshot_json,
                    "plan_id": plan_id,
                }
                meta_line = f"[EXECUTOR_RESULT] {json.dumps(meta, ensure_ascii=False)}"
                return f"{summary}\n\n{meta_line}"
        except Exception:
            continue
    return None


async def _wait_for_executor_result(
    plan_id: str,
    plan_json_cached: str,
    ctx: Context,
    *,
    timeout: float = 120.0,
) -> str:
    """等待 Executor 任务完成并返回格式化结果（[EXECUTOR_RESULT] 标记）。

    由 call_executor(wait_for_result=True) 和 get_executor_result 共用。
    """
    from src.common.mailbox import get_mailbox
    from src.supervisor_agent.v3_lifecycle import v3_manager as _v3_manager

    pid = plan_id

    try:
        mb = get_mailbox()
    except RuntimeError:
        try:
            await _v3_manager.ensure_started(ctx)
            mb = get_mailbox()
        except asyncio.CancelledError:
            raise
        except Exception as init_err:
            error_detail = f"回调邮箱未初始化，Executor 基础设施恢复失败：{init_err}"
            meta = {
                "status": "failed",
                "error_detail": error_detail,
                "updated_plan_json": "",
                "snapshot_json": "",
                "plan_id": pid,
            }
            meta_line = f"[EXECUTOR_RESULT] {json.dumps(meta, ensure_ascii=False)}"
            return f"Executor 执行失败：{error_detail}\n\n{meta_line}"

    # ---- 预检 1：非阻塞查 mailbox（结果可能已到达） ----
    completion = await mb.get_completion(pid)
    if completion is not None:
        logger.info("_wait_for_executor_result 预检命中 mailbox，plan_id=%s", pid)
        return _format_completion_result(completion.payload, pid, plan_json_cached)

    # ---- 预检 2：探测 Executor 服务（任务是否还活着） ----
    probe = await _probe_executor_task(pid, ctx)
    if probe in ("not_found", "unreachable"):
        # Executor 进程崩溃或不可达 → 构造 [EXECUTOR_RESULT] 让 dynamic_tools_node 正确更新 state
        error_detail = (
            f"Executor 进程不可达（{probe}），plan_id={pid}，任务状态已丢失。"
            if probe == "unreachable"
            else f"Executor 进程中找不到 plan_id={pid} 的任务，进程可能已重启。"
        )
        updated_plan_json = _mark_plan_steps_failed(plan_json_cached, error_detail)
        meta = {
            "status": "failed",
            "error_detail": error_detail,
            "updated_plan_json": updated_plan_json,
            "snapshot_json": "",
            "plan_id": pid,
        }
        meta_line = f"[EXECUTOR_RESULT] {json.dumps(meta, ensure_ascii=False)}"
        # 尝试清理已死的 executor 进程
        await _cleanup_dead_executor(pid, ctx)
        return f"Executor 进程异常：{error_detail}\n\n{meta_line}"

    # ---- 预检 3：任务已终态但回调丢失 → 直接从 Executor 获取结果 ----
    if probe in ("completed", "failed", "stopped"):
        direct = await _fetch_executor_result_directly(pid, plan_json_cached, ctx)
        if direct is not None:
            logger.info("_wait_for_executor_result 直接获取终态结果（回调丢失），plan_id=%s", pid)
            return direct

    # ---- 任务确认存在且运行中，等待 Mailbox（由统一 poller 写入） ----
    try:
        infra = await _v3_manager.ensure_started(ctx)
        if infra.poller:
            base_url_for_task = infra.process_manager.get_task_base_url(pid) or infra.process_manager.base_url
            infra.poller.register(pid, plan_json_cached, executor_base_url=base_url_for_task or None)
    except asyncio.CancelledError:
        raise
    except Exception:
        pass

    result_data: dict | None = None
    try:
        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            completion = await mb.get_completion(pid)
            if completion is not None:
                result_data = completion.payload
                break
            await asyncio.sleep(1.0)
    except asyncio.CancelledError:
        error_detail = (
            f"等待 Executor 结果时被中断（用户可能发送了新消息或断开连接）。"
            f"plan_id={pid}"
        )
        updated_plan_json = _mark_plan_steps_failed(plan_json_cached, error_detail)
        meta = {
            "status": "failed",
            "error_detail": error_detail,
            "updated_plan_json": updated_plan_json,
            "snapshot_json": "",
            "plan_id": pid,
        }
        meta_line = f"[EXECUTOR_RESULT] {json.dumps(meta, ensure_ascii=False)}"
        return f"Executor 执行被中断：{error_detail}\n\n{meta_line}"

    if result_data is None:
        # 超时 → 标记失败并终止卡住的 executor 进程
        error_detail = f"等待 Executor 完成超时（{int(timeout)}秒）"
        updated_plan_json = _mark_plan_steps_failed(plan_json_cached, error_detail)
        meta = {
            "status": "failed",
            "error_detail": error_detail,
            "updated_plan_json": updated_plan_json,
            "snapshot_json": "",
            "plan_id": pid,
        }
        meta_line = f"[EXECUTOR_RESULT] {json.dumps(meta, ensure_ascii=False)}"
        # 终止卡住的 executor 进程
        await _cleanup_dead_executor(pid, ctx)
        return f"Executor 执行超时\n\n{meta_line}"

    logger.info("_wait_for_executor_result 完成，plan_id=%s", pid)

    # Unregister from poller (terminal state processed)
    try:
        infra2 = await _v3_manager.ensure_started(ctx)
        if infra2.poller:
            infra2.poller.unregister(pid)
    except Exception:
        pass

    return _format_completion_result(result_data, pid, plan_json_cached)


def _format_completion_result(
    payload: dict,
    plan_id: str,
    plan_json_cached: str,
) -> str:
    """将 mailbox completion payload 格式化为 [EXECUTOR_RESULT] 标记字符串。"""
    status = payload.get("status", "failed")
    summary = payload.get("summary", "")
    updated_plan_json = payload.get("updated_plan_json", "")
    snapshot_json = payload.get("snapshot_json", "")
    error_detail = None
    if status == "failed" and not (updated_plan_json or "").strip():
        fallback_reason = "Executor 失败且未返回 updated_plan_json，已由 Supervisor 侧兜底补全。"
        updated_plan_json = _mark_plan_steps_failed(plan_json_cached, fallback_reason)
        error_detail = fallback_reason
    meta = {
        "status": status,
        "error_detail": error_detail,
        "updated_plan_json": updated_plan_json,
        "snapshot_json": snapshot_json,
        "plan_id": plan_id,
    }
    meta_line = f"[EXECUTOR_RESULT] {json.dumps(meta, ensure_ascii=False)}"
    return f"{summary}\n\n{meta_line}"


def _build_get_executor_result_tool(runtime_context: Context):
    @tool
    async def get_executor_result(
        state: Annotated[State, InjectedState],
        plan_id: str,
        detail: str = "overview",
    ) -> str:
        """获取 Executor 任务结果，或通过 ``detail`` 读取已落库的步骤级详情。

        **detail=overview（默认）**：用于异步派发后的收束——``plan_id`` 须在
        ``active_executor_tasks`` 中；阻塞等待终态，返回含 ``[EXECUTOR_RESULT]`` 的正文
        （与同步 ``call_executor`` 完成路径一致，供图节点更新会话）。

        **detail=full**：除在任务**仍在异步执行**时与 overview 相同（阻塞等待终态）外，
        还可于任务结束后读取会话中已缓存的步骤级详情（``result_summary`` /
        ``failure_reason`` 等）：此时 ``plan_id`` 须与 ``session.plan_json`` 顶层
        ``plan_id`` 一致且任务已不在活跃列表。同步 ``call_executor`` 完成后也可用
        此模式拉取同一会话内的步骤级正文。
        """
        pid = _normalize_plan_id_arg(plan_id)
        if pid is None:
            return "错误：必须提供有效的 plan_id。"

        d = (detail or "overview").strip().lower()
        if d not in ("overview", "full"):
            return '错误：detail 仅支持 "overview" 或 "full"。'

        if d == "full" and pid not in state.active_executor_tasks:
            sess_pid = _session_plan_id_for_detail_read(state.planner_session)
            if sess_pid != pid:
                return (
                    f"错误：无法读取详情。当前会话 plan 的 plan_id 为 {sess_pid!r}，"
                    f"与请求的 {pid!r} 不一致；或尚未产生执行结果。"
                )
            full = (
                state.planner_session.last_executor_full_output
                if state.planner_session
                else None
            )
            if not (full or "").strip():
                return "当前没有可查看的 Executor 完整输出（请先完成一次执行并收到概览结果）。"
            return full.strip()

        if pid not in state.active_executor_tasks:
            return f"错误：plan_id={pid} 不在活跃任务列表中。请先调用 call_executor 派发任务。"

        # Retrieve cached plan_json from unified poller (not stored in Graph State)
        plan_json_cached = ""
        try:
            from src.supervisor_agent.v3_lifecycle import v3_manager as _v3_manager
            _infra_early = await _v3_manager.ensure_started(runtime_context)
            if _infra_early.poller:
                plan_json_cached = _infra_early.poller.get_plan_json(pid)
        except Exception:
            pass

        return await _wait_for_executor_result(
            pid, plan_json_cached, runtime_context,
            timeout=runtime_context.executor_wait_timeout,
        )

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


def _build_check_executor_progress_tool(runtime_context: Context):
    @tool
    async def check_executor_progress(
        plan_id: str,
    ) -> str:
        """查看 Executor 任务的执行进度（非阻塞，用于并行派发后的轮询）。

        与同步 `call_executor` 默认路径不同：本工具**不等待**任务结束，也不返回
        ``[EXECUTOR_RESULT]``；终态契约与 session 更新仍由 ``get_executor_result`` 或
        带默认等待的 ``call_executor`` 完成。

        查询顺序：先读 Mailbox（若结果已投递则直接返回摘要）；否则对 Executor
        HTTP 服务 ``GET /status/{plan_id}``，必要时再 ``GET /result/{plan_id}``。
        """
        import httpx

        from src.common.mailbox import get_mailbox

        try:
            mb = get_mailbox()
        except RuntimeError:
            return "邮箱未初始化。请确认已按部署文档启用子进程并行执行相关配置。"

        # Check Mailbox for completion first
        completion = await mb.get_completion(plan_id)
        if completion is not None:
            payload = completion.payload
            return (
                f"任务已完成，status={payload.get('status')}。\n"
                f"摘要：{payload.get('summary', '')}"
            )

        # Poll Executor directly (try task base first, then other active executors)
        try:
            from src.supervisor_agent.v3_lifecycle import v3_manager

            infra = await v3_manager.ensure_started(runtime_context)
            pm = infra.process_manager
        except Exception:
            return "Executor 子进程基础设施不可用，无法查询 Executor。"

        bases = await _ordered_executor_bases(pm, plan_id)
        if not bases:
            return "Executor 子进程基础设施不可用，无法查询 Executor。"

        unreachable = True
        try:
            async with httpx.AsyncClient(timeout=3.0) as c:
                for base_url in bases:
                    try:
                        resp = await c.get(f"{base_url}/status/{plan_id}")
                    except (httpx.ConnectError, httpx.TimeoutException):
                        continue
                    unreachable = False
                    if resp.status_code == 200:
                        data = resp.json()
                        return (
                            f"任务运行中，当前状态：\n"
                            f"- 状态：{data.get('status', '未知')}\n"
                            f"- 当前步骤：{data.get('current_step', '未知')}\n"
                            f"- 工具调用轮数：{data.get('tool_rounds', 0)}"
                        )
                    if resp.status_code == 404:
                        try:
                            resp2 = await c.get(f"{base_url}/result/{plan_id}")
                        except (httpx.ConnectError, httpx.TimeoutException):
                            continue
                        if resp2.status_code == 200:
                            data = resp2.json()
                            return f"任务已完成，status={data.get('status')}。摘要：{data.get('summary', '')}"
        except (httpx.ConnectError, httpx.TimeoutException):
            return "无法连接到 Executor 服务。"

        if unreachable:
            return "无法连接到 Executor 服务。"

        return f"plan_id={plan_id} 暂无进度数据。任务可能尚未开始或已完成被清理。"

    return check_executor_progress


def _build_list_executor_tasks_tool(runtime_context: Context):
    @tool
    async def list_executor_tasks(
        state: Annotated[State, InjectedState],
    ) -> str:
        """列出所有已派发的 Executor 任务及其当前状态和可查询性。

        返回一个表格，包含所有已知任务的 plan_id、状态、是否可查询。
        可查询的任务可用 get_executor_result(plan_id) 取结果；步骤级正文在任务已结束后使用 detail="full"。
        不可查询的任务需要重新规划。
        """

        history = state.executor_task_history
        if not history:
            return "当前无 Executor 任务记录。"

        # Probe Executor servers for running tasks (union of /tasks keys)
        import httpx as _httpx

        executor_running: set[str] = set()
        try:
            from src.supervisor_agent.v3_lifecycle import v3_manager

            infra = await v3_manager.ensure_started(runtime_context)
            base_urls = infra.process_manager.iter_active_base_urls()
        except Exception:
            base_urls = []
        try:
            async with _httpx.AsyncClient(timeout=3.0) as client:
                for base_url in base_urls:
                    try:
                        resp = await client.get(f"{base_url}/tasks")
                        if resp.status_code == 200:
                            executor_running.update(resp.json().get("tasks", {}).keys())
                    except (_httpx.ConnectError, _httpx.TimeoutException):
                        pass
        except Exception:
            pass

        # Probe non-terminal tasks to determine queryable status
        terminal_statuses = {"completed", "failed", "stopped", "lost"}
        updates: list[dict] = []
        rows: list[str] = []
        now_iso = datetime.now().isoformat(timespec="seconds")

        # Sort: non-terminal first, then terminal by plan_id
        sorted_pids = sorted(
            history.keys(),
            key=lambda pid: (0 if history[pid].status not in terminal_statuses else 1, pid),
        )

        for pid in sorted_pids:
            record = history[pid]
            probed_status = record.status
            queryable = record.queryable
            last_updated = record.last_updated
            note = ""
            status_changed = False

            if record.status not in terminal_statuses:
                # Probe to check current status
                probe = await _probe_executor_task(pid, runtime_context)
                if probe in ("running",):
                    probed_status = "running"
                    queryable = True
                    note = "活跃于 Executor"
                    status_changed = True
                elif probe in ("completed", "failed", "stopped"):
                    probed_status = probe
                    queryable = True
                    note = "已结束，结果可查询"
                    status_changed = True
                elif probe == "not_found":
                    probed_status = "lost"
                    queryable = False
                    note = "Executor 上未找到"
                    status_changed = True
                elif probe == "unreachable":
                    queryable = False
                    note = "Executor 不可达"
                    status_changed = True
                else:
                    queryable = False
                    note = "状态未知"
                    status_changed = True
            else:
                # Terminal: keep last known state, check if still queryable
                if pid in executor_running:
                    queryable = True
                    note = "仍活跃于 Executor"
                elif record.status in ("completed", "failed", "stopped"):
                    # Quick probe to see if result is still available
                    probe = await _probe_executor_task(pid, runtime_context)
                    queryable = probe in ("completed", "failed", "stopped")
                    note = "结果可查询" if queryable else "结果已过期"
                else:
                    note = "任务已丢失"

            if status_changed:
                last_updated = now_iso

            # Format display time: relative time from LLM perspective
            display_time = "-"
            if last_updated:
                try:
                    dt = datetime.fromisoformat(last_updated)
                    display_time = _relative_time_ago(dt)
                except (ValueError, OSError):
                    display_time = last_updated[-8:] if len(last_updated) >= 8 else last_updated

            q_mark = "✅" if queryable else "❌"
            rows.append(f"  {pid}  | {probed_status:<10} | {q_mark}      | {display_time}  | {note}")
            updates.append({
                "plan_id": pid,
                "status": probed_status,
                "queryable": queryable,
                "last_updated": last_updated,
            })

        # Build output
        lines = [
            f"Executor 任务注册表 ({len(history)} 个任务)：\n",
            "  plan_id          | status     | queryable | 上次更新     | 备注",
            "  " + "-" * 80,
        ]
        lines.extend(rows)
        lines.append("")
        lines.append(
            "请使用 get_executor_result(plan_id) 查询可查询任务的结果；步骤级详情在任务已结束后使用 detail=\"full\"。"
            "不可查询的任务需要重新规划。"
        )

        # Append structured update marker for dynamic_tools_node
        updates_json = json.dumps(updates, ensure_ascii=False)
        lines.append(f"\n[EXECUTOR_REGISTRY_UPDATE] {updates_json}")

        return "\n".join(lines)

    return list_executor_tasks


async def get_tools(runtime_context: Context | None = None) -> List[Callable[..., Any]]:
    """主 ReAct 循环返回的工具集。"""
    if runtime_context is None:
        runtime_context = Context()

    tools = [
        _build_call_planner_tool(runtime_context),
        _build_call_executor_tool(runtime_context),
        _build_stop_executor_tool(runtime_context),
        _build_get_executor_result_tool(runtime_context),
        _build_check_executor_progress_tool(runtime_context),
        _build_list_executor_tasks_tool(runtime_context),
    ]

    # V4 知识树工具（条件注册）
    if runtime_context.enable_knowledge_tree:
        from src.common.knowledge_tree import build_knowledge_tree_tools
        kt_tools = build_knowledge_tree_tools(runtime_context)
        tools.extend(kt_tools)
        logger.info("Knowledge tree tools registered (%d tools)", len(kt_tools))

    return tools
