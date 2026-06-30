"""Unit tests for supervisor mode discipline — strip redundant tool_calls."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from src.supervisor_agent.graph import _looks_like_final_answer, call_model
from src.supervisor_agent.state import State


def _make_mock_llm(response: AIMessage) -> MagicMock:
    mock = MagicMock()
    mock.bind_tools = MagicMock(return_value=mock)
    mock.ainvoke = AsyncMock(return_value=response)
    return mock


# Real probe sample s002-t5 (P0 真实样本) — full markdown answer + redundant call_executor
_S002_T5_CONTENT = (
    "## 项目 timeout 配置查找结果\n\n"
    "根据已有的知识树记录和当前尝试，情况如下：\n\n"
    "### 已知发现\n\n"
    "| 检查位置 | 结果 |\n|---------|------|\n| `.env.example` | 不存在 |"
)


@pytest.mark.parametrize(
    "content,tool_calls,expected",
    [
        # 1. 真实 P0 样本 s002-t5：完整答案 + 冗余 call_executor → True（strip 生效）
        (
            _S002_T5_CONTENT,
            [{"name": "call_executor", "args": {}, "id": "1", "type": "tool_call"}],
            True,
        ),
        # 2. good mode-1 structured answer + no tool_calls → True（strip 是 no-op）
        (
            "## 1. 触发层\n\n执行器遇阻即停的机制包括三个层次的设计。每一层负责不同的检测和响应职责，"
            "确保工具调用失败时能及时停止而不是死循环。\n\n## 2. 实现层\n\n具体实现涉及多个模块协作。",
            [],
            True,
        ),
        # 3. 长度不足 → False（短确认不应触发 strip）
        (
            "好的，马上处理。",
            [{"name": "call_executor", "args": {}, "id": "1", "type": "tool_call"}],
            False,
        ),
        # 4. 无 markdown 结构 → False（纯文本再长也不像最终答案）
        (
            "x" * 200,
            [{"name": "call_executor", "args": {}, "id": "1", "type": "tool_call"}],
            False,
        ),
        # 5. 过程性措辞 → False（合理 ReAct 中间轮保护）
        (
            "## 验证计划\n\n接下来调用 Executor 验证当前假设的准确性。基于已有数据，"
            "需要确认几个关键状态变量的实际值，然后才能继续后续的分析和判断。\n\n1. 第一步\n2. 第二步",
            [{"name": "call_executor", "args": {}, "id": "1", "type": "tool_call"}],
            False,
        ),
        # 6. call_planner 短路 → False（mode-3 多步任务保护）
        (
            "## 多步执行计划\n\n本任务涉及多个数据源的查询与处理，需要分阶段完成。具体安排如下：\n\n"
            "1. 先查询用户数据并提取关键字段\n2. 然后处理数据并应用业务规则\n3. 最后生成统计报告",
            [{"name": "call_planner", "args": {}, "id": "1", "type": "tool_call"}],
            False,
        ),
        # 7. [STALE] 标记 → False（缓存内容不算最终答案）
        (
            "[STALE]\n## 旧内容\n这是上一轮缓存的过期响应，包含了一些已经失效的信息和过时结论。"
            "不应作为最终答案输出。请重新生成新的响应内容，避免使用任何缓存数据。",
            [{"name": "call_executor", "args": {}, "id": "1", "type": "tool_call"}],
            False,
        ),
        # 8. [EXECUTOR_RESULT] 内部标记 → False（工具中间产物不算 Supervisor 答案）
        (
            "[EXECUTOR_RESULT]\n## 执行结果\n工具返回的结构化数据，包含 Executor 子任务的中间状态。"
            "这些内容是工具层面的中间产物，不应被当作 Supervisor 的最终答复输出。",
            [{"name": "call_executor", "args": {}, "id": "1", "type": "tool_call"}],
            False,
        ),
    ],
)
def test_looks_like_final_answer(content: str, tool_calls: list, expected: bool) -> None:
    assert _looks_like_final_answer(content, tool_calls) is expected


# ---------------------------------------------------------------------------
# call_model integration: strip 真实生效
# ---------------------------------------------------------------------------

async def test_call_model_strips_redundant_tool_calls_when_content_complete(make_runtime) -> None:
    state = State(messages=[HumanMessage(content="查找 timeout 配置")])
    runtime = make_runtime()
    tool_response = AIMessage(
        content=_S002_T5_CONTENT,
        tool_calls=[{"name": "call_executor", "args": {}, "id": "c1", "type": "tool_call"}],
    )
    mock_llm = _make_mock_llm(tool_response)

    with patch("src.supervisor_agent.graph.load_chat_model", return_value=mock_llm):
        result = await call_model(state, runtime)

    assert result["messages"][0].tool_calls == []
    assert result["supervisor_decision"].mode == 1
    assert "## 项目 timeout 配置查找结果" in str(result["messages"][0].content)


# ---------------------------------------------------------------------------
# Rollback: env=0 disables strip
# ---------------------------------------------------------------------------

async def test_call_model_does_not_strip_when_env_disabled(make_runtime, monkeypatch) -> None:
    monkeypatch.setenv("SUPERVISOR_STRIP_REDUNDANT_TOOL_CALLS", "0")
    state = State(messages=[HumanMessage(content="查找 timeout 配置")])
    runtime = make_runtime()
    tool_response = AIMessage(
        content=_S002_T5_CONTENT,
        tool_calls=[{"name": "call_executor", "args": {}, "id": "c1", "type": "tool_call"}],
    )
    mock_llm = _make_mock_llm(tool_response)

    with patch("src.supervisor_agent.graph.load_chat_model", return_value=mock_llm):
        result = await call_model(state, runtime)

    assert len(result["messages"][0].tool_calls) == 1
    assert result["messages"][0].tool_calls[0]["name"] == "call_executor"
    assert result["supervisor_decision"].mode == 2
