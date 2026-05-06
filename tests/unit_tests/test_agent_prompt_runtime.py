"""验证 Executor 和 Planner 提示词包含运行时行为说明。"""

from src.executor_agent.prompts import get_executor_system_prompt, get_reflection_system_prompt
from src.planner_agent.prompts import get_planner_system_prompt


class TestExecutorPromptRuntimeBehavior:
    """Executor 提示词必须包含运行时行为说明。"""

    def test_mentions_observation_truncation(self):
        prompt = get_executor_system_prompt("测试能力")
        assert "截断" in prompt or "truncat" in prompt.lower()

    def test_mentions_offload(self):
        prompt = get_executor_system_prompt("测试能力")
        assert "外置" in prompt or "offload" in prompt.lower()

    def test_mentions_interrupt(self):
        prompt = get_executor_system_prompt("测试能力")
        assert "INTERRUPT" in prompt or "中断" in prompt or "停止执行" in prompt

    def test_interrupt_guidance_present(self):
        prompt = get_executor_system_prompt("测试能力")
        assert "INTERRUPT" in prompt

    def test_reflection_prompt_exists(self):
        prompt = get_reflection_system_prompt()
        assert "paused" in prompt
        assert "snapshot" in prompt


class TestPlannerPromptRuntimeBehavior:
    """Planner 提示词必须包含截断说明。"""

    def test_mentions_truncation(self):
        prompt = get_planner_system_prompt("测试能力")
        assert "截断" in prompt

    def test_still_has_readonly_tools(self):
        prompt = get_planner_system_prompt("测试能力")
        assert "read_workspace_text_file" in prompt
        assert "grep_content" in prompt

    def test_still_has_plan_json_format(self):
        prompt = get_planner_system_prompt("测试能力")
        assert "step_id" in prompt
        assert "intent" in prompt
        assert "expected_output" in prompt
