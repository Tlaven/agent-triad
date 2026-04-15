"""V4 infrastructure lifecycle manager.

Lazy singleton: starts the Mailbox HTTP server thread on first call.
Per-task Executor processes are created on-demand by call_executor.

No callback server — Executor pushes results to Mailbox HTTP thread (Push mode).

Cleanup happens via atexit or explicit stop().
"""

from __future__ import annotations

import asyncio
import atexit
import logging
from dataclasses import dataclass
from typing import Any

from src.common.context import Context

logger = logging.getLogger(__name__)


@dataclass
class V3Infrastructure:
    """Holds references to all V3/V4 async resources."""

    process_manager: Any  # ExecutorProcessManager
    mailbox: Any = None  # Mailbox
    mailbox_server: Any = None  # MailboxHTTPServer thread
    poller: Any = None  # ExecutorPoller
    started: bool = False


class V3LifecycleManager:
    """Async-safe lazy singleton for V3/V4 infrastructure."""

    def __init__(self) -> None:
        self._infra: V3Infrastructure | None = None
        self._lock = asyncio.Lock()
        self._shutting_down = False
        self._atexit_registered = False

    async def ensure_started(self, ctx: Context) -> V3Infrastructure:
        """Start V4 infrastructure (Mailbox thread + ProcessManager).

        Does NOT pre-start any Executor subprocess — those are created
        per-task by call_executor.
        """
        if self._shutting_down:
            raise RuntimeError("V3 lifecycle manager is shutting down")

        async with self._lock:
            if self._infra is not None and self._infra.started:
                return self._infra

            infra = await self._start(ctx)
            self._infra = infra
            return infra

    async def _start(self, ctx: Context) -> V3Infrastructure:
        """Internal start. Caller must hold self._lock."""
        from src.common.mailbox import Mailbox, set_mailbox
        from src.common.mailbox_server import MailboxHTTPServer
        from src.common.polling import ExecutorPoller
        from src.common.process_manager import ExecutorProcessManager

        mailbox = Mailbox()
        set_mailbox(mailbox)

        # Start Mailbox HTTP server thread
        mailbox_server = MailboxHTTPServer(mailbox, port=ctx.mailbox_port)
        mailbox_server.start()

        logger.info(
            "V4 infrastructure started: Mailbox server on port %d",
            mailbox_server.port,
        )

        pm = ExecutorProcessManager(ctx)

        # Start unified background poller (base_url set later when first Executor starts)
        poller = ExecutorPoller(mailbox)
        poller.start()

        infra = V3Infrastructure(
            process_manager=pm,
            mailbox=mailbox,
            mailbox_server=mailbox_server,
            poller=poller,
            started=True,
        )

        if not self._atexit_registered:
            atexit.register(self._sync_cleanup)
            self._atexit_registered = True

        return infra

    async def _stop_internal(self) -> None:
        """Stop infrastructure. Caller must hold self._lock."""
        if self._infra is None:
            return

        infra = self._infra
        self._infra = None

        # Stop unified background poller
        if infra.poller:
            try:
                await infra.poller.stop()
            except Exception:
                logger.exception("Error stopping ExecutorPoller")

        # Stop all Executor processes
        try:
            await infra.process_manager.stop()
        except Exception:
            logger.exception("Error stopping Executor processes")

        # Stop Mailbox HTTP server thread
        if infra.mailbox_server:
            try:
                infra.mailbox_server.stop()
            except Exception:
                logger.exception("Error stopping Mailbox server")

        infra.started = False
        logger.info("V4 infrastructure stopped")

    async def stop(self) -> None:
        """Public stop. Acquires lock. Marks as shutting down."""
        async with self._lock:
            await self._stop_internal()
            self._shutting_down = True

    def _sync_cleanup(self) -> None:
        """atexit handler — best-effort synchronous cleanup."""
        if self._infra is None:
            return
        infra = self._infra
        if infra.process_manager:
            infra.process_manager.sync_terminate()
        if infra.mailbox_server:
            infra.mailbox_server.stop()


# Module-level singleton — one per process
v3_manager = V3LifecycleManager()
