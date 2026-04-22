"""防震荡测试。"""

from src.common.knowledge_tree.optimization.anti_oscillation import (
    OptimizationHistory,
    filter_signals_by_quota,
)
from src.common.knowledge_tree.optimization.signals import OptimizationSignal


class TestOptimizationHistory:
    def test_record_and_count(self):
        h = OptimizationHistory(window=3600, max_per_window=5)
        assert h.count_in_window() == 0
        h.record()
        assert h.count_in_window() == 1

    def test_can_execute(self):
        h = OptimizationHistory(window=3600, max_per_window=2)
        assert h.can_execute() is True
        h.record()
        assert h.can_execute() is True
        h.record()
        assert h.can_execute() is False

    def test_window_expiry(self):
        h = OptimizationHistory(window=0, max_per_window=1)
        h.record()
        # window=0 → 时间戳立即过期


class TestFilterSignalsByQuota:
    def _signal(self, priority: int, signal_type: str = "rag_false_positive") -> OptimizationSignal:
        return OptimizationSignal(
            signal_type=signal_type,
            node_id="dev/a.md",
            evidence={},
            priority=priority,
        )

    def test_within_quota(self):
        history = OptimizationHistory(window=3600, max_per_window=5)
        signals = [self._signal(1), self._signal(2)]
        result = filter_signals_by_quota(signals, history)
        assert len(result) == 2

    def test_exceeds_quota(self):
        history = OptimizationHistory(window=3600, max_per_window=1)
        history.record()
        signals = [self._signal(1)]
        result = filter_signals_by_quota(signals, history)
        assert len(result) == 0

    def test_priority_ordering(self):
        history = OptimizationHistory(window=3600, max_per_window=2)
        signals = [
            self._signal(3, "content_insufficient"),
            self._signal(1, "total_failure"),
            self._signal(2, "rag_false_positive"),
        ]
        result = filter_signals_by_quota(signals, history)
        assert len(result) == 2
        assert result[0].signal_type == "total_failure"
        assert result[1].signal_type == "rag_false_positive"
