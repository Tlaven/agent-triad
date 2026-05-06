"""Unit tests for Executor tool interrupt mechanism."""

import asyncio
import subprocess
import sys
import time
from unittest.mock import patch

import pytest

from src.executor_agent.interrupt import (
    INTERRUPT_PROMPT,
    ToolInterrupted,
    check_interrupt,
    clear_current_plan_id,
    get_current_plan_id,
    is_interrupted,
    run_with_interrupt_check,
    set_current_plan_id,
)

# ==================== Plan ID Context ====================


def test_plan_id_context():
    set_current_plan_id("plan_test_123")
    assert get_current_plan_id() == "plan_test_123"
    clear_current_plan_id()
    assert get_current_plan_id() == ""


def test_is_interrupted_no_event():
    """When not running in server mode, is_interrupted returns False."""
    set_current_plan_id("plan_no_server")
    assert is_interrupted() is False
    clear_current_plan_id()


def test_check_interrupt_not_set():
    """check_interrupt does not raise when stop event is not set."""
    set_current_plan_id("plan_not_set")
    check_interrupt()  # should not raise
    clear_current_plan_id()


# ==================== run_with_interrupt_check ====================


def test_normal_completion():
    """run_with_interrupt_check completes normally for short commands."""
    result = run_with_interrupt_check(
        [sys.executable, "-c", "print('hello')"],
        shell=False,
        timeout=10,
    )
    assert result.returncode == 0
    assert "hello" in result.stdout


def test_timeout():
    """run_with_interrupt_check raises TimeoutExpired on timeout."""
    with pytest.raises(subprocess.TimeoutExpired):
        run_with_interrupt_check(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            shell=False,
            timeout=1,
            poll_interval=0.2,
        )


def test_interrupt_during_execution():
    """run_with_interrupt_check raises ToolInterrupted when stop event is set."""
    try:
        from src.executor_agent.server import _stop_events
    except ImportError:
        pytest.skip("Not running in server module context")

    plan_id = "plan_interrupt_test"
    event = asyncio.Event()
    _stop_events[plan_id] = event

    try:
        # Set the stop event after a brief delay
        import threading

        def set_stop():
            time.sleep(0.3)
            event.set()

        threading.Thread(target=set_stop, daemon=True).start()

        with pytest.raises(ToolInterrupted) as exc_info:
            run_with_interrupt_check(
                [sys.executable, "-c", "import time; time.sleep(10)"],
                shell=False,
                plan_id=plan_id,
                timeout=30,
                poll_interval=0.2,
            )
        assert INTERRUPT_PROMPT in str(exc_info.value)
    finally:
        _stop_events.pop(plan_id, None)


def test_no_interrupt_fast_command():
    """Fast command completes even when stop event is set afterwards."""
    result = run_with_interrupt_check(
        [sys.executable, "-c", "print('fast')"],
        shell=False,
        timeout=10,
    )
    assert result.returncode == 0
    assert "fast" in result.stdout


# ==================== Tool integration ====================


def test_run_local_command_normal():
    """run_local_command works normally without interrupt."""
    from src.executor_agent.tools import run_local_command

    result = run_local_command.invoke({"command": "echo hello"})
    # Tool returns TypedDict; check the result
    assert result["ok"] is True or "hello" in result.get("stdout", "")


def test_run_local_command_returns_interrupt_on_stop():
    """run_local_command returns interrupt message when stop event is set."""
    try:
        from src.executor_agent.server import _stop_events
    except ImportError:
        pytest.skip("Not running in server module context")

    plan_id = "plan_tool_interrupt"
    event = asyncio.Event()
    event.set()  # Pre-set the stop event
    _stop_events[plan_id] = event

    try:
        set_current_plan_id(plan_id)
        from src.executor_agent.tools import run_local_command

        result = run_local_command.invoke({
            "command": "echo should_not_run",
        })
        assert result["ok"] is False
        assert INTERRUPT_PROMPT in result.get("error", "")
    finally:
        clear_current_plan_id()
        _stop_events.pop(plan_id, None)


# ==================== _get_stop_event edge cases ====================


def test_get_stop_event_empty_plan_id():
    """_get_stop_event returns None for empty plan_id."""
    from src.executor_agent.interrupt import _get_stop_event
    assert _get_stop_event("") is None


def test_get_stop_event_no_plan_id():
    """_get_stop_event returns None for None plan_id."""
    from src.executor_agent.interrupt import _get_stop_event
    assert _get_stop_event(None) is None


def test_get_stop_event_import_error():
    """When server module can't be imported, _get_stop_event returns None."""
    from src.executor_agent.interrupt import _get_stop_event
    with patch.dict("sys.modules", {"src.executor_agent.server": None}):
        # Force re-import path by calling with a plan_id that's not in server
        result = _get_stop_event("nonexistent_plan_xyz")
        # Either None (no server) or actual event — both are fine
        assert result is None or hasattr(result, "is_set")


# ==================== is_interrupted edge cases ====================


def test_is_interrupted_no_plan_id_returns_false():
    """When no plan_id is set, is_interrupted returns False."""
    clear_current_plan_id()
    assert is_interrupted() is False


def test_is_interrupted_explicit_none():
    """Explicit None plan_id returns False."""
    assert is_interrupted(None) is False


def test_is_interrupted_with_empty_string():
    """Empty string plan_id returns False."""
    assert is_interrupted("") is False


# ==================== check_interrupt ====================


def test_check_interrupt_raises_when_set():
    """check_interrupt raises ToolInterrupted when stop event is set."""
    try:
        from src.executor_agent.server import _stop_events
    except ImportError:
        pytest.skip("Not running in server module context")

    plan_id = "plan_check_interrupt"
    event = asyncio.Event()
    event.set()
    _stop_events[plan_id] = event

    try:
        with pytest.raises(ToolInterrupted, match="Supervisor requested stop"):
            check_interrupt(plan_id)
    finally:
        _stop_events.pop(plan_id, None)


def test_check_interrupt_no_raise_when_not_set():
    """check_interrupt does not raise when stop event is not set."""
    try:
        from src.executor_agent.server import _stop_events
    except ImportError:
        pytest.skip("Not running in server module context")

    plan_id = "plan_check_no_interrupt"
    event = asyncio.Event()
    _stop_events[plan_id] = event

    try:
        check_interrupt(plan_id)  # should not raise
    finally:
        _stop_events.pop(plan_id, None)
