"""原子切分：将长文本按语义边界切成小片段。"""

from __future__ import annotations

import re


def _estimate_tokens(text: str) -> int:
    """简单估算 token 数。

    中文约 1.5 字/token，英文约 0.75 词/token。
    P1 用统一公式：len(text) * 0.67。
    """
    return int(len(text) * 0.67)


def chunk_text(
    text: str,
    max_tokens: int = 512,
) -> list[str]:
    """按 \\n\\n 边界切分文本。

    P1 策略：先按双换行切分，再合并过短的片段。
    任何片段都不超过 max_tokens（简单截断）。

    Args:
        text: 待切分的原始文本。
        max_tokens: 每段最大 token 数估算。

    Returns:
        切分后的文本片段列表。
    """
    if not text or not text.strip():
        return []

    # 按双换行切分
    raw_chunks = re.split(r"\n\n+", text.strip())
    # 过滤空片段
    raw_chunks = [c.strip() for c in raw_chunks if c.strip()]

    if not raw_chunks:
        return []

    # 合并过短片段
    merged: list[str] = []
    buffer = ""

    for chunk in raw_chunks:
        if not buffer:
            buffer = chunk
        elif _estimate_tokens(buffer + "\n\n" + chunk) <= max_tokens:
            buffer = buffer + "\n\n" + chunk
        else:
            merged.append(buffer)
            buffer = chunk

    if buffer:
        merged.append(buffer)

    # 对超长片段做截断
    result: list[str] = []
    for chunk in merged:
        tokens = _estimate_tokens(chunk)
        if tokens <= max_tokens:
            result.append(chunk)
        else:
            # 按 max_tokens 估算字符数截断
            max_chars = int(max_tokens / 0.67)
            result.append(chunk[:max_chars])

    return result


def chunk_conversation(
    messages: list[dict],
    max_tokens: int = 512,
) -> list[str]:
    """按对话轮切分。

    每轮消息格式：{"role": str, "content": str}。
    短轮合并，长轮独立（如超长则截断）。

    Args:
        messages: 对话消息列表。
        max_tokens: 每段最大 token 数估算。

    Returns:
        切分后的文本片段列表（每段含 1+ 轮对话）。
    """
    if not messages:
        return []

    # 格式化每轮
    turns: list[str] = []
    for msg in messages:
        role = msg.get("role", "unknown")
        content = msg.get("content", "")
        if content:
            turns.append(f"[{role}] {content}")

    # 合并短轮
    merged: list[str] = []
    buffer = ""

    for turn in turns:
        if not buffer:
            buffer = turn
        elif _estimate_tokens(buffer + "\n" + turn) <= max_tokens:
            buffer = buffer + "\n" + turn
        else:
            merged.append(buffer)
            buffer = turn

    if buffer:
        merged.append(buffer)

    # 截断超长片段
    result: list[str] = []
    for chunk in merged:
        if _estimate_tokens(chunk) <= max_tokens:
            result.append(chunk)
        else:
            max_chars = int(max_tokens / 0.67)
            result.append(chunk[:max_chars])

    return result
