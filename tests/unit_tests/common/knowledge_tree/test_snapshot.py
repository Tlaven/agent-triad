"""Tests for KT status snapshot generation."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

from src.common.knowledge_tree.snapshot import generate_kt_snapshot


class TestSnapshotGeneration:
    """验证 KT 状态快照生成。"""

    def test_snapshot_structure(self):
        """快照应包含三个区块。"""
        kt = MagicMock()
        kt.get_node_count.return_value = 48
        kt.get_directory_count.return_value = 25
        kt.get_meta_rules.return_value = []
        result = generate_kt_snapshot(
            kt,
            task_summary="测试任务",
            auto_retrieve_hits=2,
            retrieved_nodes=["a.md", "b.md"],
            agent_used_kt=True,
            confidence_level="sufficient",
            manual_retrieve_count=0,
            manual_ingest_count=0,
            auto_ingest_count=1,
            ingested_nodes=["exp.md"],
            ingest_triggers=["executor_result_failed"],
            experience_node_count=3,
            avg_retrieval_score=0.52,
        )
        assert "kt_influence" in result
        assert "kt_mutations" in result
        assert "kt_health" in result

    def test_snapshot_is_valid_json(self):
        """快照应该是可序列化的 JSON。"""
        kt = MagicMock()
        kt.get_node_count.return_value = 10
        kt.get_directory_count.return_value = 5
        kt.get_meta_rules.return_value = []
        result = generate_kt_snapshot(
            kt, task_summary="test", auto_retrieve_hits=0,
            retrieved_nodes=[], agent_used_kt=False, confidence_level="none",
            manual_retrieve_count=0, manual_ingest_count=0,
            auto_ingest_count=0, ingested_nodes=[], ingest_triggers=[],
            experience_node_count=0, avg_retrieval_score=0.0,
        )
        json_str = json.dumps(result, ensure_ascii=False)
        parsed = json.loads(json_str)
        assert parsed["kt_health"]["total_nodes"] == 10

    def test_snapshot_includes_timestamp(self):
        """快照应包含时间戳。"""
        kt = MagicMock()
        kt.get_node_count.return_value = 0
        kt.get_directory_count.return_value = 0
        kt.get_meta_rules.return_value = []
        result = generate_kt_snapshot(
            kt, task_summary="test", auto_retrieve_hits=0,
            retrieved_nodes=[], agent_used_kt=False, confidence_level="none",
            manual_retrieve_count=0, manual_ingest_count=0,
            auto_ingest_count=0, ingested_nodes=[], ingest_triggers=[],
            experience_node_count=0, avg_retrieval_score=0.0,
        )
        assert "timestamp" in result

    def test_write_snapshot_to_file(self, tmp_path):
        """快照应能写入文件。"""
        from src.common.knowledge_tree.snapshot import write_snapshot

        kt = MagicMock()
        kt.get_node_count.return_value = 5
        kt.get_directory_count.return_value = 2
        kt.get_meta_rules.return_value = []
        snapshot = generate_kt_snapshot(
            kt, task_summary="test", auto_retrieve_hits=1,
            retrieved_nodes=["a.md"], agent_used_kt=True, confidence_level="sufficient",
            manual_retrieve_count=0, manual_ingest_count=0,
            auto_ingest_count=0, ingested_nodes=[], ingest_triggers=[],
            experience_node_count=0, avg_retrieval_score=0.5,
        )
        log_file = tmp_path / "kt_snapshot.jsonl"
        write_snapshot(snapshot, log_file)
        assert log_file.exists()
        line = log_file.read_text(encoding="utf-8").strip()
        parsed = json.loads(line)
        assert parsed["kt_health"]["total_nodes"] == 5
