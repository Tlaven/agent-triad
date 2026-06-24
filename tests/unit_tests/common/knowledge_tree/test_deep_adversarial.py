"""V4 深挖测试 — 刻意触发异常行为。

目标：找到真实 bug，而非验证正确性。
每个测试类针对一个已知的潜在缺陷。
"""

import json
import sys
import time
import uuid
from pathlib import Path

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from src.common.knowledge_tree.config import KnowledgeTreeConfig
from src.common.knowledge_tree.core import KnowledgeTree
from src.common.knowledge_tree.dag.node import KnowledgeNode
from src.common.knowledge_tree.ingestion.ingest import ingest_nodes
from src.common.knowledge_tree.retrieval.log import RetrievalLog
from src.common.knowledge_tree.storage.markdown_store import MarkdownStore
from src.common.knowledge_tree.storage.overlay import OverlayStore
from src.common.knowledge_tree.storage.vector_store import (
    DirectoryAnchor,
    InMemoryVectorStore,
)
from src.common.mailbox import Mailbox, MailboxItem, _MAX_BOXES
from src.supervisor_agent.graph import _trim_messages_for_llm
from src.supervisor_agent.state import PlannerSession, State


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


# ============================================================
# BUG-1: 幽灵 tool_calls — AI 引用了被截掉的 ToolMessage
# ============================================================


class TestGhostToolCalls:
    """AI 消息留在窗口内但部分 ToolMessage 被截断。

    _trim_messages_for_llm 只处理开头的孤立 ToolMessage，
    不处理窗口内 AI 消息引用了窗口外 ToolMessage 的情况。
    """

    def test_ai_references_dropped_tool_messages(self):
        """构造：AI(tool_calls=[A,B,C]) + ToolMsg(A) + ToolMsg(B) + ToolMsg(C)
        截断后窗口只包含 AI + ToolMsg(A) + ToolMsg(B)，ToolMsg(C) 被截掉。
        LLM 看到 AI 声称调用 3 个工具，但只有 2 个结果。
        """
        tid_a, tid_b, tid_c = _tcid(), _tcid(), _tcid()

        msgs: list = []
        for i in range(50):
            msgs.append(HumanMessage(content=f"filler {i}"))
            msgs.append(AIMessage(content=f"reply {i}"))

        msgs.append(HumanMessage(content="trigger"))
        msgs.append(_ai_with_tools([tid_a, tid_b, tid_c]))
        msgs.append(_tool_msg(tid_a, "result A"))
        msgs.append(_tool_msg(tid_b, "result B"))
        msgs.append(_tool_msg(tid_c, "result C"))
        msgs.append(AIMessage(content="done"))

        trimmed = _trim_messages_for_llm(msgs, 5)

        ai_tool_msgs = [
            m for m in trimmed
            if isinstance(m, AIMessage) and getattr(m, "tool_calls", None)
        ]
        tool_result_msgs = [m for m in trimmed if isinstance(m, ToolMessage)]

        if ai_tool_msgs:
            claimed_ids = set()
            for am in ai_tool_msgs:
                for tc in am.tool_calls:
                    claimed_ids.add(tc["id"])

            result_ids = {m.tool_call_id for m in tool_result_msgs}

            ghost_ids = claimed_ids - result_ids
            assert len(ghost_ids) == 0, (
                f"BUG: LLM sees AI claiming {len(claimed_ids)} tool calls "
                f"but only {len(result_ids)} results present. "
                f"Ghost IDs: {ghost_ids}"
            )

    def test_partial_tool_results_in_window(self):
        """AI 调用 5 个工具，窗口只装下 AI + 2 个 ToolMessage。"""
        tids = [_tcid() for _ in range(5)]

        msgs: list = []
        for i in range(95):
            msgs.append(HumanMessage(content=f"q{i}"))
            msgs.append(AIMessage(content=f"a{i}"))

        msgs.append(HumanMessage(content="big batch"))
        msgs.append(_ai_with_tools(tids))
        for tid in tids:
            msgs.append(_tool_msg(tid))
        msgs.append(AIMessage(content="all done"))

        trimmed = _trim_messages_for_llm(msgs, 4)

        ai_tool_msgs = [
            m for m in trimmed
            if isinstance(m, AIMessage) and getattr(m, "tool_calls", None)
        ]
        tool_result_msgs = [m for m in trimmed if isinstance(m, ToolMessage)]

        if ai_tool_msgs:
            claimed = set()
            for am in ai_tool_msgs:
                for tc in am.tool_calls:
                    claimed.add(tc["id"])
            present = {m.tool_call_id for m in tool_result_msgs}
            missing = claimed - present
            assert len(missing) == 0, (
                f"BUG: {len(missing)} tool results missing from window. "
                f"AI claims {len(claimed)} calls, only {len(present)} results. "
                f"Missing: {missing}"
            )


