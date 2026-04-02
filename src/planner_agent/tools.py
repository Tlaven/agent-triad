"""Planner 辅助工具（V1 默认未接入 graph）。

注意：当前架构中 Planner 产出的是“意图层计划”，本文件仅提供可选辅助，
不应引入具体执行工具名/命令名/API 名。
"""

import json
from typing import Any

from langchain_core.tools import tool


@tool
async def analyze_task_complexity(task_core: str) -> str:
    """分析任务复杂度，给出规划粒度建议。"""
    text = (task_core or "").strip()
    score = 0.4
    if len(text) > 120:
        score = 0.6
    if len(text) > 300:
        score = 0.75
    if any(k in text.lower() for k in ("multi-step", "依赖", "重规划", "pipeline")):
        score = max(score, 0.8)
    result = {
        "complexity_score": score,
        "recommended_mode": "plan_then_execute" if score >= 0.6 else "direct_execute",
        "suggested_step_count": 3 if score < 0.6 else 5,
    }
    return json.dumps(result, ensure_ascii=False, indent=2)


@tool
async def decompose_task(task_core: str) -> str:
    """将任务拆解为意图层步骤草案（不含工具信息）。"""
    text = (task_core or "").strip() or "未提供 task_core"
    plan_draft = {
        "goal": text,
        "steps": [
            {
                "step_id": "step_1",
                "intent": "澄清输入约束与成功标准",
                "expected_output": "形成可执行的验收口径",
                "status": "pending",
                "result_summary": None,
                "failure_reason": None,
            },
            {
                "step_id": "step_2",
                "intent": "完成核心任务产出",
                "expected_output": "给出满足约束的主要结果",
                "status": "pending",
                "result_summary": None,
                "failure_reason": None,
            },
            {
                "step_id": "step_3",
                "intent": "校验产出并补充交付说明",
                "expected_output": "产出可验证结论与后续建议",
                "status": "pending",
                "result_summary": None,
                "failure_reason": None,
            },
        ],
    }
    return json.dumps(plan_draft, ensure_ascii=False, indent=2)


@tool
async def validate_plan(plan_json: str) -> str:
    """校验意图层计划的结构完整性。"""
    issues: list[str] = []
    try:
        plan = json.loads(plan_json)
    except json.JSONDecodeError:
        return json.dumps(
            {
                "is_valid": False,
                "issues": ["JSON 格式错误"],
                "suggestions": ["确保返回单个合法 JSON 对象"],
            },
            ensure_ascii=False,
            indent=2,
        )

    if not isinstance(plan, dict):
        issues.append("顶层必须是 JSON 对象")
    else:
        for key in ("goal", "steps"):
            if key not in plan:
                issues.append(f"缺少字段: {key}")
        steps = plan.get("steps")
        if not isinstance(steps, list) or len(steps) == 0:
            issues.append("steps 必须是非空数组")
        else:
            required_step_keys = {
                "step_id",
                "intent",
                "expected_output",
                "status",
                "result_summary",
                "failure_reason",
            }
            for idx, step in enumerate(steps, start=1):
                if not isinstance(step, dict):
                    issues.append(f"step_{idx} 必须是对象")
                    continue
                missing = sorted(required_step_keys - set(step.keys()))
                if missing:
                    issues.append(f"step_{idx} 缺少字段: {', '.join(missing)}")

    return json.dumps(
        {
            "is_valid": len(issues) == 0,
            "issues": issues,
            "suggestions": ["补齐缺失字段并保持意图层描述（不含工具名）"] if issues else [],
        },
        ensure_ascii=False,
        indent=2,
    )


@tool
async def generate_plan_template(_: str = "general") -> str:
    """返回意图层 Plan 模板（系统字段 plan_id/version 由上层托管）。"""
    template: dict[str, Any] = {
        "goal": "任务总体目标",
        "steps": [
            {
                "step_id": "step_1",
                "intent": "步骤意图",
                "expected_output": "可验证完成标准",
                "status": "pending",
                "result_summary": None,
                "failure_reason": None,
            }
        ],
        "overall_expected_output": "任务最终产出定义",
    }
    return json.dumps(template, ensure_ascii=False, indent=2)


def get_planner_tools():
    """获取规划辅助工具集合（当前默认不绑定到 Planner graph）。"""
    return [analyze_task_complexity, decompose_task, validate_plan, generate_plan_template]