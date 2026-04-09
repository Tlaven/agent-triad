"""V3 foundation helpers for fan-out / fan-in orchestration.

This module is intentionally side-effect free so it can be reused by both
LangGraph node logic and unit tests.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ExecutionBatch:
    """A schedulable unit in fan-out mode."""

    batch_id: str
    step_ids: list[str]


def build_execution_batches(steps: list[dict[str, Any]]) -> list[ExecutionBatch]:
    """Build topological execution batches from step dependency metadata.

    Rules:
    - `depends_on` is optional; missing means no dependency.
    - `parallel_group` is optional; when present, ready steps with the same label
      are grouped into one batch.
    - Raises ValueError on circular dependencies.
    """
    step_map: dict[str, dict[str, Any]] = {}
    incoming: dict[str, set[str]] = {}
    outgoing: dict[str, set[str]] = {}

    for idx, step in enumerate(steps, start=1):
        sid = str(step.get("step_id") or f"step_{idx}")
        deps = {str(d) for d in (step.get("depends_on") or [])}
        step_map[sid] = step
        incoming[sid] = set(deps)
        outgoing.setdefault(sid, set())

    for sid, deps in incoming.items():
        for dep in deps:
            outgoing.setdefault(dep, set()).add(sid)

    batches: list[ExecutionBatch] = []
    remaining = set(step_map.keys())
    round_id = 1

    while remaining:
        ready = sorted([sid for sid in remaining if not incoming[sid]])
        if not ready:
            raise ValueError("Detected circular dependencies in plan steps.")

        grouped: dict[str, list[str]] = {}
        for sid in ready:
            pg = str(step_map[sid].get("parallel_group") or sid)
            grouped.setdefault(pg, []).append(sid)

        for gname, sids in grouped.items():
            batches.append(
                ExecutionBatch(
                    batch_id=f"batch_{round_id}_{gname}",
                    step_ids=sorted(sids),
                )
            )

        for sid in ready:
            remaining.remove(sid)
            for nxt in outgoing.get(sid, set()):
                incoming[nxt].discard(sid)
        round_id += 1

    return batches


def merge_parallel_step_states(
    base_steps: list[dict[str, Any]],
    partial_step_sets: list[list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    """Merge step states from parallel executors into one canonical step list."""
    merged = [dict(step) for step in base_steps]
    index = {str(step.get("step_id")): i for i, step in enumerate(merged)}

    priority = {"failed": 3, "completed": 2, "skipped": 1, "pending": 0}

    for partial_steps in partial_step_sets:
        for pstep in partial_steps:
            sid = str(pstep.get("step_id"))
            if sid not in index:
                continue
            i = index[sid]
            current = merged[i]
            cst = str(current.get("status") or "pending")
            pst = str(pstep.get("status") or "pending")
            if priority.get(pst, 0) >= priority.get(cst, 0):
                current["status"] = pst
                if pstep.get("result_summary") is not None:
                    current["result_summary"] = pstep.get("result_summary")
                if pstep.get("failure_reason") is not None:
                    current["failure_reason"] = pstep.get("failure_reason")

    return merged


def merge_fanin_summaries(summaries: list[str], *, max_chars: int) -> str:
    """Merge executor summaries with hard character budget."""
    clean = [s.strip() for s in summaries if (s or "").strip()]
    if not clean:
        return ""
    text = "\n\n".join(f"[{idx}] {s}" for idx, s in enumerate(clean, start=1))
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + f"\n\n[已截断，原始长度 {len(text)} 字符]"


def serialize_plan_with_steps(plan_obj: dict[str, Any], steps: list[dict[str, Any]]) -> str:
    """Return a JSON plan string with replaced steps."""
    out = dict(plan_obj)
    out["steps"] = steps
    return json.dumps(out, ensure_ascii=False, indent=2)

