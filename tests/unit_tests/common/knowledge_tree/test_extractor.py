"""Unit tests for knowledge extraction from executor results (Entry A)."""

from __future__ import annotations

import json

from src.common.knowledge_tree.ingestion.extractor import (
    extract_experience_from_executor_result,
    extract_knowledge_from_executor_result,
)


def _make_plan_json(
    goal: str = "测试任务",
    steps: list[dict] | None = None,
) -> str:
    """辅助：构造 plan JSON 字符串。"""
    return json.dumps(
        {
            "plan_id": "plan_test",
            "version": 1,
            "goal": goal,
            "steps": steps or [],
        },
        ensure_ascii=False,
    )


class TestExtractFromCompletedPlan:
    """完成的计划应提取步骤级 result_summary。"""

    def test_completed_with_summaries(self):
        summary = "已完成所有步骤：创建了文件并修改了配置。"
        plan_json = _make_plan_json(
            goal="创建并配置新模块",
            steps=[
                {
                    "step_id": "step_1",
                    "intent": "创建文件",
                    "expected_output": "新文件已创建",
                    "status": "completed",
                    "result_summary": "在 src/module/ 下创建了 api.py 文件，包含 FastAPI 端点。",
                },
                {
                    "step_id": "step_2",
                    "intent": "修改配置",
                    "expected_output": "配置已更新",
                    "status": "completed",
                    "result_summary": "在 config.toml 中添加了新模块的配置项，端口设为 8080。",
                },
                {
                    "step_id": "step_3",
                    "intent": "编写测试",
                    "expected_output": "测试通过",
                    "status": "completed",
                    "result_summary": "编写了 5 个单元测试，全部通过。",
                },
            ],
        )

        result = extract_knowledge_from_executor_result(summary, plan_json, "completed")

        # 至少包含：顶层 summary + 3 个步骤 summary + goal
        assert len(result) >= 4

        # 验证包含具体内容
        all_text = " ".join(result)
        assert "api.py" in all_text
        assert "8080" in all_text
        assert "测试" in all_text

    def test_only_summary_no_plan(self):
        summary = "发现一个重要规则：向量搜索需要设置合适的相似度阈值。"
        result = extract_knowledge_from_executor_result(summary, "", "completed")
        assert len(result) >= 1
        assert "向量搜索" in result[0]


class TestExtractFromFailedPlan:
    """失败的计划应提取 failure_reason。"""

    def test_failed_step_with_reason(self):
        summary = "执行失败：第三步超时。"
        plan_json = _make_plan_json(
            steps=[
                {
                    "step_id": "step_1",
                    "intent": "初始化环境",
                    "status": "completed",
                    "result_summary": "环境初始化完成。",
                    "failure_reason": "",
                },
                {
                    "step_id": "step_2",
                    "intent": "执行长时间任务",
                    "status": "failed",
                    "result_summary": "",
                    "failure_reason": "Executor 进程超时，exit code 137 (SIGKILL)，可能是内存不足。",
                },
            ],
        )

        result = extract_knowledge_from_executor_result(summary, plan_json, "failed")
        all_text = " ".join(result)
        assert "失败原因" in all_text
        assert "SIGKILL" in all_text


class TestEdgeCases:
    """边界条件处理。"""

    def test_empty_all(self):
        result = extract_knowledge_from_executor_result("", "", "completed")
        assert result == []

    def test_empty_json_string(self):
        result = extract_knowledge_from_executor_result("summary", "  ", "completed")
        assert len(result) >= 1

    def test_invalid_json(self):
        result = extract_knowledge_from_executor_result("summary", "{malformed", "completed")
        # summary 仍应被提取
        assert len(result) >= 1

    def test_none_json(self):
        result = extract_knowledge_from_executor_result("summary", None, "completed")
        assert len(result) >= 1

    def test_plan_with_empty_steps(self):
        plan_json = _make_plan_json(steps=[])
        result = extract_knowledge_from_executor_result("", plan_json, "completed")
        # 空 steps + completed → goal 提取
        assert len(result) >= 1


class TestFilterIntegration:
    """验证提取与 should_remember filter 的交互。"""

    def test_short_summary_filtered(self):
        """过短的 summary 且无关键词应被过滤。"""
        plan_json = _make_plan_json(
            steps=[
                {
                    "step_id": "step_1",
                    "intent": "测试",
                    "status": "completed",
                    "result_summary": "ok",
                    "failure_reason": "",
                },
            ],
        )
        result = extract_knowledge_from_executor_result("", plan_json, "completed")
        # "ok" 太短 → 过滤；但 "任务目标..." 可能包含它
        for chunk in result:
            assert "ok" not in chunk or len(chunk) > 10

    def test_meaningful_summary_passes(self):
        """有意义的 summary 应通过 filter。"""
        summary = "发现重要规则：Executor 超时保护使用 terminate → kill 升级策略。"
        result = extract_knowledge_from_executor_result(summary, "", "completed")
        assert any("超时" in r or "规则" in r for r in result)

    def test_task_complete_trigger_passes(self):
        """task_complete trigger 应让大多数内容通过。"""
        summary = "在 src/common/context.py 中添加了配置字段。"
        result = extract_knowledge_from_executor_result(summary, "", "completed")
        # trigger="task_complete" → 所有非空 chunk 都应通过
        assert len(result) >= 1


