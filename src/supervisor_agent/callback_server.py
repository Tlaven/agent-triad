"""V3: Supervisor Callback Server — receives Executor callbacks.

Runs alongside the Supervisor graph on the configured callback port.
Writes received snapshots and completions into the shared Mailbox.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel

from src.common.mailbox import Mailbox, MailboxItem

logger = logging.getLogger(__name__)

# Shared mailbox instance (set during graph init)
_mailbox: Mailbox | None = None


def set_mailbox(mailbox: Mailbox) -> None:
    """Set the shared mailbox instance (called during Supervisor graph init)."""
    global _mailbox
    _mailbox = mailbox


def get_mailbox() -> Mailbox:
    """Get the shared mailbox. Raises if not initialized."""
    if _mailbox is None:
        raise RuntimeError("Callback server mailbox not initialized")
    return _mailbox


class SnapshotPayload(BaseModel):
    plan_id: str
    tool_rounds: int = 0
    current_step: str = ""
    completed_steps: int = 0
    total_steps: int = 0
    progress_summary: str = ""
    timestamp: str = ""


class CompletionPayload(BaseModel):
    plan_id: str
    status: str
    updated_plan_json: str = ""
    summary: str = ""
    snapshot_json: str = ""


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Supervisor callback server started")
    yield
    logger.info("Supervisor callback server shutting down")


callback_app = FastAPI(title="AgentTriad Supervisor Callbacks", lifespan=lifespan)


@callback_app.post("/callback/snapshot")
async def receive_snapshot(payload: SnapshotPayload):
    """Receive lightweight snapshot from Executor. Stored in mailbox (informational)."""
    mb = get_mailbox()
    item = MailboxItem(
        item_type="snapshot",
        payload=payload.model_dump(),
    )
    await mb.post(payload.plan_id, item)
    logger.debug(
        "Snapshot received: plan_id=%s, rounds=%d",
        payload.plan_id,
        payload.tool_rounds,
    )
    return {"status": "ok"}


@callback_app.post("/callback/completed")
async def receive_completion(payload: CompletionPayload):
    """Receive ExecutorResult from Executor. Must-read — stored in mailbox."""
    mb = get_mailbox()
    item = MailboxItem(
        item_type="completion",
        payload=payload.model_dump(),
    )
    await mb.post(payload.plan_id, item)
    logger.info(
        "Completion received: plan_id=%s, status=%s",
        payload.plan_id,
        payload.status,
    )
    return {"status": "ok"}


@callback_app.get("/mailbox/{plan_id}")
async def read_mailbox(plan_id: str):
    """Read all mailbox items for a plan."""
    mb = get_mailbox()
    snapshots = await mb.get_all_snapshots(plan_id)
    completion = await mb.get_completion(plan_id)
    return {
        "plan_id": plan_id,
        "snapshots": [s.payload for s in snapshots],
        "completion": completion.payload if completion else None,
        "has_completion": await mb.has_completion(plan_id),
    }


@callback_app.get("/health")
async def health():
    return {"status": "ok"}
