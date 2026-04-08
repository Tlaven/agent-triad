"""V2-a: Normalize tool observations before they enter ReAct message history."""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass
from typing import Any

from src.common.context import Context


@dataclass(frozen=True)
class NormalizedObservation:
    """Result of applying observation governance to a single tool output."""

    text: str
    truncated: bool
    offloaded: bool
    original_char_length: int
    offload_path: str | None = None


def _serialize_tool_result(result: Any) -> str:
    """Turn arbitrary tool return value into a string for length accounting."""
    if isinstance(result, str):
        return result
    try:
        return json.dumps(result, ensure_ascii=False, default=str)
    except TypeError:
        return str(result)


def normalize_observation(
    result: Any,
    *,
    context: Context,
    cwd: str | None = None,
) -> NormalizedObservation:
    """Apply truncation / offload policy so observations stay within budget.

    - If raw length > ``observation_offload_threshold_chars`` and offload is enabled,
      write full text to ``workspace/.observations/<uuid>.txt`` and return a short reference.
    - Else if raw length > ``max_observation_chars``, truncate with an explicit notice.
    - ``enable_observation_summary`` is reserved for a future optional summarization pass (V2-a stub).
    """
    _ = context.enable_observation_summary  # reserved for optional summarization (not implemented yet)
    raw = _serialize_tool_result(result)
    n = len(raw)
    base_cwd = cwd if cwd and os.path.isdir(cwd) else os.getcwd()

    if (
        context.enable_observation_offload
        and n > context.observation_offload_threshold_chars
    ):
        rel_dir = (context.observation_workspace_dir or "workspace/.observations").strip()
        abs_dir = os.path.abspath(os.path.join(base_cwd, rel_dir))
        os.makedirs(abs_dir, exist_ok=True)
        fname = f"{uuid.uuid4().hex}.txt"
        abs_path = os.path.join(abs_dir, fname)
        try:
            with open(abs_path, "w", encoding="utf-8") as f:
                f.write(raw)
        except OSError:
            # Fallback: truncate instead of failing the graph
            return _truncate_only(raw, n, context.max_observation_chars)

        rel_display = os.path.join(rel_dir, fname).replace("\\", "/")
        msg = (
            f"[工具输出已外置，原始长度 {n} 字符]\n"
            f"文件路径（相对工作目录）: {rel_display}\n"
            f"绝对路径: {abs_path}"
        )
        return NormalizedObservation(
            text=msg,
            truncated=False,
            offloaded=True,
            original_char_length=n,
            offload_path=abs_path,
        )

    if n > context.max_observation_chars:
        return _truncate_only(raw, n, context.max_observation_chars)

    return NormalizedObservation(
        text=raw,
        truncated=False,
        offloaded=False,
        original_char_length=n,
        offload_path=None,
    )


def _truncate_only(raw: str, n: int, max_chars: int) -> NormalizedObservation:
    head = raw[:max_chars]
    notice = f"\n\n[已截断，原始长度 {n} 字符]"
    return NormalizedObservation(
        text=head + notice,
        truncated=True,
        offloaded=False,
        original_char_length=n,
        offload_path=None,
    )


def normalize_tool_message_content(content: Any, *, context: Context, cwd: str | None = None) -> str:
    """Normalize LangChain ToolMessage content (str or structured blocks) to a bounded string."""
    if isinstance(content, str):
        return normalize_observation(content, context=context, cwd=cwd).text
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") == "text" and "text" in block:
                parts.append(str(block["text"]))
            else:
                parts.append(json.dumps(block, ensure_ascii=False, default=str))
        joined = "\n".join(parts)
        return normalize_observation(joined, context=context, cwd=cwd).text
    return normalize_observation(content, context=context, cwd=cwd).text
