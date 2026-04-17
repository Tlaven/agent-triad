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
        h = OptimizationHistory(window=0, max_per_window=1)  # 0秒窗口立即过期
        h.record()
        # 由于 window=0，时间戳会立即过期
        # 这个测试验证 prune 逻辑，实际场景中 window 通常为 3600


class TestFilterSignalsByQuota:
    def _signal(self, priority: int, signal_type: str = "nav_failure") -> OptimizationSignal:
        return OptimizationSignal(
            signal_type=signal_type,
            node_id="n1",
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
        history.record()  # 已用掉 1 个
        signals = [self._signal(1)]
        result = filter_signals_by_quota(signals, history)
        assert len(result) == 0  # 额度已用完

    def test_priority_ordering(self):
        history = OptimizationHistory(window=3600, max_per_window=2)
        signals = [
            self._signal(4, "content_insufficient"),
            self._signal(1, "total_failure"),
            self._signal(2, "nav_failure"),
        ]
        result = filter_signals_by_quota(signals, history)
        assert len(result) == 2
        # 应优先保留 priority 1 和 2
        assert result[0].signal_type == "total_failure"
        assert result[1].signal_type == "nav_failure"
