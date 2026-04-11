"""V3: Entry point for Executor Process B.

Usage: python -m src.executor_agent
"""

import os
import uvicorn

if __name__ == "__main__":
    port = int(os.environ.get("EXECUTOR_PORT", "8100"))
    uvicorn.run(
        "src.executor_agent.server:app",
        host="0.0.0.0",
        port=port,
        log_level="info",
    )
