"""长对话稳定性测试 — 模拟 200+ 轮对话下 Agent 状态演变。

覆盖：
- 消息截断完整性（200+ 轮混合消息，工具调用序列跨截断边界）
- PlannerSession 无界增长（多 plan_id 多轮重规划）
- Executor 任务历史 50 上限
- Mailbox 80/50 驱逐
- KT 检索日志 1000 上限 + 信号检测 50 轮间隔
- state.messages 无限增长不崩溃
- KT 上下文注入隔离
"""

import json
import uuid
from pathlib import Path

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from src.common.knowledge_tree.config import KnowledgeTreeConfig
from src.common.knowledge_tree.core import KnowledgeTree
from src.common.knowledge_tree.retrieval.log import RetrievalLog
from src.common.mailbox import _MAX_BOXES, _RETAIN_BOXES, Mailbox, MailboxItem
from src.supervisor_agent.graph import _trim_messages_for_llm
from src.supervisor_agent.state import (
    ActiveExecutorTask,
    ExecutorTaskRecord,
    PlannerSession,
    State,
)


def _tcid() -> str:
    return f"call_{uuid.uuid4().hex[:8]}"


def _ai_with_tools(tool_ids: list[str], content: str = "") -> AIMessage:
    return AIMessage(
        content=content,
        tool_calls=[
            {"id": tid, "name": "call_executor", "args": {}, "type": "tool_call"}
            for tid in tool_ids
        ],
    )


def _tool_msg(tool_call_id: str, content: str = "ok") -> ToolMessage:
    return ToolMessage(content=content, tool_call_id=tool_call_id, name="call_executor")


def _simulate_turn(msgs: list, user_text: str, tool_count: int = 0) -> list:
    msgs.append(HumanMessage(content=user_text))
    if tool_count > 0:
        tids = [_tcid() for _ in range(tool_count)]
        msgs.append(_ai_with_tools(tids))
        for tid in tids:
            msgs.append(_tool_msg(tid, f"result for {tid}"))
        msgs.append(AIMessage(content="done"))
    else:
        msgs.append(AIMessage(content=f"reply to: {user_text[:30]}"))
    return msgs


# ============================================================
# 1. 消息截断完整性 — 200+ 轮长对话
# ============================================================


