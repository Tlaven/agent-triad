from langchain_core.messages import AIMessage

from src.supervisor_agent.graph import _infer_supervisor_decision


def test_infer_supervisor_decision_mode_1_without_tools() -> None:
    decision = _infer_supervisor_decision(AIMessage(content="直接回答"))
    assert decision.mode == 1
    assert decision.confidence > 0


def test_infer_supervisor_decision_mode_3_with_generate_plan() -> None:
    msg = AIMessage(
        content="",
        tool_calls=[{"name": "generate_plan", "args": {}, "id": "1", "type": "tool_call"}],
    )
    decision = _infer_supervisor_decision(msg)
    assert decision.mode == 3


def test_infer_supervisor_decision_mode_2_with_execute_plan() -> None:
    msg = AIMessage(
        content="",
        tool_calls=[{"name": "execute_plan", "args": {}, "id": "1", "type": "tool_call"}],
    )
    decision = _infer_supervisor_decision(msg)
    assert decision.mode == 2
