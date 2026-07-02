"""N2 反向验证：_wait_for_executor_result 在隔离环境下应按时返回。

背景（2026-07-01 探测报告 N2）：
  S001-t2 真 mode3 卡死在 _wait_for_executor_result，16+ min 仍未命中 timeout 分支。
  报告自己已指出"嫌疑"——可能 hang 在上层 dev server 热重载，而非本函数。

本测试用 mock 隔离掉 dev server / 真实子进程，仅测 _wait_for_executor_result 自身：
  - wait loop 内 timeout 是否真能 fire（line 678-692 兜底分支）
  - probe 命中 unreachable/not_found 后是否立即返回（line 606-625 分支）

判读：
  - PASS → 本函数 timeout/分支逻辑正常；生产 hang 在更上层（dev server 热重载 / API 层）
  - FAIL（超出 hard timeout） → 本函数内部有 hang 点，需要继续定位（候选：
    _cleanup_dead_executor → _stop_handle 的 await handle.process.wait()）

所有测试用 asyncio.wait_for(..., timeout=N) 包一层 hard safety，避免单测自身 hang。
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.common.context import Context
from src.common.mailbox import Mailbox
from src.supervisor_agent.tools import _wait_for_executor_result


def _make_ctx() -> Context:
    return Context(executor_host="127.0.0.1", executor_port=9999)


def _empty_mailbox_stub() -> MagicMock:
    """Mailbox stub: get_completion 永远返回 None（模拟 executor 死了未 push 结果）。"""
    mb = MagicMock(spec=Mailbox)
    mb.get_completion = AsyncMock(return_value=None)
    return mb


# ---------------------------------------------------------------------------
# Test 1: probe='running' + mailbox 空 → wait loop 内 timeout 必须按时 fire
# ---------------------------------------------------------------------------


class TestWaitLoopTimeout:
    async def test_running_task_times_out_within_hard_timeout(self) -> None:
        plan_id = "plan_timeout_n2"
        mb = _empty_mailbox_stub()
        ctx = _make_ctx()

        with patch("src.common.mailbox.get_mailbox", return_value=mb), \
             patch(
                 "src.supervisor_agent.tools._probe_executor_task",
                 AsyncMock(return_value="running"),
             ), \
             patch(
                 "src.supervisor_agent.tools._cleanup_dead_executor",
                 AsyncMock(),
             ):
            loop = asyncio.get_event_loop()
            start = loop.time()
            # hard safety: 10s 上限，防止 wait loop 内 timeout 失效导致测试 hang
            result = await asyncio.wait_for(
                _wait_for_executor_result(plan_id, "{}", ctx, timeout=2.0),
                timeout=10.0,
            )
            elapsed = loop.time() - start

        # 期望 ~2s 触发 timeout；给缓冲到 4s（hard safety 10s 内）
        assert elapsed < 4.0, (
            f"expected timeout in ~2s (<4s), got {elapsed:.1f}s — "
            "wait loop 内的 timeout 分支未 fire，复现 N2 hang"
        )
        assert "[EXECUTOR_RESULT]" in result, (
            f"expected [EXECUTOR_RESULT] marker in result: {result[:300]}"
        )
        assert "超时" in result, (
            f"expected '超时' in failure detail: {result[:300]}"
        )


# ---------------------------------------------------------------------------
# Test 2: probe='unreachable' → 预检 2 命中后立即返回，不进入 wait loop
# ---------------------------------------------------------------------------


class TestProbeUnreachable:
    async def test_unreachable_returns_immediately(self) -> None:
        plan_id = "plan_unreachable_n2"
        mb = _empty_mailbox_stub()
        ctx = _make_ctx()

        cleanup_mock = AsyncMock()

        with patch("src.common.mailbox.get_mailbox", return_value=mb), \
             patch(
                 "src.supervisor_agent.tools._probe_executor_task",
                 AsyncMock(return_value="unreachable"),
             ), \
             patch(
                 "src.supervisor_agent.tools._cleanup_dead_executor",
                 cleanup_mock,
             ):
            loop = asyncio.get_event_loop()
            start = loop.time()
            result = await asyncio.wait_for(
                _wait_for_executor_result(plan_id, "{}", ctx, timeout=2.0),
                timeout=10.0,
            )
            elapsed = loop.time() - start

        # 预检 2 命中应立即返回，不应进入 2s wait loop
        assert elapsed < 1.5, (
            f"expected fast return (<1.5s), got {elapsed:.1f}s — "
            "unreachable 分支应跳过 wait loop 立即返回"
        )
        assert "[EXECUTOR_RESULT]" in result
        assert "不可达" in result or "unreachable" in result.lower(), (
            f"expected 不可达/unreachable detail: {result[:300]}"
        )
        # 清理路径应被调用
        cleanup_mock.assert_awaited_once_with(plan_id, ctx)

    async def test_not_found_returns_immediately(self) -> None:
        plan_id = "plan_not_found_n2"
        mb = _empty_mailbox_stub()
        ctx = _make_ctx()

        with patch("src.common.mailbox.get_mailbox", return_value=mb), \
             patch(
                 "src.supervisor_agent.tools._probe_executor_task",
                 AsyncMock(return_value="not_found"),
             ), \
             patch(
                 "src.supervisor_agent.tools._cleanup_dead_executor",
                 AsyncMock(),
             ):
            loop = asyncio.get_event_loop()
            start = loop.time()
            result = await asyncio.wait_for(
                _wait_for_executor_result(plan_id, "{}", ctx, timeout=2.0),
                timeout=10.0,
            )
            elapsed = loop.time() - start

        assert elapsed < 1.5
        assert "[EXECUTOR_RESULT]" in result
        assert "找不到" in result or "not_found" in result.lower(), (
            f"expected 找不到/not_found detail: {result[:300]}"
        )


# ---------------------------------------------------------------------------
# Test 3: 预检 1 命中 mailbox（结果已到达） → 立即返回，不进入 probe
# ---------------------------------------------------------------------------


class TestPrefetchMailboxHit:
    async def test_mailbox_hit_skips_probe(self) -> None:
        plan_id = "plan_mailbox_hit_n2"
        ctx = _make_ctx()

        completion_payload = {
            "status": "completed",
            "summary": "done",
            "updated_plan_json": "",
            "snapshot_json": "",
            "plan_id": plan_id,
        }

        mb = MagicMock(spec=Mailbox)
        mb_item = MagicMock()
        mb_item.payload = completion_payload
        mb.get_completion = AsyncMock(return_value=mb_item)

        probe_mock = AsyncMock(return_value="running")  # 不应被调用

        with patch("src.common.mailbox.get_mailbox", return_value=mb), \
             patch(
                 "src.supervisor_agent.tools._probe_executor_task",
                 probe_mock,
             ):
            loop = asyncio.get_event_loop()
            start = loop.time()
            result = await asyncio.wait_for(
                _wait_for_executor_result(plan_id, "{}", ctx, timeout=2.0),
                timeout=10.0,
            )
            elapsed = loop.time() - start

        assert elapsed < 1.0
        assert "[EXECUTOR_RESULT]" in result
        assert "completed" in result
        # 预检 1 命中后 probe 不应被调用
        probe_mock.assert_not_awaited()
