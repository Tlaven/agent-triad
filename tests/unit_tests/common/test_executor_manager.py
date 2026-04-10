"""ExecutorManager 单元测试。"""

import asyncio
import json
import pytest

from src.common.executor_manager import (
    ExecutorManager,
    ExecutorTask,
    get_executor_manager,
    _timestamp,
)


class TestTimestamp:
    """测试时间戳生成。"""

    def test_timestamp_format(self):
        """测试时间戳格式。"""
        ts = _timestamp()
        assert isinstance(ts, str)
        assert len(ts) > 0
        # ISO 格式应该包含 'T'
        assert "T" in ts


class TestExecutorTask:
    """测试 ExecutorTask 数据结构。"""

    def test_task_creation(self):
        """测试任务创建。"""
        task = ExecutorTask(
            task_id="exec_123",
            plan_id="plan_456",
            plan_json='{"steps": []}',
            status="pending",
        )

        assert task.task_id == "exec_123"
        assert task.plan_id == "plan_456"
        assert task.status == "pending"
        assert task.result is None
        assert task.error is None
        assert task.created_at is not None
        assert task.updated_at is not None

    def test_task_with_result(self):
        """测试带结果的任务。"""
        result = {"status": "completed", "summary": "Done"}
        task = ExecutorTask(
            task_id="exec_124",
            plan_id="plan_457",
            plan_json='{"steps": []}',
            status="completed",
            result=result,
        )

        assert task.result == result
        assert task.status == "completed"


class TestExecutorManager:
    """测试 ExecutorManager 核心功能。"""

    @pytest.fixture
    def manager(self):
        """创建测试用的 ExecutorManager 实例。"""
        # 重置单例
        ExecutorManager._instance = None
        # 创建新实例并设置为单例（模拟首次调用 get_instance()）
        mgr = ExecutorManager()
        ExecutorManager._instance = mgr
        return mgr

    def test_singleton_pattern(self, manager):
        """测试单例模式。"""
        # fixture 创建的实例被设置为单例
        # 再次调用 get_instance() 应该返回同一个实例
        manager2 = ExecutorManager.get_instance()
        assert manager is manager2
        assert id(manager) == id(manager2)

    def test_get_executor_manager(self):
        """测试 get_executor_manager 函数。"""
        # 重置单例
        ExecutorManager._instance = None
        mgr = get_executor_manager()
        assert isinstance(mgr, ExecutorManager)

    @pytest.mark.asyncio
    async def test_start_executor_success(self, manager):
        """测试启动 Executor 任务。"""
        plan_json = json.dumps({
            "plan_id": "plan_test",
            "steps": [
                {"step_id": "step_1", "intent": "测试", "status": "pending"}
            ]
        })

        # Mock context
        from src.common.context import Context
        context = Context()

        task_id = await manager.start_executor(plan_json, context)

        # 验证返回值
        assert isinstance(task_id, str)
        assert task_id.startswith("exec_")
        assert len(task_id) >= 12  # "exec_" + at least 8 chars hex

        # 验证任务已注册
        assert task_id in manager.task_data
        assert task_id in manager.active_tasks

        task_data = manager.task_data[task_id]
        assert task_data.task_id == task_id
        assert task_data.plan_id == "plan_test"
        assert task_data.status in ("pending", "running")

    @pytest.mark.asyncio
    async def test_start_executor_invalid_plan(self, manager):
        """测试启动 Executor 时 plan_json 无效。"""
        from src.common.context import Context
        context = Context()

        with pytest.raises(ValueError, match="Invalid plan_json"):
            await manager.start_executor("not a json", context)

    @pytest.mark.asyncio
    async def test_get_task_status(self, manager):
        """测试查询任务状态。"""
        # 手动创建任务数据和活动任务
        task_id = "exec_test001"
        manager.task_data[task_id] = ExecutorTask(
            task_id=task_id,
            plan_id="plan_test",
            plan_json='{"steps": []}',
            status="pending",
        )
        # 添加一个活动任务
        async def mock_task():
            await asyncio.sleep(10)
        manager.active_tasks[task_id] = asyncio.create_task(mock_task())

        status = manager.get_task_status(task_id)

        assert status["task_id"] == task_id
        assert status["plan_id"] == "plan_test"
        assert status["status"] == "pending"
        assert status["done"] is False  # 有活动任务，所以未完成
        assert "created_at" in status
        assert "updated_at" in status

        # 清理
        manager.active_tasks[task_id].cancel()

    def test_get_task_status_not_found(self, manager):
        """测试查询不存在的任务。"""
        status = manager.get_task_status("exec_nonexistent")
        assert "error" in status
        assert "not found" in status["error"].lower()

    @pytest.mark.asyncio
    async def test_cancel_task(self, manager):
        """测试取消任务。"""
        # 创建模拟任务
        async def mock_task():
            await asyncio.sleep(10)

        task_id = "exec_cancel_test"
        task = asyncio.create_task(mock_task())
        manager.active_tasks[task_id] = task

        # 取消任务
        success = manager.cancel_task(task_id)
        assert success is True

        # 等待一下确认取消生效
        await asyncio.sleep(0.01)
        assert task.done()

    def test_cancel_task_not_found(self, manager):
        """测试取消不存在的任务。"""
        success = manager.cancel_task("exec_nonexistent")
        assert success is False

    @pytest.mark.asyncio
    async def test_cleanup_task(self, manager):
        """测试清理任务。"""
        task_id = "exec_cleanup"
        manager.task_data[task_id] = ExecutorTask(
            task_id=task_id,
            plan_id="plan_test",
            plan_json='{}',
            status="completed",
        )
        # 创建一个短时任务
        async def mock_task():
            await asyncio.sleep(0)
        manager.active_tasks[task_id] = asyncio.create_task(mock_task())

        # 等待任务完成
        await asyncio.sleep(0.01)

        # 清理任务
        success = manager.cleanup_task(task_id)
        assert success is True

        # 验证已清理
        assert task_id not in manager.task_data
        assert task_id not in manager.active_tasks

    def test_get_all_tasks(self, manager):
        """测试获取所有任务列表。"""
        # 创建多个任务
        for i in range(3):
            task_id = f"exec_{i:03d}"
            manager.task_data[task_id] = ExecutorTask(
                task_id=task_id,
                plan_id=f"plan_{i}",
                plan_json='{}',
                status="pending",
            )

        all_tasks = manager.get_all_tasks()
        assert len(all_tasks) == 3

        # 验证每个任务都有必要字段
        for task_status in all_tasks:
            assert "task_id" in task_status
            assert "status" in task_status