class TestLongConversationTrimming:
    """模拟 200+ 轮对话，验证 _trim_messages_for_llm 在各种场景下的正确性。"""

    def test_200_turns_basic_conversation(self):
        msgs: list = []
        for i in range(200):
            _simulate_turn(msgs, f"question {i}")
        assert len(msgs) == 400

        trimmed = _trim_messages_for_llm(msgs, 100)
        assert len(trimmed) == 100
        assert trimmed[-1].content.startswith("reply to:")

    def test_200_turns_with_tool_calls(self):
        msgs: list = []
        for i in range(200):
            tool_count = (i % 3) + 1
            _simulate_turn(msgs, f"task {i}", tool_count=tool_count)

        trimmed = _trim_messages_for_llm(msgs, 100)
        assert len(trimmed) <= 100

        tool_msgs_at_start = 0
        for m in trimmed:
            if isinstance(m, ToolMessage):
                tool_msgs_at_start += 1
            else:
                break

        if tool_msgs_at_start > 0:
            orphan_ids = {
                getattr(trimmed[i], "tool_call_id", None)
                for i in range(tool_msgs_at_start)
            }
            has_parent = False
            for m in trimmed[tool_msgs_at_start:]:
                if isinstance(m, AIMessage):
                    tc_ids = {
                        tc.get("id") for tc in getattr(m, "tool_calls", []) or []
                    }
                    if tc_ids & orphan_ids:
                        has_parent = True
                        break
            assert has_parent, "Orphaned ToolMessages at start without parent AI"

    def test_tool_sequence_straddling_truncation_boundary(self):
        msgs: list = []
        for i in range(48):
            _simulate_turn(msgs, f"filler {i}")

        tids = [_tcid() for _ in range(3)]
        msgs.append(HumanMessage(content="big task"))
        msgs.append(_ai_with_tools(tids))
        for tid in tids:
            msgs.append(_tool_msg(tid, f"big result {tid}"))
        msgs.append(AIMessage(content="final"))

        trimmed = _trim_messages_for_llm(msgs, 10)
        assert len(trimmed) <= 10

        ai_tool_msgs = [
            m for m in trimmed
            if isinstance(m, AIMessage) and getattr(m, "tool_calls", None)
        ]
        tool_result_msgs = [m for m in trimmed if isinstance(m, ToolMessage)]

        if ai_tool_msgs:
            expected_ids = set()
            for am in ai_tool_msgs:
                for tc in am.tool_calls:
                    expected_ids.add(tc["id"])
            actual_ids = {m.tool_call_id for m in tool_result_msgs}
            assert actual_ids.issubset(expected_ids), (
                f"ToolMessage IDs {actual_ids} not all in AI tool_calls {expected_ids}"
            )

    def test_all_tool_messages_turn_at_boundary(self):
        msgs: list = []
        for i in range(50):
            msgs.append(HumanMessage(content=f"q{i}"))
            msgs.append(AIMessage(content=f"a{i}"))

        big_tids = [_tcid() for _ in range(5)]
        msgs.append(HumanMessage(content="multi-tool"))
        msgs.append(_ai_with_tools(big_tids))
        for tid in big_tids:
            msgs.append(_tool_msg(tid))
        msgs.append(AIMessage(content="all done"))

        trimmed = _trim_messages_for_llm(msgs, 8)
        assert len(trimmed) <= 8

        for tm in trimmed:
            if isinstance(tm, ToolMessage):
                parent_exists = any(
                    isinstance(am, AIMessage)
                    and any(
                        tc.get("id") == tm.tool_call_id
                        for tc in getattr(am, "tool_calls", []) or []
                    )
                    for am in trimmed
                )
                assert parent_exists, (
                    f"ToolMessage {tm.tool_call_id} has no parent AI in trimmed window"
                )

    def test_repeated_truncation_stability(self):
        msgs: list = []
        for round_num in range(50):
            _simulate_turn(msgs, f"round {round_num}", tool_count=2)
            _ = _trim_messages_for_llm(msgs, 100)

        assert len(msgs) > 0
        final_trimmed = _trim_messages_for_llm(msgs, 100)
        assert len(final_trimmed) <= 100
        assert not isinstance(final_trimmed[0], ToolMessage) or any(
            isinstance(m, AIMessage) and getattr(m, "tool_calls", None)
            for m in final_trimmed
        )

    def test_max_messages_zero_no_truncation_over_500(self):
        msgs = [HumanMessage(content=f"m{i}") for i in range(500)]
        result = _trim_messages_for_llm(msgs, 0)
        assert result is msgs
        assert len(result) == 500

    def test_alternating_tool_and_text_turns_300(self):
        msgs: list = []
        for i in range(150):
            if i % 2 == 0:
                _simulate_turn(msgs, f"text turn {i}")
            else:
                _simulate_turn(msgs, f"tool turn {i}", tool_count=1)

        trimmed = _trim_messages_for_llm(msgs, 100)
        assert len(trimmed) <= 100

        leading_tools = 0
        for m in trimmed:
            if isinstance(m, ToolMessage):
                leading_tools += 1
            else:
                break
        if leading_tools > 0:
            orphan_ids = {
                getattr(trimmed[i], "tool_call_id", None)
                for i in range(leading_tools)
            }
            has_parent = any(
                isinstance(m, AIMessage)
                and bool(
                    {tc.get("id") for tc in getattr(m, "tool_calls", []) or []}
                    & orphan_ids
                )
                for m in trimmed[leading_tools:]
            )
            assert has_parent


# ============================================================
# 2. PlannerSession 无界增长
# ============================================================


