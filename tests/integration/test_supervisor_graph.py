"""Integration tests for the full Supervisor StateGraph (mock LLM & sub-agents).

Patches:
  - src.supervisor_agent.graph.load_chat_model  → mock LLM with sequential responses
  - src.supervisor_agent.tools.run_planner      → async mock returning preset plan JSON
  - src.supervisor_agent.tools.run_executor     → async mock returning preset ExecutorResult
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

from langchain_core.messages import AIMessage, HumanMessage

from src.common.context import Context
from src.executor_agent.graph import ExecutorResult
from src.supervisor_agent.graph import graph

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_plan_json(plan_id: str = "plan_integ_sup", version: int = 1) -> str:
    return json.dumps({
        "plan_id": plan_id,
        "version": version,
        "goal": "integration test goal",
        "steps": [
            {
                "step_id": "step_1",
                "intent": "do the work",
                "expected_output": "work done",
                "status": "pending",
                "result_summary": None,
                "failure_reason": None,
            }
        ],
    }, ensure_ascii=False)


def _make_completed_executor_result(plan_id: str = "plan_integ_sup") -> ExecutorResult:
    completed_plan = json.loads(_make_plan_json(plan_id))
    completed_plan["steps"][0]["status"] = "completed"
    completed_plan["steps"][0]["result_summary"] = "done"
    return ExecutorResult(
        status="completed",
        updated_plan_json=json.dumps(completed_plan, ensure_ascii=False),
        summary="All steps completed successfully",
    )


def _make_failed_executor_result(plan_id: str = "plan_integ_sup") -> ExecutorResult:
    failed_plan = json.loads(_make_plan_json(plan_id))
    failed_plan["steps"][0]["status"] = "failed"
    failed_plan["steps"][0]["failure_reason"] = "tool call timeout"
    return ExecutorResult(
        status="failed",
        updated_plan_json=json.dumps(failed_plan, ensure_ascii=False),
        summary="工具调用超时，执行失败",
    )


def _make_mock_llm(responses: list[AIMessage]) -> MagicMock:
    mock = MagicMock()
    mock.bind_tools = MagicMock(return_value=mock)
    mock.ainvoke = AsyncMock(side_effect=list(responses))
    return mock


def _ctx(max_replan: int = 2) -> Context:
    return Context(max_replan=max_replan, max_executor_iterations=5)


# ---------------------------------------------------------------------------
# Mode 1: Direct Response (no tools)
# ---------------------------------------------------------------------------

async def test_supervisor_mode1_direct_response() -> None:
    mock_llm = _make_mock_llm([AIMessage(content="Python 是一种解释型编程语言。")])

    with patch("src.supervisor_agent.graph.load_chat_model", return_value=mock_llm):
        result = await graph.ainvoke(
            {"messages": [HumanMessage(content="什么是 Python？")]},
            context=_ctx(),
        )

    messages = result["messages"]
    final_ai = next((m for m in reversed(messages) if isinstance(m, AIMessage)), None)
    assert final_ai is not None
    assert "Python" in final_ai.content
    assert result["supervisor_decision"].mode == 1


# ---------------------------------------------------------------------------
# Mode 2: call_executor → completed → final answer
# ---------------------------------------------------------------------------

async def test_supervisor_mode2_call_executor_completed() -> None:
    exec_result = _make_completed_executor_result()

    llm_responses = [
        # Round 1: Supervisor decides to execute directly
        AIMessage(
            content="",
            tool_calls=[{
                "id": "call_exec1",
                "name": "call_executor",
                "args": {"task_description": "write hello world to a file"},
                "type": "tool_call",
            }],
        ),
        # Round 2: Supervisor synthesizes final answer
        AIMessage(content="文件已成功写入，任务完成。"),
    ]
    mock_llm = _make_mock_llm(llm_responses)

    with (
        patch("src.supervisor_agent.graph.load_chat_model", return_value=mock_llm),
        patch("src.supervisor_agent.tools.run_executor", new_callable=AsyncMock, return_value=exec_result),
    ):
        result = await graph.ainvoke(
            {"messages": [HumanMessage(content="Write hello world to a file")]},
            context=_ctx(),
        )

    final_ai = next((m for m in reversed(result["messages"]) if isinstance(m, AIMessage) and not m.tool_calls), None)
    assert final_ai is not None
    assert "任务完成" in final_ai.content or "成功" in final_ai.content


# ---------------------------------------------------------------------------
# Mode 3: call_planner → call_executor → final answer
# ---------------------------------------------------------------------------

async def test_supervisor_mode3_plan_then_execute() -> None:
    """Full Mode 3 flow: Supervisor calls call_planner, then call_executor."""
    # We patch uuid in tools.py so the plan_id is predictable
    fixed_plan_id = "plan_mode3test"
    plan_json = _make_plan_json(fixed_plan_id)
    exec_result = _make_completed_executor_result(fixed_plan_id)

    # run_planner returns a plan without plan_id; _normalize_plan_json adds it
    # We need to control the plan_id → patch uuid
    raw_plan = json.dumps({"goal": "integration test goal", "steps": [
        {"step_id": "step_1", "intent": "do work", "expected_output": "done",
         "status": "pending", "result_summary": None, "failure_reason": None}
    ]})

    llm_responses = [
        # Round 1: Supervisor decides to plan first
        AIMessage(
            content="",
            tool_calls=[{
                "id": "call_gen1",
                "name": "call_planner",
                "args": {"task_core": "multi-step integration task"},
                "type": "tool_call",
            }],
        ),
        # Round 2: After plan is ready, Supervisor executes it
        AIMessage(
            content="",
            tool_calls=[{
                "id": "call_exec1",
                "name": "call_executor",
                "args": {"plan_id": fixed_plan_id},
                "type": "tool_call",
            }],
        ),
        # Round 3: Final answer
        AIMessage(content="多步骤任务已全部完成。"),
    ]
    mock_llm = _make_mock_llm(llm_responses)

    # Make uuid.uuid4().hex[:8] → "mode3tes" so plan_id = "plan_mode3tes"
    # But we need "plan_mode3test" to match exactly, so we patch _normalize_plan_json
    # Simpler: patch run_planner to return a raw plan with no plan_id,
    # and patch uuid so the generated id is "plan_mode3test"
    mock_uuid = MagicMock()
    mock_uuid.hex = "mode3test" + "0" * 8  # enough chars for [:8] = "mode3tes"

    # Actually let's just patch uuid to make plan_id = "plan_mode3te"
    # The final plan_id from _normalize_plan_json will be: "plan_" + uuid.hex[:8]
    # So if uuid.hex = "mode3test12345678" then plan_id = "plan_mode3te"
    # Let's use a simpler approach: let run_planner return a plan that _normalize_plan_json will
    # process, and adjust the call_executor tool_call to use the real plan_id from session.

    # Better approach: patch _normalize_plan_json in tools.py to return fixed plan_json
    with (
        patch("src.supervisor_agent.graph.load_chat_model", return_value=mock_llm),
        patch("src.supervisor_agent.tools.run_planner", new_callable=AsyncMock, return_value=raw_plan),
        patch("src.supervisor_agent.tools._normalize_plan_json", return_value=plan_json),
        patch("src.supervisor_agent.tools.run_executor", new_callable=AsyncMock, return_value=exec_result),
    ):
        result = await graph.ainvoke(
            {"messages": [HumanMessage(content="multi-step integration task")]},
            context=_ctx(),
        )

    final_ai = next(
        (m for m in reversed(result["messages"]) if isinstance(m, AIMessage) and not m.tool_calls),
        None,
    )
    assert final_ai is not None
    assert "完成" in final_ai.content


# ---------------------------------------------------------------------------
# MAX_REPLAN convergence: executor fails twice → forced termination
# ---------------------------------------------------------------------------

async def test_supervisor_max_replan_forces_termination() -> None:
    """After max_replan (=2) consecutive failures, call_model must terminate
    without calling the LLM again."""
    exec_result = _make_failed_executor_result()

    # LLM is consulted for the first two rounds (both choose call_executor),
    # then the third call_model invocation short-circuits WITHOUT calling the LLM.
    llm_responses = [
        AIMessage(
            content="",
            tool_calls=[{
                "id": "call_exec1",
                "name": "call_executor",
                "args": {"task_description": "attempt 1"},
                "type": "tool_call",
            }],
        ),
        AIMessage(
            content="",
            tool_calls=[{
                "id": "call_exec2",
                "name": "call_executor",
                "args": {"task_description": "attempt 2"},
                "type": "tool_call",
            }],
        ),
        # A third LLM response that should NOT be consumed (forced termination)
        AIMessage(content="should not appear"),
    ]
    mock_llm = _make_mock_llm(llm_responses)

    with (
        patch("src.supervisor_agent.graph.load_chat_model", return_value=mock_llm),
        patch("src.supervisor_agent.tools.run_executor", new_callable=AsyncMock, return_value=exec_result),
    ):
        result = await graph.ainvoke(
            {"messages": [HumanMessage(content="task that will fail")]},
            context=_ctx(max_replan=2),
        )

    # Graph should have ended; final decision mode must be 1 (forced)
    assert result["supervisor_decision"].mode == 1
    # The "should not appear" message should never be in the output
    all_contents = [m.content for m in result["messages"] if isinstance(m, AIMessage)]
    assert "should not appear" not in all_contents
    # LLM was only called twice (not three times)
    assert mock_llm.ainvoke.call_count == 2


# ---------------------------------------------------------------------------
# Mode 2 → Mode 3 automatic upgrade (via call_model logic, no LLM needed for upgrade)
# ---------------------------------------------------------------------------

async def test_supervisor_mode2_to_mode3_upgrade_via_call_model() -> None:
    """When Mode2 executor fails with upgrade-signal summary and plan_json is empty,
    call_model automatically generates a call_planner call without LLM consultation."""
    # The failed executor result has an upgrade signal and empty plan
    failed_no_plan = ExecutorResult(
        status="failed",
        updated_plan_json="",  # Mode2 returns empty plan
        summary="需要重新规划，当前路径无法继续执行",
    )
    raw_plan = json.dumps({"goal": "retried goal", "steps": [
        {"step_id": "step_1", "intent": "redo", "expected_output": "done",
         "status": "pending", "result_summary": None, "failure_reason": None}
    ]})
    fixed_plan_id = "plan_upgrade_test"
    fixed_plan_json = _make_plan_json(fixed_plan_id)
    exec_result2 = _make_completed_executor_result(fixed_plan_id)

    llm_responses = [
        # Round 1: Mode2 - Supervisor chooses call_executor directly
        AIMessage(
            content="",
            tool_calls=[{
                "id": "call_exec1",
                "name": "call_executor",
                "args": {"task_description": "try to do the task"},
                "type": "tool_call",
            }],
        ),
        # Round 3 (after auto-upgrade call_planner + call_executor):
        # Supervisor executes the plan (LLM called again after Mode3 plan is ready)
        AIMessage(
            content="",
            tool_calls=[{
                "id": "call_exec2",
                "name": "call_executor",
                "args": {"plan_id": fixed_plan_id},
                "type": "tool_call",
            }],
        ),
        # Round 4: Final answer
        AIMessage(content="升级到 Mode3 后任务完成。"),
    ]
    mock_llm = _make_mock_llm(llm_responses)

    with (
        patch("src.supervisor_agent.graph.load_chat_model", return_value=mock_llm),
        patch(
            "src.supervisor_agent.tools.run_executor",
            new_callable=AsyncMock,
            side_effect=[failed_no_plan, exec_result2],
        ),
        patch("src.supervisor_agent.tools.run_planner", new_callable=AsyncMock, return_value=raw_plan),
        patch("src.supervisor_agent.tools._normalize_plan_json", return_value=fixed_plan_json),
    ):
        result = await graph.ainvoke(
            {"messages": [HumanMessage(content="task requiring planning")]},
            context=_ctx(max_replan=2),
        )

    final_ai = next(
        (m for m in reversed(result["messages"]) if isinstance(m, AIMessage) and not m.tool_calls),
        None,
    )
    assert final_ai is not None
    assert "完成" in final_ai.content
