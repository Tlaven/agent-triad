"""Executor FastAPI 服务端点的集成测试。

Uses httpx.AsyncClient with ASGITransport to test the server in-process.
"""

import asyncio
import json

import httpx
import pytest

from src.executor_agent.server import (
    ExecuteRequestBody,
    StopRequestBody,
    _running_tasks,
    _stop_events,
    _results,
    _statuses,
    app,
)


@pytest.fixture(autouse=True)
def _clear_state():
    """Clean up server state between tests."""
    _running_tasks.clear()
    _stop_events.clear()
    _results.clear()
    _statuses.clear()
    yield
    # Cancel any leftover tasks
    for pid, task in list(_running_tasks.items()):
        task.cancel()
    _running_tasks.clear()
    _stop_events.clear()
    _results.clear()
    _statuses.clear()


@pytest.fixture
async def client():
    """httpx async client using ASGI transport."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_health(client):
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


async def test_execute_missing_plan(client):
    """Execute endpoint requires plan_json and plan_id."""
    resp = await client.post("/execute", json={"plan_id": "p1"})
    assert resp.status_code == 422


async def test_get_status_not_found(client):
    resp = await client.get("/status/nonexistent")
    assert resp.status_code == 404


async def test_get_result_not_found(client):
    resp = await client.get("/result/nonexistent")
    assert resp.status_code == 404


async def test_stop_not_found(client):
    resp = await client.post("/stop/nonexistent", json={"reason": "test"})
    assert resp.status_code == 404


async def test_execute_accepts_valid_plan(client):
    """POST /execute with a valid plan should return accepted."""
    plan = {
        "plan_id": "plan_test_001",
        "version": 1,
        "goal": "test goal",
        "steps": [
            {
                "step_id": "step_1",
                "intent": "test intent",
                "expected_output": "test output",
                "status": "pending",
            }
        ],
    }
    resp = await client.post(
        "/execute",
        json={
            "plan_json": json.dumps(plan),
            "plan_id": "plan_test_001",
            "callback_url": "",
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["plan_id"] == "plan_test_001"
    assert data["status"] == "accepted"

    # Status should be tracked
    status_resp = await client.get("/status/plan_test_001")
    assert status_resp.status_code == 200
    assert status_resp.json()["plan_id"] == "plan_test_001"


async def test_stop_sets_flag(client):
    """POST /stop sets the asyncio.Event for graceful exit."""
    plan_id = "plan_stop_test"
    _stop_events[plan_id] = asyncio.Event()

    resp = await client.post(f"/stop/{plan_id}", json={"reason": "test stop"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["acknowledged"] is True
    assert _stop_events[plan_id].is_set()


async def test_execute_duplicate_rejected(client):
    """Duplicate plan_id should be rejected with 409."""
    plan_id = "plan_dup"
    _stop_events[plan_id] = asyncio.Event()
    _running_tasks[plan_id] = asyncio.create_task(asyncio.sleep(100))

    plan = {"plan_id": plan_id, "steps": []}
    resp = await client.post(
        "/execute",
        json={"plan_json": json.dumps(plan), "plan_id": plan_id},
    )
    assert resp.status_code == 409

    # Clean up
    _running_tasks[plan_id].cancel()
