"""Executor 子进程入口（Process B）。

Usage: python -m src.executor_agent

Supports dynamic port allocation (EXECUTOR_PORT=0):
  - Binds a socket to discover the OS-assigned port.
  - Writes the port to logs/executor.port (or logs/executor_{PLAN_ID}.port) for Supervisor discovery.
  - Passes the bound socket to uvicorn.

Environment variables:
  - EXECUTOR_PORT: Port to bind to (0 = dynamic, default).
  - PLAN_ID: Optional. If set, writes port to logs/executor_{PLAN_ID}.port for per-task discovery.
  - MAILBOX_URL: Optional. If set, Executor will push results to this URL after task completion.
"""

import os
import socket
from pathlib import Path


def _get_port_file() -> Path:
    """Determine the port file path based on PLAN_ID env var."""
    plan_id = os.environ.get("PLAN_ID", "")
    if plan_id:
        return Path(f"logs/executor_{plan_id}.port")
    return Path("logs/executor.port")


def _write_port_file(port_file: Path, port: int) -> None:
    port_file.parent.mkdir(parents=True, exist_ok=True)
    port_file.write_text(str(port))


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("EXECUTOR_PORT", "0"))
    port_file = _get_port_file()

    # Bind socket to discover OS-assigned port (port=0 → dynamic)
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if port != 0:
        # SO_REUSEADDR only meaningful for fixed-port reuse; skip on dynamic
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("0.0.0.0", port))
    actual_port = sock.getsockname()[1]

    # Persist port for Supervisor discovery
    _write_port_file(port_file, actual_port)

    sock.listen()
    uvicorn.Server(uvicorn.Config(
        "src.executor_agent.server:app",
        host="0.0.0.0",
        port=actual_port,
        log_level="info",
    )).run(sockets=[sock])
