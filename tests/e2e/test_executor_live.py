"""仅针对 Executor 的实机测试（不经 Supervisor）。

用于排查「Executor 输出无法解析 → last_executor_status=failed」等问题：

    uv run pytest tests/e2e/test_executor_live.py -m live_llm -v -s

依赖：`SILICONFLOW_API_KEY`（与 `Context.executor_model` 默认 SiliconFlow 一致）。

说明：与 `tests/integration/test_executor_graph.py`（Mock LLM）不同，本文件会**真实计费**。
"""

from __future__ import annotations

import json
import os

import pytest

from src.common.context import Context
from src.executor_agent.graph import run_executor

pytestmark = pytest.mark.live_llm


def _has_siliconflow() -> bool:
    return bool(os.getenv("SILICONFLOW_API_KEY"))


@pytest.mark.skipif(not _has_siliconflow(), reason="SILICONFLOW_API_KEY not set (Executor default model)")
async def test_executor_live_create_hello_txt(tmp_path, monkeypatch) -> None:
    """与 V1 scenario B 类似的短任务，只跑 `run_executor`。"""
    monkeypatch.setenv("AGENT_WORKSPACE_DIR", str(tmp_path))
    plan = {
        "plan_id": "plan_e2e_executor_only",
        "version": 1,
        "goal": "在当前工作区创建 hello.txt",
        "steps": [
            {
                "step_id": 1,
                "intent": "在当前工作区创建文件 hello.txt，内容为 Hello, World!（单行文本即可）",
                "expected_output": "hello.txt 存在且内容正确",
                "status": "pending",
                "result_summary": None,
                "failure_reason": None,
            }
        ],
    }
    plan_json = json.dumps(plan, ensure_ascii=False)
    ctx = Context(max_executor_iterations=15)

    result = await run_executor(plan_json, context=ctx)

    assert result.status == "completed", (
        f"期望 completed，实际 status={result.status!r}；"
        f"summary={result.summary!r}；updated_plan_json 前 200 字：{result.updated_plan_json[:200]!r}"
    )
    text = (tmp_path / "hello.txt").read_text(encoding="utf-8").strip()
    assert "Hello" in text and "World" in text, f"文件内容异常: {text!r}"
