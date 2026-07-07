"""轻量过滤：规则判断文本片段是否值得记忆。"""

from __future__ import annotations

import re
from dataclasses import dataclass

# 决策/结论关键词
_DECISION_KEYWORDS = {
    "决定",
    "结论",
    "规则",
    "发现",
    "原因",
    "因为",
    "最佳实践",
    "经验",
    "教训",
    "模式",
    "原则",
    "策略",
    "方案",
    "架构",
    "设计",
    "约束",
    "注意",
    "重要",
    "失败",
    "错误",
    "崩溃",
    "超时",
    "异常",
}

# 数字检测
_HAS_NUMBER = re.compile(r"\d+")

# 技术内容模式（URL、路径、代码片段、JSON、技术术语）
_TECHNICAL_PATTERNS = re.compile(
    r"(https?://\S+|"
    r"[a-zA-Z_]\w*\.\w+\(\)|"
    r"\{.*:.*\}|"
    r"src/[a-zA-Z_/]+\.py|"
    r"[a-zA-Z_]+\.py\s|"
    r"exit code \d+|"
    r"[A-Z_]{3,}=\S+|"
    r"(?:FastAPI|HTTP|API|SDK|SQL|JSON|REST|TCP|UDP|DNS|SSL|JWT)\b)"
)

# 通用模板文本（Executor 自动输出的低信息量短语）
_GENERIC_PATTERNS = re.compile(
    r"^(所有步骤执行完成|执行成功|任务完成|已完成|步骤\d+已成功完成)$"
)

# 低信息量模式（自动摄入时应过滤）
_LOW_VALUE_PATTERNS = re.compile(
    r"(成功列出|成功执行|已通过|成功使用|成功完成|成功创建|成功写入)"
    r".{0,20}(目录|文件|命令|步骤)"
    r".{0,30}$"
)

# 重复性任务描述模式（仅匹配成功/完成的低价值描述，不过滤失败原因）
_REDUNDANT_TASK_PATTERNS = re.compile(
    r"^(步骤\s*step_\d+\s*(?!.*失败原因)"
    r"(完成|成功|已创建|已实现|已配置|已添加|执行了))"
)

# 假阴性事实模式：Executor workspace 边界限制产生的"不存在"类结论。
# 自动摄入时过滤，避免污染 KT（夜间 probe 2026-06-25 session-003 发现的 BUG）。
# 用户显式指令不应用此过滤（should_remember 中 user_explicit 早返回）。
_NEGATIVE_FACT_PATTERNS = re.compile(
    r"(不存在|未找到|找不到|没有[该这](?:个)?文件|无此文件|无该文件"
    r"|No such file|not found|cannot find|does not exist)",
    re.IGNORECASE,
)

# 基础设施错误模式（自动摄入前置 reject）。
# 这些是框架/运行时错误，不属于业务知识。用户显式指令不走此过滤
# （在 user_explicit 早返回之后才检查）。
_INFRA_ERROR_PATTERNS = re.compile(
    r"(BlockingError|blocking\s+call|object\s+MagicMock|"
    r"Traceback\s+\(most\s+recent|"
    r"await\s+expression|"
    r"ImportError|ModuleNotFoundError|"
    r"TypeError:.*await)",
    re.IGNORECASE,
)

# 测试任务结构判据：hello world / test_runner / tmp_test / attempt N 等
# 明显测试任务模式。仅匹配结构化模式，避免词面黑名单（test/echo/mock
# 等单字）误伤合法业务。仅在 task_complete 路径前置 reject。
# 注意：hello\.{ext} 分支须配合创建动作词，避免误伤"修改 hello.py"等合法业务。
_TEST_TASK_PATTERNS = re.compile(
    r"(hello\s+world|"
    r"(?:创建|create|write|写入|建立)[^\n。]{0,40}hello\.(?:py|js|txt)\b|"
    r"test_runner\.py|tmp_test_|_test_\d+|"
    r"\battempt\s+\d+\b)",
    re.IGNORECASE,
)

# 熔断模板判据：MAX_REPLAN 触发后的固定模板回复（决策 33）。
# 这些回复是 Supervisor 状态机终止信号，无知识价值；Entry A 自动摄入时前置 reject。
# 用户显式 ingest 失败教训不走此过滤（user_explicit 早返回通过）。
# 判据特异：仅匹配 "[熔断模板]" 字面前缀——probe 脚本特殊标注，不误伤合法
# 讨论 MAX_REPLAN 机制的回答（如"已达到最大重规划次数（3）"是合法配置差异讨论）。
_CIRCUIT_BREAKER_PATTERNS = re.compile(r"\[熔断模板\]", re.IGNORECASE)


