"""Unit tests for cross-process protocol data structures."""

import json

from src.common.executor_protocol import (
    ExecuteRequest,
    ExecuteStatus,
    ExecutorResultResponse,
    StopRequest,
)
from dataclasses import asdict


def test_execute_request_defaults() -> None:
    req = ExecuteRequest(plan_json='{"steps":[]}', plan_id="p1")
    assert req.plan_id == "p1"
    assert req.config == {}


def test_execute_request_serializable() -> None:
    req = ExecuteRequest(
        plan_json='{"goal":"test"}',
        plan_id="p1",
        executor_session_id="s1",
        config={"key": "val"},
    )
    d = asdict(req)
    json.dumps(d)  # should not raise


def test_execute_status_has_timestamp() -> None:
    status = ExecuteStatus(plan_id="p1", status="running")
    assert status.started_at  # non-empty ISO timestamp
    assert status.tool_rounds == 0


def test_executor_result_response() -> None:
    resp = ExecutorResultResponse(status="completed", summary="done")
    d = asdict(resp)
    assert d["status"] == "completed"
    assert d["updated_plan_json"] == ""


def test_stop_request_reason() -> None:
    stop = StopRequest(reason="timeout exceeded")
    assert stop.reason == "timeout exceeded"
