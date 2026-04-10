#!/usr/bin/env python3
"""测试异步模式下的 system prompt 追加功能"""

import os
from dotenv import load_dotenv
from src.common.context import Context
from src.supervisor_agent.prompts import get_supervisor_system_prompt

load_dotenv()


def test_prompt_without_async():
    """测试禁用异步时的 prompt"""
    print("\n" + "="*60)
    print("测试 1: 禁用异步模式")
    print("="*60)

    ctx = Context(enable_v3plus_async=False)
    prompt = get_supervisor_system_prompt(ctx)

    print(f"\nPrompt 长度: {len(prompt)} 字符")
    print(f"包含异步说明: {'V3+ 异步' in prompt}")
    print(f"包含 call_executor_async: {'call_executor_async' in prompt}")
    print(f"包含 get_executor_status: {'get_executor_status' in prompt}")
    print(f"包含 cancel_executor: {'cancel_executor' in prompt}")

    assert "V3+ 异步" not in prompt, "禁用时不应该包含异步说明"
    assert "call_executor_async" not in prompt, "禁用时不应该包含异步工具说明"
    print("\n[OK] 禁用模式测试通过")


def test_prompt_with_async():
    """测试启用异步时的 prompt"""
    print("\n" + "="*60)
    print("测试 2: 启用异步模式")
    print("="*60)

    ctx = Context(enable_v3plus_async=True)
    prompt = get_supervisor_system_prompt(ctx)

    print(f"\nPrompt 长度: {len(prompt)} 字符")
    print(f"包含异步说明: {'V3+ 异步' in prompt}")
    print(f"包含 call_executor_async: {'call_executor_async' in prompt}")
    print(f"包含 get_executor_status: {'get_executor_status' in prompt}")
    print(f"包含 cancel_executor: {'cancel_executor' in prompt}")
    print(f"包含工作流程说明: {'异步执行工作流程' in prompt}")
    print(f"包含使用场景: {'何时选择异步模式' in prompt}")

    assert "V3+ 异步并发模式" in prompt, "启用时应该包含异步说明"
    assert "call_executor_async" in prompt, "应该说明 call_executor_async"
    assert "get_executor_status" in prompt, "应该说明 get_executor_status"
    assert "cancel_executor" in prompt, "应该说明 cancel_executor"
    print("\n[OK] 启用模式测试通过")

    # 显示追加的内容摘要
    async_section_start = prompt.find("## 异步并发执行模式")
    if async_section_start > 0:
        async_section = prompt[async_section_start:async_section_start+300]
        print(f"\n异步部分预览（前 300 字符）:")
        print(async_section)


def test_prompt_backward_compatible():
    """测试向后兼容性（不传 context 参数）"""
    print("\n" + "="*60)
    print("测试 3: 向后兼容性")
    print("="*60)

    # 不传 context 参数
    prompt = get_supervisor_system_prompt()

    print(f"\nPrompt 长度: {len(prompt)} 字符")
    print(f"包含异步说明: {'V3+ 异步' in prompt}")
    print(f"基础提示词完整: {'Supervisor Agent' in prompt}")

    assert "Supervisor Agent" in prompt, "应该包含基础提示词"
    assert "V3+ 异步" not in prompt, "不传 context 时不应该包含异步说明"
    print("\n[OK] 向后兼容性测试通过")


if __name__ == "__main__":
    print("\n[TEST] System Prompt 异步追加功能测试")
    print("="*60)

    test_prompt_without_async()
    test_prompt_with_async()
    test_prompt_backward_compatible()

    print("\n" + "="*60)
    print("[SUCCESS] 所有测试通过！")
    print("="*60)
    print("\n总结:")
    print("✅ 禁用异步时：不包含异步说明")
    print("✅ 启用异步时：自动追加异步使用指南")
    print("✅ 向后兼容：不传 context 时保持原有行为")
    print("\n现在 Supervisor 会根据配置动态调整提示词！")