@dataclass
class FilterResult:
    """过滤结果。"""

    passed: bool
    reason: str = ""
    confidence: float = 0.0


def should_remember(chunk: str, trigger: str = "") -> FilterResult:
    """规则判断是否值得记忆。

    策略：宁缺毋滥。自动摄入（task_complete）需要额外质量检查；
    用户显式指令始终通过。

    通过条件（满足任一）：
    - trigger == "user_explicit"（用户显式指令）
    - trigger == "task_complete" 且通过质量检查
    - 含决策/结论关键词 且非低信息量模式
    - 含数字 且含决策关键词
    - 文本长度 > 80 字 且含决策关键词

    Args:
        chunk: 待判断的文本片段。
        trigger: 触发类型（"user_explicit"、"task_complete" 等）。

    Returns:
        FilterResult 包含通过/不通过、原因和置信度。
    """
    if not chunk or not chunk.strip():
        return FilterResult(passed=False, reason="empty_chunk")

    text = chunk.strip()

    # 通用模板文本过滤
    if _GENERIC_PATTERNS.match(text):
        return FilterResult(passed=False, reason="generic_template", confidence=0.0)

    # 低信息量模式过滤（如"成功列出了 X 目录下所有文件"）
    if _LOW_VALUE_PATTERNS.match(text):
        return FilterResult(passed=False, reason="low_value_pattern", confidence=0.0)

    # 重复性任务描述过滤（如"步骤 step_1 在 workspace 目录下执行..."）
    if _REDUNDANT_TASK_PATTERNS.match(text):
        return FilterResult(passed=False, reason="redundant_task_desc", confidence=0.0)

    # 用户显式指令：直接通过
    if trigger == "user_explicit":
        return FilterResult(passed=True, reason="user_explicit", confidence=1.0)

    # 基础设施错误前置过滤（仅对自动摄入触发，user_explicit 已早返回通过）。
    # BlockingError / MagicMock / Traceback 等不属于业务知识。
    if trigger == "task_complete" and _INFRA_ERROR_PATTERNS.search(text):
        return FilterResult(passed=False, reason="infra_error", confidence=0.0)

    # 测试任务结构判据（仅 auto task_complete；user_explicit 已早返回）。
    # hello world / test_runner / tmp_test / attempt N 等明显测试任务模式。
    if trigger == "task_complete" and _TEST_TASK_PATTERNS.search(text):
        return FilterResult(passed=False, reason="test_task_residual", confidence=0.0)

    # 熔断模板判据（仅 auto task_complete；user_explicit 已早返回）。
    # MAX_REPLAN 触发后的固定模板回复，无知识价值。
    if trigger == "task_complete" and _CIRCUIT_BREAKER_PATTERNS.search(text):
        return FilterResult(passed=False, reason="circuit_breaker_template", confidence=0.0)

    # 含决策/结论关键词
    has_keyword = any(kw in text for kw in _DECISION_KEYWORDS)

    # 任务完成 summary：需要关键词 + 合理长度，或足够长
    if trigger == "task_complete":
        # 假阴性事实过滤：Executor workspace 边界限制产生的"不存在"类结论
        # 不应作为事实摄入 KT（用户显式指令已在前 bypass）。
        if _NEGATIVE_FACT_PATTERNS.search(text):
            return FilterResult(
                passed=False, reason="workspace_negative_fact", confidence=0.0
            )
        if has_keyword and len(text) > 15:
            return FilterResult(passed=True, reason="task_complete_with_substance", confidence=0.8)
        if len(text) > 100:
            return FilterResult(passed=True, reason="task_complete_long_summary", confidence=0.6)
        return FilterResult(passed=False, reason="task_complete_low_substance", confidence=0.0)

    # 非显式/非 task_complete 的自动摄入：需满足更严格条件
    if has_keyword:
        return FilterResult(passed=True, reason="keyword_match", confidence=0.7)

    # 含数字 + 含关键词 → 通过
    if _HAS_NUMBER.search(text) and has_keyword:
        return FilterResult(passed=True, reason="number_with_keyword", confidence=0.6)

    # 含数字（独立路径，较低置信度）
    if _HAS_NUMBER.search(text) and len(text) > 20:
        return FilterResult(passed=True, reason="has_number", confidence=0.5)

    # 技术内容（URL/路径/代码/JSON）→ 通过
    if _TECHNICAL_PATTERNS.search(text):
        return FilterResult(passed=True, reason="technical_content", confidence=0.5)

    # 长文本兜底（> 100 字符）
    if len(text) > 100:
        return FilterResult(passed=True, reason="sufficient_length", confidence=0.3)

    # 过短且无关键词
    return FilterResult(passed=False, reason="too_short_no_keyword", confidence=0.0)
