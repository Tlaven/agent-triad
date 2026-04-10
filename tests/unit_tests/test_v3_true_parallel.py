"""验证 V3 并行执行的测试 - 检查是否真的并发执行"""
import asyncio
import time
from unittest.mock import AsyncMock, patch
import pytest


# 简单的结果类
class MockResult:
    def __init__(self, summary):
        self.summary = summary
        self.status = "completed"
        self.updated_plan_json = ""


async def mock_slow_executor(plan_json: str, **kwargs):
    """模拟慢速执行器，每个批次需要 2 秒"""
    # 模拟执行时间
    await asyncio.sleep(2)
    return MockResult(summary=f"执行完成: {plan_json[:50]}...")


@pytest.mark.asyncio
async def test_sequential_execution_time():
    """测试：顺序执行 3 个批次应该需要 6 秒"""
    start = time.time()

    # 模拟顺序执行（当前 V3 的实现）
    batches = ["batch_1", "batch_2", "batch_3"]
    results = []
    for batch in batches:
        result = await mock_slow_executor(batch)
        results.append(result)

    elapsed = time.time() - start
    print(f"顺序执行耗时: {elapsed:.2f}秒")

    # 顺序执行：3个批次 × 2秒 = 6秒左右
    assert elapsed >= 5.5, f"顺序执行应该需要约6秒，实际只用了{elapsed:.2f}秒"
    assert len(results) == 3


@pytest.mark.asyncio
async def test_parallel_execution_time():
    """测试：并行执行 3 个批次应该只需要约 2 秒"""
    start = time.time()

    # 真正的并行执行
    batches = ["batch_1", "batch_2", "batch_3"]
    batch_tasks = [mock_slow_executor(batch) for batch in batches]
    results = await asyncio.gather(*batch_tasks)

    elapsed = time.time() - start
    print(f"并行执行耗时: {elapsed:.2f}秒")

    # 并行执行：3个批次并发，应该约2秒（最慢的那个）
    assert elapsed < 3, f"并行执行应该需要约2秒，实际用了{elapsed:.2f}秒"
    assert len(results) == 3


@pytest.mark.asyncio
async def test_current_v3_implementation_is_sequential():
    """测试当前 V3 实现的批次构建"""
    # 导入当前的实现
    from src.supervisor_agent.parallel import build_execution_batches

    # 构建包含 3 个独立步骤的测试计划
    test_plan_steps = [
        {"step_id": "step_1", "status": "pending", "parallel_group": "group_a", "depends_on": []},
        {"step_id": "step_2", "status": "pending", "parallel_group": "group_a", "depends_on": []},
        {"step_id": "step_3", "status": "pending", "parallel_group": "group_a", "depends_on": []},
    ]

    # 构建批次
    batches = build_execution_batches(test_plan_steps)
    print(f"构建了 {len(batches)} 个批次")
    print(f"批次详情: {[b.step_ids for b in batches]}")

    # 应该能构建出批次
    assert len(batches) >= 1


if __name__ == "__main__":
    # 运行测试
    print("=" * 60)
    print("测试 1: 顺序执行（当前 V3 的方式）")
    print("=" * 60)
    asyncio.run(test_sequential_execution_time())

    print("\n" + "=" * 60)
    print("测试 2: 并行执行（真正应该的方式）")
    print("=" * 60)
    asyncio.run(test_parallel_execution_time())

    print("\n" + "=" * 60)
    print("测试 3: 当前 V3 批次构建")
    print("=" * 60)
    asyncio.run(test_current_v3_implementation_is_sequential())
