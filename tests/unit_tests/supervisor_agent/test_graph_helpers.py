"""Unit tests for helper functions in supervisor_agent.graph and tools."""

import json

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from src.supervisor_agent.graph import (
    _build_executor_feedback_for_llm,
    _build_id_to_call,
    _build_id_to_name,
    _extract_executor_summary,
    _needs_mode3_upgrade,
    _parse_plan_meta,
    route_model_output,
)
from src.supervisor_agent.state import State
from src.supervisor_agent.tools import _normalize_plan_id_arg

# ---------------------------------------------------------------------------
# _needs_mode3_upgrade
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("summary", [
    "需要计划层重构，当前路径无法推进",
    "无法继续，请重新规划",
    "需要重新拆解意图",
    "replan needed",
    "cannot proceed further",
    "REPLAN required",  # case-insensitive
])
def test_needs_mode3_upgrade_returns_true(summary: str) -> None:
    assert _needs_mode3_upgrade(summary, None) is True


@pytest.mark.parametrize("summary", [
    "工具调用超时，请重试",
    "文件写入失败，权限不足",
    "命令执行返回码非零",
])
def test_needs_mode3_upgrade_returns_false_for_normal_failures(summary: str) -> None:
    assert _needs_mode3_upgrade(summary, None) is False


def test_needs_mode3_upgrade_both_none_returns_false() -> None:
    assert _needs_mode3_upgrade(None, None) is False


def test_needs_mode3_upgrade_checks_error_detail_too() -> None:
    assert _needs_mode3_upgrade(None, "需要重规划") is True


# ---------------------------------------------------------------------------
# _build_executor_feedback_for_llm
# ---------------------------------------------------------------------------

def _make_full_content(summary: str) -> str:
    meta = {"status": "completed", "error_detail": None, "updated_plan_json": "{}"}
    return f"{summary}\n\n[EXECUTOR_RESULT] {json.dumps(meta)}"


def test_feedback_completed_returns_summary_with_hint() -> None:
    content = _make_full_content("All tasks done successfully")
    feedback = _build_executor_feedback_for_llm(content, "completed", None)
    assert feedback.startswith("All tasks done successfully")
    assert "manage_executor" in feedback
    assert "detail" in feedback
    assert "[EXECUTOR_RESULT]" not in feedback


def test_feedback_failed_includes_error_detail() -> None:
    meta = {"status": "failed", "error_detail": "tool timeout", "updated_plan_json": "{}"}
    content = f"Something failed\n\n[EXECUTOR_RESULT] {json.dumps(meta)}"
    feedback = _build_executor_feedback_for_llm(content, "failed", "tool timeout")
    assert "failed" in feedback.lower()
    assert "tool timeout" in feedback


def test_feedback_failed_no_error_detail_uses_default() -> None:
    meta = {"status": "failed", "error_detail": None, "updated_plan_json": "{}"}
    content = f"summary\n\n[EXECUTOR_RESULT] {json.dumps(meta)}"
    feedback = _build_executor_feedback_for_llm(content, "failed", None)
    assert "failed" in feedback.lower()
    assert "未知错误" in feedback


def test_feedback_none_status_returns_summary() -> None:
    content = _make_full_content("some output")
    feedback = _build_executor_feedback_for_llm(content, None, None)
    assert "some output" in feedback


# ---------------------------------------------------------------------------
# _extract_executor_summary
# ---------------------------------------------------------------------------

def test_extract_summary_with_marker_returns_preamble() -> None:
    content = "This is the summary text.\n\n[EXECUTOR_RESULT] {}"
    assert _extract_executor_summary(content) == "This is the summary text."


def test_extract_summary_without_marker_returns_full_stripped() -> None:
    content = "  Just a plain string.  "
    assert _extract_executor_summary(content) == "Just a plain string."


def test_extract_summary_empty_before_marker() -> None:
    content = "[EXECUTOR_RESULT] {}"
    assert _extract_executor_summary(content) == ""


# ---------------------------------------------------------------------------
# _parse_plan_meta — parametrized
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw,exp_id,exp_version", [
    (json.dumps({"plan_id": "plan_abc", "version": 3, "goal": "g", "steps": []}), "plan_abc", 3),
    ("{not json}", None, None),
    (json.dumps({"version": 2, "goal": "g", "steps": []}), None, 2),
    (json.dumps({"plan_id": "plan_abc", "goal": "g", "steps": []}), "plan_abc", None),
    (json.dumps({"plan_id": "plan_abc", "version": "v1", "goal": "g"}), "plan_abc", None),
    (json.dumps([1, 2, 3]), None, None),
])
def test_parse_plan_meta(raw, exp_id, exp_version) -> None:
    plan_id, version = _parse_plan_meta(raw)
    assert plan_id == exp_id
    assert version == exp_version


