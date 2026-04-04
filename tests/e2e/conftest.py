"""E2E 专用 pytest 钩子：为真实 LLM 用例设置可配置的单测超时。

环境变量：
  E2E_TEST_TIMEOUT
    每个 live_llm 用例的最大运行时间（秒）。默认 600（10 分钟）。
    设为 0 表示不启用 pytest-timeout（调试用；若某用例卡死会无限等待）。

说明：图内还有 Context.max_executor_iterations 等限制；本超时是「整段测试」的兜底，
防止网络/SDK 异常导致 pytest 长时间无响应。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

_E2E_DIR = Path(__file__).resolve().parent
_DEFAULT_TIMEOUT_S = 600


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    raw = os.getenv("E2E_TEST_TIMEOUT", str(_DEFAULT_TIMEOUT_S)).strip()
    if raw == "0":
        return
    try:
        seconds = int(raw)
    except ValueError:
        seconds = _DEFAULT_TIMEOUT_S
    if seconds <= 0:
        return

    marker = pytest.mark.timeout(seconds)
    for item in items:
        if item.get_closest_marker("live_llm") is None:
            continue
        item_path = Path(item.path) if hasattr(item, "path") else Path(item.fspath)
        try:
            item_path.resolve().relative_to(_E2E_DIR)
        except ValueError:
            continue
        item.add_marker(marker)
