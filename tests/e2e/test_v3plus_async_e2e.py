"""V3+ 异步并发模式端到端测试。

测试场景：
1. 异步工具注册验证（启用/禁用模式）
2. 长时间任务的非阻塞执行
3. 任务状态查询
4. 任务取消功能

运行方式：
    uv run pytest tests/e2e/test_v3plus_async_e2e.py -m live_llm -v -s
    make test_e2e

环境要求：
    - SILICONFLOW_API_KEY 或 DASHSCOPE_API_KEY
    - ENABLE_V3PLUS_ASYNC=true（测试异步功能）
"""

import asyncio
import os
import re
import time
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from src.common.context import Context
from src.supervisor_agent.graph import graph
from src.supervisor_agent.tools import get_tools

pytestmark = pytest.mark.live_llm


def _has_api_keys() -> bool:
    return bool(os.getenv("SILICONFLOW_API_KEY") or os.getenv("DASHSCOPE_API_KEY"))


def _extract_task_id(response_text: str) -> str | None:
    """从工具返回文本中提取 task_id。

    格式示例：
        **后台任务 ID**: task_20260410_223045_abc123
    """
    match = re.search(r'task_[0-9]{8}_[0-9]{6}_[a-z0-9]+', response_text)
    return match.group(0) if match else None


# ---------------------------------------------------------------------------
# 场景 1：异步工具注册验证
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _has_api_keys(), reason="No API keys configured")
async def test_async_tools_registered_when_enabled(monkeypatch, tmp_path) -> None:
    """验证 ENABLE_V3PLUS_ASYNC=true 时异步工具被正确注册。"""
    monkeypatch.setenv("ENABLE_V3PLUS_ASYNC", "true")
    monkeypatch.setenv("AGENT_WORKSPACE_DIR", str(tmp_path))

    ctx = Context(enable_v3plus_async=True)
    tools = await get_tools(ctx)

    tool_names = [tool.name for tool in tools]
    assert "call_executor_async" in tool_names, "call_executor_async should be registered"
    assert "get_executor_status" in tool_names, "get_executor_status should be registered"
    assert "cancel_executor" in tool_names, "cancel_executor should be registered"


@pytest.mark.skipif(not _has_api_keys(), reason="No API keys configured")
async def test_async_tools_not_registered_when_disabled(monkeypatch, tmp_path) -> None:
    """验证 ENABLE_V3PLUS_ASYNC=false 时异步工具不被注册。"""
    monkeypatch.setenv("ENABLE_V3PLUS_ASYNC", "false")
    monkeypatch.setenv("AGENT_WORKSPACE_DIR", str(tmp_path))

    ctx = Context(enable_v3plus_async=False)
    tools = await get_tools(ctx)

    tool_names = [tool.name for tool in tools]
    assert "call_executor_async" not in tool_names, "call_executor_async should NOT be registered"
    assert "get_executor_status" not in tool_names, "get_executor_status should NOT be registered"
    assert "cancel_executor" not in tool_names, "cancel_executor should NOT be registered"


# ---------------------------------------------------------------------------
# 场景 2：长时间任务的非阻塞执行
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _has_api_keys(), reason="No API keys configured")
@pytest.mark.timeout(300)  # 5 分钟超时
async def test_long_running_task_non_blocking(monkeypatch, tmp_path) -> None:
    """验证长时间任务通过 call_executor_async 非阻塞执行。"""
    monkeypatch.setenv("ENABLE_V3PLUS_ASYNC", "true")
    monkeypatch.setenv("AGENT_WORKSPACE_DIR", str(tmp_path))

    # 创建一个需要较长时间的任务（多次文件操作）
    task = (
        "请异步执行以下任务：\n"
        "1. 创建文件夹 'workspace/test_project'\n"
        "2. 在其中创建 10 个文件（file_1.txt 到 file_10.txt），每个文件内容为文件编号\n"
        "3. 创建一个 summary.md 文件，列出所有创建的文件\n"
        "使用 call_executor_async 工具启动任务。"
    )

    ctx = Context(
        enable_v3plus_async=True,
        max_executor_iterations=20,
        max_replan=1,
    )

    result = await graph.ainvoke(
        {"messages": [HumanMessage(content=task)]},
        context=ctx,
    )

    # 提取 task_id
    messages = result["messages"]
    task_id = None

    for msg in reversed(messages):
        if isinstance(msg, AIMessage) and msg.content:
            task_id = _extract_task_id(msg.content)
            if task_id:
                break

    assert task_id is not None, "Should have received a task_id from call_executor_async"
    print(f"\n✓ 收到任务 ID: {task_id}")

    # 等待任务完成（最多 60 秒）
    max_wait = 60
    start_time = time.time()
    final_status = None

    while time.time() - start_time < max_wait:
        # 查询任务状态
        status_result = await graph.ainvoke(
            {"messages": [HumanMessage(content=f"查询任务 {task_id} 的状态")]},
            context=ctx,
        )

        status_messages = status_result["messages"]
        for msg in reversed(status_messages):
            if isinstance(msg, AIMessage) and msg.content:
                if "completed" in msg.content.lower():
                    final_status = "completed"
                    print(f"\n✓ 任务已完成")
                    break
                elif "running" in msg.content.lower() or "执行中" in msg.content:
                    print(f"\n⏳ 任务执行中...")
                    break

        if final_status == "completed":
            break

        await asyncio.sleep(3)

    assert final_status == "completed", f"Task did not complete within {max_wait}s"

    # 验证文件已创建
    test_dir = tmp_path / "workspace" / "test_project"
    assert test_dir.exists(), "Test project directory should be created"

    for i in range(1, 11):
        file_path = test_dir / f"file_{i}.txt"
        assert file_path.exists(), f"File {file_path} should exist"

    summary_file = test_dir / "summary.md"
    assert summary_file.exists(), "Summary file should exist"


