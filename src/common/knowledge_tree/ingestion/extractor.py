"""Executor 结果知识提取器（Entry A）。

从 Executor 完成结果中提取有价值的知识片段，
返回经 should_remember 过滤后的候选文本列表。
"""

from __future__ import annotations

import json
import logging

from src.common.knowledge_tree.ingestion.filter import should_remember

logger = logging.getLogger(__name__)


def extract_knowledge_from_executor_result(
    summary: str,
    updated_plan_json: str,
    status: str,
) -> list[str]:
    """从 Executor 完成结果中提取知识候选。

    Args:
        summary: Executor 返回的 summary 文本。
        updated_plan_json: Executor 返回的 updated_plan_json 字符串。
        status: Executor 状态（"completed"/"failed"/"paused"）。

    Returns:
        通过 should_remember 过滤的文本片段列表。
    """
    candidates: list[str] = []
    trigger = "task_complete"

    # 1. 顶层 summary
    if summary and summary.strip():
        candidates.append(summary.strip())

    # 2. 从 updated_plan_json 提取步骤级信息
    if updated_plan_json and updated_plan_json.strip():
        try:
            plan = json.loads(updated_plan_json)
        except (json.JSONDecodeError, TypeError):
            logger.debug("extractor: updated_plan_json 解析失败，跳过步骤提取")
        else:
            steps = plan.get("steps", [])
            for step in steps:
                step_id = step.get("step_id", "?")
                intent = step.get("intent", "")

                result_summary = step.get("result_summary", "")
                if result_summary and result_summary.strip():
                    candidates.append(
                        f"步骤 {step_id} ({intent}): {result_summary.strip()}"
                    )

                failure_reason = step.get("failure_reason", "")
                if failure_reason and failure_reason.strip():
                    candidates.append(
                        f"步骤 {step_id} ({intent}) 失败原因: {failure_reason.strip()}"
                    )

            # 提取 plan 级别的 goal 作为上下文
            goal = plan.get("goal", "")
            if goal and goal.strip() and status == "completed":
                candidates.append(
                    f"任务目标「{goal.strip()}」已完成。{summary.strip() if summary else ''}"
                )

    if not candidates:
        return []

    # 3. 过滤
    passed: list[str] = []
    for chunk in candidates:
        result = should_remember(chunk, trigger=trigger)
        if result.passed:
            passed.append(chunk)
        else:
            logger.debug(
                "extractor: chunk filtered out (%s): %.80s...",
                result.reason,
                chunk,
            )

    if passed:
        logger.info(
            "extractor: extracted %d/%d knowledge chunks from executor result",
            len(passed),
            len(candidates),
        )

    return passed
