"""异步批量优化器。"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from src.common.knowledge_tree.optimization.anti_oscillation import (
    OptimizationHistory,
    filter_signals_by_quota,
)
from src.common.knowledge_tree.optimization.signals import (
    OptimizationSignal,
    detect_signals,
)
from src.common.knowledge_tree.retrieval.log import RetrievalLog

logger = logging.getLogger(__name__)


@dataclass
class OptimizationReport:
    """一轮优化的执行报告。"""

    signals_detected: int = 0
    signals_filtered: int = 0
    actions_executed: int = 0
    actions: list[dict[str, Any]] = field(default_factory=list)


def run_optimization_cycle(
    logs: list[RetrievalLog],
    history: OptimizationHistory,
    nav_failure_threshold: int = 5,
    rag_false_positive_threshold: int = 3,
    total_failure_threshold: int = 3,
    content_insufficient_threshold: int = 5,
) -> OptimizationReport:
    """执行一轮优化。

    流程：
    1. 从检索日志检测信号
    2. 防震荡过滤
    3. 对每个信号记录优化动作（P1 为日志记录，P2+ 将触发实际优化）

    P1 阶段仅记录信号和动作意图，不执行实际树结构修改。
    实际优化由后续阶段通过日志分析手动或自动触发。

    Returns:
        OptimizationReport 包含本轮优化的统计和动作记录。
    """
    report = OptimizationReport()

    # 1. 信号检测
    signals = detect_signals(
        logs,
        nav_failure_threshold=nav_failure_threshold,
        rag_false_positive_threshold=rag_false_positive_threshold,
        total_failure_threshold=total_failure_threshold,
        content_insufficient_threshold=content_insufficient_threshold,
    )
    report.signals_detected = len(signals)

    if not signals:
        return report

    # 2. 防震荡过滤
    filtered = filter_signals_by_quota(signals, history)
    report.signals_filtered = len(signals) - len(filtered)

    if not filtered:
        logger.info("All signals filtered by anti-oscillation quota")
        return report

    # 3. 为每个信号生成优化动作（P1: 仅记录意图）
    for signal in filtered:
        action = _plan_action(signal)
        report.actions.append(action)
        history.record()
        report.actions_executed += 1
        logger.info(
            "Optimization action planned: %s for signal %s (node=%s)",
            action["action"],
            signal.signal_type,
            signal.node_id,
        )

    return report


def _plan_action(signal: OptimizationSignal) -> dict[str, Any]:
    """根据信号类型规划优化动作（P1: 仅返回意图）。"""
    action_map = {
        "total_failure": "create_node",
        "nav_failure": "split_or_rewrite_summary",
        "rag_false_positive": "adjust_vector_weights",
        "content_insufficient": "update_content",
    }

    return {
        "action": action_map.get(signal.signal_type, "unknown"),
        "signal_type": signal.signal_type,
        "node_id": signal.node_id,
        "evidence": signal.evidence,
        "status": "planned",  # P1: planned; P2+: executed
    }