# ============================================================
# BUG-2: Mailbox 未完成 box 堆积 — 驱逐只删 has_completion=True
# ============================================================


class TestMailboxIncompletePileUp:
    """如果所有 box 都是 status（无 completion），驱逐不触发→无限增长。"""

    def test_incomplete_boxes_bypass_eviction(self):
        mb = Mailbox()

        for i in range(200):
            pid = f"stuck_{i:04d}"
            mb._post_sync(pid, MailboxItem(
                item_type="status", payload={"progress": "50%"},
            ))

        assert len(mb._boxes) <= _MAX_BOXES, (
            f"BUG: {len(mb._boxes)} boxes accumulated "
            f"(max={_MAX_BOXES}), eviction skipped all "
            f"because none has_completion=True"
        )


# ============================================================
# BUG-3: PlannerSession 历史无上限 — 内存退化
# ============================================================


class TestPlannerSessionUnbounded:
    """单 plan_id 1000 次重规划，历史无限增长。"""

    def test_1000_replans_memory_growth(self):
        session = PlannerSession(session_id="s1")
        pid = "plan_persistent"

        for v in range(1, 1001):
            history = list(session.planner_history_by_plan_id.get(pid, []))
            history.append({"role": "user", "content": f"replan task v{v} " + "x" * 200})
            history.append({
                "role": "assistant",
                "content": json.dumps({
                    "plan_id": pid, "version": v,
                    "goal": f"goal v{v}",
                    "steps": [{"step_id": f"s{i}", "intent": f"step {i}"} for i in range(5)],
                }),
            })
            session.planner_history_by_plan_id[pid] = history

            archive = list(session.plan_archive_by_plan_id.get(pid, []))
            archive.append(json.dumps({
                "plan_id": pid, "version": v - 1,
                "steps": [{"step_id": f"s{i}", "intent": f"old step {i}"} for i in range(5)],
            }))
            session.plan_archive_by_plan_id[pid] = archive

        history_len = len(session.planner_history_by_plan_id[pid])
        archive_len = len(session.plan_archive_by_plan_id[pid])

        assert history_len == 2000
        assert archive_len == 1000

        serialized = json.dumps({
            "history": session.planner_history_by_plan_id,
            "archive": session.plan_archive_by_plan_id,
        })
        size_mb = len(serialized.encode("utf-8")) / (1024 * 1024)
        assert size_mb < 50, (
            f"WARNING: PlannerSession serialized to {size_mb:.1f}MB "
            f"after 1000 replans (history={history_len}, archive={archive_len})"
        )


# ============================================================
# BUG-4: replan_count 不随新用户消息重置
# ============================================================


class TestReplanCountNoReset:
    """replan_count 只在 executor completed 时重置，
    新用户消息不会重置。如果用户放弃当前任务开始新话题，
    replan_count 仍然很高。
    """

    def test_replan_count_persists_across_user_messages(self):
        state = State(
            messages=[HumanMessage(content="task 1")],
            replan_count=3,
        )

        state.messages.append(HumanMessage(content="new topic, forget old task"))

        assert state.replan_count == 3, (
            "replan_count should persist (it only resets on executor completed)"
        )


# ============================================================
# BUG-5: KT 自动摄入无节点上限 — 检索质量退化
# ============================================================


