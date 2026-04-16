"""Unit tests for supervisor_agent.graph.dynamic_tools_node (with mocked ToolNode)."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from src.supervisor_agent.graph import dynamic_tools_node
from src.supervisor_agent.state import PlannerSession, State


def _make_state_with_tool_call(
    tool_name: str,
    tool_args: dict,
    call_id: str = "call_test1",
    planner_session: PlannerSession | None = None,
    replan_count: int = 0,
) -> State:
    """Build a State whose last message is an AIMessage with one tool_call."""
    msg = AIMessage(
        content="",
        tool_calls=[{
            "id": call_id,
            "name": tool_name,
            "args": tool_args,
            "type": "tool_call",
        }],
    )
    return State(
        messages=[HumanMessage(content="user input"), msg],
        planner_session=planner_session,
        replan_count=replan_count,
    )


def _make_tool_node_mock(tool_message: ToolMessage) -> MagicMock:
    """Build a ToolNode mock that returns a single ToolMessage."""
    mock_tn = MagicMock()
    mock_tn.ainvoke = AsyncMock(return_value={"messages": [tool_message]})
    return mock_tn


# ---------------------------------------------------------------------------
# call_planner ? PlannerSession updated
# ---------------------------------------------------------------------------

async def test_call_planner_creates_planner_session(make_runtime) -> None:
    plan_json = json.dumps({
        "plan_id": "plan_newplan",
        "version": 1,
        "goal": "build something",
        "steps": [],
    })
    state = _make_state_with_tool_call(
        "call_planner", {"task_core": "build something"}, call_id="call_gen1"
    )
    tm = ToolMessage(content=plan_json, tool_call_id="call_gen1")

    with patch("src.supervisor_agent.graph.ToolNode", return_value=_make_tool_node_mock(tm)):
        result = await dynamic_tools_node(state, make_runtime())

    assert "planner_session" in result
    session: PlannerSession = result["planner_session"]
    assert session.plan_json == plan_json
    assert "plan_newplan" in session.planner_history_by_plan_id


async def test_call_planner_stores_task_core_in_history(make_runtime) -> None:
    plan_json = json.dumps({"plan_id": "plan_abc", "version": 1, "goal": "g", "steps": []})
    state = _make_state_with_tool_call(
        "call_planner", {"task_core": "my important task"}, call_id="call_gen2"
    )
    tm = ToolMessage(content=plan_json, tool_call_id="call_gen2")

    with patch("src.supervisor_agent.graph.ToolNode", return_value=_make_tool_node_mock(tm)):
        result = await dynamic_tools_node(state, make_runtime())

    history = result["planner_session"].planner_history_by_plan_id.get("plan_abc", [])
    assert any("my important task" in msg.get("content", "") for msg in history)


async def test_call_planner_archives_old_version(make_runtime) -> None:
    """When call_planner produces a newer version, the old plan must be archived."""
    old_plan = json.dumps({"plan_id": "plan_same", "version": 1, "goal": "g", "steps": []})
    new_plan = json.dumps({"plan_id": "plan_same", "version": 2, "goal": "g", "steps": []})

    existing_session = PlannerSession(session_id="sess1", plan_json=old_plan)
    state = _make_state_with_tool_call(
        "call_planner", {"task_core": "update", "plan_id": "plan_same"},
        call_id="call_gen3",
        planner_session=existing_session,
    )
    tm = ToolMessage(content=new_plan, tool_call_id="call_gen3")

    with patch("src.supervisor_agent.graph.ToolNode", return_value=_make_tool_node_mock(tm)):
        result = await dynamic_tools_node(state, make_runtime())

    archive = result["planner_session"].plan_archive_by_plan_id.get("plan_same", [])
    assert old_plan in archive


# ---------------------------------------------------------------------------
# call_executor ? replan_count updated, planner_session synced
# ---------------------------------------------------------------------------

async def test_call_executor_completed_resets_replan_count(
    make_runtime,
    sample_executor_result_completed,
) -> None:
    state = _make_state_with_tool_call(
        "call_executor", {"plan_id": "plan_test0001"},
        call_id="call_exec1",
        planner_session=PlannerSession(
            session_id="s1",
            plan_json=json.dumps({"plan_id": "plan_test0001", "version": 1, "goal": "g", "steps": []}),
        ),
        replan_count=1,
    )
    tm = ToolMessage(content=sample_executor_result_completed, tool_call_id="call_exec1")

    with patch("src.supervisor_agent.graph.ToolNode", return_value=_make_tool_node_mock(tm)):
        result = await dynamic_tools_node(state, make_runtime())

    assert result["replan_count"] == 0
    assert result["planner_session"].last_executor_status == "completed"


async def test_call_executor_failed_increments_replan_count(
    make_runtime,
    sample_executor_result_failed,
) -> None:
    state = _make_state_with_tool_call(
        "call_executor", {"plan_id": "plan_test0001"},
        call_id="call_exec2",
        planner_session=PlannerSession(
            session_id="s1",
            plan_json=json.dumps({"plan_id": "plan_test0001", "version": 1, "goal": "g", "steps": []}),
        ),
        replan_count=0,
    )
    tm = ToolMessage(content=sample_executor_result_failed, tool_call_id="call_exec2")

    with patch("src.supervisor_agent.graph.ToolNode", return_value=_make_tool_node_mock(tm)):
        result = await dynamic_tools_node(state, make_runtime())

    assert result["replan_count"] == 1
    assert result["planner_session"].last_executor_status == "failed"


async def test_call_executor_failed_writes_updated_plan_to_session(
    make_runtime,
    sample_executor_result_failed,
    sample_failed_plan_json,
) -> None:
    state = _make_state_with_tool_call(
        "call_executor", {"plan_id": "plan_test0001"},
        call_id="call_exec3",
        planner_session=PlannerSession(
            session_id="s1",
            plan_json=json.dumps({"plan_id": "plan_test0001", "version": 1, "goal": "g", "steps": []}),
        ),
    )
    tm = ToolMessage(content=sample_executor_result_failed, tool_call_id="call_exec3")

    with patch("src.supervisor_agent.graph.ToolNode", return_value=_make_tool_node_mock(tm)):
        result = await dynamic_tools_node(state, make_runtime())

    assert (result["planner_session"].plan_json or "").strip()


async def test_call_executor_llm_receives_sanitized_feedback(
    make_runtime,
    sample_executor_result_completed,
) -> None:
    """The ToolMessage visible to the LLM must NOT contain updated_plan_json."""
    state = _make_state_with_tool_call(
        "call_executor", {"plan_id": "plan_test0001"},
        call_id="call_exec4",
        planner_session=PlannerSession(
            session_id="s1",
            plan_json=json.dumps({"plan_id": "plan_test0001", "version": 1, "goal": "g", "steps": []}),
        ),
    )
    tm = ToolMessage(content=sample_executor_result_completed, tool_call_id="call_exec4")

    with patch("src.supervisor_agent.graph.ToolNode", return_value=_make_tool_node_mock(tm)):
        result = await dynamic_tools_node(state, make_runtime())

    sanitized_msgs = result["messages"]
    assert len(sanitized_msgs) == 1
    content = sanitized_msgs[0].content
    assert "[EXECUTOR_RESULT]" not in content
    assert "updated_plan_json" not in content


# ---------------------------------------------------------------------------
# Non call_planner / call_executor tools pass through unchanged
# ---------------------------------------------------------------------------

async def test_unknown_tool_passthrough(make_runtime) -> None:
    state = _make_state_with_tool_call("some_other_tool", {"x": 1}, call_id="call_other1")
    tm = ToolMessage(content="raw result from other tool", tool_call_id="call_other1")

    with patch("src.supervisor_agent.graph.ToolNode", return_value=_make_tool_node_mock(tm)):
        result = await dynamic_tools_node(state, make_runtime())

    assert result["messages"][0].content == "raw result from other tool"
    assert "planner_session" not in result


# ---------------------------------------------------------------------------
# call_executor 异步派发（fire-and-forget）检测
# ---------------------------------------------------------------------------

async def test_call_executor_v3_dispatch_stores_active_task(make_runtime) -> None:
    """异步派发（无 [EXECUTOR_RESULT]）时写入 ActiveExecutorTask。"""
    dispatch_content = (
        "Executor dispatched, plan_id=plan_dispatch_001, status=accepted."
        '\n[EXECUTOR_DISPATCH] {"plan_id": "plan_dispatch_001", "status": "accepted"}'
    )
    plan_json = json.dumps({
        "plan_id": "plan_dispatch_001", "version": 1, "goal": "g", "steps": [],
    })
    state = _make_state_with_tool_call(
        "call_executor", {"plan_id": "plan_dispatch_001"},
        call_id="call_dispatch1",
        planner_session=PlannerSession(session_id="s1", plan_json=plan_json),
    )
    tm = ToolMessage(content=dispatch_content, tool_call_id="call_dispatch1")

    with patch("src.supervisor_agent.graph.ToolNode", return_value=_make_tool_node_mock(tm)):
        result = await dynamic_tools_node(state, make_runtime())

    assert "active_executor_tasks" in result
    assert "plan_dispatch_001" in result["active_executor_tasks"]
    assert result["active_executor_tasks"]["plan_dispatch_001"].status == "dispatched"
    assert "[EXECUTOR_DISPATCH]" not in result["messages"][0].content
    assert "plan_dispatch_001" in result["messages"][0].content
    assert "planner_session" not in result


async def test_get_executor_result_completed_updates_session(
    make_runtime,
    sample_executor_result_completed,
) -> None:
    """get_executor_result 的完成处理与同步 call_executor 一致。"""
    plan_json = json.dumps({
        "plan_id": "plan_test0001", "version": 1, "goal": "g", "steps": [],
    })
    state = _make_state_with_tool_call(
        "get_executor_result", {"plan_id": "plan_test0001"},
        call_id="call_result1",
        planner_session=PlannerSession(session_id="s1", plan_json=plan_json),
        replan_count=2,
    )
    tm = ToolMessage(content=sample_executor_result_completed, tool_call_id="call_result1")

    with patch("src.supervisor_agent.graph.ToolNode", return_value=_make_tool_node_mock(tm)):
        result = await dynamic_tools_node(state, make_runtime())

    assert result["planner_session"].last_executor_status == "completed"
    assert result["replan_count"] == 0


async def test_get_executor_result_detail_full_appends_step_detail(
    make_runtime,
    sample_executor_result_completed,
) -> None:
    """detail=full 时在精简反馈后追加 last_executor_full_output（步骤级）。"""
    plan_json = json.dumps({
        "plan_id": "plan_test0001", "version": 1, "goal": "g", "steps": [],
    })
    state = _make_state_with_tool_call(
        "get_executor_result", {"plan_id": "plan_test0001", "detail": "full"},
        call_id="call_result_full1",
        planner_session=PlannerSession(session_id="s1", plan_json=plan_json),
        replan_count=2,
    )
    tm = ToolMessage(content=sample_executor_result_completed, tool_call_id="call_result_full1")

    with patch("src.supervisor_agent.graph.ToolNode", return_value=_make_tool_node_mock(tm)):
        result = await dynamic_tools_node(state, make_runtime())

    out = result["messages"][0].content
    assert "## Executor 执行详情" in out
    assert "步骤级执行结果" in out
    assert "[EXECUTOR_RESULT]" not in out


async def test_get_executor_result_cache_detail_no_meta_passthrough(make_runtime) -> None:
    """detail=full 命中缓存路径时无 [EXECUTOR_RESULT]，原样透传 ToolMessage。"""
    body = "仅缓存的步骤级正文，无 EXECUTOR_RESULT 标记。"
    plan_json = json.dumps({"plan_id": "plan_cache", "version": 1, "goal": "g", "steps": []})
    state = _make_state_with_tool_call(
        "get_executor_result", {"plan_id": "plan_cache", "detail": "full"},
        call_id="call_cache1",
        planner_session=PlannerSession(session_id="s1", plan_json=plan_json),
    )
    tm = ToolMessage(content=body, tool_call_id="call_cache1")

    with patch("src.supervisor_agent.graph.ToolNode", return_value=_make_tool_node_mock(tm)):
        result = await dynamic_tools_node(state, make_runtime())

    assert result["messages"][0].content == body
    assert "planner_session" not in result


async def test_get_executor_result_failed_increments_replan_count(
    make_runtime,
    sample_executor_result_failed,
) -> None:
    """get_executor_result with failed result increments replan_count."""
    plan_json = json.dumps({
        "plan_id": "plan_test0001", "version": 1, "goal": "g", "steps": [],
    })
    state = _make_state_with_tool_call(
        "get_executor_result", {"plan_id": "plan_test0001"},
        call_id="call_result2",
        planner_session=PlannerSession(session_id="s1", plan_json=plan_json),
        replan_count=0,
    )
    tm = ToolMessage(content=sample_executor_result_failed, tool_call_id="call_result2")

    with patch("src.supervisor_agent.graph.ToolNode", return_value=_make_tool_node_mock(tm)):
        result = await dynamic_tools_node(state, make_runtime())

    assert result["planner_session"].last_executor_status == "failed"
    assert result["replan_count"] == 1
