"""防震荡：独立阈值 + 全局频率上限。"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field

from src.common.knowledge_tree.optimization.signals import OptimizationSignal


@dataclass
class OptimizationHistory:
    """优化执行历史，用于频率控制。"""

    # 最近优化动作的时间戳队列
    recent_timestamps: deque[float] = field(default_factory=lambda: deque())
    # 时间窗口（秒）
    window: int = 3600
    # 每个窗口最大优化次数
    max_per_window: int = 10

    def record(self) -> None:
        """记录一次优化执行。"""
        self.recent_timestamps.append(time.monotonic())
        self._prune()

    def count_in_window(self) -> int:
        """当前窗口内的优化次数。"""
        self._prune()
        return len(self.recent_timestamps)

    def can_execute(self) -> bool:
        """是否还可以执行优化。"""
        return self.count_in_window() < self.max_per_window

    def _prune(self) -> None:
        """清除窗口外的旧记录。"""
        cutoff = time.monotonic() - self.window
        while self.recent_timestamps and self.recent_timestamps[0] < cutoff:
            self.recent_timestamps.popleft()


def filter_signals_by_quota(
    signals: list[OptimizationSignal],
    history: OptimizationHistory,
) -> list[OptimizationSignal]:
    """根据全局频率上限过滤信号。

    按优先级排序后，在限额内尽可能多地保留信号。
    """
    # 按优先级排序
    sorted_signals = sorted(signals, key=lambda s: s.priority)

    remaining = history.max_per_window - history.count_in_window()
    if remaining <= 0:
        return []

    return sorted_signals[:remaining]
