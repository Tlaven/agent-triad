from langchain_core.messages import AIMessage

from src.supervisor_agent.graph import _infer_supervisor_decision


def test_infer_supervisor_decision_mode_1_without_tools() -> None:
    decision = _infer_supervisor_decision(AIMessage(content="直接回答"))
    assert decision.mode == 1
    assert decision.confidence > 0


def test_infer_supervisor_decision_mode_3_with_call_planner() -> None:
    msg = AIMessage(
        content="",
        tool_calls=[{"name": "call_planner", "args": {}, "id": "1", "type": "tool_call"}],
    )
    decision = _infer_supervisor_decision(msg)
    assert decision.mode == 3


def test_infer_supervisor_decision_mode_2_with_call_executor() -> None:
    msg = AIMessage(
        content="",
        tool_calls=[{"name": "call_executor", "args": {}, "id": "1", "type": "tool_call"}],
    )
    decision = _infer_supervisor_decision(msg)
    assert decision.mode == 2
