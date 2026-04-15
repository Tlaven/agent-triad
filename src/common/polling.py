"""Unified background polling task for Executor results.

Instead of scattering individual HTTP requests across graph nodes and tools,
a single ExecutorPoller asyncio.Task periodically checks all active plan IDs
and writes completions to the Mailbox.

Usage:
    poller = ExecutorPoller(mailbox, base_url="http://localhost:8765")
    poller.start()                              # launch background task
    poller.register("plan_abc", plan_json)      # start tracking a task
    await poller.force_poll_once()              # immediate flush before LLM call
    poller.unregister("plan_abc")               # stop tracking (terminal state)
    await poller.stop()                         # cancel background task
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.common.mailbox import Mailbox

logger = logging.getLogger(__name__)

_TERMINAL_STATUSES = frozenset({"completed", "failed", "stopped"})


class ExecutorPoller:
    """Single background asyncio.Task that polls all active plan IDs.

    Design goals:
    - One shared httpx.AsyncClient (connection-pool reuse).
    - asyncio.Semaphore limits concurrent in-flight requests.
    - force_poll_once() lets callers request an immediate sweep.
    - plan_json cache stores the original plan for _mark_plan_steps_failed fallback.
    """

    def __init__(
        self,
        mailbox: "Mailbox",
        interval: float = 1.5,
        max_concurrent: int = 5,
    ) -> None:
        self._mailbox = mailbox
        self._interval = interval
        self._semaphore = asyncio.Semaphore(max_concurrent)

        # Active tracking: plan_id -> plan_json (may be "")
        self._active: dict[str, str] = {}
        self._active_lock = asyncio.Lock()

        # base_url is set when the Executor process is ready (fallback for register)
        self._base_url: str = ""

        # plan_id -> Executor HTTP base when multiple subprocesses are active
        self._executor_base_urls: dict[str, str] = {}

        self._task: asyncio.Task | None = None
        # Event to trigger an immediate extra sweep
        self._force_event = asyncio.Event()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def set_base_url(self, base_url: str) -> None:
        """Update the default Executor base URL (fallback when register omits url)."""
        self._base_url = base_url

    def start(self) -> None:
        """Launch the background polling loop (idempotent)."""
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.create_task(
            self._poll_loop(), name="executor-poller"
        )
        logger.info("ExecutorPoller started (interval=%.1fs)", self._interval)

    async def stop(self) -> None:
        """Cancel the background task and wait for it to finish."""
        if self._task is not None and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
        self._task = None
        logger.info("ExecutorPoller stopped")

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(
        self,
        plan_id: str,
        plan_json: str = "",
        *,
        executor_base_url: str | None = None,
    ) -> None:
        """Start tracking a plan_id. Stores plan_json for fallback use."""
        self._active[plan_id] = plan_json
        url = (executor_base_url or "").strip() or self._base_url
        if url:
            self._executor_base_urls[plan_id] = url
        logger.debug("ExecutorPoller: registered plan_id=%s", plan_id)

    def unregister(self, plan_id: str) -> None:
        """Stop tracking a plan_id (call after terminal state is processed)."""
        self._active.pop(plan_id, None)
        self._executor_base_urls.pop(plan_id, None)
        logger.debug("ExecutorPoller: unregistered plan_id=%s", plan_id)

    def get_plan_json(self, plan_id: str) -> str:
        """Return the cached plan_json for a plan_id (empty string if unknown)."""
        return self._active.get(plan_id, "")

    # ------------------------------------------------------------------
    # On-demand sweep
    # ------------------------------------------------------------------

    def _any_poll_base(self) -> bool:
        """True if any active plan can be polled (per-plan URL or fallback)."""
        if not self._active:
            return False
        if self._base_url:
            return True
        return any(self._executor_base_urls.get(pid) for pid in self._active)

    async def force_poll_once(self) -> None:
        """Trigger an immediate extra sweep and wait for it to complete.

        Called by call_model and dynamic_tools_node so LLM always sees
        up-to-date Mailbox state before making a decision.
        """
        if not self._active or not self._any_poll_base():
            return
        await self._sweep()

    # ------------------------------------------------------------------
    # Internal loop
    # ------------------------------------------------------------------

    async def _poll_loop(self) -> None:
        """Main loop: sweep every interval or when force_event fires."""
        import httpx

        async with httpx.AsyncClient(timeout=3.0) as client:
            while True:
                try:
                    # Wait up to _interval OR until force_event fires
                    try:
                        await asyncio.wait_for(
                            self._force_event.wait(), timeout=self._interval
                        )
                    except asyncio.TimeoutError:
                        pass
                    self._force_event.clear()

                    if self._active and self._any_poll_base():
                        ids = list(self._active.keys())
                        coros = [self._poll_one(client, pid) for pid in ids]
                        await asyncio.gather(*coros, return_exceptions=True)

                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.warning("ExecutorPoller loop error: %s", exc)

    async def _sweep(self) -> None:
        """Immediate single sweep using a short-lived client."""
        import httpx

        ids = list(self._active.keys())
        if not ids:
            return
        async with httpx.AsyncClient(timeout=3.0) as client:
            coros = [self._poll_one(client, pid) for pid in ids]
            await asyncio.gather(*coros, return_exceptions=True)

    async def _poll_one(self, client, plan_id: str) -> None:
        """Poll /result/{plan_id} once; write to Mailbox on terminal status."""
        from src.common.mailbox import MailboxItem

        # Skip if Mailbox already has a completion
        if await self._mailbox.has_completion(plan_id):
            self.unregister(plan_id)
            return

        base = self._executor_base_urls.get(plan_id) or self._base_url
        if not base:
            return

        async with self._semaphore:
            try:
                import httpx
                r = await client.get(f"{base}/result/{plan_id}")
                if r.status_code == 200:
                    data = r.json()
                    status = data.get("status", "")
                    if status in _TERMINAL_STATUSES:
                        await self._mailbox.post(
                            plan_id,
                            MailboxItem(item_type="completion", payload=data),
                        )
                        logger.info(
                            "ExecutorPoller: completion written to Mailbox "
                            "plan_id=%s status=%s",
                            plan_id,
                            status,
                        )
                        self.unregister(plan_id)
            except Exception as exc:
                logger.debug(
                    "ExecutorPoller: poll failed plan_id=%s: %s", plan_id, exc
                )
