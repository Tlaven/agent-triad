"""Unit tests for V3 Supervisor Callback Server."""

import asyncio

import httpx
import pytest

from src.common.mailbox import Mailbox, MailboxItem
from src.supervisor_agent.callback_server import (
    callback_app,
    get_mailbox,
    set_mailbox,
)


@pytest.fixture
def mailbox():
    mb = Mailbox()
    set_mailbox(mb)
    return mb


@pytest.fixture
async def client(mailbox):
    transport = httpx.ASGITransport(app=callback_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_callback_health(client):
    resp = await client.get("/health")
    assert resp.status_code == 200


async def test_receive_snapshot(client, mailbox):
    resp = await client.post(
        "/callback/snapshot",
        json={
            "plan_id": "p1",
            "tool_rounds": 5,
            "current_step": "step_2",
            "completed_steps": 1,
            "total_steps": 3,
        },
    )
    assert resp.status_code == 200

    # Verify it's in the mailbox
    snap = await mailbox.get_latest_snapshot("p1")
    assert snap is not None
    assert snap.payload["tool_rounds"] == 5


async def test_receive_completion(client, mailbox):
    resp = await client.post(
        "/callback/completed",
        json={
            "plan_id": "p1",
            "status": "completed",
            "summary": "All done",
            "updated_plan_json": "{}",
        },
    )
    assert resp.status_code == 200

    comp = await mailbox.get_completion("p1")
    assert comp is not None
    assert comp.payload["status"] == "completed"


async def test_read_mailbox(client, mailbox):
    # Post a snapshot and completion
    await client.post(
        "/callback/snapshot",
        json={"plan_id": "p2", "tool_rounds": 3},
    )
    await client.post(
        "/callback/completed",
        json={"plan_id": "p2", "status": "failed", "summary": "error"},
    )

    resp = await client.get("/mailbox/p2")
    assert resp.status_code == 200
    data = resp.json()
    assert data["has_completion"] is True
    assert len(data["snapshots"]) == 1
    assert data["completion"]["status"] == "failed"


async def test_read_empty_mailbox(client, mailbox):
    resp = await client.get("/mailbox/nonexistent")
    assert resp.status_code == 200
    data = resp.json()
    assert data["has_completion"] is False
    assert data["snapshots"] == []
    assert data["completion"] is None
