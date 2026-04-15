"""Unit tests for Executor Process Manager (mocked async subprocess)."""

import asyncio
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.common.context import Context
from src.common.process_manager import ExecutorProcessManager, PORT_FILE, ProcessHandle


def _make_ctx(**overrides) -> Context:
    defaults = {
        "executor_host": "localhost",
        "executor_port": 0,
        "executor_startup_timeout": 2.0,
    }
    defaults.update(overrides)
    return Context(**defaults)


def _make_mock_process(**overrides) -> MagicMock:
    mock = MagicMock()
    mock.pid = 12345
    mock.returncode = None
    mock.terminate = MagicMock()
    mock.wait = AsyncMock(return_value=0)
    mock.update(overrides)
    return mock


def _make_mock_client() -> AsyncMock:
    mock_response = MagicMock()
    mock_response.status_code = 200

    client = AsyncMock()
    client.get = AsyncMock(return_value=mock_response)
    client.post = AsyncMock()
    client.aclose = AsyncMock()
    client.is_closed = False
    return client


def _make_mock_popen_process(**overrides) -> MagicMock:
    mock = MagicMock(spec=subprocess.Popen)
    mock.pid = 23456
    mock.poll = MagicMock(return_value=None)
    mock.terminate = MagicMock()
    mock.kill = MagicMock()
    mock.wait = MagicMock(return_value=0)
    mock.stdout = MagicMock()
    for key, value in overrides.items():
        setattr(mock, key, value)
    return mock


# ==================== start_for_task ====================


async def test_start_for_task_spawns_process() -> None:
    ctx = _make_ctx()
    mgr = ExecutorProcessManager(ctx)
    mock_process = _make_mock_process()
    mock_client = _make_mock_client()

    with (
        patch.object(mgr, "_read_port_file", return_value=8199),
        patch("asyncio.create_subprocess_exec", return_value=mock_process),
        patch("httpx.AsyncClient", return_value=mock_client),
    ):
        handle = await mgr.start_for_task("plan_test", ctx, mailbox_url="http://127.0.0.1:5555")

    assert handle.plan_id == "plan_test"
    assert handle.base_url == "http://localhost:8199"
    assert mgr.get_task_base_url("plan_test") == "http://localhost:8199"
    assert mgr.is_running


async def test_start_for_task_passes_env_vars() -> None:
    ctx = _make_ctx()
    mgr = ExecutorProcessManager(ctx)
    mock_process = _make_mock_process()
    mock_client = _make_mock_client()

    captured_env = {}

    async def fake_exec(*args, **kwargs):
        captured_env.update(kwargs.get("env", {}))
        return mock_process

    with (
        patch.object(mgr, "_read_port_file", return_value=8199),
        patch("asyncio.create_subprocess_exec", side_effect=fake_exec),
        patch("httpx.AsyncClient", return_value=mock_client),
    ):
        await mgr.start_for_task("plan_env_test", ctx, mailbox_url="http://127.0.0.1:5555")

    assert captured_env["EXECUTOR_PORT"] == "0"
    assert captured_env["PLAN_ID"] == "plan_env_test"
    assert captured_env["MAILBOX_URL"] == "http://127.0.0.1:5555"


async def test_start_for_task_evicts_dead_handle_then_spawns_again() -> None:
    """Same plan_id after subprocess exit must get a fresh handle (plan_id ↔ active executor)."""
    ctx = _make_ctx()
    mgr = ExecutorProcessManager(ctx)
    dead_proc = _make_mock_process()
    dead_proc.returncode = 0
    dead_client = _make_mock_client()
    mgr._task_processes["plan_reuse"] = ProcessHandle(
        plan_id="plan_reuse",
        process=dead_proc,
        base_url="http://localhost:1111",
        port=1111,
        client=dead_client,
    )
    mock_process = _make_mock_process()
    mock_client = _make_mock_client()

    with (
        patch.object(mgr, "_read_port_file", return_value=8200),
        patch("asyncio.create_subprocess_exec", return_value=mock_process),
        patch("httpx.AsyncClient", return_value=mock_client),
    ):
        handle = await mgr.start_for_task("plan_reuse", ctx, mailbox_url="http://127.0.0.1:5555")

    assert handle.plan_id == "plan_reuse"
    assert handle.base_url == "http://localhost:8200"
    dead_client.aclose.assert_awaited_once()


