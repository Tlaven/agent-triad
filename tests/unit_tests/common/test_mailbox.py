"""Unit tests for V3 in-memory mailbox (Pull mode — completion only)."""

import pytest

from src.common.mailbox import Mailbox, MailboxItem


@pytest.fixture
def mailbox() -> Mailbox:
    return Mailbox()


def _completion(plan_id: str, status: str = "completed") -> MailboxItem:
    return MailboxItem(
        item_type="completion",
        payload={"plan_id": plan_id, "status": status, "summary": "done"},
    )


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


async def test_clear_removes_all(mailbox: Mailbox) -> None:
    await mailbox.post("p1", _completion("p1"))
    await mailbox.clear("p1")
    assert await mailbox.has_completion("p1") is False


async def test_empty_mailbox_returns_none(mailbox: Mailbox) -> None:
    assert await mailbox.get_completion("no_such_plan") is None


async def test_remove_deletes_mailbox(mailbox: Mailbox) -> None:
    await mailbox.post("p1", _completion("p1"))
    await mailbox.remove("p1")
    assert await mailbox.get_completion("p1") is None
    assert await mailbox.has_completion("p1") is False


async def test_set_and_get_mailbox() -> None:
    """set_mailbox / get_mailbox module-level functions work."""
    from src.common.mailbox import set_mailbox, get_mailbox

    mb = Mailbox()
    set_mailbox(mb)
    assert get_mailbox() is mb


async def test_get_mailbox_raises_when_not_set() -> None:
    """get_mailbox raises RuntimeError when not initialized."""
    from src.common.mailbox import get_mailbox as _get

    # Reset global state
    import src.common.mailbox as _mod
    _mod._mailbox = None
    with pytest.raises(RuntimeError, match="Mailbox not initialized"):
        _get()
