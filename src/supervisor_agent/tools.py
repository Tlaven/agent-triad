"""AutoDL-Agent 主循环工具 - 永远只绑定两个工具"""

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
from src.supervisor_agent.parallel import (
    build_execution_batches,
    merge_fanin_summaries,
    merge_parallel_step_states,
    serialize_plan_with_steps,
)
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

        logger.info(
            "Executor 开始执行，executor_session_id=%s，planner_session_id=%s",
            executor_session_id,
            planner_session_id,
        )

        snapshot_json = ""
        try:
            # V3: 检查是否可以并行执行
            plan_obj = json.loads(plan_json)
            steps = plan_obj.get("steps", [])

            # 尝试构建执行批次
            try:
                batches = build_execution_batches(steps)
                can_parallelize = len(batches) > 1 or any(
                    len(batch.step_ids) > 1 for batch in batches
                )
            except (ValueError, KeyError):
                # 如果无法构建批次（循环依赖等），回退到串行执行
                can_parallelize = False

            if can_parallelize and pid is not None:
                # V3 并行执行模式（仅 Mode3 支持）
                logger.info(
                    "V3: 检测到可并行步骤，使用并行执行模式，批次数=%d",
                    len(batches),
                )

                # 为每个批次创建子计划
                batch_tasks = []
                for batch in batches:
                    # 创建仅包含当前批次步骤的子计划
                    batch_steps = [
                        step for step in steps if step.get("step_id") in batch.step_ids
                    ]
                    batch_plan_json = serialize_plan_with_steps(plan_obj, batch_steps)

                    # 创建并发任务（不等待执行）
                    task = run_executor(
                        batch_plan_json,
                        context=runtime_context,
                    )
                    batch_tasks.append(task)

                # 真正的并行执行：使用 asyncio.gather() 并发运行所有批次
                batch_results = await asyncio.gather(*batch_tasks)

                # 合并结果
                all_summaries = [br.summary for br in batch_results]
                merged_summary = merge_fanin_summaries(
                    all_summaries,
                    max_chars=runtime_context.max_observation_chars,
                )

                # 合并步骤状态
                partial_step_sets = []
                for br in batch_results:
                    if br.updated_plan_json:
                        try:
                            partial_plan = json.loads(br.updated_plan_json)
                            partial_step_sets.append(partial_plan.get("steps", []))
                        except json.JSONDecodeError:
                            pass

                if partial_step_sets:
                    merged_steps = merge_parallel_step_states(steps, partial_step_sets)
                    updated_plan_json = serialize_plan_with_steps(plan_obj, merged_steps)
                else:
                    updated_plan_json = plan_json

                # 确定最终状态
                statuses = [br.status for br in batch_results]
                if "failed" in statuses:
                    status = "failed"
                elif all(s == "completed" for s in statuses):
                    status = "completed"
                else:
                    status = "failed"

                summary = merged_summary
                snapshot_json = ""
                for br in batch_results:
                    if hasattr(br, "snapshot_json") and br.snapshot_json:
                        snapshot_json = br.snapshot_json
                        break

                error_detail: str | None = None
                if status == "failed" and not (updated_plan_json or "").strip():
                    fallback_reason = "并行执行失败且未返回 updated_plan_json，已由 Supervisor 侧兜底补全。"
                    updated_plan_json = _mark_plan_steps_failed(plan_json, fallback_reason)
                    error_detail = fallback_reason

                logger.info(
                    "V3: 并行执行完成，status=%s，executor_session_id=%s",
                    status,
                    executor_session_id,
                )
            else:
                # V2 串行执行模式（原逻辑）
                executor_result = await run_executor(
                    plan_json,
                    context=runtime_context,
                )
                status = executor_result.status
                summary = executor_result.summary
                updated_plan_json = executor_result.updated_plan_json
                snapshot_json = getattr(executor_result, "snapshot_json", "") or ""
                error_detail: str | None = None
                # 文档约束：Mode3（按 plan_id 执行）在失败时必须返回可复用 plan 状态（updated_plan_json 非空）。
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
            # 把异常原因标注到 plan_json 所有 pending/running 步骤的 failure_reason，
            # 确保 Planner 重规划时能看到失败信息
            updated_plan_json = _mark_plan_steps_failed(plan_json, error_detail)
            logger.error(
                "Executor 执行失败，executor_session_id=%s，错误：%s",
                executor_session_id,
                error_detail,
            )

        # 结构化返回，供 dynamic_tools_node 解析 updated_plan_json 写回 State。
        # 注意：updated_plan_json 仅用于状态同步，不应直接暴露给 Supervisor LLM。
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


# =============================================================================
# V3+ 异步并发工具（仅在 enable_v3plus_async 启用时注册）
# =============================================================================


