#!/usr/bin/env python3
"""Simple test for async prompt append functionality"""

import os
from dotenv import load_dotenv
from src.common.context import Context
from src.supervisor_agent.prompts import (
    get_supervisor_system_prompt,
    SUPERVISOR_SYSTEM_PROMPT,
    V3PLUS_ASYNC_INSTRUCTIONS,
)

load_dotenv()


def test_base_prompt():
    """Test that base prompt doesn't contain async content"""
    print("\n" + "="*60)
    print("Test 1: Base Prompt")
    print("="*60)

    print(f"\nBase prompt length: {len(SUPERVISOR_SYSTEM_PROMPT)}")
    print(f"Contains 'async': {'async' in SUPERVISOR_SYSTEM_PROMPT.lower()}")
    print(f"Contains 'call_executor_async': {'call_executor_async' in SUPERVISOR_SYSTEM_PROMPT}")

    assert "async" not in SUPERVISOR_SYSTEM_PROMPT.lower(), "Base prompt should not contain 'async'"
    assert "call_executor_async" not in SUPERVISOR_SYSTEM_PROMPT, "Base prompt should not mention async tools"

    print("[OK] Base prompt is clean")


def test_async_instructions():
    """Test that async instructions contain the right content"""
    print("\n" + "="*60)
    print("Test 2: Async Instructions")
    print("="*60)

    print(f"\nAsync instructions length: {len(V3PLUS_ASYNC_INSTRUCTIONS)}")
    print(f"Contains 'call_executor_async': {'call_executor_async' in V3PLUS_ASYNC_INSTRUCTIONS}")
    print(f"Contains 'get_executor_status': {'get_executor_status' in V3PLUS_ASYNC_INSTRUCTIONS}")
    print(f"Contains 'cancel_executor': {'cancel_executor' in V3PLUS_ASYNC_INSTRUCTIONS}")
    print(f"Contains workflow guide: {'Async Execution Workflow' in V3PLUS_ASYNC_INSTRUCTIONS}")

    assert "call_executor_async" in V3PLUS_ASYNC_INSTRUCTIONS
    assert "get_executor_status" in V3PLUS_ASYNC_INSTRUCTIONS
    assert "cancel_executor" in V3PLUS_ASYNC_INSTRUCTIONS

    print("[OK] Async instructions are complete")


def test_prompt_with_current_env():
    """Test prompt using current environment (ENABLE_V3PLUS_ASYNC=true in .env)"""
    print("\n" + "="*60)
    print("Test 3: Prompt with Current Environment")
    print("="*60)

    env_value = os.getenv("ENABLE_V3PLUS_ASYNC", "not set")
    print(f"\nCurrent ENABLE_V3PLUS_ASYNC env: {env_value}")

    # Create context without passing enable_v3plus_async (will read from env)
    ctx = Context()
    print(f"Context enable_v3plus_async: {ctx.enable_v3plus_async}")

    prompt = get_supervisor_system_prompt(ctx)
    print(f"\nPrompt length: {len(prompt)}")
    print(f"Contains async section: {'Asynchronous Concurrent' in prompt}")
    print(f"Contains call_executor_async: {'call_executor_async' in prompt}")

    if ctx.enable_v3plus_async:
        assert "Asynchronous Concurrent" in prompt, "Should contain async section when enabled"
        assert "call_executor_async" in prompt, "Should mention async tools"
        print("[OK] Async mode correctly adds instructions")
    else:
        print("[OK] Async mode disabled (no extra instructions)")


def test_backwards_compatibility():
    """Test backwards compatibility when no context is passed"""
    print("\n" + "="*60)
    print("Test 4: Backwards Compatibility")
    print("="*60)

    # Call without context parameter
    prompt = get_supervisor_system_prompt()

    print(f"\nPrompt length: {len(prompt)}")
    print(f"Matches base prompt: {prompt == SUPERVISOR_SYSTEM_PROMPT}")
    print(f"Contains async: {'async' in prompt.lower()}")

    assert prompt == SUPERVISOR_SYSTEM_PROMPT, "Without context, should return base prompt"
    assert "async" not in prompt.lower(), "Should not contain async without context"

    print("[OK] Backwards compatibility maintained")


if __name__ == "__main__":
    print("\n[TEST] Async Prompt Append - Simple Tests")
    print("="*60)

    test_base_prompt()
    test_async_instructions()
    test_prompt_with_current_env()
    test_backwards_compatibility()

    print("\n" + "="*60)
    print("[SUCCESS] All tests passed!")
    print("="*60)
    print("\nSummary:")
    print("- Base prompt is clean (no async content)")
    print("- Async instructions are complete")
    print("- When ENABLE_V3PLUS_ASYNC=true, async instructions are appended")
    print("- Backwards compatible (no context = base prompt)")
    print("\nSupervisor will now know how to use async tools!")
