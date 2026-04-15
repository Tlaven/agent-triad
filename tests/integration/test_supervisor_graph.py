"""Supervisor StateGraph 全链路集成测试（mock LLM + mock Executor HTTP）。

Patches:
  - src.supervisor_agent.graph.load_chat_model   → mock LLM with sequential responses
  - src.supervisor_agent.tools.run_planner       → async mock returning preset plan JSON
  - src.supervisor_agent.v3_lifecycle.v3_manager  → mock 子进程基础设施
  - httpx.AsyncClient                             → mock HTTP responses from Executor subprocess
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from langchain_core.messages import AIMessage, HumanMessage

from src.common.context import Context
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


def _make_mock_llm(responses: list[AIMessage]) -> MagicMock:
    mock = MagicMock()
    mock.bind_tools = MagicMock(return_value=mock)
    mock.ainvoke = AsyncMock(side_effect=list(responses))
    return mock


def _ctx(max_replan: int = 2) -> Context:
    return Context(max_replan=max_replan, max_executor_iterations=5)


def _make_mock_v3_infra() -> tuple[MagicMock, MagicMock]:
    """Create mock subprocess infrastructure (v3_manager + infra)."""
    mock_pm = MagicMock()
    mock_pm.base_url = "http://localhost:9999"
    mock_pm.is_running = True

    mock_infra = MagicMock()
    mock_infra.process_manager = mock_pm

    mock_v3_mgr = MagicMock()
    mock_v3_mgr.ensure_started = AsyncMock(return_value=mock_infra)

    return mock_v3_mgr, mock_infra


def _make_mock_httpx_completed(plan_id: str = "plan_integ_sup") -> AsyncMock:
    """Mock httpx.AsyncClient that returns completed executor result.

    Uses URL-based routing: GET /health, GET /tasks, GET /result, POST /execute.
    """
    completed_plan = json.loads(_make_plan_json(plan_id))
    completed_plan["steps"][0]["status"] = "completed"
    completed_plan["steps"][0]["result_summary"] = "done"

    async def _get(url, **kwargs):
        resp = MagicMock()
        if "/health" in url:
            resp.status_code = 200
            resp.json.return_value = {"status": "ok"}
        elif "/tasks" in url:
            resp.status_code = 200
            resp.json.return_value = {"tasks": {}, "count": 0}
        elif "/result/" in url:
            resp.status_code = 200
            resp.json.return_value = {
                "status": "completed",
                "updated_plan_json": json.dumps(completed_plan, ensure_ascii=False),
                "summary": "All steps completed successfully",
                "snapshot_json": "",
            }
        else:
            resp.status_code = 404
        return resp

    async def _post(url, **kwargs):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"plan_id": plan_id, "status": "accepted"}
        return resp

    client = AsyncMock()
    client.get = AsyncMock(side_effect=_get)
    client.post = AsyncMock(side_effect=_post)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)

    return client


def _make_mock_httpx_failed(plan_id: str = "plan_integ_sup") -> AsyncMock:
    """Mock httpx.AsyncClient that returns failed executor result."""
    failed_plan = json.loads(_make_plan_json(plan_id))
    failed_plan["steps"][0]["status"] = "failed"
    failed_plan["steps"][0]["failure_reason"] = "tool call timeout"

    async def _get(url, **kwargs):
        resp = MagicMock()
        if "/health" in url:
            resp.status_code = 200
            resp.json.return_value = {"status": "ok"}
        elif "/tasks" in url:
            resp.status_code = 200
            resp.json.return_value = {"tasks": {}, "count": 0}
        elif "/result/" in url:
            resp.status_code = 200
            resp.json.return_value = {
                "status": "failed",
                "updated_plan_json": json.dumps(failed_plan, ensure_ascii=False),
                "summary": "工具调用超时，执行失败",
                "snapshot_json": "",
            }
        else:
            resp.status_code = 404
        return resp

    async def _post(url, **kwargs):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"plan_id": plan_id, "status": "accepted"}
        return resp

    client = AsyncMock()
    client.get = AsyncMock(side_effect=_get)
    client.post = AsyncMock(side_effect=_post)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)

    return client


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_mailbox():
    """Reset and initialize mailbox singleton between tests."""
    import src.common.mailbox as _mb_mod
    from src.common.mailbox import Mailbox, set_mailbox
    _mb_mod._mailbox = None
    set_mailbox(Mailbox())
    yield
    _mb_mod._mailbox = None


# ---------------------------------------------------------------------------
# Mode 1: Direct Response (no tools)
# ---------------------------------------------------------------------------


async def test_supervisor_mode1_direct_response() -> None:
    mock_llm = _make_mock_llm([AIMessage(content="Python 是一种解释型编程语言。")])
    mock_v3_mgr, _ = _make_mock_v3_infra()

    with (
        patch("src.supervisor_agent.graph.load_chat_model", return_value=mock_llm),
        patch("src.supervisor_agent.v3_lifecycle.v3_manager", mock_v3_mgr),
    ):
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
    mock_v3_mgr, _ = _make_mock_v3_infra()
    mock_httpx = _make_mock_httpx_completed()

    with (
        patch("src.supervisor_agent.graph.load_chat_model", return_value=mock_llm),
        patch("src.supervisor_agent.v3_lifecycle.v3_manager", mock_v3_mgr),
        patch("httpx.AsyncClient", return_value=mock_httpx),
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
    fixed_plan_id = "plan_mode3test"
    plan_json = _make_plan_json(fixed_plan_id)

    raw_plan = json.dumps({"goal": "integration test goal", "steps": [
        {"step_id": "step_1", "intent": "do work", "expected_output": "done",
         "status": "pending", "result_summary": None, "failure_reason": None}
    ]})

    llm_responses = [
        AIMessage(
            content="",
            tool_calls=[{
                "id": "call_gen1",
                "name": "call_planner",
                "args": {"task_core": "multi-step integration task"},
                "type": "tool_call",
            }],
        ),
        AIMessage(
            content="",
            tool_calls=[{
                "id": "call_exec1",
                "name": "call_executor",
                "args": {"plan_id": fixed_plan_id},
                "type": "tool_call",
            }],
        ),
        AIMessage(content="多步骤任务已全部完成。"),
    ]
    mock_llm = _make_mock_llm(llm_responses)
    mock_v3_mgr, _ = _make_mock_v3_infra()
    mock_httpx = _make_mock_httpx_completed(fixed_plan_id)

    with (
        patch("src.supervisor_agent.graph.load_chat_model", return_value=mock_llm),
        patch("src.supervisor_agent.v3_lifecycle.v3_manager", mock_v3_mgr),
        patch("httpx.AsyncClient", return_value=mock_httpx),
        patch("src.supervisor_agent.tools.run_planner", new_callable=AsyncMock, return_value=raw_plan),
        patch("src.supervisor_agent.tools._normalize_plan_json", return_value=plan_json),
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
    mock_v3_mgr, _ = _make_mock_v3_infra()

    # Each call_executor invocation needs its own mock client
    mock_httpx_1 = _make_mock_httpx_failed()
    mock_httpx_2 = _make_mock_httpx_failed()

    call_count = 0
    original_client = None

    def _client_factory(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count <= 2:
            return mock_httpx_1
        return mock_httpx_2

    with (
        patch("src.supervisor_agent.graph.load_chat_model", return_value=mock_llm),
        patch("src.supervisor_agent.v3_lifecycle.v3_manager", mock_v3_mgr),
        patch("httpx.AsyncClient", side_effect=_client_factory),
    ):
        result = await graph.ainvoke(
            {"messages": [HumanMessage(content="task that will fail")]},
            context=_ctx(max_replan=2),
        )

    assert result["supervisor_decision"].mode == 1
    all_contents = [m.content for m in result["messages"] if isinstance(m, AIMessage)]
    assert "should not appear" not in all_contents
    assert mock_llm.ainvoke.call_count == 2
