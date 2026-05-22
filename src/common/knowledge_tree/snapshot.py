"""KT 状态快照：面向人类开发者的可观测性报告。"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def generate_kt_snapshot(
    kt: Any,
    task_summary: str,
    auto_retrieve_hits: int,
    retrieved_nodes: list[str],
    agent_used_kt: bool,
    confidence_level: str,
    manual_retrieve_count: int,
    manual_ingest_count: int,
    auto_ingest_count: int,
    ingested_nodes: list[str],
    ingest_triggers: list[str],
    experience_node_count: int,
    avg_retrieval_score: float,
) -> dict:
    """生成 KT 状态快照。

    Args:
        kt: KnowledgeTree 实例。
        task_summary: 任务摘要。
        auto_retrieve_hits: 自动检索命中数。
        retrieved_nodes: 检索到的节点列表。
        agent_used_kt: Agent 是否使用了 KT 内容。
        confidence_level: 置信度级别。
        manual_retrieve_count: 主动检索次数。
        manual_ingest_count: 主动摄入次数。
        auto_ingest_count: 自动摄入次数。
        ingested_nodes: 摄入的节点列表。
        ingest_triggers: 摄入触发类型列表。
        experience_node_count: 经验节点数。
        avg_retrieval_score: 平均检索分数。

    Returns:
        可 JSON 序列化的快照字典。
    """
    total_nodes = kt.get_node_count() if hasattr(kt, "get_node_count") else 0
    total_dirs = kt.get_directory_count() if hasattr(kt, "get_directory_count") else 0

    return {
        "timestamp": datetime.now(UTC).isoformat(),
        "task_summary": task_summary[:100],
        "kt_influence": {
            "auto_retrieve_hits": auto_retrieve_hits,
            "retrieved_nodes": retrieved_nodes[:5],
            "agent_used_kt": agent_used_kt,
            "confidence_level": confidence_level,
            "manual_retrieve_count": manual_retrieve_count,
            "manual_ingest_count": manual_ingest_count,
        },
        "kt_mutations": {
            "auto_ingest_count": auto_ingest_count,
            "ingested_nodes": ingested_nodes[:5],
            "ingest_triggers": ingest_triggers,
            "meta_rules_active": len(kt.get_meta_rules()) if hasattr(kt, "get_meta_rules") else 0,
        },
        "kt_health": {
            "total_nodes": total_nodes,
            "total_directories": total_dirs,
            "experience_nodes": experience_node_count,
            "avg_retrieval_score": round(avg_retrieval_score, 2),
        },
    }


def write_snapshot(snapshot: dict, log_file: Path) -> None:
    """将快照追加写入 JSONL 文件。"""
    try:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(snapshot, ensure_ascii=False) + "\n")
    except Exception as e:
        logger.warning("Failed to write KT snapshot: %s", e)
