"""消息截断函数 _trim_messages_for_llm 测试。

验证消息历史截断逻辑：
- 不截断短历史
- 截断长历史
- 保持工具调用序列完整性
- 不切断 AI tool_calls → ToolMessages 的配对
"""

import uuid

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from src.supervisor_agent.graph import _trim_messages_for_llm


def _tool_call_id() -> str:
    return f"call_{uuid.uuid4().hex[:8]}"


def _make_ai_with_tool_calls(tool_ids: list[str]) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[
            {"id": tid, "name": "call_executor", "args": {}, "type": "tool_call"}
            for tid in tool_ids
        ],
    )


def _make_tool_message(tool_call_id: str, content: str = "result") -> ToolMessage:
    return ToolMessage(content=content, tool_call_id=tool_call_id, name="call_executor")


class TestTrimMessagesNoTruncation:
    """短历史不应截断。"""

    def test_zero_max_returns_all(self):
        msgs = [HumanMessage(content="hi"), AIMessage(content="hello")]
        assert _trim_messages_for_llm(msgs, 0) is msgs

    def test_negative_max_returns_all(self):
        msgs = [HumanMessage(content="hi")]
        assert _trim_messages_for_llm(msgs, -1) is msgs

    def test_under_limit_returns_all(self):
        msgs = [HumanMessage(content="hi"), AIMessage(content="hello")]
        assert _trim_messages_for_llm(msgs, 10) is msgs

    def test_exact_limit_returns_all(self):
        msgs = [HumanMessage(content="hi"), AIMessage(content="hello")]
        assert _trim_messages_for_llm(msgs, 2) is msgs


class TestTrimMessagesBasicTruncation:
    """基本截断：保留最后 N 条。"""

    def test_truncates_to_last_n(self):
        msgs = [
            HumanMessage(content=f"msg{i}") for i in range(10)
        ]
        result = _trim_messages_for_llm(msgs, 5)
        assert len(result) == 5
        assert result[0].content == "msg5"
        assert result[-1].content == "msg9"

    def test_preserves_conversation_order(self):
        msgs = [
            HumanMessage(content="user1"),
            AIMessage(content="ai1"),
            HumanMessage(content="user2"),
            AIMessage(content="ai2"),
            HumanMessage(content="user3"),
        ]
        result = _trim_messages_for_llm(msgs, 3)
        assert len(result) == 3
        # msgs = [user1, ai1, user2, ai2, user3], keep last 3 = [user2, ai2, user3]
        assert result[0].content == "user2"
        assert result[1].content == "ai2"
        assert result[2].content == "user3"


class TestTrimMessagesToolCallIntegrity:
    """工具调用序列完整性。"""

    def test_orphaned_tool_messages_at_start_are_dropped(self):
        """如果截断后开头是孤立的 ToolMessage（无对应 AI），应丢弃。"""
        tc_id = _tool_call_id()
        msgs = [
            # This AI message with tool_calls will be trimmed away
            _make_ai_with_tool_calls([tc_id]),
            _make_tool_message(tc_id, "result1"),
            # These should be kept
            HumanMessage(content="user2"),
            AIMessage(content="ai2"),
        ]
        result = _trim_messages_for_llm(msgs, 3)
        # Last 3 = [tool_msg, user2, ai2]
        # ToolMessage is orphaned (no AI with tool_calls in result) → should be dropped
        assert len(result) <= 3
        # First message should not be an orphaned ToolMessage
        if result and isinstance(result[0], ToolMessage):
            # If it remains, it must have a corresponding AI message
            found_ai = any(
                isinstance(m, AIMessage) and getattr(m, "tool_calls", None)
                for m in result
            )
            assert found_ai, "Orphaned ToolMessage without corresponding AI message"

    def test_complete_tool_sequence_preserved(self):
        """完整的工具调用序列应被保留。"""
        tc_id1 = _tool_call_id()
        tc_id2 = _tool_call_id()
        msgs = [
            HumanMessage(content="user1"),
            _make_ai_with_tool_calls([tc_id1, tc_id2]),
            _make_tool_message(tc_id1, "result1"),
            _make_tool_message(tc_id2, "result2"),
            HumanMessage(content="user2"),
            AIMessage(content="final answer"),
        ]
        result = _trim_messages_for_llm(msgs, 6)
        # All 6 fit within limit → no truncation
        assert len(result) == 6

    def test_tool_sequence_at_end_preserved(self):
        """末尾的工具调用序列不应被截断。"""
        tc_id = _tool_call_id()
        msgs = [
            HumanMessage(content=f"msg{i}") for i in range(10)
        ] + [
            _make_ai_with_tool_calls([tc_id]),
            _make_tool_message(tc_id, "result"),
        ]
        result = _trim_messages_for_llm(msgs, 4)
        # Last 4: [msg8, msg9, ai_with_tools, tool_result]
        assert len(result) == 4
        # AI message with tool_calls should be present
        ai_msgs = [m for m in result if isinstance(m, AIMessage) and getattr(m, "tool_calls", None)]
        assert len(ai_msgs) >= 1
        # Corresponding ToolMessage should be present
        tool_msgs = [m for m in result if isinstance(m, ToolMessage)]
        assert len(tool_msgs) >= 1


class TestTrimMessagesEdgeCases:
    """边界条件。"""

    def test_empty_messages(self):
        result = _trim_messages_for_llm([], 5)
        assert result == []

    def test_single_message(self):
        msgs = [HumanMessage(content="hi")]
        result = _trim_messages_for_llm(msgs, 1)
        assert result is msgs

    def test_only_tool_messages(self):
        """全部是 ToolMessage 时不应崩溃。"""
        tc_ids = [_tool_call_id() for _ in range(5)]
        msgs = [_make_tool_message(tid) for tid in tc_ids]
        result = _trim_messages_for_llm(msgs, 3)
        assert len(result) <= 3