# ---------------------------------------------------------------------------
# _build_id_to_name and _build_id_to_call
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("messages", [
    [],
    [HumanMessage(content="hi")],
    [AIMessage(content="direct answer")],
])
def test_build_id_to_name_returns_empty_dict(messages) -> None:
    state = State(messages=messages)
    assert _build_id_to_name(state) == {}


def test_build_id_to_name_with_tool_calls() -> None:
    msg = AIMessage(
        content="",
        tool_calls=[
            {"id": "call_abc", "name": "call_planner", "args": {}, "type": "tool_call"},
            {"id": "call_def", "name": "call_executor", "args": {}, "type": "tool_call"},
        ],
    )
    state = State(messages=[msg])
    assert _build_id_to_name(state) == {"call_abc": "call_planner", "call_def": "call_executor"}


def test_build_id_to_call_with_tool_calls() -> None:
    tc = {"id": "call_xyz", "name": "call_executor", "args": {"task_description": "run task"}, "type": "tool_call"}
    msg = AIMessage(content="", tool_calls=[tc])
    state = State(messages=[msg])
    mapping = _build_id_to_call(state)
    assert "call_xyz" in mapping
    assert mapping["call_xyz"]["name"] == "call_executor"


# ---------------------------------------------------------------------------
# route_model_output
# ---------------------------------------------------------------------------

def test_route_model_output_no_tool_calls_returns_end() -> None:
    state = State(messages=[AIMessage(content="direct answer")])
    assert route_model_output(state) == "__end__"


def test_route_model_output_with_tool_calls_returns_tools() -> None:
    msg = AIMessage(
        content="",
        tool_calls=[{"name": "call_executor", "args": {}, "id": "1", "type": "tool_call"}],
    )
    state = State(messages=[msg])
    assert route_model_output(state) == "tools"


def test_route_model_output_non_ai_message_raises_value_error() -> None:
    state = State(messages=[HumanMessage(content="hi")])
    with pytest.raises(ValueError):
        route_model_output(state)


# ---------------------------------------------------------------------------
# _normalize_plan_id_arg
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("arg", [None, "", "   "])
def test_normalize_plan_id_arg_empty_inputs_return_none(arg) -> None:
    assert _normalize_plan_id_arg(arg) is None


def test_normalize_plan_id_arg_valid_returns_stripped() -> None:
    assert _normalize_plan_id_arg("  plan_abc  ") == "plan_abc"
    assert _normalize_plan_id_arg("plan_xyz") == "plan_xyz"


# ---------------------------------------------------------------------------
# _split_planner_output
# ---------------------------------------------------------------------------

def test_split_planner_output_with_markers() -> None:
    from src.supervisor_agent.graph import _split_planner_output
    content = "prefix[PLANNER_REASONING]\nstep1\nstep2\n[/PLANNER_REASONING]\n{\"goal\":\"x\"}"
    reasoning, plan = _split_planner_output(content)
    assert "step1" in reasoning
    assert "step2" in reasoning
    assert '{"goal":"x"}' in plan


def test_split_planner_output_no_markers() -> None:
    from src.supervisor_agent.graph import _split_planner_output
    content = '{"goal":"x","steps":[]}'
    reasoning, plan = _split_planner_output(content)
    assert reasoning == ""
    assert plan == content


def test_split_planner_output_empty_markers() -> None:
    from src.supervisor_agent.graph import _split_planner_output
    content = "[PLANNER_REASONING][/PLANNER_REASONING]{\"goal\":\"x\"}"
    reasoning, plan = _split_planner_output(content)
    assert reasoning == ""


# ---------------------------------------------------------------------------
# _extract_plan_id_from_meta
# ---------------------------------------------------------------------------

def test_extract_plan_id_from_meta_success() -> None:
    from src.supervisor_agent.graph import _extract_plan_id_from_meta
    content = 'result [EXECUTOR_RESULT] {"status":"completed","plan_id":"plan_abc"}'
    assert _extract_plan_id_from_meta(content) == "plan_abc"


def test_extract_plan_id_from_meta_no_marker() -> None:
    from src.supervisor_agent.graph import _extract_plan_id_from_meta
    assert _extract_plan_id_from_meta("no marker here") is None


def test_extract_plan_id_from_meta_invalid_json() -> None:
    from src.supervisor_agent.graph import _extract_plan_id_from_meta
    content = "result [EXECUTOR_RESULT] {bad json}"
    assert _extract_plan_id_from_meta(content) is None