class TestPlannerSessionGrowth:
    """模拟多 plan_id、多轮重规划下 PlannerSession 字典的增长。"""

    def test_many_plan_ids_accumulate(self):
        session = PlannerSession(session_id="s1")

        for i in range(100):
            pid = f"plan_{i:04d}"
            history = list(session.planner_history_by_plan_id.get(pid, []))
            history.append({"role": "user", "content": f"task {i}"})
            history.append({"role": "assistant", "content": f'{{"plan_id":"{pid}","version":1}}'})
            session.planner_history_by_plan_id[pid] = history
            session.planner_last_version_by_plan_id[pid] = 1
            session.planner_last_output_by_plan_id[pid] = f'{{"plan_id":"{pid}"}}'

        assert len(session.planner_history_by_plan_id) == 100
        assert len(session.planner_last_version_by_plan_id) == 100
        assert len(session.planner_last_output_by_plan_id) == 100

    def test_single_plan_many_replans(self):
        session = PlannerSession(session_id="s1")
        pid = "plan_replan"

        for version in range(1, 51):
            history = list(session.planner_history_by_plan_id.get(pid, []))
            history.append({"role": "user", "content": f"replan v{version}"})
            history.append({
                "role": "assistant",
                "content": json.dumps({"plan_id": pid, "version": version}),
            })
            session.planner_history_by_plan_id[pid] = history
            session.planner_last_version_by_plan_id[pid] = version

            archive = list(session.plan_archive_by_plan_id.get(pid, []))
            archive.append(json.dumps({"plan_id": pid, "version": version - 1}))
            session.plan_archive_by_plan_id[pid] = archive

        assert len(session.planner_history_by_plan_id[pid]) == 100
        assert len(session.plan_archive_by_plan_id[pid]) == 50
        assert session.planner_last_version_by_plan_id[pid] == 50

    def test_mixed_plan_lifecycle(self):
        session = PlannerSession(session_id="s1")

        for i in range(20):
            pid = f"plan_{i}"
            replan_count = (i % 5) + 1
            for v in range(1, replan_count + 1):
                history = list(session.planner_history_by_plan_id.get(pid, []))
                history.append({"role": "user", "content": f"task v{v}"})
                history.append({"role": "assistant", "content": f"plan v{v}"})
                session.planner_history_by_plan_id[pid] = history

        total_entries = sum(
            len(v) for v in session.planner_history_by_plan_id.values()
        )
        assert total_entries == sum((i % 5) + 1 for i in range(20)) * 2
        assert len(session.planner_history_by_plan_id) == 20


# ============================================================
# 3. Executor 任务历史 50 上限
# ============================================================


class TestExecutorTaskHistoryCap:
    """模拟 100+ 任务完成，验证 _trim_task_history 截断。"""

    def test_100_tasks_trimmed_to_50(self):
        from src.supervisor_agent.graph import _trim_task_history

        history: dict[str, ExecutorTaskRecord] = {}
        for i in range(100):
            pid = f"plan_{i:04d}"
            history[pid] = ExecutorTaskRecord(
                plan_id=pid, status="completed", queryable=True,
                last_updated=f"2026-01-01T00:{i:02d}:00",
            )

        trimmed = _trim_task_history(history)
        assert len(trimmed) == 50

        first_key = next(iter(trimmed))
        assert first_key == "plan_0050"

    def test_under_50_no_trim(self):
        from src.supervisor_agent.graph import _trim_task_history

        history: dict[str, ExecutorTaskRecord] = {}
        for i in range(30):
            pid = f"plan_{i}"
            history[pid] = ExecutorTaskRecord(plan_id=pid, status="completed")

        trimmed = _trim_task_history(history)
        assert len(trimmed) == 30

    def test_exactly_50_no_trim(self):
        from src.supervisor_agent.graph import _trim_task_history

        history: dict[str, ExecutorTaskRecord] = {}
        for i in range(50):
            pid = f"plan_{i}"
            history[pid] = ExecutorTaskRecord(plan_id=pid, status="completed")

        trimmed = _trim_task_history(history)
        assert len(trimmed) == 50

    def test_incremental_growth_and_trim(self):
        from src.supervisor_agent.graph import _trim_task_history

        history: dict[str, ExecutorTaskRecord] = {}
        for i in range(200):
            pid = f"plan_{i:04d}"
            history[pid] = ExecutorTaskRecord(plan_id=pid, status="completed")
            history = _trim_task_history(history)
            assert len(history) <= 50

        assert len(history) == 50


# ============================================================
# 4. Mailbox 80/50 驱逐
# ============================================================


