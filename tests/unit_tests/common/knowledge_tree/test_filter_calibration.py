"""Filter 校准测试——用真实 Executor 输出模式验证 should_remember 行为。"""

from __future__ import annotations

import pytest

from src.common.knowledge_tree.ingestion.filter import should_remember

# 真实 Executor 输出模式 + 期望行为
# (text, trigger, expected_passed, description)
REAL_EXECUTOR_PATTERNS: list[tuple[str, str, bool, str]] = [
    # task_complete trigger — 应全部通过（宁多勿漏）
    (
        "在 src/common/context.py 中添加了 kt_rag_similarity_threshold 字段，默认值 0.15。",
        "task_complete",
        True,
        "具体配置变更",
    ),
    (
        "步骤1完成：已创建文件 src/executor_agent/server.py，实现了 FastAPI 端点。",
        "task_complete",
        True,
        "步骤完成含文件创建",
    ),
    (
        "发现原因：httpx.AsyncClient 的 timeout 参数需要显式设置，否则默认无限等待。",
        "task_complete",
        True,
        "根因发现",
    ),
    (
        "ok",
        "task_complete",
        True,
        "task_complete 下短文本仍通过",
    ),
    (
        "执行成功",
        "task_complete",
        False,
        "通用模板文本即使 task_complete 也应过滤",
    ),
    (
        "失败原因：Executor 进程崩溃，exit code 137 (SIGKILL)。",
        "task_complete",
        True,
        "失败原因含数字",
    ),
    # user_explicit trigger — 应全部通过
    (
        "记住这个规则",
        "user_explicit",
        True,
        "用户显式指令",
    ),
    # 无 trigger — 依赖关键词/长度/数字
    (
        "架构决定：Supervisor 使用 Mode 1/2/3 三级决策路由，通过 _infer_supervisor_decision 解析。",
        "",
        True,
        "含'决定'关键词 + 含数字",
    ),
    (
        "最佳实践：在 LangGraph graph 中，kt_retrieve 节点应在 __start__ 后、call_model 前执行。",
        "",
        True,
        "含'最佳实践'关键词",
    ),
    (
        "发现一个重要模式：子进程超时时使用 terminate → kill 升级策略可以确保清理。",
        "",
        True,
        "含'发现'+'模式'关键词",
    ),
    (
        "失败原因：端口被占用导致 Executor 无法启动，需要先检查端口占用情况。",
        "",
        True,
        "含'原因'关键词",
    ),
    (
        "经验教训：Executor 超时保护必须设置，否则子进程可能永久挂起。",
        "",
        True,
        "含'经验'+'教训'关键词",
    ),
    # 过短无关键词 — 应过滤
    (
        "执行成功",
        "",
        False,
        "过短无关键词无 trigger",
    ),
    (
        "ok",
        "",
        False,
        "极短无关键词无 trigger",
    ),
    # 足够长度但无关键词 — 应通过（len > 50）
    (
        "完成了文件的创建和修改工作，所有测试用例都通过了，代码质量良好，"
        "覆盖率达到了百分之八十五以上，满足项目的质量要求。",
        "",
        True,
        "长度足够（>50 字）",
    ),
]


@pytest.fixture(params=REAL_EXECUTOR_PATTERNS, ids=lambda p: p[3])
def pattern(request):
    return request.param


class TestFilterCalibration:
    """用真实 Executor 输出模式校准 filter。"""

    def test_expected_pass_fail(self, pattern):
        text, trigger, expected_passed, _desc = pattern
        result = should_remember(text, trigger=trigger)
        assert result.passed == expected_passed, (
            f"Filter miscalibration for '{_desc}':\n"
            f"  text={text!r:.60}...\n"
            f"  trigger={trigger!r}\n"
            f"  expected_passed={expected_passed}, got passed={result.passed}\n"
            f"  reason={result.reason}, confidence={result.confidence}"
        )


class TestFilterScoreDistribution:
    """验证 filter 在不同类别上的分数分布。"""

    def test_trigger_confidence_hierarchy(self):
        """trigger 置信度应高于关键词匹配。"""
        r_trigger = should_remember("ok", trigger="task_complete")
        r_keyword = should_remember("架构决定：使用三层层级", trigger="")

        assert r_trigger.confidence >= r_keyword.confidence

    def test_empty_input_rejected(self):
        result = should_remember("")
        assert not result.passed

    def test_whitespace_only_rejected(self):
        result = should_remember("   \n\t  ")
        assert not result.passed