class TestKTAutoIngestUnbounded:
    """500 次自动摄入后，知识树节点无上限，检索噪声增加。"""

    def test_500_ingestions_search_noise(self, tmp_path: Path):
        md_store = MarkdownStore(tmp_path / "md")
        vector_store = InMemoryVectorStore(dimension=64)
        overlay_store = OverlayStore(tmp_path / "md" / ".overlay.json")

        import random
        rng = random.Random(42)

        def embedder(text: str) -> list[float]:
            vec = [0.0] * 64
            for i, c in enumerate(text):
                idx = (ord(c) + i * 7) % 64
                vec[idx] += 1.0
            mag = sum(x * x for x in vec) ** 0.5
            if mag > 0:
                vec = [x / mag for x in vec]
            return vec

        for i in range(500):
            node = KnowledgeNode.create(
                node_id="",
                title=f"Auto-ingested knowledge {i}",
                content=f"Technical content about topic {i} with details and numbers {i * 7}.",
                source="auto:executor",
            )
            node.embedding = embedder(node.content)
            directory = f"topic_{i % 20}"
            md_store.ensure_directory(directory)
            node.node_id = f"{directory}/auto_{i}.md"
            node.directory = directory
            md_store.write_node(node)
            vector_store.upsert_embedding(node.node_id, node.embedding)

        all_nodes = md_store.list_node_ids()
        assert len(all_nodes) == 500

        query_emb = embedder("Technical content about topic 42 with details")
        start = time.monotonic()
        results = vector_store.similarity_search(query_emb, top_k=5, threshold=0.0)
        elapsed = time.monotonic() - start

        assert len(results) == 5
        assert elapsed < 1.0, f"500-node search took {elapsed:.3f}s"

        top_ids = [r[0] for r in results]
        has_topic_42 = any("topic_42" in nid for nid in top_ids)

        top_scores = [r[1] for r in results]
        score_spread = max(top_scores) - min(top_scores)
        assert score_spread > 0.001, (
            f"WARNING: Top 5 scores too close (spread={score_spread:.6f}), "
            f"cannot distinguish relevant from noise"
        )


# ============================================================
# BUG-6: 消息截断性能 — 10K 消息
# ============================================================


class TestTrimmingPerformance:
    """大量消息下 _trim_messages_for_llm 的性能。"""

    def test_10k_messages_trimming_speed(self):
        msgs: list = []
        for i in range(5000):
            msgs.append(HumanMessage(content=f"q{i}"))
            msgs.append(AIMessage(content=f"a{i}"))

        assert len(msgs) == 10000

        start = time.monotonic()
        for _ in range(100):
            _trim_messages_for_llm(msgs, 100)
        elapsed = time.monotonic() - start

        per_call_ms = (elapsed / 100) * 1000
        assert per_call_ms < 50, (
            f"WARNING: Trimming 10K messages takes {per_call_ms:.1f}ms per call"
        )

    def test_10k_messages_with_tools_trimming_speed(self):
        msgs: list = []
        for i in range(2000):
            msgs.append(HumanMessage(content=f"q{i}"))
            tid = _tcid()
            msgs.append(_ai_with_tools([tid]))
            msgs.append(_tool_msg(tid))
            msgs.append(AIMessage(content=f"a{i}"))
            msgs.append(HumanMessage(content=f"follow {i}"))

        assert len(msgs) == 10000

        start = time.monotonic()
        for _ in range(100):
            _trim_messages_for_llm(msgs, 100)
        elapsed = time.monotonic() - start

        per_call_ms = (elapsed / 100) * 1000
        assert per_call_ms < 50, (
            f"WARNING: Trimming 10K tool-heavy messages takes {per_call_ms:.1f}ms"
        )


# ============================================================
# BUG-7: plan_archive 无上限
# ============================================================


class TestPlanArchiveUnbounded:
    """plan_archive_by_plan_id 每次重规划追加旧 plan JSON，无上限。"""

    def test_archive_grows_without_bound(self):
        session = PlannerSession(session_id="s1")
        pid = "plan_archive_test"

        for v in range(500):
            archive = list(session.plan_archive_by_plan_id.get(pid, []))
            plan_json = json.dumps({
                "plan_id": pid,
                "version": v,
                "goal": f"Goal version {v}",
                "steps": [
                    {"step_id": f"s{i}", "intent": f"Step {i} at v{v}", "status": "completed"}
                    for i in range(10)
                ],
            })
            archive.append(plan_json)
            session.plan_archive_by_plan_id[pid] = archive

        archive_len = len(session.plan_archive_by_plan_id[pid])
        assert archive_len == 500

        total_chars = sum(
            len(s) for s in session.plan_archive_by_plan_id[pid]
        )
        assert total_chars > 100_000, (
            f"Archive is only {total_chars} chars — expected >100K for 500 entries"
        )


# ============================================================
# BUG-8: state.messages 无限增长 → 序列化成本
# ============================================================