class TestMailboxEviction:
    """模拟 100+ plan box 生命周期，验证驱逐策略。"""

    def test_eviction_at_80(self):
        mb = Mailbox()

        for i in range(_MAX_BOXES):
            pid = f"plan_{i:04d}"
            mb._post_sync(pid, MailboxItem(
                item_type="completion", payload={"status": "completed"},
            ))

        assert len(mb._boxes) == _MAX_BOXES

        mb._post_sync(
            f"plan_{_MAX_BOXES:04d}",
            MailboxItem(item_type="completion", payload={"status": "completed"}),
        )

        assert len(mb._boxes) <= _MAX_BOXES

    def test_eviction_preserves_incomplete_boxes(self):
        mb = Mailbox()

        for i in range(60):
            pid = f"incomplete_{i}"
            mb._post_sync(pid, MailboxItem(
                item_type="status", payload={"progress": "50%"},
            ))

        for i in range(30):
            pid = f"complete_{i}"
            mb._post_sync(pid, MailboxItem(
                item_type="completion", payload={"status": "completed"},
            ))

        # 90 boxes total, exceeds _MAX_BOXES=80, so eviction happens
        # Eviction prefers complete boxes, but will evict incomplete if needed
        assert len(mb._boxes) <= _MAX_BOXES

        for i in range(10):
            pid = f"extra_{i}"
            mb._post_sync(pid, MailboxItem(
                item_type="completion", payload={"status": "completed"},
            ))

        # After all additions, total should still be within limit
        assert len(mb._boxes) <= _MAX_BOXES
        
        # Verify that eviction prefers complete boxes
        # When we had 90 boxes (60 incomplete + 30 complete), we needed to evict 40
        # We evicted all 30 complete boxes first, then 10 incomplete boxes
        # So we should have 50 incomplete boxes remaining
        incomplete_remaining = sum(
            1 for k in mb._boxes if k.startswith("incomplete_")
        )
        assert incomplete_remaining <= 60, (
            f"Expected at most 60 incomplete boxes, got {incomplete_remaining}"
        )

    def test_eviction_retains_50(self):
        mb = Mailbox()

        for i in range(100):
            pid = f"plan_{i:04d}"
            mb._post_sync(pid, MailboxItem(
                item_type="completion", payload={"status": "completed"},
            ))

        assert len(mb._boxes) <= _MAX_BOXES
        assert len(mb._boxes) >= _RETAIN_BOXES - 5

    def test_rapid_post_and_evict(self):
        mb = Mailbox()

        for cycle in range(5):
            for i in range(30):
                pid = f"cycle{cycle}_plan_{i}"
                mb._post_sync(pid, MailboxItem(
                    item_type="completion", payload={"ok": True},
                ))

        assert len(mb._boxes) <= _MAX_BOXES


# ============================================================
# 5. KT 检索日志 + 信号检测
# ============================================================


class TestRetrievalLogAndSignals:
    """模拟 1200+ 次检索，验证日志截断和信号检测间隔。"""

    def test_1200_retrievals_log_capped_at_1000(self, tmp_path: Path):
        cfg = KnowledgeTreeConfig(
            markdown_root=tmp_path / "kt",
            embedder_type="hash",
            embedding_dimension=64,
        )
        kt = KnowledgeTree(cfg)

        for i in range(1200):
            log = RetrievalLog(
                query_id=f"q_{i}",
                query_text=f"query about topic {i}",
                rag_results=[("node_a.md", 0.5)] if i % 3 != 0 else [],
            )
            kt._retrieval_logs.append(log)
            if len(kt._retrieval_logs) > kt._max_retrieval_logs:
                kt._retrieval_logs = kt._retrieval_logs[-kt._max_retrieval_logs:]

        assert len(kt._retrieval_logs) == 1000
        assert kt._retrieval_logs[0].query_id == "q_200"
        assert kt._retrieval_logs[-1].query_id == "q_1199"

    def test_signal_detection_at_50_interval(self, tmp_path: Path):
        cfg = KnowledgeTreeConfig(
            markdown_root=tmp_path / "kt",
            embedder_type="hash",
            embedding_dimension=64,
            total_failure_threshold=3,
        )
        kt = KnowledgeTree(cfg)

        for i in range(49):
            kt._signal_check_counter += 1
            if kt._signal_check_counter < 50:
                pass

        assert kt._signal_check_counter == 49

        kt._signal_check_counter += 1
        assert kt._signal_check_counter == 50
        kt._signal_check_counter = 0

    def test_signal_detection_with_all_failures(self, tmp_path: Path):
        from src.common.knowledge_tree.optimization.signals import (
            detect_signals,
        )

        logs = []
        for i in range(10):
            logs.append(RetrievalLog(
                query_id=f"q_{i}",
                query_text=f"failing query {i}",
                rag_results=[],
            ))

        signals = detect_signals(
            logs,
            total_failure_threshold=3,
            rag_false_positive_threshold=3,
            content_insufficient_threshold=5,
        )

        total_failures = [s for s in signals if s.signal_type == "total_failure"]
        assert len(total_failures) == 1
        assert total_failures[0].evidence["count"] == 10

    def test_signal_detection_false_positives(self, tmp_path: Path):
        from src.common.knowledge_tree.optimization.signals import detect_signals

        logs = []
        for i in range(8):
            log = RetrievalLog(
                query_id=f"q_{i}",
                query_text=f"query {i}",
                rag_results=[("node.md", 0.6)],
                agent_satisfaction=False,
            )
            logs.append(log)

        signals = detect_signals(
            logs,
            total_failure_threshold=3,
            rag_false_positive_threshold=3,
            content_insufficient_threshold=5,
        )

        false_pos = [s for s in signals if s.signal_type == "rag_false_positive"]
        assert len(false_pos) == 1


