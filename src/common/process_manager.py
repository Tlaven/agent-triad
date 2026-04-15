"""V3: Executor Process Manager — spawn, monitor, stop Process B.

Supports per-task Executor processes:
- start_for_task(plan_id, ctx, mailbox_url): spawn a dedicated Executor for one task
- stop_task(plan_id): stop a specific task's process
- stop(): stop all running processes (graceful shutdown)

Each task gets its own port file at logs/executor_{plan_id}.port.
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from src.common.context import Context

logger = logging.getLogger(__name__)

PORT_FILE = Path("logs/executor.port")


@dataclass
class ProcessHandle:
    """Tracks a single Executor subprocess."""
    plan_id: str
    process: Any
    base_url: str
    port: int
    client: httpx.AsyncClient | None = None

    def get_client(self) -> httpx.AsyncClient:
        if self.client is None or self.client.is_closed:
            self.client = httpx.AsyncClient(base_url=self.base_url, timeout=30.0)
        return self.client


class ExecutorProcessManager:
    """Manages per-task Executor subprocess lifecycles."""

    def __init__(self, ctx: Context) -> None:
        self._ctx = ctx
        self._task_processes: dict[str, ProcessHandle] = {}

    def iter_active_base_urls(self) -> list[str]:
        """Distinct base_url of subprocesses that are still running."""
        seen: set[str] = set()
        out: list[str] = []
        for h in self._task_processes.values():
            if h.process.returncode is None and h.base_url not in seen:
                seen.add(h.base_url)
                out.append(h.base_url)
        return out

    @property
    def base_url(self) -> str:
        """Return base_url of any running process (for backward compat). Empty if none."""
        for handle in self._task_processes.values():
            if handle.process.returncode is None:
                return handle.base_url
        if self._base_url_legacy:
            return self._base_url_legacy
        return ""

    @property
    def client(self) -> httpx.AsyncClient:
        """Return client of any running process (for backward compat)."""
        for handle in self._task_processes.values():
            if handle.process.returncode is None:
                return handle.get_client()
        raise RuntimeError("No running Executor process")

    @property
    def _process(self) -> Any | None:
        """Backward compat: return first active process."""
        for handle in self._task_processes.values():
            if handle.process.returncode is None:
                return handle.process
        return None

    @property
    def is_running(self) -> bool:
        return any(
            h.process.returncode is None for h in self._task_processes.values()
        )

    def get_task_handle(self, plan_id: str) -> ProcessHandle | None:
        """Get the process handle for a specific task."""
        handle = self._task_processes.get(plan_id)
        if handle and handle.process.returncode is None:
            return handle
        return None

    def get_task_base_url(self, plan_id: str) -> str | None:
        """Get base_url for a specific task's Executor."""
        handle = self.get_task_handle(plan_id)
        return handle.base_url if handle else None

    def get_task_client(self, plan_id: str) -> httpx.AsyncClient | None:
        """Get HTTP client for a specific task's Executor."""
        handle = self.get_task_handle(plan_id)
        return handle.get_client() if handle else None

    # ------------------------------------------------------------------
    # Port file helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _port_file_for_task(plan_id: str) -> Path:
        return Path(f"logs/executor_{plan_id}.port")

    async def _read_port_file(self, port_file: Path) -> int | None:
        """Read port from file, return None if missing/invalid.

        Uses asyncio.to_thread to avoid blocking the ASGI event loop.
        """
        def _sync_read() -> int | None:
            try:
                text = port_file.read_text().strip()
                return int(text) if text.isdigit() and int(text) > 0 else None
            except (OSError, ValueError):
                return None
        return await asyncio.to_thread(_sync_read)

    async def _clear_port_file(self, port_file: Path) -> None:
        """Remove port file. Uses asyncio.to_thread to avoid blocking."""
        def _sync_unlink() -> None:
            try:
                port_file.unlink(missing_ok=True)
            except OSError:
                pass
        await asyncio.to_thread(_sync_unlink)

    # ------------------------------------------------------------------
    # Subprocess output capture
    # ------------------------------------------------------------------

    async def _collect_stdout(self, process: asyncio.subprocess.Process) -> str:
        """Read remaining stdout from a subprocess (non-blocking, best-effort).

        Used to capture crash logs when Executor fails to start.
        """
        if getattr(process, "stdout", None) is None:
            return ""
        try:
            # process.stdout might already have been partially consumed;
            # read whatever remains.
            remaining = await asyncio.wait_for(process.stdout.read(), timeout=2.0)
            return remaining.decode(errors="replace").strip()
        except Exception:
            return ""

    async def _spawn_executor_process(self, env: dict[str, str]):
        """Spawn Executor subprocess with a Windows-compatible fallback."""
        try:
            return await asyncio.create_subprocess_exec(
                sys.executable, "-m", "src.executor_agent",
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
        except NotImplementedError:
            # Windows SelectorEventLoop does not implement subprocess APIs.
            # Fall back to subprocess.Popen so call_executor can still boot Executor.
            logger.warning(
                "asyncio subprocess is unsupported in current event loop; "
                "falling back to subprocess.Popen"
            )
            popen_proc = await asyncio.to_thread(
                subprocess.Popen,
                [sys.executable, "-m", "src.executor_agent"],
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
            return _PopenProcessAdapter(popen_proc)

    # ------------------------------------------------------------------
    # Per-task lifecycle
    # ------------------------------------------------------------------

    async def _evict_dead_handle(self, plan_id: str) -> None:
        """Remove finished subprocess entry so the same plan_id can map to a new Executor."""
        handle = self._task_processes.get(plan_id)
        if handle is None or handle.process.returncode is None:
            return
        self._task_processes.pop(plan_id, None)
        if handle.client is not None and not handle.client.is_closed:
            try:
                await handle.client.aclose()
            except Exception:
                logger.exception("Error closing httpx client for dead plan_id=%s", plan_id)

    async def start_for_task(self, plan_id: str, ctx: Context, mailbox_url: str = "") -> ProcessHandle:
        """Start a dedicated Executor subprocess for a specific task.

        Args:
            plan_id: Unique task identifier.
            ctx: Runtime context (model, workspace, etc.).
            mailbox_url: URL of the Mailbox HTTP server thread for push notifications.

        Returns:
            ProcessHandle with process reference and base_url.
        """
        await self._evict_dead_handle(plan_id)
        # Check if already running
        existing = self.get_task_handle(plan_id)
        if existing is not None:
            logger.warning("Executor already running for plan_id=%s, reusing", plan_id)
            return existing

        port_file = self._port_file_for_task(plan_id)

        # Build environment
        env = os.environ.copy()
        env["EXECUTOR_PORT"] = "0"
        env["PLAN_ID"] = plan_id
        if mailbox_url:
            env["MAILBOX_URL"] = mailbox_url

        # Clear stale port file
        await self._clear_port_file(port_file)

        logger.info("Starting Executor for plan_id=%s ...", plan_id)

        process = await self._spawn_executor_process(env)

        # Wait for port file
        deadline = asyncio.get_event_loop().time() + ctx.executor_startup_timeout
        discovered_port: int | None = None

        while asyncio.get_event_loop().time() < deadline:
            discovered_port = await self._read_port_file(port_file)
            if discovered_port is not None:
                break
            await asyncio.sleep(0.3)

        if discovered_port is None:
            # Capture subprocess output for diagnostics before terminating
            stdout_snippet = await self._collect_stdout(process)
            process.terminate()
            detail = (
                f"Executor for {plan_id} failed to write port file within "
                f"{ctx.executor_startup_timeout}s"
            )
            if stdout_snippet:
                detail += f"\nExecutor stdout:\n{stdout_snippet[:2000]}"
            logger.error(detail)
            raise TimeoutError(detail)

        base_url = f"http://{ctx.executor_host}:{discovered_port}"

        # Poll /health
        client = httpx.AsyncClient(base_url=base_url, timeout=5.0)
        while asyncio.get_event_loop().time() < deadline:
            try:
                resp = await client.get("/health")
                if resp.status_code == 200:
                    logger.info(
                        "Executor ready for plan_id=%s (PID=%d, port=%d)",
                        plan_id, process.pid, discovered_port,
                    )
                    handle = ProcessHandle(
                        plan_id=plan_id,
                        process=process,
                        base_url=base_url,
                        port=discovered_port,
                        client=client,
                    )
                    self._task_processes[plan_id] = handle
                    return handle
            except (httpx.ConnectError, httpx.TimeoutException):
                pass
            await asyncio.sleep(0.3)

        # Timeout
        stdout_snippet = await self._collect_stdout(process)
        process.terminate()
        await client.aclose()
        detail = (
            f"Executor for {plan_id} health check failed within "
            f"{ctx.executor_startup_timeout}s"
        )
        if stdout_snippet:
            detail += f"\nExecutor stdout:\n{stdout_snippet[:2000]}"
        logger.error(detail)
        raise TimeoutError(detail)

    async def stop_task(self, plan_id: str) -> None:
        """Stop a specific task's Executor process."""
        handle = self._task_processes.pop(plan_id, None)
        if handle is None:
            return

        await self._stop_handle(handle)

    async def _stop_handle(self, handle: ProcessHandle) -> None:
        """Stop a single process handle."""
        port_file = self._port_file_for_task(handle.plan_id)

        # Try HTTP shutdown
        try:
            await handle.get_client().post("/shutdown")
        except (httpx.ConnectError, httpx.TimeoutException):
            pass

        # Wait for exit
        try:
            await asyncio.wait_for(handle.process.wait(), timeout=10.0)
        except asyncio.TimeoutError:
            logger.warning("Executor for %s didn't exit, terminating", handle.plan_id)
            handle.process.terminate()
            try:
                await asyncio.wait_for(handle.process.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                handle.process.kill()
                await handle.process.wait()

        logger.info("Executor stopped for plan_id=%s (PID=%d)", handle.plan_id, handle.process.pid)
        await self._clear_port_file(port_file)

        if handle.client and not handle.client.is_closed:
            await handle.client.aclose()

    # ------------------------------------------------------------------
    # Legacy compatibility (recover_or_start uses shared port file)
    # ------------------------------------------------------------------

    async def recover_or_start(self) -> None:
        """Try to recover existing Executor from shared port file, else start new.

        Legacy method for backward compatibility.
        """
        port = await self._read_port_file(PORT_FILE)
        if port is not None:
            candidate_url = f"http://{self._ctx.executor_host}:{port}"
            try:
                async with httpx.AsyncClient(base_url=candidate_url, timeout=5.0) as probe:
                    resp = await probe.get("/health")
                    if resp.status_code == 200:
                        logger.info("Recovered existing Executor on port %d", port)
                        self._base_url_legacy = candidate_url
                        return
            except (httpx.ConnectError, httpx.TimeoutException):
                pass
        await self.start()

    _base_url_legacy: str = ""

    async def start(self) -> None:
        """Start a shared Executor subprocess (legacy, for backward compat)."""
        env = os.environ.copy()
        env["EXECUTOR_PORT"] = "0"

        await self._clear_port_file(PORT_FILE)

        logger.info("Starting shared Executor process ...")

        process = await self._spawn_executor_process(env)

        deadline = asyncio.get_event_loop().time() + self._ctx.executor_startup_timeout
        discovered_port: int | None = None

        while asyncio.get_event_loop().time() < deadline:
            discovered_port = await self._read_port_file(PORT_FILE)
            if discovered_port is not None:
                break
            await asyncio.sleep(0.3)

        if discovered_port is None:
            process.terminate()
            raise TimeoutError(
                f"Executor failed to write port file within {self._ctx.executor_startup_timeout}s"
            )

        base_url = f"http://{self._ctx.executor_host}:{discovered_port}"
        client = httpx.AsyncClient(base_url=base_url, timeout=30.0)

        while asyncio.get_event_loop().time() < deadline:
            try:
                resp = await client.get("/health")
                if resp.status_code == 200:
                    logger.info("Shared Executor ready (PID=%d, port=%d)", process.pid, discovered_port)
                    # Register under a special key
                    handle = ProcessHandle(
                        plan_id="__shared__",
                        process=process,
                        base_url=base_url,
                        port=discovered_port,
                        client=client,
                    )
                    self._task_processes["__shared__"] = handle
                    self._base_url_legacy = base_url
                    return
            except (httpx.ConnectError, httpx.TimeoutException):
                pass
            await asyncio.sleep(0.3)

        process.terminate()
        await client.aclose()
        raise TimeoutError(
            f"Executor health check failed within {self._ctx.executor_startup_timeout}s"
        )

    # ------------------------------------------------------------------
    # Stop all processes
    # ------------------------------------------------------------------

    async def stop(self) -> None:
        """Stop all running Executor processes."""
        handles = list(self._task_processes.values())
        self._task_processes.clear()

        for handle in handles:
            try:
                await self._stop_handle(handle)
            except Exception:
                logger.exception("Error stopping Executor for %s", handle.plan_id)

    # ------------------------------------------------------------------
    # Sync cleanup (for atexit handlers — best effort)
    # ------------------------------------------------------------------

    def sync_terminate(self) -> None:
        """Best-effort synchronous termination for atexit."""
        for handle in self._task_processes.values():
            if handle.process.returncode is None:
                try:
                    handle.process.terminate()
                except ProcessLookupError:
                    pass


class _AsyncStdoutReader:
    """Adapter: provide async read() over subprocess.Popen stdout."""

    def __init__(self, stream: Any) -> None:
        self._stream = stream

    async def read(self, n: int = -1) -> bytes:
        return await asyncio.to_thread(self._stream.read, n)


class _PopenProcessAdapter:
    """Minimal asyncio-subprocess-like wrapper over subprocess.Popen."""

    def __init__(self, popen: subprocess.Popen) -> None:
        self._popen = popen
        self.stdout = _AsyncStdoutReader(popen.stdout) if popen.stdout is not None else None

    @property
    def pid(self) -> int:
        return self._popen.pid

    @property
    def returncode(self) -> int | None:
        return self._popen.poll()

    async def wait(self) -> int:
        return await asyncio.to_thread(self._popen.wait)

    def terminate(self) -> None:
        self._popen.terminate()

    def kill(self) -> None:
        self._popen.kill()