class TestRealExecutorFormat:
    """模拟真实 Executor 结果格式。"""

    def test_real_format_parsing(self):
        summary = (
            "已完成文件创建和测试验证。\n"
            "创建了 src/common/knowledge_tree/ingestion/extractor.py。\n"
            "所有测试通过。"
        )
        plan_json = _make_plan_json(
            goal="实现 Executor 结果知识提取器",
            steps=[
                {
                    "step_id": "step_1",
                    "intent": "创建提取器模块",
                    "expected_output": "extractor.py 文件已创建",
                    "status": "completed",
                    "result_summary": "创建了 extractor.py，实现了 extract_knowledge_from_executor_result 函数。",
                    "failure_reason": "",
                },
                {
                    "step_id": "step_2",
                    "intent": "编写单元测试",
                    "expected_output": "测试文件已创建",
                    "status": "completed",
                    "result_summary": "编写了 10 个测试用例覆盖正常和边界情况。",
                    "failure_reason": "",
                },
            ],
        )

        result = extract_knowledge_from_executor_result(summary, plan_json, "completed")
        assert len(result) >= 3

        all_text = " ".join(result)
        assert "extractor.py" in all_text
        assert "测试" in all_text


class TestExperienceExtraction:
    """验证经验节点提取（元认知阶段 1）。"""

    def test_failed_task_extracts_experience(self):
        """失败任务应提取经验四元组。"""
        summary = "执行失败：在处理大规模文件时 Executor 超时退出。"
        plan_json = _make_plan_json(
            steps=[
                {
                    "step_id": "step_1",
                    "intent": "扫描目录下所有文件",
                    "status": "failed",
                    "result_summary": "",
                    "failure_reason": "Executor 进程超时，可能是文件数量过多导致内存溢出。",
                },
            ],
        )
        result = extract_experience_from_executor_result(summary, plan_json, "failed")
        assert len(result) == 1
        text = result[0]
        assert "情境" in text
        assert "行动" in text
        assert "教训" in text
        assert "失败" in text

    def test_completed_with_discovery_extracts_experience(self):
        """完成但含有发现性内容时提取经验。"""
        summary = "发现重要模式：在 config.yaml 中设置 worker_timeout 时需要同步修改 supervisor_timeout，否则会导致进程假死。"
        plan_json = _make_plan_json(
            goal="配置超时参数",
            steps=[
                {
                    "step_id": "step_1",
                    "intent": "修改配置文件",
                    "status": "completed",
                    "result_summary": "发现 worker_timeout 和 supervisor_timeout 需要同步修改。",
                    "failure_reason": "",
                },
            ],
        )
        result = extract_experience_from_executor_result(summary, plan_json, "completed")
        assert len(result) == 1
        text = result[0]
        assert "情境" in text
        assert "教训" in text

    def test_completed_trivial_no_experience(self):
        """简单确认性完成不提取经验。"""
        summary = "已读取文件。"
        plan_json = _make_plan_json(
            steps=[
                {
                    "step_id": "step_1",
                    "intent": "读取配置",
                    "status": "completed",
                    "result_summary": "已读取。",
                    "failure_reason": "",
                },
            ],
        )
        result = extract_experience_from_executor_result(summary, plan_json, "completed")
        assert len(result) == 0

    def test_empty_input_no_experience(self):
        """空输入不提取经验。"""
        result = extract_experience_from_executor_result("", "", "completed")
        assert result == []

    def test_experience_format_contains_all_fields(self):
        """经验节点包含完整的四元组字段。"""
        summary = "使用 uv run pytest 运行测试时，需要确保 .env 文件存在，否则会加载失败。"
        plan_json = _make_plan_json(
            steps=[
                {
                    "step_id": "step_1",
                    "intent": "运行测试",
                    "status": "failed",
                    "result_summary": "",
                    "failure_reason": "缺少 .env 文件导致配置加载失败。",
                },
            ],
        )
        result = extract_experience_from_executor_result(summary, plan_json, "failed")
        assert len(result) == 1
        for field in ["情境", "行动", "结果", "教训", "适用"]:
            assert field in result[0]