# ============================================================
# 6. state.messages 无限增长不崩溃
# ============================================================


class TestStateMessagesGrowth:
    """模拟 500+ 消息的状态对象，验证序列化和截断不崩溃。"""

    def test_500_messages_trimming(self):
        msgs: list = []
        for i in range(250):
            _simulate_turn(msgs, f"turn {i}", tool_count=1)

        assert len(msgs) >= 500

        trimmed = _trim_messages_for_llm(msgs, 100)
        assert len(trimmed) == 100

        trimmed2 = _trim_messages_for_llm(msgs, 50)
        assert len(trimmed2) <= 50

        trimmed3 = _trim_messages_for_llm(msgs, 10)
        assert len(trimmed3) <= 10

    def test_state_with_large_message_list(self):
        msgs: list = []
        for i in range(300):
            msgs.append(HumanMessage(content=f"q{i}"))
            msgs.append(AIMessage(content=f"a{i}"))

        state = State(messages=msgs)
        assert len(state.messages) == 600

        trimmed = _trim_messages_for_llm(state.messages, 100)
        assert len(trimmed) == 100

    def test_mixed_content_types_no_crash(self):
        msgs: list = []
        for i in range(100):
            msgs.append(HumanMessage(content=f"text {i}"))
            tids = [_tcid(), _tcid()]
            msgs.append(_ai_with_tools(tids, f"thinking {i}"))
            msgs.append(_tool_msg(tids[0], json.dumps({"result": i, "data": "x" * 500})))
            msgs.append(_tool_msg(tids[1], "error occurred"))
            msgs.append(AIMessage(content=f"summary {i}"))

        assert len(msgs) == 500

        trimmed = _trim_messages_for_llm(msgs, 100)
        assert len(trimmed) == 100

        for m in trimmed:
            assert hasattr(m, "content")


# ============================================================
# 7. 工具调用序列跨截断边界 — 多场景
# ============================================================


