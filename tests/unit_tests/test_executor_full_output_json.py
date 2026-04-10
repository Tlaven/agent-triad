"""测试 get_executor_full_output 工具返回 JSON 格式"""
import json
import pytest
from src.supervisor_agent.graph import _build_executor_full_output


def test_executor_full_output_returns_json():
    """测试：_build_executor_full_output 应该返回有效的 JSON 字符串"""
    # 模拟 Executor 达到最大步数的情况
    summary = "已达到最大执行步数限制，无法继续调用工具。根据已有信息输出执行摘要。"
    status = "failed"
    error_detail = "达到最大迭代次数"
    updated_plan_json = None
    snapshot_json = None

    result = _build_executor_full_output(
        summary, status, error_detail, updated_plan_json, snapshot_json
    )

    # 验证返回的是有效的 JSON
    try:
        parsed = json.loads(result)
        print("[OK] 返回的是有效的 JSON")
    except json.JSONDecodeError as e:
        pytest.fail(f"返回的不是有效的 JSON: {e}\n原始内容:\n{result}")

    # 验证 JSON 结构
    assert "status" in parsed, "JSON 应该包含 status 字段"
    assert parsed["status"] == status
    assert "summary" in parsed, "JSON 应该包含 summary 字段"
    assert parsed["summary"] == summary
    assert "error_detail" in parsed, "JSON 应该包含 error_detail 字段"
    assert parsed["error_detail"] == error_detail

    print(f"[OK] JSON 结构正确: {json.dumps(parsed, ensure_ascii=False, indent=2)}")


def test_executor_full_output_with_steps():
    """测试：包含步骤信息的完整输出"""
    summary = "任务执行完成"
    status = "completed"
    error_detail = None

    # 模拟带步骤的计划
    updated_plan_json = json.dumps({
        "plan_id": "test_plan",
        "steps": [
            {
                "step_id": "step_1",
                "intent": "创建文件",
                "status": "completed",
                "result_summary": "文件创建成功",
                "failure_reason": None
            },
            {
                "step_id": "step_2",
                "intent": "执行命令",
                "status": "failed",
                "result_summary": None,
                "failure_reason": "命令未找到"
            }
        ]
    }, ensure_ascii=False)

    result = _build_executor_full_output(
        summary, status, error_detail, updated_plan_json, None
    )

    # 验证返回的是有效的 JSON
    parsed = json.loads(result)

    # 验证步骤信息
    assert "steps" in parsed, "JSON 应该包含 steps 字段"
    assert len(parsed["steps"]) == 2, "应该包含 2 个步骤"

    # 验证第一个步骤
    step_1 = parsed["steps"][0]
    assert step_1["step_id"] == "step_1"
    assert step_1["status"] == "completed"
    assert step_1["result_summary"] == "文件创建成功"

    # 验证第二个步骤
    step_2 = parsed["steps"][1]
    assert step_2["step_id"] == "step_2"
    assert step_2["status"] == "failed"
    assert step_2["failure_reason"] == "命令未找到"

    print(f"[OK] 包含步骤信息的 JSON:\n{json.dumps(parsed, ensure_ascii=False, indent=2)}")


def test_executor_full_output_with_snapshot():
    """测试：包含 Reflection 快照的完整输出"""
    summary = "执行到检查点"
    status = "paused"
    error_detail = None

    # 模拟 Reflection 快照
    snapshot_json = json.dumps({
        "trigger_type": "interval",
        "current_step": "step_2",
        "confidence_score": 0.5,
        "reflection_analysis": "任务可能偏离目标",
        "suggestion": "replan",
        "progress_summary": "已完成 2/5 步骤"
    }, ensure_ascii=False)

    result = _build_executor_full_output(
        summary, status, error_detail, None, snapshot_json
    )

    # 验证返回的是有效的 JSON
    parsed = json.loads(result)

    # 验证快照信息
    assert "snapshot" in parsed, "JSON 应该包含 snapshot 字段"
    assert parsed["snapshot"]["trigger_type"] == "interval"
    assert parsed["snapshot"]["confidence_score"] == 0.5
    assert parsed["snapshot"]["suggestion"] == "replan"

    print(f"[OK] 包含快照的 JSON:\n{json.dumps(parsed, ensure_ascii=False, indent=2)}")


def test_executor_full_output_no_markdown():
    """测试：确保返回的内容不包含 Markdown 格式"""
    summary = "测试摘要"
    status = "completed"
    error_detail = None
    updated_plan_json = None
    snapshot_json = None

    result = _build_executor_full_output(
        summary, status, error_detail, updated_plan_json, snapshot_json
    )

    # 验证不包含 Markdown 标记
    assert "##" not in result, "不应该包含 Markdown 标题标记 (##)"
    assert "###" not in result, "不应该包含 Markdown 子标题标记 (###)"
    assert "**" not in result, "不应该包含 Markdown 粗体标记 (**)"

    # 验证是 JSON
    parsed = json.loads(result)
    assert parsed["summary"] == summary

    print("[OK] 不包含 Markdown 格式标记")


if __name__ == "__main__":
    print("=" * 70)
    print("测试 _build_executor_full_output 返回 JSON 格式")
    print("=" * 70)

    print("\n【测试 1】基本 JSON 格式")
    print("-" * 70)
    test_executor_full_output_returns_json()

    print("\n【测试 2】包含步骤信息")
    print("-" * 70)
    test_executor_full_output_with_steps()

    print("\n【测试 3】包含 Reflection 快照")
    print("-" * 70)
    test_executor_full_output_with_snapshot()

    print("\n【测试 4】不包含 Markdown 格式")
    print("-" * 70)
    test_executor_full_output_no_markdown()

    print("\n" + "=" * 70)
    print("[SUCCESS] 所有测试通过！get_executor_full_output 现在返回 JSON")
    print("=" * 70)
