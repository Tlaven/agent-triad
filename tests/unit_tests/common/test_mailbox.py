"""Unit tests for V3 in-memory mailbox."""

import asyncio

import pytest

from src.common.mailbox import Mailbox, MailboxItem


@pytest.fixture
def mailbox() -> Mailbox:
    return Mailbox()


def _snapshot(plan_id: str, rounds: int = 1) -> MailboxItem:
    return MailboxItem(
        item_type="snapshot",
        payload={"plan_id": plan_id, "tool_rounds": rounds},
    )


def _completion(plan_id: str, status: str = "completed") -> MailboxItem:
    return MailboxItem(
        item_type="completion",
        payload={"plan_id": plan_id, "status": status, "summary": "done"},
    )


async def test_post_and_read_snapshot(mailbox: Mailbox) -> None:
    await mailbox.post("p1", _snapshot("p1", 3))
    latest = await mailbox.get_latest_snapshot("p1")
    assert latest is not None
    assert latest.payload["tool_rounds"] == 3


async def test_latest_snapshot_returns_newest(mailbox: Mailbox) -> None:
    await mailbox.post("p1", _snapshot("p1", 1))
    await mailbox.post("p1", _snapshot("p1", 5))
    await mailbox.post("p1", _snapshot("p1", 10))
    latest = await mailbox.get_latest_snapshot("p1")
    assert latest.payload["tool_rounds"] == 10


async def test_has_completion_false_before_post(mailbox: Mailbox) -> None:
    assert await mailbox.has_completion("p1") is False


async def test_has_completion_true_after_post(mailbox: Mailbox) -> None:
    await mailbox.post("p1", _completion("p1"))
    assert await mailbox.has_completion("p1") is True


async def test_get_completion_returns_item(mailbox: Mailbox) -> None:
    await mailbox.post("p1", _completion("p1", "failed"))
    comp = await mailbox.get_completion("p1")
    assert comp is not None
    assert comp.payload["status"] == "failed"


async def test_wait_for_completion_returns_immediately_if_ready(mailbox: Mailbox) -> None:
    await mailbox.post("p1", _completion("p1"))
    result = await mailbox.wait_for_completion("p1", timeout=1.0)
    assert result is not None
    assert result.item_type == "completion"


async def test_wait_for_completion_times_out(mailbox: Mailbox) -> None:
    result = await mailbox.wait_for_completion("nonexistent", timeout=0.1, poll_interval=0.05)
    assert result is None


async def test_wait_for_completion_wakes_on_post(mailbox: Mailbox) -> None:
    """Post completion from another task; wait_for_completion should return."""

    async def delayed_post() -> None:
        await asyncio.sleep(0.05)
        await mailbox.post("p1", _completion("p1"))

    asyncio.get_event_loop().create_task(delayed_post())
    result = await mailbox.wait_for_completion("p1", timeout=2.0, poll_interval=0.1)
    assert result is not None


async def test_get_all_snapshots(mailbox: Mailbox) -> None:
    await mailbox.post("p1", _snapshot("p1", 1))
    await mailbox.post("p1", _snapshot("p1", 2))
    await mailbox.post("p1", _completion("p1"))
    await mailbox.post("p1", _snapshot("p1", 3))  # after completion, unlikely but valid

    snaps = await mailbox.get_all_snapshots("p1")
    assert len(snaps) == 3


async def test_clear_removes_all(mailbox: Mailbox) -> None:
    await mailbox.post("p1", _snapshot("p1", 1))
    await mailbox.post("p1", _completion("p1"))
    await mailbox.clear("p1")

    assert await mailbox.has_completion("p1") is False
    assert await mailbox.get_latest_snapshot("p1") is None


async def test_empty_mailbox_returns_none(mailbox: Mailbox) -> None:
    assert await mailbox.get_latest_snapshot("no_such_plan") is None
    assert await mailbox.get_completion("no_such_plan") is None
    assert await mailbox.get_all_snapshots("no_such_plan") == []
