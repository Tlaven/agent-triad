"""Normalize tool observations before they enter ReAct message history.
改进：使用头尾保留 + 智能断行的 _truncate_smart 替代纯头部截断。"""

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


# ====================== 智能截断相关配置 ======================

_DEFAULT_HEAD_RATIO = 0.55          # 头部占比（稍偏尾部，更容易看到最终结果/错误）
_LINE_BREAK_WINDOW = 200            # 寻找自然断行点的窗口大小（字符）
_MIN_TAIL_CHARS = 150               # 最小保留尾部字符数（确保能看到关键的错误/总结）


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

    - If raw length > observation_offload_threshold_chars and offload is enabled → offload
    - Else if raw length > max_observation_chars → smart truncate (head + tail)
    - enable_observation_summary is reserved for future summarization.
    """
    _ = context.enable_observation_summary  # reserved for optional summarization

    raw = _serialize_tool_result(result)
    n = len(raw)
    base_cwd = cwd if cwd and os.path.isdir(cwd) else os.getcwd()

    # 1. 优先尝试外置大输出
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
            # Fallback: smart truncate instead of failing
            return _truncate_smart(raw, n, context.max_observation_chars)

        rel_display = os.path.join(rel_dir, fname).replace("\\", "/")
        msg = (
            f"[工具输出已外置，原始长度 {n:,} 字符]\n"
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

    # 2. 需要截断时使用智能头尾保留
    if n > context.max_observation_chars:
        return _truncate_smart(raw, n, context.max_observation_chars)

    # 3. 正常情况
    return NormalizedObservation(
        text=raw,
        truncated=False,
        offloaded=False,
        original_char_length=n,
        offload_path=None,
    )


def _truncate_smart(
    raw: str, n: int, max_chars: int, *, head_ratio: float = _DEFAULT_HEAD_RATIO
) -> NormalizedObservation:
    """保留头部 + 尾部，中间插入清晰的省略提示，并在自然断行处切割。"""

    if max_chars < 300:  # 极端小预算，直接 fallback
        return _truncate_fallback(raw, n, max_chars)

    # 计算 notice 占用的长度（使用占位符估算）
    _placeholder_notice = (
        f"\n\n{'─' * 40}\n"
        f"⌇ 已省略中间部分，约 {n:,} 字符 (xxxx + xxxx / {n:,})\n"
        f"{'─' * 40}\n\n"
    )
    notice_len = len(_placeholder_notice)

    available = max_chars - notice_len
    if available <= _MIN_TAIL_CHARS + 100:
        return _truncate_fallback(raw, n, max_chars)

    # 分配 head / tail 预算（带最小尾部保护）
    tail_budget = max(_MIN_TAIL_CHARS, int(available * (1 - head_ratio)))
    head_budget = available - tail_budget

    # 在自然断行处切割
    head_end = _find_break_point(raw, head_budget, direction="forward")
    head_text = raw[:head_end]

    tail_start = _find_break_point(raw, n - tail_budget, direction="backward")
    tail_text = raw[tail_start:]

    # 计算实际省略量并生成最终 notice
    actual_omitted = n - len(head_text) - len(tail_text)
    notice = (
        f"\n\n{'─' * 40}\n"
        f"⌇ 已省略中间部分，约 {actual_omitted:,} 字符 "
        f"({len(head_text):,} + {len(tail_text):,} / {n:,})\n"
        f"{'─' * 40}\n\n"
    )

    # 如果 notice 比预估的长，微调 head（极少发生）
    notice_delta = len(notice) - notice_len
    if notice_delta > 0 and len(head_text) > notice_delta:
        head_text = head_text[:-notice_delta]

    return NormalizedObservation(
        text=head_text + notice + tail_text,
        truncated=True,
        offloaded=False,
        original_char_length=n,
        offload_path=None,
    )


def _find_break_point(raw: str, pos: int, *, direction: str = "forward") -> int:
    """在 pos 附近寻找自然的断行点（换行符或空格），避免割裂单词或行。"""
    if pos <= 0:
        return 0
    if pos >= len(raw):
        return len(raw)

    window = _LINE_BREAK_WINDOW
    search_start = max(0, pos - window)
    search_end = min(len(raw), pos + window)

    if direction == "forward":
        # 优先找换行
        idx = raw.find("\n", pos, search_end)
        if idx != -1:
            return idx + 1
        # 其次找空格
        idx = raw.find(" ", pos, search_end)
        if idx != -1:
            return idx + 1
        return pos
    else:  # backward
        # 优先找换行（从 pos 往前）
        idx = raw.rfind("\n", search_start, pos + 1)
        if idx != -1:
            return idx + 1
        # 其次找空格
        idx = raw.rfind(" ", search_start, pos + 1)
        if idx != -1:
            return idx + 1
        return pos


def _truncate_fallback(raw: str, n: int, max_chars: int) -> NormalizedObservation:
    """极端情况后备：纯头部截断。"""
    head = raw[:max_chars]
    notice = f"\n\n[已截断，原始长度 {n:,} 字符]"
    return NormalizedObservation(
        text=head + notice,
        truncated=True,
        offloaded=False,
        original_char_length=n,
        offload_path=None,
    )


def normalize_tool_message_content(
    content: Any, *, context: Context, cwd: str | None = None
) -> str:
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