def _build_call_executor_async_tool(runtime_context: Context):
    @tool
    async def call_executor_async(
        state: Annotated[State, InjectedState],
    ) -> str:
        """非阻塞启动 Executor 执行（V3+ 异步模式）。

        此工具仅在环境变量 ENABLE_V3PLUS_ASYNC=true 时可用。
        Executor 在后台异步执行，立即返回 task_id，不阻塞 Supervisor。
        Supervisor 可以继续处理用户输入，使用 get_executor_status 查询进度。

        使用场景：
        - 长时间运行的任务（需要数分钟以上）
        - 需要与用户交互的同时监控执行进度
        - 并发执行多个独立任务

        Returns:
            任务启动确认信息，包含 task_id
        """
        # 获取 plan_json
        plan_json = state.plan_json
        if not plan_json:
            return "错误：当前没有 plan_json，请先使用 call_planner 生成执行计划。"

        try:
            # 导入 ExecutorManager
            from src.common.executor_manager import get_executor_manager

            manager = get_executor_manager()

            # 启动后台任务
            task_id = await manager.start_executor(plan_json, runtime_context)

            # 解析 plan_id 用于友好输出
            try:
                plan_data = json.loads(plan_json)
                plan_id = plan_data.get("plan_id", "unknown")
            except json.JSONDecodeError:
                plan_id = "unknown"

            return f"""✅ Executor 已启动（后台异步执行模式）

**任务 ID**: {task_id}
**计划 ID**: {plan_id}

**后续操作**：
- 使用 `get_executor_status` 工具查询执行进度
- Executor 完成前，你可以继续下达其他指令
- 完成后可使用 `get_executor_full_output` 查看详细结果

**注意**：此模式下 Executor 在后台运行，不会阻塞 Supervisor。"""

        except Exception as e:
            logger.exception("Failed to start async executor")
            return f"❌ 启动 Executor 失败：{type(e).__name__}: {str(e)}"

    return call_executor_async


@tool
def get_executor_status(
    task_id: str,
) -> str:
    """查询 Executor 后台任务的状态和进度。

    此工具仅在环境变量 ENABLE_V3PLUS_ASYNC=true 时可用。
    用于查询由 call_executor_async 启动的后台任务状态。

    Args:
        task_id: 后台任务 ID（由 call_executor_async 返回）

    Returns:
        当前状态、进度、结果（如果已完成）
    """
    from src.common.executor_manager import get_executor_manager

    manager = get_executor_manager()
    status_info = manager.get_task_status(task_id)

    if "error" in status_info and status_info["error"].startswith("Task not found"):
        return f"❌ {status_info['error']}"

    # 格式化输出
    status_emoji = {
        "pending": "⏳",
        "running": "▶️",
        "completed": "✅",
        "failed": "❌",
        "cancelled": "⏹️",
    }

    emoji = status_emoji.get(status_info["status"], "❓")

    output = f"""📊 **任务状态报告**

{emoji} **任务 ID**: {status_info['task_id']}
📋 **计划 ID**: {status_info['plan_id']}
🔄 **状态**: {status_info['status']}
✓ **完成**: {'是' if status_info['done'] else '否'}
🕐 **创建时间**: {status_info['created_at']}
🕒 **更新时间**: {status_info['updated_at']}"""

    # 添加结果（仅完成时）
    if status_info['status'] == 'completed' and status_info.get('result'):
        result = status_info['result']
        output += f"\n\n**执行结果**：\n{json.dumps(result, ensure_ascii=False, indent=2)}"

    # 添加错误（仅失败时）
    if status_info.get('error'):
        output += f"\n\n**错误信息**：\n{status_info['error']}"

    return output


@tool
def cancel_executor(
    task_id: str,
) -> str:
    """取消正在运行的 Executor 后台任务。

    此工具仅在环境变量 ENABLE_V3PLUS_ASYNC=true 时可用。
    用于取消由 call_executor_async 启动的后台任务。

    Args:
        task_id: 要取消的任务 ID

    Returns:
        操作结果
    """
    from src.common.executor_manager import get_executor_manager

    manager = get_executor_manager()
    success = manager.cancel_task(task_id)

    if success:
        return f"✅ 任务 {task_id} 已取消"
    else:
        return f"⚠️ 无法取消任务 {task_id}（可能已完成或不存在）"


async def get_tools(runtime_context: Context | None = None) -> List[Callable[..., Any]]:
    """主 ReAct 循环返回的工具集。"""
    if runtime_context is None:
        runtime_context = Context()

    # 基础工具集（始终可用）
    tools = [
        _build_call_planner_tool(runtime_context),
        _build_call_executor_tool(runtime_context),
        _build_get_executor_full_output_tool(),
    ]

    # V3+ 异步工具（仅在启用时注册）
    if runtime_context.enable_v3plus_async:
        tools.extend([
            _build_call_executor_async_tool(runtime_context),
            get_executor_status,
            cancel_executor,
        ])
        logger.info("V3+ async tools registered")

    return tools
