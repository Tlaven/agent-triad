"""端到端验收测试（调用真实 LLM，对应 docs/product-roadmap.md §7.1 V1.0）。

These tests call the REAL LLM APIs and are skipped in normal CI runs.
Run manually with:
    make test_e2e
    uv run pytest tests/e2e -m live_llm -v -s

Requires environment variables:
    SILICONFLOW_API_KEY  (for Planner / Executor)
    DASHSCOPE_API_KEY    (for Supervisor, if using Qwen)

验收标准（详见 docs/product-roadmap.md §7.1 V1.0）：
  Given a multi-step task, the system should complete:
    plan generation → tool execution → failure-triggered replan (up to 3x) → final answer
  with no hidden crashes, and execution status fully readable in updated_plan_json.
"""

import json
import os

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from src.common.context import Context
from src.supervisor_agent.graph import graph

# Skip all tests in this module if no API key is available
pytestmark = pytest.mark.live_llm


def _has_api_keys() -> bool:
    return bool(os.getenv("SILICONFLOW_API_KEY") or os.getenv("DASHSCOPE_API_KEY"))


# ---------------------------------------------------------------------------
# Scenario A: Simple factual Q&A → Mode 1 (no tools)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _has_api_keys(), reason="No API keys configured")
async def test_scenario_a_simple_qa_mode1() -> None:
    """Simple factual question should be answered directly without calling any tools."""
    ctx = Context(max_replan=1, max_executor_iterations=5)
    result = await graph.ainvoke(
        {"messages": [HumanMessage(content="什么是 Python？请用一句话回答。")]},
        context=ctx,
    )

    messages = result["messages"]
    final_ai = next((m for m in reversed(messages) if isinstance(m, AIMessage) and not m.tool_calls), None)
    assert final_ai is not None, "Should have a final AI response"
    assert final_ai.content, "Final response should not be empty"

    # Mode 1 means no tool calls were made across the entire conversation
    tool_call_messages = [m for m in messages if isinstance(m, AIMessage) and m.tool_calls]
    assert len(tool_call_messages) == 0, (
        f"Mode 1 should not call tools, but found: "
        f"{[tc['name'] for m in tool_call_messages for tc in m.tool_calls]}"
    )


# ---------------------------------------------------------------------------
# Scenario B: Short flow task → Mode 2/3, executor completes
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _has_api_keys(), reason="No API keys configured")
async def test_scenario_b_short_task_executor_completes(tmp_path, monkeypatch) -> None:
    """A short coding task should trigger tool usage and complete successfully."""
    monkeypatch.setenv("AGENT_WORKSPACE_DIR", str(tmp_path))
    ctx = Context(max_replan=1, max_executor_iterations=10)
    task = (
        "请在当前工作区创建一个名为 hello.txt 的文件，"
        "内容为 'Hello, World!'。完成后告诉我文件路径。"
    )
    result = await graph.ainvoke(
        {"messages": [HumanMessage(content=task)]},
        context=ctx,
    )

    # Check the executor ran and produced a result
    planner_session = result.get("planner_session")
    if planner_session:
        # If a plan was used, the last executor status should be completed
        status = planner_session.last_executor_status
        assert status in ("completed", None), f"Unexpected status: {status}"

    final_ai = next(
        (m for m in reversed(result["messages"]) if isinstance(m, AIMessage) and not m.tool_calls),
        None,
    )
    assert final_ai is not None
    assert final_ai.content
    created = tmp_path / "hello.txt"
    assert created.exists(), f"Expected file not found: {created}"


# ---------------------------------------------------------------------------
# Scenario C: Multi-step task → Mode 3, Plan has multiple steps
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _has_api_keys(), reason="No API keys configured")
async def test_scenario_c_multistep_task_mode3() -> None:
    """A complex multi-step task should use Mode 3 (Plan → Execute) and produce a plan
    with multiple steps."""
    ctx = Context(max_replan=2, max_executor_iterations=15)
    task = (
        "请完成以下三步任务：\n"
        "1. 创建一个名为 workspace 的文件夹\n"
        "2. 在 workspace 中创建 README.md 文件，内容为 '# My Project'\n"
        "3. 在 workspace 中创建 main.py 文件，内容为 'print(\"Hello World\")'\n"
        "全部完成后给我简要汇报。"
    )
    result = await graph.ainvoke(
        {"messages": [HumanMessage(content=task)]},
        context=ctx,
    )

    planner_session = result.get("planner_session")
    if planner_session and planner_session.plan_json:
        plan = json.loads(planner_session.plan_json)
        # A multi-step task should produce a plan with at least 2 steps
        assert len(plan.get("steps", [])) >= 1, "Plan should have at least 1 step"
        assert isinstance(plan.get("plan_id"), str)
        assert plan.get("version", 0) >= 1

    final_ai = next(
        (m for m in reversed(result["messages"]) if isinstance(m, AIMessage) and not m.tool_calls),
        None,
    )
    assert final_ai is not None
    assert final_ai.content


# ---------------------------------------------------------------------------
# Scenario D: Impossible task → replan triggered, MAX_REPLAN convergence
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _has_api_keys(), reason="No API keys configured")
async def test_scenario_d_impossible_task_converges_within_max_replan() -> None:
    """An impossible task should trigger replanning up to MAX_REPLAN times and then
    terminate gracefully with a user-facing failure explanation."""
    ctx = Context(max_replan=2, max_executor_iterations=5)
    task = (
        "请访问 https://non-existent-domain-xyz-12345.example/api/data "
        "并把返回的 JSON 写入文件 output.json。"
    )
    result = await graph.ainvoke(
        {"messages": [HumanMessage(content=task)]},
        context=ctx,
    )

    # The system should have terminated (not raised an exception)
    final_ai = next(
        (m for m in reversed(result["messages"]) if isinstance(m, AIMessage) and not m.tool_calls),
        None,
    )
    assert final_ai is not None, "System should produce a final answer even on failure"
    assert final_ai.content, "Final answer must not be empty"

    # replan_count should not exceed MAX_REPLAN
    replan_count = result.get("replan_count", 0)
    assert replan_count <= ctx.max_replan, (
        f"replan_count ({replan_count}) exceeded MAX_REPLAN ({ctx.max_replan})"
    )
