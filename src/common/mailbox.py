"""V3: In-memory mailbox for async cross-process communication.

Executor posts snapshots (fire-and-forget) and completion (must-read).
Supervisor reads via the unified call_executor tool, which internally calls wait_for_completion.

Thread-safe via asyncio.Lock. One mailbox instance per Supervisor process, keyed by plan_id.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Literal

logger = logging.getLogger(__name__)


@dataclass
class MailboxItem:
    """A single item in a plan's mailbox."""

    item_type: Literal["snapshot", "completion"]
    payload: dict
    read: bool = False


@dataclass
class PlanMailbox:
    """Per-plan mailbox holding snapshots and one completion notice."""

    plan_id: str
    items: list[MailboxItem] = field(default_factory=list)
    has_completion: bool = False

    def post(self, item: MailboxItem) -> None:
        """Add an item. Marks completion if applicable."""
        self.items.append(item)
        if item.item_type == "completion":
            self.has_completion = True
            logger.info("Mailbox[%s]: completion received", self.plan_id)

    def latest_snapshot(self) -> MailboxItem | None:
        """Return the most recent unread snapshot, or None."""
        for item in reversed(self.items):
            if item.item_type == "snapshot":
                return item
        return None

    def snapshots(self) -> list[MailboxItem]:
        """Return all snapshot items."""
        return [i for i in self.items if i.item_type == "snapshot"]

    def completion(self) -> MailboxItem | None:
        """Return the completion item if present."""
        for item in self.items:
            if item.item_type == "completion":
                return item
        return None

    def clear(self) -> None:
        """Remove all items."""
        self.items.clear()
        self.has_completion = False


class Mailbox:
    """In-memory mailbox manager, keyed by plan_id.

    Usage:
        mailbox = Mailbox()
        await mailbox.post("plan_1", MailboxItem(item_type="snapshot", payload={...}))
        result = await mailbox.wait_for_completion("plan_1", timeout=60.0)
    """

    def __init__(self) -> None:
        self._boxes: dict[str, PlanMailbox] = {}
        self._lock = asyncio.Lock()
        # Event per plan_id, set when completion arrives
        self._completion_events: dict[str, asyncio.Event] = {}

    def _get_or_create(self, plan_id: str) -> PlanMailbox:
        if plan_id not in self._boxes:
            self._boxes[plan_id] = PlanMailbox(plan_id=plan_id)
        if plan_id not in self._completion_events:
            self._completion_events[plan_id] = asyncio.Event()
        return self._boxes[plan_id]

    async def post(self, plan_id: str, item: MailboxItem) -> None:
        """Post an item to a plan's mailbox."""
        async with self._lock:
            box = self._get_or_create(plan_id)
            box.post(item)
            if item.item_type == "completion":
                self._completion_events[plan_id].set()

    async def has_completion(self, plan_id: str) -> bool:
        """Check if a completion has been posted (non-blocking)."""
        async with self._lock:
            box = self._boxes.get(plan_id)
            return box.has_completion if box else False

    async def wait_for_completion(
        self,
        plan_id: str,
        timeout: float = 300.0,
        poll_interval: float = 1.0,
    ) -> MailboxItem | None:
        """Wait until a completion is posted or timeout.

        Returns the completion item, or None on timeout.
        """
        # Ensure event exists
        async with self._lock:
            self._get_or_create(plan_id)
            event = self._completion_events[plan_id]

        # Poll with timeout
        import asyncio as _asyncio

        deadline = _asyncio.get_event_loop().time() + timeout
        while _asyncio.get_event_loop().time() < deadline:
            if event.is_set():
                async with self._lock:
                    box = self._boxes.get(plan_id)
                    return box.completion() if box else None
            remaining = deadline - _asyncio.get_event_loop().time()
            if remaining <= 0:
                break
            try:
                await _asyncio.wait_for(
                    event.wait(),
                    timeout=min(poll_interval, remaining),
                )
            except _asyncio.TimeoutError:
                continue
        return None

    async def get_latest_snapshot(self, plan_id: str) -> MailboxItem | None:
        """Get latest snapshot for a plan (non-blocking)."""
        async with self._lock:
            box = self._boxes.get(plan_id)
            return box.latest_snapshot() if box else None

    async def get_all_snapshots(self, plan_id: str) -> list[MailboxItem]:
        """Get all snapshots for a plan."""
        async with self._lock:
            box = self._boxes.get(plan_id)
            return box.snapshots() if box else []

    async def get_completion(self, plan_id: str) -> MailboxItem | None:
        """Get completion item if available (non-blocking)."""
        async with self._lock:
            box = self._boxes.get(plan_id)
            return box.completion() if box else None

    async def clear(self, plan_id: str) -> None:
        """Clear mailbox for a plan."""
        async with self._lock:
            box = self._boxes.get(plan_id)
            if box:
                box.clear()
            event = self._completion_events.get(plan_id)
            if event:
                event.clear()