# ---------------------------------------------------------------------------
# 场景 3：任务状态查询
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _has_api_keys(), reason="No API keys configured")
@pytest.mark.timeout(180)
async def test_task_status_query(monkeypatch, tmp_path) -> None:
    """验证通过 get_executor_status 查询任务状态。"""
    monkeypatch.setenv("ENABLE_V3PLUS_ASYNC", "true")
    monkeypatch.setenv("AGENT_WORKSPACE_DIR", str(tmp_path))

    # 创建一个简单任务
    task = "创建文件 status_test.txt，内容为 'Status Test'。使用 call_executor_async 启动。"

    ctx = Context(
        enable_v3plus_async=True,
        max_executor_iterations=10,
        max_replan=1,
    )

    # 启动任务
    result = await graph.ainvoke(
        {"messages": [HumanMessage(content=task)]},
        context=ctx,
    )

    # 提取 task_id
    messages = result["messages"]
    task_id = None
    for msg in reversed(messages):
        if isinstance(msg, AIMessage) and msg.content:
            task_id = _extract_task_id(msg.content)
            if task_id:
                break

    assert task_id is not None, "Should have received a task_id"

    # 查询状态
    status_query = f"使用 get_executor_status 查询任务 {task_id} 的状态"
    status_result = await graph.ainvoke(
        {"messages": [HumanMessage(content=status_query)]},
        context=ctx,
    )

    status_messages = status_result["messages"]
    found_status_info = False

    for msg in reversed(status_messages):
        if isinstance(msg, AIMessage) and msg.content:
            # 检查是否包含状态信息
            if any(keyword in msg.content for keyword in ["状态", "status", "running", "completed", "pending"]):
                found_status_info = True
                print(f"\n✓ 状态查询响应: {msg.content[:200]}...")
                break

    assert found_status_info, "Should receive status information"


# ---------------------------------------------------------------------------
# 场景 4：任务取消
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _has_api_keys(), reason="No API keys configured")
@pytest.mark.timeout(120)
async def test_task_cancellation(monkeypatch, tmp_path) -> None:
    """验证通过 cancel_executor 取消任务。"""
    monkeypatch.setenv("ENABLE_V3PLUS_ASYNC", "true")
    monkeypatch.setenv("AGENT_WORKSPACE_DIR", str(tmp_path))

    # 创建一个需要较长时间的任务（可以被中断）
    task = (
        "创建一个循环任务：使用 call_executor_async 启动一个任务，"
        "让它循环 100 次，每次迭代都向文件 loop.txt 追加一行内容。"
    )

    ctx = Context(
        enable_v3plus_async=True,
        max_executor_iterations=50,
        max_replan=1,
    )

    # 启动任务
    result = await graph.ainvoke(
        {"messages": [HumanMessage(content=task)]},
        context=ctx,
    )

    # 提取 task_id
    messages = result["messages"]
    task_id = None
    for msg in reversed(messages):
        if isinstance(msg, AIMessage) and msg.content:
            task_id = _extract_task_id(msg.content)
            if task_id:
                break

    assert task_id is not None, "Should have received a task_id"
    print(f"\n✓ 收到任务 ID: {task_id}")

    # 等待一下确保任务开始
    await asyncio.sleep(3)

    # 取消任务
    cancel_command = f"使用 cancel_executor 取消任务 {task_id}"
    cancel_result = await graph.ainvoke(
        {"messages": [HumanMessage(content=cancel_command)]},
        context=ctx,
    )

    # 验证取消响应
    cancel_messages = cancel_result["messages"]
    cancelled = False

    for msg in reversed(cancel_messages):
        if isinstance(msg, AIMessage) and msg.content:
            if "cancelled" in msg.content.lower() or "取消" in msg.content or "canceled" in msg.content.lower():
                cancelled = True
                print(f"\n✓ 任务已取消: {msg.content[:200]}...")
                break

    assert cancelled, "Task should be cancelled"


