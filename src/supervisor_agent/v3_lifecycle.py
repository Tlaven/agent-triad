"""V3 infrastructure lifecycle manager.

Lazy singleton: Executor subprocess + callback server are started once,
on the first call_model invocation where enable_v3_parallel=True.
They persist across multiple graph.ainvoke() calls within the same process.

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
    """Holds references to all V3 async resources."""

    process_manager: Any  # ExecutorProcessManager
    callback_server: Any  # uvicorn.Server
    callback_task: asyncio.Task | None = None
    mailbox: Any = None  # Mailbox
    started: bool = False


class V3LifecycleManager:
    """Async-safe lazy singleton for V3 infrastructure."""

    def __init__(self) -> None:
        self._infra: V3Infrastructure | None = None
        self._lock = asyncio.Lock()
        self._shutting_down = False

    async def ensure_started(self, ctx: Context) -> V3Infrastructure:
        """Start V3 infrastructure if not already running. Idempotent.

        Safe to call from any asyncio task on the same event loop.
        """
        if self._shutting_down:
            raise RuntimeError("V3 lifecycle manager is shutting down")

        # Fast path: already started and Executor is still alive
        if self._infra is not None and self._infra.started:
            if self._infra.process_manager.is_running:
                return self._infra
            # Executor process died — restart
            logger.warning("Executor process died, restarting...")
            async with self._lock:
                # Double-check after acquiring lock
                if self._infra is not None and self._infra.started:
                    await self._stop_internal()
                # Fall through to start below

        async with self._lock:
            # Double-check after acquiring lock
            if self._infra is not None and self._infra.started:
                if self._infra.process_manager.is_running:
                    return self._infra
                await self._stop_internal()

            infra = await self._start(ctx)
            self._infra = infra
            return infra

    async def _start(self, ctx: Context) -> V3Infrastructure:
        """Internal start. Caller must hold self._lock."""
        import uvicorn

        from src.common.mailbox import Mailbox
        from src.common.process_manager import ExecutorProcessManager
        from src.supervisor_agent.callback_server import callback_app, set_mailbox

        mailbox = Mailbox()
        set_mailbox(mailbox)

        config = uvicorn.Config(
            callback_app,
            host="0.0.0.0",
            port=ctx.supervisor_callback_port,
            log_level="warning",
        )
        server = uvicorn.Server(config)
        callback_task = asyncio.create_task(server.serve(), name="callback_server")

        pm = ExecutorProcessManager(ctx)
        await pm.start()

        logger.info(
            "V3 infrastructure started: Executor on port %d, callback on port %d",
            ctx.executor_port,
            ctx.supervisor_callback_port,
        )

        infra = V3Infrastructure(
            process_manager=pm,
            callback_server=server,
            callback_task=callback_task,
            mailbox=mailbox,
            started=True,
        )

        atexit.register(self._sync_cleanup)
        return infra

    async def _stop_internal(self) -> None:
        """Stop infrastructure. Caller must hold self._lock."""
        if self._infra is None:
            return

        infra = self._infra
        self._infra = None

        try:
            await infra.process_manager.stop()
        except Exception:
            logger.exception("Error stopping Executor process")

        infra.callback_server.should_exit = True
        if infra.callback_task is not None:
            try:
                await asyncio.wait_for(infra.callback_task, timeout=5.0)
            except asyncio.TimeoutError:
                infra.callback_task.cancel()
            except Exception:
                logger.exception("Error stopping callback server")

        infra.started = False
        logger.info("V3 infrastructure stopped")

    async def stop(self) -> None:
        """Public stop. Acquires lock."""
        async with self._lock:
            await self._stop_internal()
            self._shutting_down = True

    def _sync_cleanup(self) -> None:
        """atexit handler — best-effort synchronous cleanup."""
        if self._infra is None:
            return
        infra = self._infra
        if infra.process_manager and infra.process_manager.is_running:
            try:
                infra.process_manager._process.terminate()
                infra.process_manager._process.wait(timeout=3)
            except Exception:
                try:
                    infra.process_manager._process.kill()
                except Exception:
                    pass


# Module-level singleton — one per process
v3_manager = V3LifecycleManager()
