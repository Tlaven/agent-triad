import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from dotenv import load_dotenv
from langchain_core.messages import AIMessage

ROOT = Path(__file__).resolve().parent.parent
root_str = str(ROOT)
if root_str not in sys.path:
    sys.path.insert(0, root_str)

# Load .env so that E2E tests can access real API keys
load_dotenv(ROOT / ".env", override=False)


# ---------------------------------------------------------------------------
# Context fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_context():
    """A Context instance with test-friendly limits (fast, low iteration caps)."""
    from src.common.context import Context
    return Context(max_replan=2, max_executor_iterations=5)


@pytest.fixture
def default_context():
    """A Context instance with default settings."""
    from src.common.context import Context
    return Context()


# ---------------------------------------------------------------------------
# Sample plan JSON fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_plan_json():
    """A minimal pending plan JSON string."""
    return json.dumps({
        "plan_id": "plan_test0001",
        "version": 1,
        "goal": "测试目标",
        "steps": [
            {
                "step_id": "step_1",
                "intent": "执行第一步",
                "expected_output": "第一步完成",
                "status": "pending",
                "result_summary": None,
                "failure_reason": None,
            }
        ],
    }, ensure_ascii=False, indent=2)


@pytest.fixture
def sample_completed_plan_json():
    """A plan JSON string with all steps completed."""
    return json.dumps({
        "plan_id": "plan_test0001",
        "version": 1,
        "goal": "测试目标",
        "steps": [
            {
                "step_id": "step_1",
                "intent": "执行第一步",
                "expected_output": "第一步完成",
                "status": "completed",
                "result_summary": "步骤1已成功完成",
                "failure_reason": None,
            }
        ],
    }, ensure_ascii=False, indent=2)


@pytest.fixture
def sample_failed_plan_json():
    """A plan JSON string with the first step failed."""
    return json.dumps({
        "plan_id": "plan_test0001",
        "version": 1,
        "goal": "测试目标",
        "steps": [
            {
                "step_id": "step_1",
                "intent": "执行第一步",
                "expected_output": "第一步完成",
                "status": "failed",
                "result_summary": None,
                "failure_reason": "工具调用超时",
            }
        ],
    }, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# Executor result content fixtures (EXECUTOR_RESULT wire format)
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_executor_result_completed(sample_completed_plan_json):
    """Full ToolMessage content string for a completed executor run."""
    summary = "所有步骤执行完成"
    meta = {
        "status": "completed",
        "error_detail": None,
        "updated_plan_json": sample_completed_plan_json,
    }
    return f"{summary}\n\n[EXECUTOR_RESULT] {json.dumps(meta, ensure_ascii=False)}"


@pytest.fixture
def sample_executor_result_failed(sample_failed_plan_json):
    """Full ToolMessage content string for a failed executor run."""
    summary = "执行失败：工具调用超时"
    meta = {
        "status": "failed",
        "error_detail": "工具调用超时",
        "updated_plan_json": sample_failed_plan_json,
    }
    return f"{summary}\n\n[EXECUTOR_RESULT] {json.dumps(meta, ensure_ascii=False)}"


# ---------------------------------------------------------------------------
# Mock LLM factory
# ---------------------------------------------------------------------------

def make_mock_llm(responses: list[AIMessage]) -> MagicMock:
    """Return a mock LLM that returns `responses` sequentially on ainvoke() calls.

    The mock also supports `.bind_tools()` (returns itself) so it can be used
    directly as a drop-in for `load_chat_model(...)`.
    """
    mock = MagicMock()
    mock.bind_tools = MagicMock(return_value=mock)
    mock.ainvoke = AsyncMock(side_effect=list(responses))
    return mock


@pytest.fixture
def make_mock_llm_fixture():
    """Fixture version of make_mock_llm factory."""
    return make_mock_llm
