#!/usr/bin/env python3
"""手动测试脚本 - 直接调用 Supervisor"""

import asyncio
import os
from pathlib import Path

from dotenv import load_dotenv
from langchain_core.messages import HumanMessage
from src.common.context import Context
from src.supervisor_agent.graph import graph

# 加载环境变量
load_dotenv()


async def test_simple_qa():
    """测试简单问答（Mode 1）"""
    print("\n" + "="*60)
    print("测试场景 A: 简单问答")
    print("="*60)

    ctx = Context(max_replan=1, max_executor_iterations=5)

    result = await graph.ainvoke(
        {"messages": [HumanMessage(content="什么是 Python？用一句话回答。")]},
        context=ctx,
    )

    messages = result["messages"]
    for msg in messages:
        if hasattr(msg, 'content') and msg.content:
            print(f"\n响应: {msg.content[:200]}")

    print("\n[OK] 测试完成")


async def test_file_operations():
    """测试文件操作（Mode 2/3）"""
    print("\n" + "="*60)
    print("测试场景 B: 文件操作")
    print("="*60)

    # 设置工作目录
    workspace = Path("workspace/test_manual")
    workspace.mkdir(parents=True, exist_ok=True)

    ctx = Context(
        max_replan=1,
        max_executor_iterations=10,
    )

    task = f"在 {workspace} 目录创建 hello.txt，内容为 Hello World"

    result = await graph.ainvoke(
        {"messages": [HumanMessage(content=task)]},
        context=ctx,
    )

    messages = result["messages"]
    for msg in messages:
        if hasattr(msg, 'content') and msg.content:
            print(f"\n响应: {msg.content[:300]}")

    # 检查文件是否创建
    created_file = workspace / "hello.txt"
    if created_file.exists():
        print(f"\n[OK] 文件创建成功: {created_file}")
        print(f"  内容: {created_file.read_text()}")
    else:
        print(f"\n[FAIL] 文件未创建: {created_file}")


async def test_multistep_task():
    """测试多步骤任务（Mode 3）"""
    print("\n" + "="*60)
    print("测试场景 C: 多步骤任务")
    print("="*60)

    workspace = Path("workspace/test_multistep")
    workspace.mkdir(parents=True, exist_ok=True)

    ctx = Context(
        max_replan=2,
        max_executor_iterations=15,
    )

    task = f"""
    请完成以下任务：
    1. 在 {workspace} 创建 README.md，内容为 '# Test Project'
    2. 创建 main.py，内容为 'print("Hello")'
    3. 读取 README.md 的内容并显示
    """

    result = await graph.ainvoke(
        {"messages": [HumanMessage(content=task)]},
        context=ctx,
    )

    messages = result["messages"]
    for msg in messages:
        if hasattr(msg, 'content') and msg.content:
            print(f"\n响应片段: {msg.content[:400]}")

    print("\n[OK] 测试完成")


async def test_v3plus_async():
    """测试 V3+ 异步执行"""
    print("\n" + "="*60)
    print("测试场景 D: V3+ 异步执行")
    print("="*60)

    # 检查是否启用
    if not os.getenv("ENABLE_V3PLUS_ASYNC"):
        print("\n[WARNING] V3+ async feature not enabled")
        print("   Set in .env: ENABLE_V3PLUS_ASYNC=true")
        return

    workspace = Path("workspace/test_async")
    workspace.mkdir(parents=True, exist_ok=True)

    ctx = Context(
        enable_v3plus_async=True,
        max_replan=1,
        max_executor_iterations=10,
    )

    task = f"创建 5 个文件（async_1.txt 到 async_5.txt），每个内容为文件编号"

    result = await graph.ainvoke(
        {"messages": [HumanMessage(content=task)]},
        context=ctx,
    )

    messages = result["messages"]
    for msg in messages:
        if hasattr(msg, 'content') and msg.content:
            print(f"\n响应: {msg.content[:400]}")

    print("\n[OK] 测试完成")


async def main():
    """运行所有测试"""
    print("\n[TEST] AgentTriad Manual Testing")
    print("="*60)

    try:
        # 场景 A: 简单问答
        await test_simple_qa()

        # 场景 B: 文件操作
        await test_file_operations()

        # 场景 C: 多步骤任务
        await test_multistep_task()

        # 场景 D: V3+ 异步（如果启用）
        await test_v3plus_async()

        print("\n" + "="*60)
        print("✅ 所有测试场景执行完成")
        print("="*60)

    except Exception as e:
        print(f"\n[ERROR] Test failed: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())