class TestStateMessagesSerializationCost:
    """state.messages 无限增长，LangGraph checkpoint 序列化成本线性增加。"""

    def test_2000_messages_serialization_size(self):
        msgs: list = []
        for i in range(1000):
            msgs.append(HumanMessage(content=f"User question {i}: " + "x" * 100))
            tid = _tcid()
            msgs.append(_ai_with_tools([tid], f"Thinking about {i}"))
            msgs.append(_tool_msg(tid, json.dumps({
                "status": "completed",
                "summary": f"Task {i} result " + "y" * 200,
            })))
            msgs.append(AIMessage(content=f"Done with task {i}"))

        assert len(msgs) == 4000

        serialized = json.dumps([
            {"role": type(m).__name__, "content": str(m.content)[:500]}
            for m in msgs
        ])
        size_mb = len(serialized) / (1024 * 1024)
        assert size_mb < 100, (
            f"WARNING: 4K messages serialize to {size_mb:.1f}MB "
            f"(even truncated to 500 chars each)"
        )


# ============================================================
# BUG-9: 孤立 ToolMessage 保留场景 — parent AI 在窗口内
# ============================================================


class TestOrphanRetainedWhenParentExists:
    """当 parent AI 在窗口内时，开头的 ToolMessage 应保留。
    但如果 parent AI 的 tool_calls 数量 > 窗口内 ToolMessage 数量，
    部分 tool_call 没有对应结果。
    """

    def test_parent_in_window_but_partial_results(self):
        tid_a, tid_b, tid_c = _tcid(), _tcid(), _tcid()

        msgs: list = []
        msgs.append(HumanMessage(content="start"))
        msgs.append(_ai_with_tools([tid_a, tid_b, tid_c]))
        msgs.append(_tool_msg(tid_a, "A result"))
        msgs.append(_tool_msg(tid_b, "B result"))

        for i in range(20):
            msgs.append(HumanMessage(content=f"filler {i}"))
            msgs.append(AIMessage(content=f"reply {i}"))

        trimmed = _trim_messages_for_llm(msgs, 10)

        ai_with_tools = [
            m for m in trimmed
            if isinstance(m, AIMessage) and getattr(m, "tool_calls", None)
        ]
        tool_results = [m for m in trimmed if isinstance(m, ToolMessage)]

        if ai_with_tools:
            all_claimed = set()
            for am in ai_with_tools:
                for tc in am.tool_calls:
                    all_claimed.add(tc["id"])
            all_present = {m.tool_call_id for m in tool_results}

            missing = all_claimed - all_present
            if missing:
                assert False, (
                    f"BUG: AI in window claims tool_calls {all_claimed}, "
                    f"but results only have {all_present}. "
                    f"Missing: {missing}. LLM will be confused."
                )


# ============================================================
# BUG-10: KT 检索日志内存 — 1000 条 × 高维向量
# ============================================================


class TestRetrievalLogMemory:
    """1000 条检索日志 × 1024 维向量的内存占用。"""

    def test_1000_logs_with_1024d_vectors(self, tmp_path: Path):
        cfg = KnowledgeTreeConfig(
            markdown_root=tmp_path / "kt",
            embedder_type="hash",
            embedding_dimension=1024,
        )
        kt = KnowledgeTree(cfg)

        for i in range(1000):
            query_vec = [float((i + j) % 100) / 100.0 for j in range(1024)]
            log = RetrievalLog(
                query_id=f"q_{i}",
                query_text=f"query about topic {i}",
                rag_results=[(f"node_{j}.md", 0.5 + j * 0.01) for j in range(5)],
            )
            kt._retrieval_logs.append(log)

        assert len(kt._retrieval_logs) == 1000

        estimated_bytes = 0
        for log in kt._retrieval_logs:
            estimated_bytes += sys.getsizeof(log.query_text)
            estimated_bytes += sys.getsizeof(log.query_id)
            estimated_bytes += sys.getsizeof(log.rag_results) * 5

        estimated_mb = estimated_bytes / (1024 * 1024)
        assert estimated_mb < 100, (
            f"WARNING: 1000 retrieval logs estimated at {estimated_mb:.1f}MB "
            f"(without counting query_vector)"
        )


# ============================================================
# BUG-11: ExecutorPoller 无上限堆积
# ============================================================