async def test_start_for_task_falls_back_to_popen_when_asyncio_subprocess_unsupported() -> None:
    ctx = _make_ctx()
    mgr = ExecutorProcessManager(ctx)
    mock_client = _make_mock_client()
    mock_popen = _make_mock_popen_process()

    with (
        patch.object(mgr, "_read_port_file", return_value=8199),
        patch("asyncio.create_subprocess_exec", side_effect=NotImplementedError),
        patch("subprocess.Popen", return_value=mock_popen) as mock_popen_ctor,
        patch("httpx.AsyncClient", return_value=mock_client),
    ):
        handle = await mgr.start_for_task("plan_fallback", ctx, mailbox_url="http://127.0.0.1:5555")

    assert handle.plan_id == "plan_fallback"
    assert handle.base_url == "http://localhost:8199"
    assert handle.process.pid == 23456
    mock_popen_ctor.assert_called_once()


# ==================== stop_task ====================


async def test_stop_task_stops_specific_process() -> None:
    ctx = _make_ctx()
    mgr = ExecutorProcessManager(ctx)
    mock_process = _make_mock_process()
    mock_client = _make_mock_client()

    handle = ProcessHandle(
        plan_id="plan_stop_test",
        process=mock_process,
        base_url="http://localhost:8199",
        port=8199,
        client=mock_client,
    )
    mgr._task_processes["plan_stop_test"] = handle

    with patch.object(mgr, "_clear_port_file"):
        await mgr.stop_task("plan_stop_test")

    assert "plan_stop_test" not in mgr._task_processes
    mock_client.post.assert_called_once_with("/shutdown")


# ==================== stop all ====================


async def test_stop_stops_all_processes() -> None:
    ctx = _make_ctx()
    mgr = ExecutorProcessManager(ctx)

    for pid in ("p1", "p2"):
        mock_proc = _make_mock_process()
        mock_client = _make_mock_client()
        mgr._task_processes[pid] = ProcessHandle(
            plan_id=pid,
            process=mock_proc,
            base_url=f"http://localhost:81{pid[-1]}00",
            port=8100,
            client=mock_client,
        )

    with patch.object(mgr, "_clear_port_file"):
        await mgr.stop()

    assert len(mgr._task_processes) == 0


async def test_stop_noop_when_no_process() -> None:
    ctx = _make_ctx()
    mgr = ExecutorProcessManager(ctx)
    await mgr.stop()  # should not raise


# ==================== is_running ====================


async def test_is_running_false_when_empty() -> None:
    mgr = ExecutorProcessManager(_make_ctx())
    assert mgr.is_running is False


async def test_is_running_true_with_active_handle() -> None:
    mgr = ExecutorProcessManager(_make_ctx())
    mock_proc = _make_mock_process()
    mgr._task_processes["p1"] = ProcessHandle(
        plan_id="p1", process=mock_proc, base_url="http://localhost:8199", port=8199,
    )
    assert mgr.is_running is True


# ==================== get_task_handle ====================


async def test_get_task_handle_returns_none_for_unknown() -> None:
    mgr = ExecutorProcessManager(_make_ctx())
    assert mgr.get_task_handle("nonexistent") is None


async def test_get_task_handle_returns_none_for_dead_process() -> None:
    mgr = ExecutorProcessManager(_make_ctx())
    mock_proc = _make_mock_process()
    mock_proc.returncode = 1  # dead
    mgr._task_processes["dead"] = ProcessHandle(
        plan_id="dead", process=mock_proc, base_url="http://localhost:8199", port=8199,
    )
    assert mgr.get_task_handle("dead") is None


# ==================== recover_or_start (legacy) ====================


async def test_recover_reuses_existing_executor() -> None:
    ctx = _make_ctx()
    mgr = ExecutorProcessManager(ctx)

    mock_response = MagicMock()
    mock_response.status_code = 200

    with (
        patch.object(mgr, "_read_port_file", return_value=8199),
        patch("httpx.AsyncClient") as MockClient,
    ):
        instance = AsyncMock()
        instance.get = AsyncMock(return_value=mock_response)
        instance.__aenter__ = AsyncMock(return_value=instance)
        instance.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value = instance

        await mgr.recover_or_start()

    assert mgr.base_url == "http://localhost:8199"