# ---------------------------------------------------------------------------
# 场景 5：多个任务并发执行
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _has_api_keys(), reason="No API keys configured")
@pytest.mark.timeout(240)
async def test_multiple_concurrent_tasks(monkeypatch, tmp_path) -> None:
    """验证可以同时启动多个后台任务。"""
    monkeypatch.setenv("ENABLE_V3PLUS_ASYNC", "true")
    monkeypatch.setenv("AGENT_WORKSPACE_DIR", str(tmp_path))

    ctx = Context(
        enable_v3plus_async=True,
        max_executor_iterations=10,
        max_replan=1,
    )

    task_ids = []

    # 启动 3 个独立任务
    for i in range(1, 4):
        task = f"创建文件 task_{i}.txt，内容为 'Task {i} completed'。使用 call_executor_async。"
        result = await graph.ainvoke(
            {"messages": [HumanMessage(content=task)]},
            context=ctx,
        )

        messages = result["messages"]
        for msg in reversed(messages):
            if isinstance(msg, AIMessage) and msg.content:
                tid = _extract_task_id(msg.content)
                if tid and tid not in task_ids:
                    task_ids.append(tid)
                    print(f"\n✓ 任务 {i} 启动: {tid}")
                    break

    assert len(task_ids) >= 2, f"Should start at least 2 tasks, got {len(task_ids)}"

    # 等待所有任务完成
    max_wait = 60
    start_time = time.time()

    while time.time() - start_time < max_wait:
        completed_count = 0
        for task_id in task_ids:
            # 查询每个任务状态
            status_result = await graph.ainvoke(
                {"messages": [HumanMessage(content=f"查询任务 {task_id} 状态")]},
                context=ctx,
            )

            for msg in reversed(status_result["messages"]):
                if isinstance(msg, AIMessage) and msg.content:
                    if "completed" in msg.content.lower():
                        completed_count += 1
                        break

        if completed_count >= 2:
            print(f"\n✓ {completed_count} 个任务已完成")
            break

        await asyncio.sleep(3)

    # 验证文件创建
    for i in range(1, 4):
        file_path = tmp_path / f"task_{i}.txt"
        # 至少有一个文件被创建
        if file_path.exists():
            print(f"\n✓ 文件 task_{i}.txt 已创建")
            break
    else:
        # 至少应该有一个文件被创建
        assert any((tmp_path / f"task_{i}.txt").exists() for i in range(1, 4)), \
            "At least one task file should be created"


# ---------------------------------------------------------------------------
# 场景 6：异步模式下的完整工作流
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _has_api_keys(), reason="No API keys configured")
@pytest.mark.timeout(300)
async def test_async_workflow_integration(monkeypatch, tmp_path) -> None:
    """验证异步模式下的完整工作流：启动 → 查询 → 等待 → 获取结果。"""
    monkeypatch.setenv("ENABLE_V3PLUS_ASYNC", "true")
    monkeypatch.setenv("AGENT_WORKSPACE_DIR", str(tmp_path))

    task = (
        "执行以下任务：\n"
        "1. 创建文件夹 'workflow_test'\n"
        "2. 在其中创建 data.txt，内容为 'Sample Data'\n"
        "3. 读取 data.txt 内容并写入 output.txt\n"
        "使用 call_executor_async 启动此任务。"
    )

    ctx = Context(
        enable_v3plus_async=True,
        max_executor_iterations=15,
        max_replan=2,
    )

    # 步骤 1: 启动任务
    result = await graph.ainvoke(
        {"messages": [HumanMessage(content=task)]},
        context=ctx,
    )

    task_id = None
    for msg in reversed(result["messages"]):
        if isinstance(msg, AIMessage) and msg.content:
            task_id = _extract_task_id(msg.content)
            if task_id:
                break

    assert task_id is not None, "Should receive task_id"
    print(f"\n✓ 步骤 1: 任务已启动 - {task_id}")

    # 步骤 2: 等待并查询状态
    max_wait = 60
    start_time = time.time()
    task_completed = False

    while time.time() - start_time < max_wait and not task_completed:
        status_result = await graph.ainvoke(
            {"messages": [HumanMessage(content=f"任务 {task_id} 的状态？")]},
            context=ctx,
        )

        for msg in reversed(status_result["messages"]):
            if isinstance(msg, AIMessage) and msg.content:
                if "completed" in msg.content.lower():
                    task_completed = True
                    print(f"\n✓ 步骤 2: 任务已完成")
                    break
                elif "running" in msg.content.lower() or "执行中" in msg.content:
                    print(f"\n⏳ 步骤 2: 任务执行中...")
                    break

        if not task_completed:
            await asyncio.sleep(3)

    assert task_completed, "Task should complete within timeout"

    # 步骤 3: 获取完整输出
    output_result = await graph.ainvoke(
        {"messages": [HumanMessage(content=f"使用 get_executor_full_output 获取任务 {task_id} 的完整输出")]},
        context=ctx,
    )

    # 验证工作区文件
    workflow_dir = tmp_path / "workflow_test"
    assert workflow_dir.exists(), "Workflow directory should exist"

    data_file = workflow_dir / "data.txt"
    assert data_file.exists(), "data.txt should exist"

    output_file = workflow_dir / "output.txt"
    assert output_file.exists(), "output.txt should exist"

    # 验证内容
    content = output_file.read_text()
    assert "Sample Data" in content, "output.txt should contain the data from data.txt"
    print(f"\n✓ 步骤 3: 工作流验证通过，文件内容正确")