# ---------------------------------------------------------------------------
# _extract_registry_updates
# ---------------------------------------------------------------------------

def test_extract_registry_updates_success() -> None:
    from src.supervisor_agent.graph import _extract_registry_updates
    content = 'some text [EXECUTOR_REGISTRY_UPDATE] [{"plan_id":"p1","status":"completed","queryable":true}]'
    result = _extract_registry_updates(content)
    assert "p1" in result
    assert result["p1"].status == "completed"


def test_extract_registry_updates_no_marker() -> None:
    from src.supervisor_agent.graph import _extract_registry_updates
    assert _extract_registry_updates("no registry marker") == {}


# ---------------------------------------------------------------------------
# _try_auto_ingest_executor_result — Entry A wiring
# ---------------------------------------------------------------------------


class TestAutoIngestExecutorResult:
    """Verify Entry A auto-ingest handles completed and failed statuses."""

    def test_completed_status_triggers_ingest(self, tmp_path, monkeypatch):
        """completed status should trigger knowledge extraction."""
        from src.supervisor_agent.graph import _try_auto_ingest_executor_result

        ingested_chunks = []

        class FakeKT:
            def ingest(self, chunk, trigger="", source="", **kwargs):
                ingested_chunks.append(chunk)
                class Report:
                    nodes_ingested = 1
                    nodes_deduplicated = 0
                return Report()

        class FakeConfig:
            pass

        monkeypatch.setattr(
            "src.common.knowledge_tree.get_or_create_kt", lambda config: FakeKT()
        )
        monkeypatch.setattr(
            "src.common.knowledge_tree.config.KnowledgeTreeConfig",
            type("FakeKTConfig", (), {"from_context": staticmethod(lambda ctx: FakeConfig())}),
        )

        plan_json = json.dumps({
            "plan_id": "plan_test",
            "goal": "configure timeout settings",
            "steps": [
                {"step_id": "s1", "intent": "do", "status": "completed",
                 "result_summary": "发现最佳超时配置为 180s，避免长时间阻塞。", "failure_reason": ""},
            ],
        })
        content = f"发现最佳超时配置\n\n[EXECUTOR_RESULT] {{\"status\":\"completed\",\"updated_plan_json\":{json.dumps(plan_json)}}}"

        _try_auto_ingest_executor_result(content, None, "completed")

        assert len(ingested_chunks) > 0

    def test_failed_status_triggers_ingest(self, tmp_path, monkeypatch):
        """failed status should also trigger knowledge extraction (failure_reason as lessons)."""
        from src.supervisor_agent.graph import _try_auto_ingest_executor_result

        ingested_chunks = []

        class FakeKT:
            def ingest(self, chunk, trigger="", source="", **kwargs):
                ingested_chunks.append(chunk)
                class Report:
                    nodes_ingested = 1
                    nodes_deduplicated = 0
                return Report()

        class FakeConfig:
            pass

        monkeypatch.setattr(
            "src.common.knowledge_tree.get_or_create_kt", lambda config: FakeKT()
        )
        monkeypatch.setattr(
            "src.common.knowledge_tree.config.KnowledgeTreeConfig",
            type("FakeKTConfig", (), {"from_context": staticmethod(lambda ctx: FakeConfig())}),
        )

        plan_json = json.dumps({
            "plan_id": "plan_fail",
            "goal": "deploy",
            "steps": [
                {"step_id": "s1", "intent": "check env", "status": "failed",
                 "result_summary": "", "failure_reason": ".env encoding error: UTF-8 BOM."},
            ],
        })
        content = f"Deploy failed\n\n[EXECUTOR_RESULT] {{\"status\":\"failed\",\"updated_plan_json\":{json.dumps(plan_json)}}}"

        _try_auto_ingest_executor_result(content, None, "failed")

        assert len(ingested_chunks) > 0
        # failure_reason should appear in extracted chunks
        all_text = " ".join(ingested_chunks)
        assert "失败原因" in all_text or "encoding" in all_text.lower()

    def test_exception_does_not_propagate(self, monkeypatch):
        """KT failures should be silently caught, never breaking the graph."""
        from src.supervisor_agent.graph import _try_auto_ingest_executor_result

        def raise_error(config):
            raise RuntimeError("KT is broken")

        class FakeConfig:
            pass

        monkeypatch.setattr(
            "src.common.knowledge_tree.get_or_create_kt", raise_error
        )
        monkeypatch.setattr(
            "src.common.knowledge_tree.config.KnowledgeTreeConfig",
            type("FakeKTConfig", (), {"from_context": staticmethod(lambda ctx: FakeConfig())}),
        )

        # Should not raise
        _try_auto_ingest_executor_result("some content", None, "completed")

    def test_no_chunks_means_no_ingest(self, monkeypatch):
        """Empty executor result should not trigger any ingest calls."""
        from src.supervisor_agent.graph import _try_auto_ingest_executor_result

        ingest_called = []

        class FakeKT:
            def ingest(self, chunk, trigger="", source="", **kwargs):
                ingest_called.append(chunk)
                class Report:
                    nodes_ingested = 0
                    nodes_deduplicated = 0
                return Report()

        class FakeConfig:
            pass

        monkeypatch.setattr(
            "src.common.knowledge_tree.get_or_create_kt", lambda config: FakeKT()
        )
        monkeypatch.setattr(
            "src.common.knowledge_tree.config.KnowledgeTreeConfig",
            type("FakeKTConfig", (), {"from_context": staticmethod(lambda ctx: FakeConfig())}),
        )

        # Generic template content — should be filtered
        content = "执行成功\n\n[EXECUTOR_RESULT] {\"status\":\"completed\"}"

        _try_auto_ingest_executor_result(content, None, "completed")

        # "执行成功" is filtered by generic_template, so no ingest should happen
        assert len(ingest_called) == 0