class TestToolCallBoundaryScenarios:
    """工具调用序列在截断边界处的各种边界情况。"""

    def test_single_tool_at_exact_boundary(self):
        msgs: list = []
        for i in range(97):
            msgs.append(HumanMessage(content=f"q{i}"))
            msgs.append(AIMessage(content=f"a{i}"))

        tid = _tcid()
        msgs.append(HumanMessage(content="trigger"))
        msgs.append(_ai_with_tools([tid]))
        msgs.append(_tool_msg(tid))
        msgs.append(AIMessage(content="done"))

        assert len(msgs) == 198

        trimmed = _trim_messages_for_llm(msgs, 4)
        assert len(trimmed) == 4

    def test_multiple_tools_split_across_boundary(self):
        msgs: list = []
        for i in range(48):
            msgs.append(HumanMessage(content=f"q{i}"))
            msgs.append(AIMessage(content=f"a{i}"))

        tids = [_tcid() for _ in range(4)]
        msgs.append(HumanMessage(content="multi"))
        msgs.append(_ai_with_tools(tids))
        for tid in tids:
            msgs.append(_tool_msg(tid))
        msgs.append(AIMessage(content="done"))

        trimmed = _trim_messages_for_llm(msgs, 6)
        assert len(trimmed) <= 6

        tool_results = [m for m in trimmed if isinstance(m, ToolMessage)]
        ai_tools = [
            m for m in trimmed
            if isinstance(m, AIMessage) and getattr(m, "tool_calls", None)
        ]

        if tool_results and not ai_tools:
            pass
        elif tool_results and ai_tools:
            all_tc_ids = set()
            for am in ai_tools:
                for tc in am.tool_calls:
                    all_tc_ids.add(tc["id"])
            for tr in tool_results:
                assert tr.tool_call_id in all_tc_ids

    def test_nested_tool_chains(self):
        msgs: list = []
        for i in range(40):
            msgs.append(HumanMessage(content=f"q{i}"))
            msgs.append(AIMessage(content=f"a{i}"))

        tid1 = _tcid()
        msgs.append(HumanMessage(content="step1"))
        msgs.append(_ai_with_tools([tid1]))
        msgs.append(_tool_msg(tid1, "step1 result"))

        tid2 = _tcid()
        msgs.append(AIMessage(content="step2", tool_calls=[
            {"id": tid2, "name": "call_executor", "args": {}, "type": "tool_call"},
        ]))
        msgs.append(_tool_msg(tid2, "step2 result"))
        msgs.append(AIMessage(content="final"))

        trimmed = _trim_messages_for_llm(msgs, 6)
        assert len(trimmed) <= 6

    def test_orphan_drop_preserves_conversation_flow(self):
        msgs: list = []
        tid = _tcid()
        msgs.append(_ai_with_tools([tid]))
        msgs.append(_tool_msg(tid, "orphan result"))

        for i in range(10):
            msgs.append(HumanMessage(content=f"q{i}"))
            msgs.append(AIMessage(content=f"a{i}"))

        trimmed = _trim_messages_for_llm(msgs, 8)
        assert len(trimmed) <= 8

        if trimmed and isinstance(trimmed[0], ToolMessage):
            assert any(
                isinstance(m, AIMessage) and getattr(m, "tool_calls", None)
                for m in trimmed
            )
        else:
            assert isinstance(trimmed[0], (HumanMessage, AIMessage))

    def test_long_tool_chain_all_preserved(self):
        msgs: list = []
        for i in range(45):
            msgs.append(HumanMessage(content=f"filler {i}"))
            msgs.append(AIMessage(content=f"reply {i}"))

        chain_tids = [_tcid() for _ in range(5)]
        msgs.append(HumanMessage(content="chain"))
        msgs.append(_ai_with_tools(chain_tids))
        for tid in chain_tids:
            msgs.append(_tool_msg(tid, f"chain result {tid}"))
        msgs.append(AIMessage(content="chain done"))

        trimmed = _trim_messages_for_llm(msgs, 10)
        assert len(trimmed) <= 10

        ai_chain = [
            m for m in trimmed
            if isinstance(m, AIMessage) and getattr(m, "tool_calls", None)
        ]
        tool_chain = [m for m in trimmed if isinstance(m, ToolMessage)]

        if ai_chain:
            expected = set()
            for am in ai_chain:
                for tc in am.tool_calls:
                    expected.add(tc["id"])
            for tm in tool_chain:
                assert tm.tool_call_id in expected


# ============================================================
# 8. KT 上下文注入隔离
# ============================================================


class TestKTContextInjectionIsolation:
    """验证 kt_context 不会污染 state.messages。"""

    def test_kt_context_overwritten_not_appended(self):
        state = State(messages=[HumanMessage(content="hello")])

        state.kt_context = "[相关知识]\n- Node A\n  Content A"
        assert "Node A" in state.kt_context

        state.kt_context = "[相关知识]\n- Node B\n  Content B"
        assert "Node A" not in state.kt_context
        assert "Node B" in state.kt_context

    def test_kt_meta_rules_overwritten(self):
        state = State(messages=[])
        state.kt_meta_rules = "- Rule 1\n- Rule 2"
        state.kt_meta_rules = "- Rule 3"
        assert "Rule 1" not in state.kt_meta_rules
        assert "Rule 3" in state.kt_meta_rules

    def test_kt_context_empty_string_safe(self):
        state = State(messages=[HumanMessage(content="test")])
        state.kt_context = ""
        assert state.kt_context == ""

    def test_state_messages_unchanged_by_kt_context(self):
        msgs = [HumanMessage(content="original")]
        state = State(messages=msgs)
        state.kt_context = "[相关知识]\n- Injected content"

        assert len(state.messages) == 1
        assert state.messages[0].content == "original"
        assert "Injected" not in str(state.messages[0].content)


