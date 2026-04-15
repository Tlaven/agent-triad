"""Unit tests for MailboxHTTPServer thread and Mailbox thread-safety."""

import json
import time

import pytest

from src.common.mailbox import Mailbox, MailboxItem
from src.common.mailbox_server import MailboxHTTPServer, MAILBOX_PORT_FILE


@pytest.fixture
def mailbox() -> Mailbox:
    return Mailbox()


@pytest.fixture
def server(mailbox: Mailbox) -> MailboxHTTPServer:
    """Start a MailboxHTTPServer on a dynamic port."""
    srv = MailboxHTTPServer(mailbox, port=0)
    srv.start()
    # Wait for the server to be ready
    deadline = time.monotonic() + 5.0
    while srv.port == 0 and time.monotonic() < deadline:
        time.sleep(0.05)
    assert srv.port > 0, "Server did not start within timeout"
    yield srv
    srv.stop()


def test_server_starts_on_dynamic_port(server: MailboxHTTPServer) -> None:
    assert server.port > 0
    assert server.base_url.startswith("http://127.0.0.1:")
    assert MAILBOX_PORT_FILE.exists()
    assert MAILBOX_PORT_FILE.read_text() == str(server.port)


def test_server_health_endpoint(server: MailboxHTTPServer) -> None:
    import urllib.request
    resp = urllib.request.urlopen(f"{server.base_url}/health")
    assert resp.status == 200
    data = json.loads(resp.read())
    assert data["status"] == "ok"


def test_post_completion_to_inbox(server: MailboxHTTPServer, mailbox: Mailbox) -> None:
    import urllib.request
    payload = json.dumps({
        "plan_id": "plan_test_1",
        "item_type": "completion",
        "payload": {"status": "completed", "summary": "done"},
    }).encode()

    req = urllib.request.Request(
        f"{server.base_url}/inbox",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    resp = urllib.request.urlopen(req)
    assert resp.status == 200
    data = json.loads(resp.read())
    assert data["ok"] is True

    # Verify the completion is readable from mailbox
    assert mailbox._has_completion_sync("plan_test_1")
    comp = mailbox._get_completion_sync("plan_test_1")
    assert comp is not None
    assert comp.payload["status"] == "completed"


def test_post_status_to_inbox(server: MailboxHTTPServer, mailbox: Mailbox) -> None:
    import urllib.request
    payload = json.dumps({
        "plan_id": "plan_test_2",
        "item_type": "status",
        "payload": {"current_step": "step_1", "tool_rounds": 3},
    }).encode()

    req = urllib.request.Request(
        f"{server.base_url}/inbox",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    resp = urllib.request.urlopen(req)
    assert resp.status == 200

    # Verify the status is readable
    status = mailbox._latest_status_sync("plan_test_2")
    assert status is not None
    assert status.payload["current_step"] == "step_1"


def test_post_invalid_json_returns_400(server: MailboxHTTPServer) -> None:
    import urllib.request
    import urllib.error
    req = urllib.request.Request(
        f"{server.base_url}/inbox",
        data=b"not json",
        headers={"Content-Type": "application/json"},
    )
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(req)
    assert exc_info.value.code == 400


def test_post_missing_fields_returns_400(server: MailboxHTTPServer) -> None:
    import urllib.request
    import urllib.error
    payload = json.dumps({"plan_id": "p1"}).encode()
    req = urllib.request.Request(
        f"{server.base_url}/inbox",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(req)
    assert exc_info.value.code == 400


def test_server_stop_cleans_port_file(server: MailboxHTTPServer) -> None:
    port = server.port
    server.stop()
    assert not server.is_alive()
    assert not MAILBOX_PORT_FILE.exists()


def test_mailbox_sync_methods(mailbox: Mailbox) -> None:
    """Sync methods work correctly for cross-thread access."""
    mailbox._post_sync("p1", MailboxItem(
        item_type="status",
        payload={"step": "step_1"},
    ))
    assert mailbox._latest_status_sync("p1") is not None
    assert mailbox._latest_status_sync("p1").payload["step"] == "step_1"

    mailbox._post_sync("p1", MailboxItem(
        item_type="completion",
        payload={"status": "completed"},
    ))
    assert mailbox._has_completion_sync("p1")
    assert mailbox._get_completion_sync("p1").payload["status"] == "completed"

    mailbox._clear_sync("p1")
    assert not mailbox._has_completion_sync("p1")
    assert mailbox._latest_status_sync("p1") is None


def test_mailbox_async_api_still_works(mailbox: Mailbox) -> None:
    """Async methods (backward-compatible API) work via sync delegation."""

    async def _test():
        await mailbox.post("p1", MailboxItem(
            item_type="completion",
            payload={"status": "completed", "summary": "done"},
        ))
        assert await mailbox.has_completion("p1")
        comp = await mailbox.get_completion("p1")
        assert comp.payload["summary"] == "done"

        await mailbox.remove("p1")
        assert not await mailbox.has_completion("p1")

    import asyncio
    asyncio.get_event_loop().run_until_complete(_test())
