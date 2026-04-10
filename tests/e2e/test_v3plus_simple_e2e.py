"""V3+ async concurrent mode simplified E2E tests.

Verify basic async tool functionality in real Supervisor workflow.

Run with:
    ENABLE_V3PLUS_ASYNC=true uv run pytest tests/e2e/test_v3plus_simple_e2e.py -m live_llm -v -s
"""

import os
import time

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from src.common.context import Context
from src.supervisor_agent.graph import graph
from src.supervisor_agent.tools import get_tools

pytestmark = pytest.mark.live_llm


def _has_api_keys() -> bool:
    return bool(os.getenv("SILICONFLOW_API_KEY") or os.getenv("DASHSCOPE_API_KEY"))


@pytest.mark.skipif(not _has_api_keys(), reason="No API keys configured")
async def test_v3plus_async_tools_available(monkeypatch, tmp_path) -> None:
    """Verify async tools are available when V3+ is enabled."""
    monkeypatch.setenv("ENABLE_V3PLUS_ASYNC", "true")
    monkeypatch.setenv("AGENT_WORKSPACE_DIR", str(tmp_path))

    ctx = Context(enable_v3plus_async=True)
    tools = await get_tools(ctx)

    tool_names = [tool.name for tool in tools]

    # Verify base tools exist
    assert "call_planner" in tool_names
    assert "call_executor" in tool_names

    # Verify async tools exist
    assert "call_executor_async" in tool_names
    assert "get_executor_status" in tool_names
    assert "cancel_executor" in tool_names

    print(f"\n[OK] Total {len(tools)} tools registered")
    print(f"[OK] Async tools available: call_executor_async, get_executor_status, cancel_executor")


@pytest.mark.skipif(not _has_api_keys(), reason="No API keys configured")
async def test_v3plus_async_not_available_when_disabled(monkeypatch, tmp_path) -> None:
    """Verify async tools are NOT available when V3+ is disabled."""
    monkeypatch.setenv("ENABLE_V3PLUS_ASYNC", "false")
    monkeypatch.setenv("AGENT_WORKSPACE_DIR", str(tmp_path))

    ctx = Context(enable_v3plus_async=False)
    tools = await get_tools(ctx)

    tool_names = [tool.name for tool in tools]

    # Verify base tools exist
    assert "call_planner" in tool_names
    assert "call_executor" in tool_names

    # Verify async tools do NOT exist
    assert "call_executor_async" not in tool_names
    assert "get_executor_status" not in tool_names
    assert "cancel_executor" not in tool_names

    print(f"\n[OK] Total {len(tools)} tools registered (V3+ async tools disabled)")


@pytest.mark.skipif(not _has_api_keys(), reason="No API keys configured")
@pytest.mark.timeout(180)
async def test_v3plus_supervisor_basic_query(monkeypatch, tmp_path) -> None:
    """Test Supervisor can handle basic queries in async mode."""
    monkeypatch.setenv("ENABLE_V3PLUS_ASYNC", "true")
    monkeypatch.setenv("AGENT_WORKSPACE_DIR", str(tmp_path))

    ctx = Context(
        enable_v3plus_async=True,
        max_executor_iterations=5,
        max_replan=1,
    )

    # Simple question that should be answered directly (Mode 1)
    question = "What is Python? Answer in one sentence."

    start_time = time.time()
    result = await graph.ainvoke(
        {"messages": [HumanMessage(content=question)]},
        context=ctx,
    )
    elapsed = time.time() - start_time

    # Verify response
    messages = result["messages"]
    final_ai = next(
        (m for m in reversed(messages) if isinstance(m, AIMessage) and not m.tool_calls),
        None,
    )
    assert final_ai is not None, "Should have a final AI response"
    assert final_ai.content, "Response should not be empty"

    print(f"\n[OK] Response time: {elapsed:.2f} seconds")
    print(f"[OK] Response: {final_ai.content[:150]}...")


@pytest.mark.skipif(not _has_api_keys(), reason="No API keys configured")
@pytest.mark.timeout(120)
async def test_v3plus_multiple_sequential_requests(monkeypatch, tmp_path) -> None:
    """Test Supervisor can handle multiple sequential requests in async mode."""
    monkeypatch.setenv("ENABLE_V3PLUS_ASYNC", "true")
    monkeypatch.setenv("AGENT_WORKSPACE_DIR", str(tmp_path))

    ctx = Context(
        enable_v3plus_async=True,
        max_executor_iterations=5,
        max_replan=1,
    )

    # Send multiple sequential requests
    requests = [
        "Hello",
        "What is the weather today?",
        "Tell me about Python",
    ]

    responses = []
    for req in requests:
        result = await graph.ainvoke(
            {"messages": [HumanMessage(content=req)]},
            context=ctx,
        )

        messages = result["messages"]
        final_ai = next(
            (m for m in reversed(messages) if isinstance(m, AIMessage) and not m.tool_calls),
            None,
        )
        if final_ai:
            responses.append(final_ai.content)

    # Verify each request got a response
    assert len(responses) == len(requests), "All requests should receive responses"

    print(f"\n[OK] Successfully processed {len(responses)} sequential requests")
    for i, (req, resp) in enumerate(zip(requests, responses), 1):
        print(f"  {i}. Q: {req[:30]}")
        print(f"     A: {resp[:50]}...")