class TestExecutorManagerIntegration:
    """集成测试（需要真实 Executor 图）。"""

    @pytest.fixture
    def manager(self):
        """创建测试用的 ExecutorManager 实例。"""
        ExecutorManager._instance = None
        return ExecutorManager()

    @pytest.mark.asyncio
    @pytest.mark.slow
    async def test_full_task_lifecycle(self, manager):
        """测试完整任务生命周期。"""
        # 创建简单计划
        plan_json = json.dumps({
            "plan_id": "plan_lifecycle",
            "steps": [
                {
                    "step_id": "step_1",
                    "intent": "列出当前目录文件",
                    "expected_output": "文件列表",
                    "status": "pending"
                }
            ]
        })

        from src.common.context import Context
        context = Context()

        # 1. 启动任务
        task_id = await manager.start_executor(plan_json, context)
        assert task_id in manager.task_data

        # 2. 查询初始状态
        status = manager.get_task_status(task_id)
        assert status["status"] in ("pending", "running")

        # 3. 等待任务完成（最多 30 秒）
        max_wait = 30
        waited = 0
        while waited < max_wait:
            await asyncio.sleep(1)
            status = manager.get_task_status(task_id)
            if status["done"]:
                break
            waited += 1

        # 4. 验证最终状态
        final_status = manager.get_task_status(task_id)
        assert final_status["done"] is True
        assert final_status["status"] in ("completed", "failed")

        # 5. 清理任务
        manager.cleanup_task(task_id)
        assert task_id not in manager.task_data