class TestExecutorPollerUnbounded:
    """ExecutorPoller 注册的 plan_id 无上限，长时间运行后堆积。"""

    def test_poller_accumulates_without_cleanup(self):
        from src.common.polling import ExecutorPoller

        mb = Mailbox()
        poller = ExecutorPoller(mb)

        for i in range(200):
            plan_id = f"plan_{i:04d}"
            poller.register(
                plan_id,
                "test_json",
                executor_base_url=f"http://localhost:{8000 + i}",
            )

        assert len(poller._active) <= 100, (
            f"BUG: ExecutorPoller accumulated {len(poller._active)} tasks "
            f"without cleanup. Should have max limit or auto-unregister."
        )


# ============================================================
# BUG-12: KT 向量存储 10K+ 节点性能退化
# ============================================================


class TestKTVectorStorePerformance:
    """KT 向量存储 10K+ 节点性能退化。"""

    def test_10k_nodes_search_performance(self, tmp_path: Path):
        md_store = MarkdownStore(tmp_path / "md")
        vector_store = InMemoryVectorStore(dimension=1024)

        def embedder(text: str) -> list[float]:
            vec = [0.0] * 1024
            for i, c in enumerate(text):
                idx = (ord(c) + i * 7) % 1024
                vec[idx] += 1.0
            mag = sum(x * x for x in vec) ** 0.5
            if mag > 0:
                vec = [x / mag for x in vec]
            return vec

        for i in range(10_000):
            node = KnowledgeNode.create(
                node_id="",
                title=f"Node {i}",
                content=f"Content {i} " + "x" * 100,
                source="test",
            )
            node.embedding = embedder(node.content)
            directory = f"dir_{i % 50}"
            md_store.ensure_directory(directory)
            node.node_id = f"{directory}/node_{i}.md"
            node.directory = directory
            md_store.write_node(node)
            vector_store.upsert_embedding(node.node_id, node.embedding)

        query_emb = embedder("Content 5000 with details")

        start = time.monotonic()
        results = vector_store.similarity_search(query_emb, top_k=5, threshold=0.0)
        elapsed = time.monotonic() - start

        assert len(results) == 5
        assert elapsed < 2.0, (
            f"WARNING: 10K-node search took {elapsed:.3f}s "
            f"(target <2s). Linear scan may not scale."
        )

    def test_50k_nodes_memory_pressure(self, tmp_path: Path):
        vector_store = InMemoryVectorStore(dimension=512)

        import random
        rng = random.Random(99)

        for i in range(50_000):
            vec = [rng.random() for _ in range(512)]
            mag = sum(x * x for x in vec) ** 0.5
            vec = [x / mag for x in vec]
            vector_store.upsert_embedding(f"node_{i}", vec)

        query = [rng.random() for _ in range(512)]
        mag = sum(x * x for x in query) ** 0.5
        query = [x / mag for x in query]

        start = time.monotonic()
        results = vector_store.similarity_search(query, top_k=10, threshold=0.0)
        elapsed = time.monotonic() - start

        assert len(results) == 10
        assert elapsed < 10.0, (
            f"WARNING: 50K-node search took {elapsed:.3f}s "
            f"(target <10s). Memory pressure may cause degradation."
        )


# ============================================================
# BUG-13: 消息截断极端边界
# ============================================================


