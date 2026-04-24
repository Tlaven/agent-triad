"""优化信号检测测试。"""

from src.common.knowledge_tree.optimization.signals import (
    SIGNAL_PRIORITY,
    OptimizationSignal,
    detect_signals,
)
from src.common.knowledge_tree.retrieval.log import RetrievalLog


def _log(query: str, rag_results=None, satisfaction=None) -> RetrievalLog:
    """创建测试用 RetrievalLog。"""
    log = RetrievalLog.create(query)
    log.rag_results = rag_results or []
    log.agent_satisfaction = satisfaction
    return log


class TestDetectSignals:
    def test_no_signals_on_empty_logs(self):
        assert detect_signals([]) == []

    def test_no_signals_when_satisfied(self):
        logs = [
            _log("query", [("a.md", 0.8)], satisfaction=True),
            _log("query2", [("b.md", 0.9)], satisfaction=True),
        ]
        assert detect_signals(logs) == []

    def test_total_failure_signal(self):
        logs = [_log(f"query{i}", []) for i in range(5)]
        signals = detect_signals(logs, total_failure_threshold=3)
        assert len(signals) == 1
        assert signals[0].signal_type == "total_failure"
        assert signals[0].priority == SIGNAL_PRIORITY["total_failure"]
        assert signals[0].evidence["count"] == 5

    def test_total_failure_below_threshold(self):
        logs = [_log("q", []) for _ in range(2)]
        signals = detect_signals(logs, total_failure_threshold=3)
        assert len(signals) == 0

    def test_rag_false_positive_signal(self):
        logs = [
            _log("q", [("a.md", 0.8)], satisfaction=False),
            _log("q", [("b.md", 0.9)], satisfaction=False),
            _log("q", [("c.md", 0.7)], satisfaction=False),
        ]
        signals = detect_signals(logs, rag_false_positive_threshold=3)
        assert len(signals) == 1
        assert signals[0].signal_type == "rag_false_positive"

    def test_content_insufficient_signal(self):
        logs = [
            _log("q", [("a.md", 0.8)], satisfaction=False),
        ] * 6  # 6 logs all pointing to a.md and unsatisfied
        signals = detect_signals(logs, content_insufficient_threshold=5)
        types = [s.signal_type for s in signals]
        assert "content_insufficient" in types
        ci = next(s for s in signals if s.signal_type == "content_insufficient")
        assert ci.node_id == "a.md"

    def test_mixed_signals(self):
        """多种信号同时出现。"""
        logs = (
            [_log(f"empty{i}", []) for i in range(4)]  # total_failure
            + [_log("bad", [("x.md", 0.7)], satisfaction=False)] * 4  # false positive
        )
        signals = detect_signals(
            logs,
            total_failure_threshold=3,
            rag_false_positive_threshold=3,
        )
        types = {s.signal_type for s in signals}
        assert "total_failure" in types
        assert "rag_false_positive" in types

    def test_signal_auto_timestamp(self):
        signal = OptimizationSignal(
            signal_type="total_failure",
            node_id=None,
            evidence={},
            priority=1,
        )
        assert signal.detected_at != ""
        assert "T" in signal.detected_at  # ISO format
