"""Soft interrupt mechanism for Executor tools.

Provides a decorator and helpers so that tools can check for stop signals
during execution. When interrupted:
  1. Any running subprocess is terminated
  2. An injected prompt is returned as the tool result
  3. The LLM receives this and naturally stops calling tools

Plan ID is propagated via module-level context variable (set by tools_node).
"""

from __future__ import annotations

import logging
import subprocess
import threading
import time
from typing import Any, Callable, TypeVar

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Plan ID context — set by tools_node before tool execution
# ---------------------------------------------------------------------------

_current_plan_id = threading.local()


def set_current_plan_id(plan_id: str) -> None:
    _current_plan_id.plan_id = plan_id


def get_current_plan_id() -> str:
    return getattr(_current_plan_id, "plan_id", "")


def clear_current_plan_id() -> None:
    _current_plan_id.plan_id = ""

# ---------------------------------------------------------------------------
# Stop event access
# ---------------------------------------------------------------------------


def _get_stop_event(plan_id: str):
    """Get the asyncio.Event for a plan_id from the server's _stop_events dict.

    Returns None if not in server mode or plan_id not found.
    """
    if not plan_id:
        return None
    try:
        from src.executor_agent.server import _stop_events
        return _stop_events.get(plan_id)
    except ImportError:
        return None


def is_interrupted(plan_id: str | None = None) -> bool:
    """Check if a stop signal has been set for the current plan."""
    pid = plan_id or get_current_plan_id()
    if not pid:
        return False
    event = _get_stop_event(pid)
    return event is not None and event.is_set()


def check_interrupt(plan_id: str | None = None) -> None:
    """Raise ToolInterrupted if stop signal is set. Called at tool start."""
    if is_interrupted(plan_id):
        raise ToolInterrupted("Supervisor requested stop")


class ToolInterrupted(Exception):
    """Raised when a stop signal is detected during tool execution."""


INTERRUPT_PROMPT = "[INTERRUPT] Supervisor 要求立即停止执行"

# ---------------------------------------------------------------------------
# Interruptible Popen runner (replaces subprocess.run for long commands)
# ---------------------------------------------------------------------------


def run_with_interrupt_check(
    args: list[str] | str,
    *,
    plan_id: str | None = None,
    shell: bool = False,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
    timeout: int = 120,
    poll_interval: float = 0.5,
) -> subprocess.CompletedProcess:
    """Run a subprocess that can be interrupted by a stop event.

    Like subprocess.run, but polls stop_event every poll_interval seconds.
    If interrupted, terminates the subprocess and raises ToolInterrupted.

    Returns CompletedProcess on normal completion.
    """
    pid = plan_id or get_current_plan_id()
    stop_event = _get_stop_event(pid) if pid else None

    proc = subprocess.Popen(
        args,
        shell=shell,
        cwd=cwd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    deadline = time.monotonic() + timeout

    try:
        while proc.poll() is None:
            # Check interrupt
            if stop_event and stop_event.is_set():
                logger.info("Interrupt detected for plan_id=%s, terminating subprocess", pid)
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)
                raise ToolInterrupted(INTERRUPT_PROMPT)

            # Check timeout
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)
                stdout, stderr = proc.communicate()
                raise subprocess.TimeoutExpired(
                    cmd=args if isinstance(args, str) else " ".join(args),
                    timeout=timeout,
                    output=stdout,
                    stderr=stderr,
                )

            time.sleep(min(poll_interval, remaining))
    except ToolInterrupted:
        raise
    except Exception:
        # Ensure process is cleaned up on unexpected errors
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        raise

    stdout, stderr = proc.communicate()
    return subprocess.CompletedProcess(
        args=args,
        returncode=proc.returncode,
        stdout=stdout,
        stderr=stderr,
    )