class TestMessageTrimmingEdgeCases:
    """消息截断的极端边界情况。"""

    def test_all_tool_messages_no_ai(self):
        """全部是 ToolMessage，没有 AI 消息。"""
        tids = [_tcid() for _ in range(10)]
        msgs = [_tool_msg(tid) for tid in tids]

        trimmed = _trim_messages_for_llm(msgs, 5)

        assert len(trimmed) == 0, (
            f"BUG: All ToolMessages without parent AI should be dropped, "
            f"but got {len(trimmed)} messages"
        )

    def test_interleaved_orphans(self):
        """交错的孤立 ToolMessage 和正常消息。
        _trim_messages_for_llm 只处理开头的连续 ToolMessage 块，
        不处理散布在中间的孤立 ToolMessage。
        """
        tid_orphan = _tcid()
        tid_normal = _tcid()

        msgs: list = []
        msgs.append(_tool_msg(tid_orphan, "orphan"))
        msgs.append(HumanMessage(content="q1"))
        msgs.append(AIMessage(content="a1"))
        msgs.append(_tool_msg(tid_orphan, "orphan again"))
        msgs.append(HumanMessage(content="q2"))
        msgs.append(_ai_with_tools([tid_normal]))
        msgs.append(_tool_msg(tid_normal, "normal"))
        msgs.append(AIMessage(content="done"))

        trimmed = _trim_messages_for_llm(msgs, 10)

        orphan_count = sum(
            1 for m in trimmed
            if isinstance(m, ToolMessage) and m.tool_call_id == tid_orphan
        )
        assert orphan_count == 0, (
            f"BUG: {orphan_count} orphan ToolMessages remain in window. "
            f"_trim_messages_for_llm only handles leading contiguous orphans, "
            f"not interleaved ones."
        )

    def test_zero_max_messages(self):
        """max_messages=0 表示不限制，应该返回全部消息。"""
        msgs = [HumanMessage(content="test")]
        trimmed = _trim_messages_for_llm(msgs, 0)
        assert trimmed == msgs, (
            "max_messages=0 means no limit, should return all messages"
        )

    def test_negative_max_messages(self):
        """max_messages<0 表示不限制，应该返回全部消息。"""
        msgs = [HumanMessage(content="test")]
        trimmed = _trim_messages_for_llm(msgs, -1)
        assert trimmed == msgs, (
            "max_messages<0 means no limit, should return all messages"
        )


# ============================================================
# BUG-14: KT 检索极端边界
# ============================================================


class TestKTRetrievalEdgeCases:
    """KT 检索的极端边界情况。"""

    def test_retrieve_with_empty_tree(self, tmp_path: Path):
        cfg = KnowledgeTreeConfig(
            markdown_root=tmp_path / "kt",
            embedder_type="hash",
            embedding_dimension=64,
        )
        kt = KnowledgeTree(cfg)

        results, log = kt.retrieve("any query")

        assert len(results) == 0, (
            f"BUG: Empty tree should return 0 results, got {len(results)}"
        )

    def test_retrieve_with_single_node(self, tmp_path: Path):
        cfg = KnowledgeTreeConfig(
            markdown_root=tmp_path / "kt",
            embedder_type="hash",
            embedding_dimension=64,
        )
        kt = KnowledgeTree(cfg)

        node = KnowledgeNode.create(
            node_id="test.md",
            title="Test",
            content="Test content",
            source="test",
        )
        kt.md_store.ensure_directory("")
        kt.md_store.write_node(node)
        kt.vector_store.upsert_embedding(node.node_id, kt.embedder(node.content))

        results, log = kt.retrieve("test query")

        assert len(results) <= 1, (
            f"BUG: Single-node tree returned {len(results)} results"
        )

    def test_retrieve_all_nodes_identical(self, tmp_path: Path):
        """所有节点内容完全相同，应该去重或限制返回数量。"""
        cfg = KnowledgeTreeConfig(
            markdown_root=tmp_path / "kt",
            embedder_type="hash",
            embedding_dimension=64,
        )
        kt = KnowledgeTree(cfg)

        for i in range(100):
            node = KnowledgeNode.create(
                node_id=f"node_{i}.md",
                title="Identical",
                content="Same content",
                source="test",
            )
            kt.md_store.ensure_directory("")
            kt.md_store.write_node(node)
            kt.vector_store.upsert_embedding(node.node_id, kt.embedder(node.content))

        results = kt.retrieve("identical query")

        assert len(results) <= 10, (
            f"WARNING: 100 identical nodes returned {len(results)} results. "
            f"Should deduplicate or limit."
        )


# ============================================================
# BUG-15: KT 摄入极端边界
# ============================================================


