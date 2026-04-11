"""V3: Executor Process Manager — spawn, monitor, stop Process B.

Supervisor uses this to manage the Executor subprocess lifecycle:
- start(): spawn via subprocess.Popen, poll /health until ready
- stop(): POST /shutdown, wait, then terminate/kill if needed
- client: httpx.AsyncClient for making HTTP calls to Executor
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import sys
from typing import Any

import httpx

from src.common.context import Context

logger = logging.getLogger(__name__)


class ExecutorProcessManager:
    """Manages the Executor subprocess lifecycle."""

    def __init__(self, ctx: Context) -> None:
        self._ctx = ctx
        self._process: subprocess.Popen[bytes] | None = None
        self._base_url = f"http://{ctx.executor_host}:{ctx.executor_port}"
        self._client: httpx.AsyncClient | None = None

    @property
    def base_url(self) -> str:
        return self._base_url

    @property
    def client(self) -> httpx.AsyncClient:
        """Lazy-init async HTTP client."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(base_url=self._base_url, timeout=30.0)
        return self._client

    async def start(self) -> None:
        """Spawn Executor subprocess and wait for health check."""
        if self._process is not None and self._process.poll() is None:
            logger.warning("Executor process already running (PID=%d)", self._process.pid)
            return

        env = os.environ.copy()
        env["EXECUTOR_PORT"] = str(self._ctx.executor_port)

        logger.info(
            "Starting Executor process on port %d ...",
            self._ctx.executor_port,
        )
        self._process = subprocess.Popen(
            [sys.executable, "-m", "src.executor_agent"],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        # Poll /health until ready or timeout
        deadline = asyncio.get_event_loop().time() + self._ctx.executor_startup_timeout
        while asyncio.get_event_loop().time() < deadline:
            try:
                resp = await self.client.get("/health")
                if resp.status_code == 200:
                    logger.info(
                        "Executor process ready (PID=%d, port=%d)",
                        self._process.pid,
                        self._ctx.executor_port,
                    )
                    return
            except (httpx.ConnectError, httpx.TimeoutException):
                pass
            await asyncio.sleep(0.5)

        # Timeout
        self._process.terminate()
        try:
            self._process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self._process.kill()
        raise TimeoutError(
            f"Executor process failed to start within {self._ctx.executor_startup_timeout}s"
        )

    async def stop(self) -> None:
        """Gracefully stop the Executor process."""
        # Try HTTP shutdown first
        try:
            await self.client.post("/shutdown")
        except (httpx.ConnectError, httpx.TimeoutException):
            pass

        if self._process is None:
            return

        # Wait for process to exit
        try:
            loop = asyncio.get_event_loop()
            await asyncio.wait_for(
                loop.run_in_executor(None, self._process.wait),
                timeout=10.0,
            )
        except asyncio.TimeoutError:
            logger.warning("Executor didn't exit, sending SIGTERM")
            self._process.terminate()
            try:
                await asyncio.wait_for(
                    loop.run_in_executor(None, self._process.wait),
                    timeout=5.0,
                )
            except asyncio.TimeoutError:
                logger.warning("Executor didn't terminate, sending SIGKILL")
                self._process.kill()
                self._process.wait()

        logger.info("Executor process stopped (PID=%d)", self._process.pid)
        self._process = None

        # Close HTTP client
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    @property
    def is_running(self) -> bool:
        return self._process is not None and self._process.poll() is None
