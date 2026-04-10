"""Executor 后台任务管理器。

支持 V3+ 异步并发模式：管理 Executor 后台任务的生命周期，
包括启动、状态查询、取消等功能。
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.common.context import Context

logger = logging.getLogger(__name__)


@dataclass
class ExecutorTask:
    """Executor 后台任务数据结构。"""

    task_id: str
    plan_id: str
    plan_json: str
    status: "task_status"  # "pending" | "running" | "completed" | "failed" | "cancelled"
    result: dict | None = None
    error: str | None = None
    created_at: str = field(default_factory=lambda: _timestamp())
    updated_at: str = field(default_factory=lambda: _timestamp())


def _timestamp() -> str:
    """生成 ISO 格式时间戳。"""
    from datetime import datetime

    return datetime.now().isoformat()


class ExecutorManager:
    """管理 Executor 后台任务的全局单例。

    功能：
    - 启动后台异步任务
    - 查询任务状态和进度
    - 取消正在运行的任务
    - 清理已完成的历史任务
    """

    _instance: "ExecutorManager | None" = None

    def __init__(self) -> None:
        """初始化任务管理器。"""
        self.active_tasks: dict[str, asyncio.Task] = {}
        self.task_data: dict[str, ExecutorTask] = {}
        logger.info("ExecutorManager initialized")

    @classmethod
    def get_instance(cls) -> "ExecutorManager":
        """获取单例实例（延迟初始化）。"""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    async def start_executor(
        self,
        plan_json: str,
        context: "Context",
    ) -> str:
        """启动 Executor 后台任务（非阻塞）。

        Args:
            plan_json: 执行计划 JSON 字符串
            context: 运行时配置

        Returns:
            task_id: 后台任务 ID，用于后续状态查询

        Raises:
            ValueError: plan_json 无效
        """
        # 解析 plan_id
        try:
            plan_data = json.loads(plan_json)
            plan_id = plan_data.get("plan_id", "unknown")
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid plan_json: {e}")

        # 生成任务 ID
        task_id = f"exec_{uuid.uuid4().hex[:8]}"

        # 创建任务数据
        task_data = ExecutorTask(
            task_id=task_id,
            plan_id=plan_id,
            plan_json=plan_json,
            status="pending",
        )
        self.task_data[task_id] = task_data

        # 创建后台任务（不等待）
        task = asyncio.create_task(
            self._run_executor_wrapper(task_id, plan_json, context)
        )
        self.active_tasks[task_id] = task

        logger.info(
            "Executor task started: task_id=%s, plan_id=%s",
            task_id,
            plan_id,
        )

        return task_id

    async def _run_executor_wrapper(
        self,
        task_id: str,
        plan_json: str,
        context: "Context",
    ) -> None:
        """包装 Executor 执行逻辑（在后台任务中运行）。

        Args:
            task_id: 任务 ID
            plan_json: 执行计划 JSON
            context: 运行时配置
        """
        task_data = self.task_data.get(task_id)
        if not task_data:
            logger.error("Task not found: %s", task_id)
            return

        task_data.status = "running"
        task_data.updated_at = _timestamp()

        try:
            # 导入 Executor 图
            from src.executor_agent.graph import executor_graph

            # 使用独立的 thread_id 避免 Supervisor 状态冲突
            config = {
                "configurable": {
                    "thread_id": f"executor_{task_id}",
                }
            }

            # 调用 Executor 图
            result = await executor_graph.ainvoke(
                {"plan_json": plan_json},
                config,
            )

            # 保存结果
            task_data.status = "completed"
            task_data.result = result if isinstance(result, dict) else {"output": result}
            task_data.updated_at = _timestamp()

            logger.info(
                "Executor task completed: task_id=%s, plan_id=%s",
                task_id,
                task_data.plan_id,
            )

        except asyncio.CancelledError:
            # 任务被取消
            task_data.status = "cancelled"
            task_data.error = "Task was cancelled"
            task_data.updated_at = _timestamp()
            logger.info("Executor task cancelled: %s", task_id)

        except Exception as e:
            # 任务失败
            task_data.status = "failed"
            task_data.error = f"{type(e).__name__}: {str(e)}"
            task_data.updated_at = _timestamp()
            logger.exception(
                "Executor task failed: task_id=%s, error=%s",
                task_id,
                task_data.error,
            )

    def get_task_status(self, task_id: str) -> dict:
        """查询任务状态。

        Args:
            task_id: 任务 ID

        Returns:
            状态信息字典，包含：
            - task_id: 任务 ID
            - plan_id: 计划 ID
            - status: 当前状态
            - done: 是否完成（包括完成、失败、取消）
            - result: 执行结果（仅当 status=completed 时）
            - error: 错误信息（仅当 status=failed 时）
            - created_at: 创建时间
            - updated_at: 更新时间
        """
        if task_id not in self.task_data:
            return {
                "error": f"Task not found: {task_id}",
                "task_id": task_id,
            }

        task_data = self.task_data[task_id]
        active_task = self.active_tasks.get(task_id)

        # 判断是否完成
        done = (
            active_task is None
            or active_task.done()
            or task_data.status in ("completed", "failed", "cancelled")
        )

        response = {
            "task_id": task_data.task_id,
            "plan_id": task_data.plan_id,
            "status": task_data.status,
            "done": done,
            "created_at": task_data.created_at,
            "updated_at": task_data.updated_at,
        }

        # 添加结果（仅完成时）
        if task_data.status == "completed" and task_data.result:
            response["result"] = task_data.result

        # 添加错误（仅失败时）
        if task_data.error:
            response["error"] = task_data.error

        return response

    def cancel_task(self, task_id: str) -> bool:
        """取消正在运行的任务。

        Args:
            task_id: 任务 ID

        Returns:
            是否成功取消
        """
        if task_id not in self.active_tasks:
            logger.warning("Cannot cancel task: not found in active_tasks: %s", task_id)
            return False

        task = self.active_tasks[task_id]

        # 已经完成或取消的任务无需操作
        if task.done():
            logger.info("Task already done: %s", task_id)
            return False

        # 取消任务
        task.cancel()
        logger.info("Task cancelled: %s", task_id)
        return True

    def cleanup_task(self, task_id: str) -> bool:
        """清理已完成的历史任务数据。

        Args:
            task_id: 任务 ID

        Returns:
            是否成功清理
        """
        if task_id in self.task_data:
            del self.task_data[task_id]

        if task_id in self.active_tasks:
            del self.active_tasks[task_id]

        logger.debug("Task cleaned up: %s", task_id)
        return True

    def get_all_tasks(self) -> list[dict]:
        """获取所有任务的状态列表。

        Returns:
            任务状态列表
        """
        return [
            self.get_task_status(task_id) for task_id in list(self.task_data.keys())
        ]


def get_executor_manager() -> ExecutorManager:
    """获取 ExecutorManager 单例实例。

    延迟初始化：仅在首次调用时创建实例。

    Returns:
        ExecutorManager 实例
    """
    return ExecutorManager.get_instance()
