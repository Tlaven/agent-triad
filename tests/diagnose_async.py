#!/usr/bin/env python3
"""诊断异步工具是否正确加载"""

import asyncio
import os
from dotenv import load_dotenv
from src.common.context import Context
from src.supervisor_agent.tools import get_tools
from src.supervisor_agent.prompts import get_supervisor_system_prompt

load_dotenv()


async def diagnose():
    print("\n" + "="*60)
    print("V3+ 异步模式诊断")
    print("="*60)

    # 1. 检查环境变量
    print("\n[1] 环境变量检查")
    env_async = os.getenv("ENABLE_V3PLUS_ASYNC", "not set")
    print(f"    ENABLE_V3PLUS_ASYNC = {env_async}")
    if env_async.lower() in ("true", "1", "yes"):
        print("    [OK] 环境变量已启用")
    else:
        print("    [WARNING] 环境变量未启用")

    # 2. 检查 Context
    print("\n[2] Context 配置检查")
    ctx = Context()
    print(f"    Context.enable_v3plus_async = {ctx.enable_v3plus_async}")
    if ctx.enable_v3plus_async:
        print("    [OK] Context 启用异步")
    else:
        print("    [WARNING] Context 未启用异步")

    # 3. 检查工具列表
    print("\n[3] 工具列表检查")
    tools = await get_tools(ctx)
    print(f"    总工具数: {len(tools)}")

    tool_names = [t.name for t in tools]
    print(f"\n    所有工具:")
    for i, name in enumerate(tool_names, 1):
        marker = "[ASYNC]" if name in ["call_executor_async", "get_executor_status", "cancel_executor"] else "[BASE]"
        print(f"      {i:2d}. {marker:8} {name}")

    async_tools = ["call_executor_async", "get_executor_status", "cancel_executor"]
    missing = [t for t in async_tools if t not in tool_names]

    if missing:
        print(f"\n    [ERROR] 缺少异步工具: {missing}")
    else:
        print(f"\n    [OK] 所有异步工具已加载")

    # 4. 检查 system prompt
    print("\n[4] System Prompt 检查")
    prompt = get_supervisor_system_prompt(ctx)
    print(f"    Prompt 长度: {len(prompt)} 字符")

    has_async_section = "Asynchronous Concurrent" in prompt
    has_call_executor_async = "call_executor_async" in prompt

    print(f"    包含异步章节: {has_async_section}")
    print(f"    包含异步工具说明: {has_call_executor_async}")

    if has_async_section and has_call_executor_async:
        print(f"    [OK] System prompt 包含异步指南")
    else:
        print(f"    [WARNING] System prompt 缺少异步指南")

    # 5. 总结
    print("\n" + "="*60)
    print("诊断总结")
    print("="*60)

    all_ok = (
        env_async.lower() in ("true", "1", "yes") and
        ctx.enable_v3plus_async and
        not missing and
        has_async_section
    )

    if all_ok:
        print("\n    [SUCCESS] 所有检查通过！")
        print("\n    如果在 Studio UI 中看不到异步工具，请尝试：")
        print("    1. 在浏览器中硬刷新 (Ctrl+Shift+R)")
        print("    2. 清除浏览器缓存")
        print("    3. 关闭并重新打开 Studio UI 标签页")
        print("    4. 检查是否选择了正确的 'supervisor' 助手")
        print("\n    Studio UI 地址:")
        print("    https://smith.langchain.com/studio/?baseUrl=http://127.0.0.1:2024")
    else:
        print("\n    [WARNING] 发现问题，请检查上述错误")
        print("\n    解决方案:")
        if not ctx.enable_v3plus_async:
            print("    1. 确保 .env 中设置了 ENABLE_V3PLUS_ASYNC=true")
        if missing:
            print("    2. 工具加载异常，请检查代码")
        if not has_async_section:
            print("    3. System prompt 未更新，请检查 prompts.py")

    print("\n" + "="*60 + "\n")


if __name__ == "__main__":
    asyncio.run(diagnose())
