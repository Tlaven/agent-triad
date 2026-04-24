"""Unit tests for ExecutorPoller staleness detection and auto-unregister."""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.common.mailbox import Mailbox, MailboxItem
from src.common.polling import ExecutorPoller, _Registration


@pytest.fixture
def mailbox() -> Mailbox:
    return Mailbox()


@pytest.fixture
def poller(mailbox: Mailbox) -> ExecutorPoller:
    return ExecutorPoller(
        mailbox,
        interval=0.1,
        max_staleness=5.0,
        max_consecutive_failures=3,
    )


def _registration(
    plan_json: str = "",
    base_url: str = "http://localhost:9999",
    age: float = 0.0,
    failures: int = 0,
) -> _Registration:
    return _Registration(
        plan_json=plan_json,
        base_url=base_url,
        registered_at=time.monotonic() - age,
        consecutive_failures=failures,
    )


# ---------------------------------------------------------------------------
# get_plan_json
# ---------------------------------------------------------------------------


def test_get_plan_json_returns_cached_value(poller: ExecutorPoller) -> None:
    poller.register("p1", plan_json='{"goal":"test"}', executor_base_url="http://localhost:1")
    assert poller.get_plan_json("p1") == '{"goal":"test"}'


def test_get_plan_json_returns_empty_for_unknown(poller: ExecutorPoller) -> None:
    assert poller.get_plan_json("no_such_plan") == ""


# ---------------------------------------------------------------------------
# Staleness detection
# ---------------------------------------------------------------------------


async def test_stale_registration_posts_synthetic_failure(
    poller: ExecutorPoller, mailbox: Mailbox
) -> None:
    """Registration older than max_staleness auto-unregisters with synthetic failure."""
    # Simulate an old registration
    poller._active["p_stale"] = _registration(
        plan_json='{"plan_id":"p_stale","steps":[]}',
        base_url="http://localhost:9999",
        age=10.0,  # > max_staleness=5.0
    )

    # Run _poll_one (no real HTTP call needed — staleness check happens first)
    mock_client = MagicMock()
    await poller._poll_one(mock_client, "p_stale")

    # Should be unregistered
    assert "p_stale" not in poller._active

    # Should have posted a synthetic failure to mailbox
    comp = await mailbox.get_completion("p_stale")
    assert comp is not None
    assert comp.payload["status"] == "failed"
    assert "stale" in comp.payload["summary"].lower()


# ---------------------------------------------------------------------------
# Consecutive failure detection
# ---------------------------------------------------------------------------


async def test_consecutive_failures_posts_synthetic_failure(
    poller: ExecutorPoller, mailbox: Mailbox
) -> None:
    """Exceeding max_consecutive_failures auto-unregisters with synthetic failure."""
    poller._active["p_fail"] = _registration(
        plan_json='{"plan_id":"p_fail","steps":[]}',
        base_url="http://localhost:9999",
        failures=3,  # == max_consecutive_failures
    )

    mock_client = MagicMock()
    await poller._poll_one(mock_client, "p_fail")

    assert "p_fail" not in poller._active

    comp = await mailbox.get_completion("p_fail")
    assert comp is not None
    assert comp.payload["status"] == "failed"
    assert "unreachable" in comp.payload["summary"].lower()


async def test_successful_poll_resets_failure_counter(
    poller: ExecutorPoller,
) -> None:
    """A successful HTTP 200 with non-terminal status resets consecutive_failures."""
    poller._active["p_run"] = _registration(
        base_url="http://localhost:9999",
        failures=2,
    )

    # Mock a 200 response with running status
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"status": "running"}

    mock_client = MagicMock()
    mock_client.get = AsyncMock(return_value=mock_resp)

    await poller._poll_one(mock_client, "p_run")

    # Should still be registered (running is not terminal)
    assert "p_run" in poller._active
    # Failure counter should be reset
    assert poller._active["p_run"].consecutive_failures == 0


async def test_404_resets_failure_counter(
    poller: ExecutorPoller,
) -> None:
    """HTTP 404 resets consecutive_failures (task might still be starting)."""
    poller._active["p_404"] = _registration(
        base_url="http://localhost:9999",
        failures=2,
    )

    mock_resp = MagicMock()
    mock_resp.status_code = 404

    mock_client = MagicMock()
    mock_client.get = AsyncMock(return_value=mock_resp)

    await poller._poll_one(mock_client, "p_404")

    assert "p_404" in poller._active
    assert poller._active["p_404"].consecutive_failures == 0


async def test_exception_increments_failure_counter(
    poller: ExecutorPoller,
) -> None:
    """Connection exception increments consecutive_failures."""
    import httpx

    poller._active["p_err"] = _registration(
        base_url="http://localhost:9999",
        failures=0,
    )

    mock_client = MagicMock()
    mock_client.get = AsyncMock(side_effect=httpx.ConnectError("refused"))

    await poller._poll_one(mock_client, "p_err")

    assert "p_err" in poller._active
    assert poller._active["p_err"].consecutive_failures == 1


# ---------------------------------------------------------------------------
# Mailbox already has completion
# ---------------------------------------------------------------------------


async def test_skips_poll_if_mailbox_has_completion(
    poller: ExecutorPoller, mailbox: Mailbox
) -> None:
    """If Mailbox already has a completion, poller skips and unregisters."""
    await mailbox.post(
        "p_done",
        MailboxItem(
            item_type="completion",
            payload={"status": "completed", "summary": "done"},
        ),
    )
    poller._active["p_done"] = _registration(base_url="http://localhost:9999")

    mock_client = MagicMock()
    await poller._poll_one(mock_client, "p_done")

    # Should be unregistered
    assert "p_done" not in poller._active
    # No HTTP call made
    mock_client.get.assert_not_called()


# ---------------------------------------------------------------------------
# Terminal status handling (existing behavior)
# ---------------------------------------------------------------------------


async def test_terminal_status_posts_to_mailbox_and_unregisters(
    poller: ExecutorPoller, mailbox: Mailbox
) -> None:
    """HTTP 200 with terminal status posts to Mailbox and unregisters."""
    poller._active["p_term"] = _registration(base_url="http://localhost:9999")

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "status": "completed",
        "summary": "all done",
    }

    mock_client = MagicMock()
    mock_client.get = AsyncMock(return_value=mock_resp)

    await poller._poll_one(mock_client, "p_term")

    assert "p_term" not in poller._active
    comp = await mailbox.get_completion("p_term")
    assert comp is not None
    assert comp.payload["status"] == "completed"
