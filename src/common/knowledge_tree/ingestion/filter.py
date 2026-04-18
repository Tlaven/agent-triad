"""轻量过滤：规则判断文本片段是否值得记忆。"""

from __future__ import annotations

import re
from dataclasses import dataclass

# 决策/结论关键词
_DECISION_KEYWORDS = {
    "决定", "结论", "规则", "发现", "原因", "因为",
    "最佳实践", "经验", "教训", "模式", "原则", "策略",
    "方案", "架构", "设计", "约束", "注意", "重要",
}

# 数字检测
_HAS_NUMBER = re.compile(r"\d+")


@dataclass
class FilterResult:
    """过滤结果。"""

    passed: bool
    reason: str = ""
    confidence: float = 0.0


def should_remember(chunk: str, trigger: str = "") -> FilterResult:
    """规则判断是否值得记忆。低阈值（宁多勿漏）。

    通过条件（满足任一）：
    - trigger == "user_explicit"（用户显式指令）
    - trigger == "task_complete"（任务完成的 summary）
    - 含决策/结论关键词
    - 含数字
    - 文本长度 > 50 字（信息密度足够）

    Args:
        chunk: 待判断的文本片段。
        trigger: 触发类型（"user_explicit"、"task_complete" 等）。

    Returns:
        FilterResult 包含通过/不通过、原因和置信度。
    """
    if not chunk or not chunk.strip():
        return FilterResult(passed=False, reason="empty_chunk")

    text = chunk.strip()

    # 用户显式指令：直接通过
    if trigger == "user_explicit":
        return FilterResult(passed=True, reason="user_explicit", confidence=1.0)

    # 任务完成 summary：直接通过
    if trigger == "task_complete":
        return FilterResult(passed=True, reason="task_complete", confidence=0.9)

    # 含决策/结论关键词
    for kw in _DECISION_KEYWORDS:
        if kw in text:
            return FilterResult(
                passed=True,
                reason=f"keyword:{kw}",
                confidence=0.7,
            )

    # 含数字（可能包含具体信息）
    if _HAS_NUMBER.search(text):
        return FilterResult(passed=True, reason="has_number", confidence=0.5)

    # 文本长度足够
    if len(text) > 50:
        return FilterResult(passed=True, reason="sufficient_length", confidence=0.3)

    # 过短且无关键词
    return FilterResult(passed=False, reason="too_short_no_keyword", confidence=0.0)
