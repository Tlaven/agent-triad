#!/usr/bin/env python3
"""快速验证 V3+ 异步工具是否正确加载"""

import asyncio
from dotenv import load_dotenv
from src.common.context import Context
from src.supervisor_agent.tools import get_tools

load_dotenv()

async def verify_async_tools():
    print("\n[VERIFY] V3+ Async Tools Check")
    print("="*60)

    # 检查环境变量
    import os
    enabled = os.getenv("ENABLE_V3PLUS_ASYNC", "false").lower() in ("true", "1", "yes", "on")
    print(f"\n[ENV] ENABLE_V3PLUS_ASYNC = {enabled}")

    # 加载工具
    ctx = Context(enable_v3plus_async=enabled)
    tools = await get_tools(ctx)

    tool_names = [tool.name for tool in tools]

    print(f"\n[TOOLS] Total tools loaded: {len(tools)}")
    print("\n[TOOLS] Available tools:")
    for i, name in enumerate(tool_names, 1):
        marker = "[ASYNC]" if name in ["call_executor_async", "get_executor_status", "cancel_executor"] else "[BASE]"
        print(f"  {i:2d}. {marker} {name}")

    # 验证异步工具
    async_tools = ["call_executor_async", "get_executor_status", "cancel_executor"]
    print("\n[CHECK] Async tools status:")

    all_found = True
    for tool in async_tools:
        status = "FOUND" if tool in tool_names else "MISSING"
        symbol = "[OK]" if tool in tool_names else "[X]"
        print(f"  {symbol} {tool}: {status}")
        if tool not in tool_names:
            all_found = False

    print("\n" + "="*60)
    if enabled and all_found:
        print("[SUCCESS] V3+ Async mode is ENABLED and all tools are loaded!")
        print("\nYou can now test async functionality:")
        print("  1. Open Studio UI: https://smith.langchain.com/studio/?baseUrl=http://127.0.0.1:2024")
        print("  2. Select 'supervisor' assistant")
        print("  3. Send task: 'Create 5 files using async mode'")
    elif enabled and not all_found:
        print("[WARNING] V3+ Async is ENABLED but some tools are MISSING!")
    else:
        print("[INFO] V3+ Async mode is DISABLED")
        print("  To enable: Add 'ENABLE_V3PLUS_ASYNC=true' to .env file")

    print("="*60 + "\n")

if __name__ == "__main__":
    asyncio.run(verify_async_tools())
