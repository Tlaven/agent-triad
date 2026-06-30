"""Unit tests for Entry A provenance tagging — executor_status metadata + [失败教训] tag."""

import asyncio
import json
from dataclasses import dataclass
from unittest.mock import MagicMock, patch

from langchain_core.messages import HumanMessage

from src.supervisor_agent.graph import _try_auto_ingest_executor_result, kt_retrieve
from src.supervisor_agent.state import State

# ---------------------------------------------------------------------------
# Helpers — FakeKT recording ingest calls with metadata
# ---------------------------------------------------------------------------


class _FakeReport:
    nodes_ingested = 1
    nodes_deduplicated = 0


class _FakeKT:
    def __init__(self) -> None:
        self.ingest_calls: list[dict] = []
        self.embedder_type = "hash"

    def ingest(self, chunk, trigger="", source="", metadata=None, **kwargs):
        self.ingest_calls.append(
            {
                "chunk": chunk,
                "trigger": trigger,
                "source": source,
                "metadata": metadata or {},
            }
        )
        return _FakeReport()


def _patch_kt(monkeypatch, fake_kt: _FakeKT) -> None:
    monkeypatch.setattr(
        "src.common.knowledge_tree.get_or_create_kt", lambda config: fake_kt
    )
    monkeypatch.setattr(
        "src.common.knowledge_tree.config.KnowledgeTreeConfig",
        type(
            "FakeKTConfig",
            (),
            {"from_context": staticmethod(lambda ctx: MagicMock())},
        ),
    )


def _make_executor_content(summary: str, plan_json: str, status: str = "completed") -> str:
    return (
        f"{summary}\n\n[EXECUTOR_RESULT] "
        f'{{"status":"{status}","updated_plan_json":{json.dumps(plan_json)}}}'
    )


def _make_plan(goal: str, step_result: str = "", step_failure: str = "") -> str:
    return json.dumps(
        {
            "plan_id": "plan_test",
            "goal": goal,
            "steps": [
                {
                    "step_id": "s1",
                    "intent": "do",
                    "status": "completed" if step_result else "failed",
                    "result_summary": step_result,
                    "failure_reason": step_failure,
                }
            ],
        }
    )


# ---------------------------------------------------------------------------
# Cases 1-3: 摄入层 executor_status metadata 传递
# ---------------------------------------------------------------------------


class TestExecutorStatusMetadata:
    """_try_auto_ingest_executor_result 应把 exec_status 写入 metadata.executor_status。"""

    def test_chunk_ingest_carries_failed_status(self, monkeypatch):
        fake_kt = _FakeKT()
        _patch_kt(monkeypatch, fake_kt)

        plan = _make_plan(
            "查找 timeout 配置",
            step_failure="Executor 返回 unreachable 状态，无法访问执行器子进程",
        )
        content = _make_executor_content("查找失败", plan, "failed")

        _try_auto_ingest_executor_result(content, None, "failed")

        executor_calls = [c for c in fake_kt.ingest_calls if c["source"] == "auto:executor"]
        assert len(executor_calls) > 0, f"No executor chunk ingested: {fake_kt.ingest_calls}"
        assert any(
            c["metadata"].get("executor_status") == "failed" for c in executor_calls
        ), f"executor_status=failed not propagated: {executor_calls}"

    def test_chunk_ingest_carries_completed_status(self, monkeypatch):
        fake_kt = _FakeKT()
        _patch_kt(monkeypatch, fake_kt)

        plan = _make_plan("实现 timeout 配置", step_result="在 context.py 添加了 180s 超时")
        content = _make_executor_content("执行成功", plan, "completed")

        _try_auto_ingest_executor_result(content, None, "completed")

        executor_calls = [c for c in fake_kt.ingest_calls if c["source"] == "auto:executor"]
        assert len(executor_calls) > 0
        assert any(
            c["metadata"].get("executor_status") == "completed" for c in executor_calls
        ), f"executor_status=completed not propagated: {executor_calls}"

    def test_experience_node_carries_failed_status_and_node_type(self, monkeypatch):
        fake_kt = _FakeKT()
        _patch_kt(monkeypatch, fake_kt)

        plan = _make_plan("部署生产环境", step_failure="UTF-8 BOM 编码错误导致 .env 解析失败")
        content = _make_executor_content("部署失败", plan, "failed")

        _try_auto_ingest_executor_result(content, None, "failed")

        experience_calls = [
            c for c in fake_kt.ingest_calls if c["source"] == "auto:executor_experience"
        ]
        assert len(experience_calls) > 0, (
            f"No experience node ingested: {fake_kt.ingest_calls}"
        )
        for call in experience_calls:
            assert call["metadata"].get("node_type") == "experience"
            assert call["metadata"].get("executor_status") == "failed"


