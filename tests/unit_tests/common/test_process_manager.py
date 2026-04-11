"""Unit tests for V3 Executor Process Manager (mocked subprocess)."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.common.context import Context
from src.common.process_manager import ExecutorProcessManager


def _make_ctx(**overrides) -> Context:
    defaults = {
        "executor_host": "localhost",
        "executor_port": 8100,
        "executor_startup_timeout": 2.0,
    }
    defaults.update(overrides)
    return Context(**defaults)


async def test_start_spawns_process_and_polls_health() -> None:
    ctx = _make_ctx()
    mgr = ExecutorProcessManager(ctx)

    mock_process = MagicMock()
    mock_process.pid = 12345
    mock_process.poll.return_value = None

    mock_response = MagicMock()
    mock_response.status_code = 200

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_response)
    mock_client.aclose = AsyncMock()
    mock_client.is_closed = False

    mgr._client = mock_client

    with patch("src.common.process_manager.subprocess.Popen", return_value=mock_process):
        await mgr.start()

    assert mgr.is_running


async def test_stop_posts_shutdown_and_waits() -> None:
    ctx = _make_ctx()
    mgr = ExecutorProcessManager(ctx)

    mock_process = MagicMock()
    mock_process.pid = 12345
    mock_process.wait.return_value = 0

    mock_client = AsyncMock()
    mock_client.post = AsyncMock()
    mock_client.aclose = AsyncMock()
    mock_client.is_closed = False

    mgr._process = mock_process
    mgr._client = mock_client

    await mgr.stop()

    mock_client.post.assert_called_once_with("/shutdown")
    assert mgr._process is None


async def test_stop_closes_client() -> None:
    ctx = _make_ctx()
    mgr = ExecutorProcessManager(ctx)

    mock_process = MagicMock()
    mock_process.pid = 12345
    mock_process.wait.return_value = 0

    mock_client = AsyncMock()
    mock_client.post = AsyncMock()
    mock_client.aclose = AsyncMock()
    mock_client.is_closed = False

    mgr._process = mock_process
    mgr._client = mock_client

    await mgr.stop()

    mock_client.aclose.assert_called_once()
    assert mgr._client is None


async def test_base_url_constructed_from_context() -> None:
    ctx = _make_ctx(executor_host="192.168.1.10", executor_port=9999)
    mgr = ExecutorProcessManager(ctx)
    assert mgr.base_url == "http://192.168.1.10:9999"


async def test_is_running_false_when_no_process() -> None:
    ctx = _make_ctx()
    mgr = ExecutorProcessManager(ctx)
    assert mgr.is_running is False


async def test_client_lazy_init() -> None:
    ctx = _make_ctx()
    mgr = ExecutorProcessManager(ctx)
    assert mgr._client is None
    c = mgr.client
    assert c is not None
    # Close after test
    await c.aclose()


async def test_stop_noop_when_no_process() -> None:
    ctx = _make_ctx()
    mgr = ExecutorProcessManager(ctx)
    # Should not raise
    await mgr.stop()
    assert mgr._process is None