class TestKTIngestionEdgeCases:
    """KT 摄入的极端边界情况。"""

    def test_ingest_empty_content(self, tmp_path: Path):
        md_store = MarkdownStore(tmp_path / "md")
        vector_store = InMemoryVectorStore(dimension=64)
        overlay_store = OverlayStore(tmp_path / "md" / ".overlay.json")

        def embedder(text: str) -> list[float]:
            return [0.0] * 64

        node = KnowledgeNode.create(
            node_id="",
            title="",
            content="",
            source="test",
        )

        report = ingest_nodes(
            [node], vector_store, md_store, overlay_store, embedder,
            attach_threshold=0.99,
        )

        assert report.nodes_ingested == 0 or report.nodes_ingested == 1, (
            f"Empty content ingestion: {report.nodes_ingested} nodes"
        )

    def test_ingest_very_long_content(self, tmp_path: Path):
        md_store = MarkdownStore(tmp_path / "md")
        vector_store = InMemoryVectorStore(dimension=64)
        overlay_store = OverlayStore(tmp_path / "md" / ".overlay.json")

        def embedder(text: str) -> list[float]:
            vec = [0.0] * 64
            for i, c in enumerate(text[:1000]):
                idx = (ord(c) + i) % 64
                vec[idx] += 1.0
            mag = sum(x * x for x in vec) ** 0.5
            if mag > 0:
                vec = [x / mag for x in vec]
            return vec

        long_content = "x" * 1_000_000
        node = KnowledgeNode.create(
            node_id="",
            title="Long",
            content=long_content,
            source="test",
        )

        start = time.monotonic()
        report = ingest_nodes(
            [node], vector_store, md_store, overlay_store, embedder,
            attach_threshold=0.99,
        )
        elapsed = time.monotonic() - start

        assert report.nodes_ingested == 1
        assert elapsed < 5.0, (
            f"WARNING: 1MB content ingestion took {elapsed:.3f}s"
        )

    def test_ingest_unicode_content(self, tmp_path: Path):
        md_store = MarkdownStore(tmp_path / "md")
        vector_store = InMemoryVectorStore(dimension=64)
        overlay_store = OverlayStore(tmp_path / "md" / ".overlay.json")

        def embedder(text: str) -> list[float]:
            vec = [0.0] * 64
            for i, c in enumerate(text):
                idx = (ord(c) + i) % 64
                vec[idx] += 1.0
            mag = sum(x * x for x in vec) ** 0.5
            if mag > 0:
                vec = [x / mag for x in vec]
            return vec

        node = KnowledgeNode.create(
            node_id="",
            title="中文标题",
            content="中文内容 🎉 emoji",
            source="test",
        )

        report = ingest_nodes(
            [node], vector_store, md_store, overlay_store, embedder,
            attach_threshold=0.99,
        )

        assert report.nodes_ingested == 1
        assert report.errors == []


# ============================================================
# BUG-16: 并发访问边界
# ============================================================


class TestConcurrentAccess:
    """并发访问的边界情况。"""

    def test_concurrent_vector_store_access(self, tmp_path: Path):
        import threading

        vector_store = InMemoryVectorStore(dimension=64)

        def writer(thread_id: int):
            for i in range(100):
                vec = [float(thread_id * 100 + i)] * 64
                vector_store.upsert_embedding(f"node_{thread_id}_{i}", vec)

        threads = [threading.Thread(target=writer, args=(t,)) for t in range(10)]

        start = time.monotonic()
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        elapsed = time.monotonic() - start

        assert len(vector_store._embeddings) == 1000
        assert elapsed < 5.0, (
            f"WARNING: Concurrent writes took {elapsed:.3f}s"
        )


# ============================================================
# BUG-17: replan_count 边界情况
# ============================================================


class TestReplanCountEdgeCases:
    """replan_count 的边界情况。"""

    def test_replan_count_increments_on_pause(self):
        """paused 状态不应该增加 replan_count。"""
        from src.supervisor_agent.graph import _process_executor_completion
        from src.supervisor_agent.state import State

        state = State(
            messages=[HumanMessage(content="test")],
            replan_count=2,
        )

        content = '[EXECUTOR_RESULT] {"status": "paused", "summary": "Task paused"}'
        tm = _tool_msg(_tcid(), content)
        updates = _process_executor_completion(
            state,
            content,
            tm,
            [],
        )

        assert updates.get("replan_count") == 2, (
            f"BUG: paused should not increment replan_count, "
            f"got {updates.get('replan_count')}"
        )

    def test_replan_count_resets_on_completed(self):
        """completed 应该重置 replan_count。"""
        from src.supervisor_agent.graph import _process_executor_completion
        from src.supervisor_agent.state import State

        state = State(
            messages=[HumanMessage(content="test")],
            replan_count=5,
        )

        content = '[EXECUTOR_RESULT] {"status": "completed", "summary": "Task completed"}'
        tm = _tool_msg(_tcid(), content)
        updates = _process_executor_completion(
            state,
            content,
            tm,
            [],
        )

        assert updates.get("replan_count") == 0, (
            f"BUG: completed should reset replan_count to 0, "
            f"got {updates.get('replan_count')}"
        )