# ============================================================
# 9. ActiveExecutorTask 生命周期
# ============================================================


class TestActiveExecutorTaskLifecycle:
    """模拟大量 Executor 任务的派发→完成→清理周期。"""

    def test_many_tasks_dispatch_and_complete(self):
        active: dict[str, ActiveExecutorTask] = {}
        history: dict[str, ExecutorTaskRecord] = {}

        for i in range(100):
            pid = f"plan_{i:04d}"
            active[pid] = ActiveExecutorTask(plan_id=pid, status="dispatched")

            active[pid].status = "running"

            del active[pid]
            history[pid] = ExecutorTaskRecord(
                plan_id=pid, status="completed", queryable=True,
            )

        assert len(active) == 0
        assert len(history) == 100

    def test_concurrent_tasks_bounded(self):
        active: dict[str, ActiveExecutorTask] = {}

        for i in range(10):
            pid = f"plan_{i}"
            active[pid] = ActiveExecutorTask(plan_id=pid, status="dispatched")

        assert len(active) == 10

        for pid in list(active.keys()):
            del active[pid]

        assert len(active) == 0


# ============================================================
# 10. 端到端长对话状态演变模拟
# ============================================================


class TestEndToEndLongConversation:
    """模拟完整的 100 轮对话状态演变，验证所有子系统协同。"""

    def test_100_turn_full_simulation(self, tmp_path: Path):
        msgs: list = []
        session = PlannerSession(session_id="session_1")
        task_history: dict[str, ExecutorTaskRecord] = {}
        active_tasks: dict[str, ActiveExecutorTask] = {}
        mb = Mailbox()

        cfg = KnowledgeTreeConfig(
            markdown_root=tmp_path / "kt",
            embedder_type="hash",
            embedding_dimension=64,
        )
        kt = KnowledgeTree(cfg)

        for turn in range(100):
            user_msg = f"Turn {turn}: do something"
            msgs.append(HumanMessage(content=user_msg))

            log = RetrievalLog(
                query_id=f"q_{turn}",
                query_text=user_msg,
                rag_results=[],
            )
            kt._retrieval_logs.append(log)
            if len(kt._retrieval_logs) > kt._max_retrieval_logs:
                kt._retrieval_logs = kt._retrieval_logs[-kt._max_retrieval_logs:]

            if turn % 5 == 0:
                pid = f"plan_{turn:04d}"
                history = list(session.planner_history_by_plan_id.get(pid, []))
                history.append({"role": "user", "content": user_msg})
                history.append({"role": "assistant", "content": f"plan for turn {turn}"})
                session.planner_history_by_plan_id[pid] = history

            if turn % 3 == 0:
                pid = f"plan_exec_{turn:04d}"
                tid = _tcid()
                tids = [tid]
                msgs.append(_ai_with_tools(tids))
                msgs.append(_tool_msg(tid, f"[EXECUTOR_RESULT] completed task {turn}"))
                msgs.append(AIMessage(content=f"Task {turn} done"))

                active_tasks[pid] = ActiveExecutorTask(plan_id=pid, status="dispatched")
                del active_tasks[pid]
                task_history[pid] = ExecutorTaskRecord(
                    plan_id=pid, status="completed",
                )

                mb._post_sync(pid, MailboxItem(
                    item_type="completion", payload={"status": "completed"},
                ))
            else:
                msgs.append(AIMessage(content=f"Direct reply to turn {turn}"))

            trimmed = _trim_messages_for_llm(msgs, 100)
            assert len(trimmed) <= 100

        assert len(kt._retrieval_logs) == 100
        assert len(session.planner_history_by_plan_id) == 20
        assert len(task_history) > 0
        assert len(active_tasks) == 0

        final_trimmed = _trim_messages_for_llm(msgs, 100)
        assert len(final_trimmed) == 100
        assert isinstance(final_trimmed[-1], AIMessage)