def test_extract_registry_updates_invalid_json() -> None:
    from src.supervisor_agent.graph import _extract_registry_updates
    content = "[EXECUTOR_REGISTRY_UPDATE] [not valid json]"
    assert _extract_registry_updates(content) == {}


def test_extract_registry_updates_skips_entries_without_plan_id() -> None:
    from src.supervisor_agent.graph import _extract_registry_updates
    content = '[EXECUTOR_REGISTRY_UPDATE] [{"status":"running"},{"plan_id":"p2","status":"failed"}]'
    result = _extract_registry_updates(content)
    assert len(result) == 1
    assert "p2" in result


# ---------------------------------------------------------------------------
# _trim_task_history
# ---------------------------------------------------------------------------

def test_trim_task_history_under_limit_unchanged() -> None:
    from src.supervisor_agent.graph import _trim_task_history
    history = {f"plan_{i}": f"record_{i}" for i in range(10)}
    assert _trim_task_history(history) is history


def test_trim_task_history_over_limit_trims_oldest() -> None:
    from src.supervisor_agent.graph import _MAX_TASK_HISTORY, _trim_task_history
    history = {f"plan_{i:03d}": f"record_{i}" for i in range(60)}
    result = _trim_task_history(history)
    assert len(result) == _MAX_TASK_HISTORY
    # Oldest entries removed
    assert "plan_000" not in result
    # Newest entries kept
    assert "plan_059" in result


# ---------------------------------------------------------------------------
# _extract_snapshot_json
# ---------------------------------------------------------------------------

def test_extract_snapshot_json_success() -> None:
    from src.supervisor_agent.graph import _extract_snapshot_json
    content = 'result [EXECUTOR_RESULT] {"status":"paused","snapshot_json":"{\\"step\\":1}"}'
    assert _extract_snapshot_json(content) == '{"step":1}'


def test_extract_snapshot_json_empty_returns_none() -> None:
    from src.supervisor_agent.graph import _extract_snapshot_json
    content = 'result [EXECUTOR_RESULT] {"status":"completed","snapshot_json":""}'
    assert _extract_snapshot_json(content) is None


def test_extract_snapshot_json_no_marker() -> None:
    from src.supervisor_agent.graph import _extract_snapshot_json
    assert _extract_snapshot_json("no marker") is None


# ---------------------------------------------------------------------------
# _extract_dispatched_plan_id
# ---------------------------------------------------------------------------

def test_extract_dispatched_plan_id_success() -> None:
    from src.supervisor_agent.graph import _extract_dispatched_plan_id
    content = 'dispatched [EXECUTOR_DISPATCH] {"plan_id":"plan_dispatch_001"}'
    assert _extract_dispatched_plan_id(content) == "plan_dispatch_001"


def test_extract_dispatched_plan_id_no_marker() -> None:
    from src.supervisor_agent.graph import _extract_dispatched_plan_id
    assert _extract_dispatched_plan_id("no dispatch marker") is None


def test_extract_dispatched_plan_id_invalid_json() -> None:
    from src.supervisor_agent.graph import _extract_dispatched_plan_id
    content = "[EXECUTOR_DISPATCH] {not json}"
    assert _extract_dispatched_plan_id(content) is None
