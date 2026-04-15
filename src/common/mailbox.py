"""In-memory mailbox for Executor result caching + real-time status.

Thread-safe via threading.Lock — safe for concurrent access from:
  - Supervisor's asyncio event loop (reads)
  - Mailbox HTTP server thread (writes from Executor pushes)
  - Direct sync writes from within the same process

Mailbox is consumed at these moments:
  1. get_executor_result tool (Agent active query)
  2. _build_executor_status_brief (idle injection into system prompt)
  3. dynamic_tools_node post-processing
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from typing import Literal

logger = logging.getLogger(__name__)

_MAX_BOXES = 80
_RETAIN_BOXES = 50

# ---------------------------------------------------------------------------
# Module-level singleton access
# ---------------------------------------------------------------------------

_mailbox: Mailbox | None = None


def set_mailbox(mailbox: Mailbox) -> None:
    """Set the shared mailbox instance (called during V3 infrastructure init)."""
    global _mailbox
    _mailbox = mailbox


def get_mailbox() -> Mailbox:
    """Get the shared mailbox. Raises if not initialized."""
    if _mailbox is None:
        raise RuntimeError("Mailbox not initialized — call set_mailbox() first")
    return _mailbox


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class MailboxItem:
    """A single item in a plan's mailbox."""

    item_type: Literal["completion", "status"]
    payload: dict
    read: bool = False


@dataclass
class PlanMailbox:
    """Per-plan mailbox holding completion results and status updates."""

    plan_id: str
    items: list[MailboxItem] = field(default_factory=list)
    has_completion: bool = False

    def post(self, item: MailboxItem) -> None:
        """Add an item. Marks completion if applicable."""
        self.items.append(item)
        if item.item_type == "completion":
            self.has_completion = True
            logger.info("Mailbox[%s]: completion received", self.plan_id)
        elif item.item_type == "status":
            logger.debug("Mailbox[%s]: status update", self.plan_id)

    def completion(self) -> MailboxItem | None:
        """Return the completion item if present."""
        for item in self.items:
            if item.item_type == "completion":
                return item
        return None

    def latest_status(self) -> MailboxItem | None:
        """Return the most recent status item."""
        for item in reversed(self.items):
            if item.item_type == "status":
                return item
        return None

    def clear(self) -> None:
        """Remove all items."""
        self.items.clear()
        self.has_completion = False


class Mailbox:
    """In-memory mailbox manager, keyed by plan_id.

    Thread-safe: uses threading.Lock so it can be written from the
    MailboxHTTPServer thread and read from the Supervisor's asyncio loop.

    Sync methods (_post_sync, etc.) are for the HTTP server thread.
    Async methods (post, etc.) are for the Supervisor's asyncio context —
    they just call the sync methods (lock hold time is microseconds).

    Usage:
        mailbox = Mailbox()
        await mailbox.post("plan_1", MailboxItem(item_type="completion", payload={...}))
        result = await mailbox.get_completion("plan_1")
    """

    def __init__(self) -> None:
        self._boxes: dict[str, PlanMailbox] = {}
        self._lock = threading.Lock()

    def _get_or_create(self, plan_id: str) -> PlanMailbox:
        if plan_id not in self._boxes:
            self._boxes[plan_id] = PlanMailbox(plan_id=plan_id)
        return self._boxes[plan_id]

    # -- Sync methods (for MailboxHTTPServer thread or direct calls) --

    def _maybe_evict(self) -> None:
        """保留最近 _RETAIN_BOXES 条已终态的 box，按插入顺序驱逐最旧的。

        只驱逐 has_completion=True 的 box，避免误删运行中任务的状态。
        调用者必须持有 self._lock。
        """
        if len(self._boxes) <= _MAX_BOXES:
            return
        keys = list(self._boxes.keys())
        to_remove = keys[: len(keys) - _RETAIN_BOXES]
        for k in to_remove:
            box = self._boxes.get(k)
            if box and box.has_completion:
                del self._boxes[k]

    def _post_sync(self, plan_id: str, item: MailboxItem) -> None:
        with self._lock:
            box = self._get_or_create(plan_id)
            box.post(item)
            self._maybe_evict()

    def _has_completion_sync(self, plan_id: str) -> bool:
        with self._lock:
            box = self._boxes.get(plan_id)
            return box.has_completion if box else False

    def _get_completion_sync(self, plan_id: str) -> MailboxItem | None:
        with self._lock:
            box = self._boxes.get(plan_id)
            return box.completion() if box else None

    def _latest_status_sync(self, plan_id: str) -> MailboxItem | None:
        with self._lock:
            box = self._boxes.get(plan_id)
            return box.latest_status() if box else None

    def _clear_sync(self, plan_id: str) -> None:
        with self._lock:
            box = self._boxes.get(plan_id)
            if box:
                box.clear()

    def _remove_sync(self, plan_id: str) -> None:
        with self._lock:
            self._boxes.pop(plan_id, None)

    def _all_plan_ids_sync(self) -> list[str]:
        with self._lock:
            return list(self._boxes.keys())

    # -- Async methods (backward-compatible API for Supervisor asyncio context) --

    async def post(self, plan_id: str, item: MailboxItem) -> None:
        """Post an item to a plan's mailbox."""
        self._post_sync(plan_id, item)

    async def has_completion(self, plan_id: str) -> bool:
        """Check if a completion has been posted (non-blocking)."""
        return self._has_completion_sync(plan_id)

    async def get_completion(self, plan_id: str) -> MailboxItem | None:
        """Get completion item if available (non-blocking)."""
        return self._get_completion_sync(plan_id)

    async def clear(self, plan_id: str) -> None:
        """Clear mailbox for a plan."""
        self._clear_sync(plan_id)

    async def remove(self, plan_id: str) -> None:
        """Fully remove a plan's mailbox (for cleanup after completion)."""
        self._remove_sync(plan_id)
