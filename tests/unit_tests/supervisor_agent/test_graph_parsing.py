from src.supervisor_agent.graph import (
    _extract_executor_status,
    _extract_updated_plan_from_executor,
)


def test_extract_updated_plan_from_executor_success() -> None:
    content = (
        "executor done\n\n"
        '[EXECUTOR_RESULT] {"status":"completed","updated_plan_json":"{\\"steps\\": []}"}'
    )
    assert _extract_updated_plan_from_executor(content) == '{"steps": []}'


def test_extract_executor_status_success() -> None:
    content = (
        "executor failed\n\n"
        '[EXECUTOR_RESULT] {"status":"failed","error_detail":"tool timeout"}'
    )
    assert _extract_executor_status(content) == ("failed", "tool timeout")


def test_extract_executor_status_invalid_json() -> None:
    content = "xxx [EXECUTOR_RESULT] {not json}"
    assert _extract_executor_status(content) == (None, None)
