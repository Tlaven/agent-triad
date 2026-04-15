"""Checkpoint Recorder — semi-automated testing infrastructure.

Records labeled data at key execution points, then writes a markdown report.
The report is designed for AI review: no assertions, just captured state.

Usage:
    recorder = CheckpointRecorder("test_name")

    cp = recorder.checkpoint("subprocess_spawn")
    cp.record("port_file_content", pm._read_port_file())
    cp.record("base_url", pm.base_url)

    recorder.write_report()  # -> logs/checkpoints/{test_name}.md
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

REPORT_DIR = Path("logs/checkpoints")


def _serialize(obj: Any) -> str:
    """Convert any object to a human-readable string."""
    if obj is None:
        return "None"
    if isinstance(obj, str):
        return obj
    if isinstance(obj, (dict, list, tuple)):
        try:
            return json.dumps(obj, indent=2, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            return str(obj)
    return str(obj)


class Checkpoint:
    """A single checkpoint with labeled records."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.records: list[tuple[str, str]] = []
        self.timestamp = datetime.now().isoformat(timespec="seconds")

    def record(self, label: str, data: Any) -> None:
        """Capture a piece of data with a descriptive label."""
        self.records.append((label, _serialize(data)))

    def record_raw(self, label: str, text: str) -> None:
        """Capture pre-formatted text directly."""
        self.records.append((label, text))


class CheckpointRecorder:
    """Collects checkpoints and writes a review report."""

    def __init__(self, test_name: str) -> None:
        self.test_name = test_name
        self.checkpoints: list[Checkpoint] = []
        self.start_time = datetime.now()

    def checkpoint(self, name: str) -> Checkpoint:
        """Create and return a new checkpoint."""
        cp = Checkpoint(name)
        self.checkpoints.append(cp)
        return cp

    def write_report(self) -> Path:
        """Write all checkpoints to a markdown file for review."""
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        path = REPORT_DIR / f"{self.test_name}.md"

        lines: list[str] = []
        lines.append(f"# Checkpoint Report: {self.test_name}")
        lines.append(f"")
        lines.append(f"- Generated: {datetime.now().isoformat(timespec='seconds')}")
        lines.append(f"- Duration: {datetime.now() - self.start_time}")
        lines.append(f"- Checkpoints: {len(self.checkpoints)}")
        lines.append(f"")

        for i, cp in enumerate(self.checkpoints, 1):
            lines.append(f"---")
            lines.append(f"## CP{i}: {cp.name}")
            lines.append(f"`timestamp: {cp.timestamp}`")
            lines.append(f"")
            for label, data in cp.records:
                lines.append(f"### {label}")
                lines.append(f"```")
                if "\n" in data:
                    lines.append(data)
                else:
                    lines.append(data)
                lines.append(f"```")
                lines.append(f"")

        path.write_text("\n".join(lines), encoding="utf-8")
        return path
