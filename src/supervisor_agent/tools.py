"""AutoDL-Agent 主循环工具 - 永远只绑定两个工具"""

import json
import logging
import uuid
from datetime import UTC, datetime
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


def _resolve_planner_input_for_generate_plan(
    task_core: str,
    plan_id: str | None,
    planner_session: PlannerSession | None,
) -> tuple[str | None, str | None]:
    """校验 generate_plan 参数，返回 (错误信息, 重规划用的 plan_json)。

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
    plan_id = parsed.get("plan_id") or prev_plan_id or f"plan_{uuid.uuid4().hex[:8]}"
    parsed["plan_id"] = plan_id

    prev_version = prev.get("version")
    prev_version = prev_version if isinstance(prev_version, int) else 0
    current_version = parsed.get("version")
    if isinstance(current_version, int):
        parsed["version"] = max(current_version, prev_version + 1 if prev_plan_id else 1)
    else:
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


def _build_generate_plan_tool(runtime_context: Context):
    @tool
    async def generate_plan(
        state: Annotated[State, InjectedState],
        task_core: str = "",
        plan_id: str | None = None,
    ) -> str:
        """生成或更新意图层 Plan（JSON）。

        与架构一致：Planner 只接收 **task_core** 与（重规划时）由 **plan_id** 从状态中解析出的完整计划。

        - **首次规划**：必须提供非空且**足够详细**的 ``task_core``（目标、约束、验收标准、关键上下文等），不要传 ``plan_id``。
        - **重规划**：传入当前计划中的 ``plan_id``（与 ``PlannerSession`` 内 JSON 一致）；**强烈建议**同时提供详尽的 ``task_core`` 说明修订重点。完整带执行状态的计划由工具从状态中读取，无需在参数里粘贴 JSON。
        """

        if state.planner_session is None:
            session_id = f"plan_{uuid.uuid4().hex[:8]}"
        else:
            session_id = state.planner_session.session_id

        err, replan_plan_json = _resolve_planner_input_for_generate_plan(
            task_core,
            plan_id,
            state.planner_session,
        )
        if err:
            return err

        previous_plan_json = state.planner_session.plan_json if state.planner_session else None

        plan_json = await run_planner(
            task_core,
            replan_plan_json=replan_plan_json,
            context=runtime_context,
        )
        normalized = _normalize_plan_json(plan_json, previous_plan_json=previous_plan_json)

        logger.info("Planner 生成计划，session_id=%s，长度=%d", session_id, len(normalized))
        return normalized

    return generate_plan


def _build_execute_plan_tool(runtime_context: Context):
    @tool
    async def execute_plan(state: Annotated[State, InjectedState]) -> str:
        """按当前计划执行深度学习任务。
        从 State 中读取最新的 JSON 计划，交给 Executor Agent 执行。
        调用前必须已经通过 generate_plan 生成了计划。
        """
        if state.planner_session is None:
            return "错误：尚未生成计划，请先调用 generate_plan。"
        if not state.planner_session.plan_json:
            return "错误：计划内容为空，请先调用 generate_plan 生成有效计划。"

        plan_json = state.planner_session.plan_json
        executor_session_id = f"exec_{uuid.uuid4().hex[:8]}"

        logger.info(
            "Executor 开始执行，executor_session_id=%s，planner_session_id=%s",
            executor_session_id,
            state.planner_session.session_id,
        )

        try:
            executor_result = await run_executor(
                plan_json,
                context=runtime_context,
            )
            status = executor_result.status
            summary = executor_result.summary
            updated_plan_json = executor_result.updated_plan_json
            error_detail: str | None = None
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

        # 结构化返回，供 dynamic_tools_node 解析 updated_plan_json 写回 State
        # 格式约定：[EXECUTOR_RESULT] 标记行后接 JSON
        meta = {
            "executor_session_id": executor_session_id,
            "planner_session_id": state.planner_session.session_id,
            "status": status,
            "error_detail": error_detail,
            "started_at": datetime.now(UTC).isoformat(),
            "updated_plan_json": updated_plan_json,
        }
        meta_line = f"[EXECUTOR_RESULT] {json.dumps(meta, ensure_ascii=False)}"

        return f"{summary}\n\n{meta_line}"

    return execute_plan


def _mark_plan_steps_failed(plan_json: str, error_detail: str) -> str:
    """将 plan_json 中所有 pending/running 步骤标记为 failed，并写入 failure_reason。

    当 Executor 因异常崩溃，无法返回带状态的 updated_plan 时调用。
    保证 Planner 重规划时能看到哪些步骤未完成及失败原因。
    """
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


async def get_tools(runtime_context: Context | None = None) -> List[Callable[..., Any]]:
    """主 ReAct 循环永远只返回这两个工具。"""
    if runtime_context is None:
        runtime_context = Context()
    return [
        _build_generate_plan_tool(runtime_context),
        _build_execute_plan_tool(runtime_context),
    ]
