"""Executor 结果知识提取器（Entry A）。

从 Executor 完成结果中提取有价值的知识片段，
返回经 should_remember 过滤后的候选文本列表。
"""

from __future__ import annotations

import json
import logging
import re

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


# 经验提炼关键词：检测 completion summary 中是否有知识发现性内容
_DISCOVERY_PATTERNS = re.compile(
    r"发现|确认|正确的.*是|需要先|必须|关键|重要.*模式|导致.*原因|因为|只有.*才能"
)


def extract_experience_from_executor_result(
    summary: str,
    updated_plan_json: str,
    status: str,
) -> list[str]:
    """从 Executor 结果中提取结构化经验节点。

    与 extract_knowledge_from_executor_result 不同，此函数输出
    格式化的经验四元组（情境/行动/结果/教训/适用），用于元认知。

    Args:
        summary: Executor 返回的 summary 文本。
        updated_plan_json: Executor 返回的 updated_plan_json 字符串。
        status: Executor 状态（"completed"/"failed"/"paused"）。

    Returns:
        格式化的经验文本列表。每个元素是一个完整的经验节点内容。
    """  # noqa: D415
    # 收集素材
    goal = ""
    step_intents: list[str] = []
    step_failures: list[str] = []
    step_results: list[str] = []

    if updated_plan_json and updated_plan_json.strip():
        try:
            plan = json.loads(updated_plan_json)
        except (json.JSONDecodeError, TypeError):
            plan = {}
        goal = plan.get("goal", "")
        for step in plan.get("steps", []):
            intent = step.get("intent", "")
            step_intents.append(intent)
            fr = step.get("failure_reason", "")
            if fr and fr.strip():
                step_failures.append(f"步骤「{intent}」失败：{fr.strip()}")
            rs = step.get("result_summary", "")
            if rs and rs.strip():
                step_results.append(f"步骤「{intent}」：{rs.strip()}")

    # 判断是否值得提取经验
    if status == "failed":
        # 失败任务只在有具体失败原因时提取
        if not step_failures and (not summary or len(summary.strip()) < 20):
            return []
        # 过滤掉测试/框架级别的失败（非项目知识）
        combined = f"{summary} {' '.join(step_failures)}"
        _FRAMEWORK_ERRORS = re.compile(
            r"(mock|MagicMock|TypeError|await|import\s+error|module\s+not\s+found)",
            re.IGNORECASE,
        )
        if _FRAMEWORK_ERRORS.search(combined) and not step_failures:
            return []
    elif status == "completed":
        # 完成任务只有含有发现性内容时才提取
        combined = f"{summary} {' '.join(step_results)}"
        if not _DISCOVERY_PATTERNS.search(combined):
            return []
        if len(combined.strip()) < 50:
            return []
    else:
        # paused 等其他状态不提取经验
        return []

    # 信息密度检查：goal + intents 都太短时，经验无实际价值
    context = goal if goal else ""
    actions = "；".join(step_intents) if step_intents else ""
    if len(context) < 5 and len(actions) < 10:
        return []

    # 构造情境
    context = context or "（无明确目标）"
    actions = actions or "（执行了任务）"

    # 构造结果和教训
    if status == "failed":
        outcome = "失败"
        lessons = "；".join(step_failures) if step_failures else summary
        lesson_text = f"避免{lessons}" if lessons else "需要进一步分析失败原因"
    else:
        outcome = "成功"
        lesson_text = summary if summary else "。".join(step_results)

    # 适用范围
    applicable = goal if goal else "；".join(step_intents[:2])

    experience = (
        f"[经验] {context[:30]}\n"
        f"情境：{context}\n"
        f"行动：{actions}\n"
        f"结果：{outcome} — {summary[:100] if summary else '见步骤详情'}\n"
        f"教训：{lesson_text}\n"
        f"适用：涉及「{applicable}」类型的任务"
    )

    return [experience]
