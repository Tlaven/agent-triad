"""优化信号数据模型与检测。"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from src.common.knowledge_tree.retrieval.log import RetrievalLog


@dataclass
class OptimizationSignal:
    """优化信号（决策 23）。"""

    signal_type: str  # "nav_failure" | "rag_false_positive" | "total_failure" | "content_insufficient"
    node_id: str | None
    evidence: dict[str, Any]
    priority: int  # 1-4, 1 最高
    detected_at: str = ""

    def __post_init__(self):
        if not self.detected_at:
            self.detected_at = datetime.now(timezone.utc).isoformat()


# 优先级映射
SIGNAL_PRIORITY: dict[str, int] = {
    "total_failure": 1,
    "nav_failure": 2,
    "rag_false_positive": 3,
    "content_insufficient": 4,
}


def detect_signals(
    logs: list[RetrievalLog],
    nav_failure_threshold: int = 5,
    rag_false_positive_threshold: int = 3,
    total_failure_threshold: int = 3,
    content_insufficient_threshold: int = 5,
) -> list[OptimizationSignal]:
    """从检索日志中检测优化信号。

    Args:
        logs: 累积的检索日志。
        *_threshold: 各信号类型的触发阈值。

    Returns:
        检测到的优化信号列表（可能为空）。
    """
    signals: list[OptimizationSignal] = []

    # 按节点统计导航失败
    nav_fail_count: dict[str, int] = defaultdict(int)
    nav_fail_queries: dict[str, list[str]] = defaultdict(list)

    # RAG 假阳性
    rag_false_positive_count: int = 0
    rag_false_positive_nodes: list[str] = []

    # 整体失败
    total_failure_count: int = 0
    total_failure_queries: list[str] = []

    # 内容不足
    content_insufficient_count: dict[str, int] = defaultdict(int)

    for log in logs:
        # 整体失败
        if log.fusion_mode == "none":
            total_failure_count += 1
            total_failure_queries.append(log.query_text)

        # 导航失败
        if log.tree_success is False and len(log.tree_path) > 0:
            last_node = log.tree_path[-1]
            nav_fail_count[last_node] += 1
            nav_fail_queries[last_node].append(log.query_text)

        # RAG 假阳性（Agent 明确标注不满意）
        if log.rag_triggered and log.agent_satisfaction is False:
            rag_false_positive_count += 1
            for node_id, _ in log.rag_results:
                rag_false_positive_nodes.append(node_id)

        # 内容不足（tree+rag 模式或 tree 成功但不满意）
        if log.fusion_mode == "tree+rag" or (
            log.fusion_mode == "tree" and log.agent_satisfaction is False
        ):
            for nid in log.final_node_ids:
                content_insufficient_count[nid] += 1

    # 生成信号
    if total_failure_count >= total_failure_threshold:
        signals.append(OptimizationSignal(
            signal_type="total_failure",
            node_id=None,
            evidence={
                "count": total_failure_count,
                "sample_queries": total_failure_queries[:5],
            },
            priority=SIGNAL_PRIORITY["total_failure"],
        ))

    for node_id, count in nav_fail_count.items():
        if count >= nav_failure_threshold:
            signals.append(OptimizationSignal(
                signal_type="nav_failure",
                node_id=node_id,
                evidence={
                    "count": count,
                    "sample_queries": nav_fail_queries[node_id][:5],
                },
                priority=SIGNAL_PRIORITY["nav_failure"],
            ))

    if rag_false_positive_count >= rag_false_positive_threshold:
        signals.append(OptimizationSignal(
            signal_type="rag_false_positive",
            node_id=None,
            evidence={
                "count": rag_false_positive_count,
                "affected_nodes": list(set(rag_false_positive_nodes)),
            },
            priority=SIGNAL_PRIORITY["rag_false_positive"],
        ))

    for node_id, count in content_insufficient_count.items():
        if count >= content_insufficient_threshold:
            signals.append(OptimizationSignal(
                signal_type="content_insufficient",
                node_id=node_id,
                evidence={"count": count},
                priority=SIGNAL_PRIORITY["content_insufficient"],
            ))

    return signals
