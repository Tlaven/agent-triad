"""Lightweight HTTP server thread for receiving Executor push messages.

Runs in a separate thread inside the Supervisor process.
Executor subprocesses POST their results and status updates here.

Architecture:
    Executor Process ──POST /inbox──> MailboxHTTPServer (thread) ──> Mailbox (shared storage)
    Supervisor asyncio loop ──reads──> Mailbox (shared storage)

Uses stdlib http.server — zero dependencies, Windows-compatible.
"""

from __future__ import annotations

import json
import logging
import socket
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from typing import Any

from src.common.mailbox import Mailbox, MailboxItem

logger = logging.getLogger(__name__)

# Port file for discovery (same pattern as logs/executor.port)
MAILBOX_PORT_FILE = Path("logs/mailbox.port")


class _MailboxHTTPServer(HTTPServer):
    """HTTPServer subclass that carries a mailbox reference for per-request handlers."""

    mailbox: Mailbox


class _InboxHandler(BaseHTTPRequestHandler):
    """Handles POST /inbox from Executor processes."""

    def _get_mailbox(self) -> Mailbox:
        """Retrieve mailbox from the server instance (not class-level)."""
        return self.server.mailbox  # type: ignore[attr-defined]

    def do_POST(self) -> None:
        if self.path != "/inbox":
            self.send_response(404)
            self.end_headers()
            return

        try:
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length)
            data = json.loads(body)
        except (json.JSONDecodeError, ValueError) as e:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(json.dumps({"error": f"Invalid JSON: {e}"}).encode())
            return

        plan_id = data.get("plan_id")
        item_type = data.get("item_type")
        payload = data.get("payload", {})

        if not plan_id or not item_type:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(json.dumps({"error": "Missing plan_id or item_type"}).encode())
            return

        if item_type not in ("completion", "status"):
            self.send_response(400)
            self.end_headers()
            self.wfile.write(json.dumps({"error": f"Unknown item_type: {item_type}"}).encode())
            return

        item = MailboxItem(item_type=item_type, payload=payload)
        self._get_mailbox()._post_sync(plan_id, item)

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"ok": True, "plan_id": plan_id}).encode())

    def do_GET(self) -> None:
        if self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "ok"}).encode())
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format: str, *args: Any) -> None:
        # Suppress default stderr logging; use Python logging instead
        logger.debug("MailboxHTTPServer: %s", format % args)


class MailboxHTTPServer(threading.Thread):
    """Background thread running a lightweight HTTP server for Executor push messages.

    Usage:
        server = MailboxHTTPServer(mailbox, port=0)
        server.start()   # starts the thread
        ...
        server.stop()    # stops the server and joins the thread
    """

    def __init__(self, mailbox: Mailbox, port: int = 0, host: str = "127.0.0.1") -> None:
        super().__init__(daemon=True, name="mailbox-http-server")
        self.mailbox = mailbox
        self._host = host
        self._requested_port = port
        self._server: HTTPServer | None = None
        self._port: int = 0
        self._base_url: str = ""
        self._stop_event = threading.Event()

    @property
    def port(self) -> int:
        """Actual port the server is listening on. 0 until started."""
        return self._port

    @property
    def base_url(self) -> str:
        """Full base URL, e.g. 'http://127.0.0.1:12345'."""
        return self._base_url

    def run(self) -> None:
        """Thread entry point — starts the HTTP server (blocking)."""
        # Bind to find actual port
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((self._host, self._requested_port))
        sock.listen(5)
        actual_port = sock.getsockname()[1]

        self._port = actual_port
        self._base_url = f"http://{self._host}:{actual_port}"

        # Write port file
        MAILBOX_PORT_FILE.parent.mkdir(parents=True, exist_ok=True)
        MAILBOX_PORT_FILE.write_text(str(actual_port))

        # Create HTTPServer with the pre-bound socket
        self._server = _MailboxHTTPServer((self._host, actual_port), _InboxHandler)
        self._server.socket = sock  # replace the server's socket with our bound one
        self._server.mailbox = self.mailbox  # instance-level, not class-level

        # Set a short timeout so we can check _stop_event periodically
        self._server.timeout = 0.5

        logger.info(
            "MailboxHTTPServer started on %s (port=%d)",
            self._base_url, actual_port,
        )

        # Serve until stop requested
        while not self._stop_event.is_set():
            self._server.handle_request()

        # Cleanup
        self._server.server_close()
        MAILBOX_PORT_FILE.unlink(missing_ok=True)
        logger.info("MailboxHTTPServer stopped (port=%d)", actual_port)

    def stop(self) -> None:
        """Signal the server to stop and wait for the thread to finish."""
        self._stop_event.set()
        self.join(timeout=5.0)
        if self.is_alive():
            logger.warning("MailboxHTTPServer thread did not stop within timeout")