# ---------------------------------------------------------------------------
# Cases 4-6: inject 层 [失败教训] tag
# ---------------------------------------------------------------------------


@dataclass
class _MockContext:
    enable_knowledge_tree: bool = True
    knowledge_tree_root: str = "workspace/knowledge_tree"
    kt_embedding_model: str = "hash"


class _MockRuntime:
    def __init__(self, ctx: _MockContext | None = None) -> None:
        self.context = ctx or _MockContext()


def _make_node(title: str, content: str, metadata: dict | None = None) -> MagicMock:
    node = MagicMock()
    node.title = title
    node.content = content
    node.metadata = metadata if metadata is not None else {}
    return node


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


class TestInjectFailedLessonTag:
    """kt_retrieve 注入时应给 executor_status=failed 的节点加 [失败教训] 前缀。"""

    @patch("src.common.knowledge_tree.config.KnowledgeTreeConfig.from_context")
    @patch("src.common.knowledge_tree.get_or_create_kt")
    def test_failed_node_gets_failure_lesson_tag(self, mock_get_kt, mock_from_ctx):
        mock_kt = MagicMock()
        mock_kt.retrieve.return_value = (
            [
                (
                    _make_node(
                        "失败教训",
                        "Executor unreachable 导致假阴性",
                        metadata={"executor_status": "failed"},
                    ),
                    0.85,
                )
            ],
            None,
        )
        mock_kt.embedder_type = "hash"
        mock_get_kt.return_value = mock_kt

        runtime = _MockRuntime()
        state = State(messages=[HumanMessage(content="查找 timeout 配置")])

        result = _run(kt_retrieve(state, runtime))

        assert "[失败教训]" in result["kt_context"], (
            f"Failed-lesson tag missing: {result['kt_context']}"
        )

    @patch("src.common.knowledge_tree.config.KnowledgeTreeConfig.from_context")
    @patch("src.common.knowledge_tree.get_or_create_kt")
    def test_completed_node_no_extra_tag(self, mock_get_kt, mock_from_ctx):
        mock_kt = MagicMock()
        mock_kt.retrieve.return_value = (
            [
                (
                    _make_node(
                        "成功知识",
                        "executor 在 180s 内完成",
                        metadata={"executor_status": "completed"},
                    ),
                    0.85,
                )
            ],
            None,
        )
        mock_kt.embedder_type = "hash"
        mock_get_kt.return_value = mock_kt

        runtime = _MockRuntime()
        state = State(messages=[HumanMessage(content="timeout 配置")])

        result = _run(kt_retrieve(state, runtime))

        assert "[失败教训]" not in result["kt_context"], (
            f"Completed node should not get tag: {result['kt_context']}"
        )

    @patch("src.common.knowledge_tree.config.KnowledgeTreeConfig.from_context")
    @patch("src.common.knowledge_tree.get_or_create_kt")
    def test_legacy_node_without_metadata_no_crash(self, mock_get_kt, mock_from_ctx):
        mock_kt = MagicMock()
        mock_kt.retrieve.return_value = (
            [
                (
                    _make_node("旧节点", "已存节点无 executor_status 字段", metadata={}),
                    0.85,
                )
            ],
            None,
        )
        mock_kt.embedder_type = "hash"
        mock_get_kt.return_value = mock_kt

        runtime = _MockRuntime()
        state = State(messages=[HumanMessage(content="查询")])

        result = _run(kt_retrieve(state, runtime))

        assert "[失败教训]" not in result["kt_context"]
        assert "旧节点" in result["kt_context"]
