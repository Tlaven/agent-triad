"""V3: Cross-process protocol data structures for Executor ↔ Supervisor communication.

All dataclasses are HTTP-serializable (JSON-safe). Used by:
- Executor Server: receives ExecuteRequest, exposes ExecuteStatus / ExecutorResultResponse
- Process Manager: uses these for HTTP body serialization
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal


@dataclass
class ExecuteRequest:
    """Supervisor → Executor: start a plan execution."""

    plan_json: str
    plan_id: str
    executor_session_id: str = ""
    config: dict[str, Any] = field(default_factory=dict)


@dataclass
class ExecuteStatus:
    """Lightweight status overview for a running (or completed) execution."""

    plan_id: str
    status: Literal["running", "completed", "failed", "stopped"]
    tool_rounds: int = 0
    current_step: str = ""
    started_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


@dataclass
class ExecutorResultResponse:
    """HTTP-serializable ExecutorResult sent back on completion."""

    status: Literal["completed", "failed", "stopped"]
    updated_plan_json: str = ""
    summary: str = ""
    snapshot_json: str = ""


@dataclass
class StopRequest:
    """Supervisor → Executor: request graceful stop."""

    reason: str = ""
