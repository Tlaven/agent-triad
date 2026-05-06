"""验证 Supervisor 系统提示词包含知识树认知段和执行状态处理。"""

from src.common.context import Context
from src.supervisor_agent.prompts import get_supervisor_system_prompt


class TestSupervisorPromptKTIntegration:
    """系统提示词必须包含知识树相关指导。"""

    def test_prompt_mentions_knowledge_tree(self):
        prompt = get_supervisor_system_prompt()
        assert "知识树" in prompt or "Knowledge Tree" in prompt

    def test_prompt_explains_auto_inject_source(self):
        """提示词必须说明 [相关知识] 不是用户输入，而是记忆系统注入。"""
        prompt = get_supervisor_system_prompt()
        assert "[相关知识]" in prompt
        assert "不是用户说的" in prompt or "非用户输入" in prompt

    def test_prompt_lists_kt_tools(self):
        """提示词必须列出 4 个 KT 工具及其用途。"""
        prompt = get_supervisor_system_prompt()
        assert "knowledge_tree_retrieve" in prompt
        assert "knowledge_tree_ingest" in prompt
        assert "knowledge_tree_status" in prompt
        assert "knowledge_tree_list" in prompt

    def test_prompt_explains_quality_tags(self):
        """提示词必须解释 [高可信] 和 [参考] 标记的含义。"""
        prompt = get_supervisor_system_prompt()
        assert "高可信" in prompt
        assert "参考" in prompt
        assert "0.7" in prompt or "≥0.7" in prompt

    def test_prompt_guides_when_to_ingest(self):
        """提示词必须说明何时使用 ingest。"""
        prompt = get_supervisor_system_prompt()
        assert "记住" in prompt or "ingest" in prompt.lower()

    def test_prompt_with_kt_disabled(self):
        """即使 KT 关闭，提示词仍包含 KT 指导（工具不会注册，但 Agent 知道概念）。"""
        ctx = Context(enable_knowledge_tree=False)
        prompt = get_supervisor_system_prompt(ctx)
        assert "知识树" in prompt


class TestSupervisorPromptExecutorStatusHandling:
    """系统提示词必须指导 Supervisor 处理 Executor 的三种返回状态。"""

    def test_prompt_mentions_completed_status(self):
        prompt = get_supervisor_system_prompt()
        assert "completed" in prompt

    def test_prompt_mentions_failed_status(self):
        prompt = get_supervisor_system_prompt()
        assert "failed" in prompt

    def test_prompt_mentions_paused_status(self):
        """提示词必须解释 paused 状态的含义。"""
        prompt = get_supervisor_system_prompt()
        assert "paused" in prompt

    def test_prompt_guides_paused_decision(self):
        """提示词必须指导 Supervisor 在 paused 时做出决策（继续/重规划/终止）。"""
        prompt = get_supervisor_system_prompt()
        assert "继续执行" in prompt or "continue" in prompt.lower()
        assert "重规划" in prompt or "replan" in prompt.lower()

    def test_prompt_explains_snapshot(self):
        """提示词必须解释 Checkpoint 快照的含义。"""
        prompt = get_supervisor_system_prompt()
        assert "快照" in prompt or "snapshot" in prompt.lower() or "Checkpoint" in prompt

    def test_prompt_explains_replan_limit(self):
        """提示词必须说明重规划次数限制。"""
        prompt = get_supervisor_system_prompt()
        assert "重规划" in prompt
        assert "2" in prompt  # 最大 2 次


class TestSupervisorPromptAsyncHonesty:
    """异步派发说明必须诚实——不承诺无法兑现的并行执行。"""

    def test_prompt_mentions_async_dispatch(self):
        """提示词应该包含异步派发说明。"""
        prompt = get_supervisor_system_prompt()
        assert "wait_for_result" in prompt or "异步" in prompt

    def test_prompt_does_not_promise_parallel_group_split(self):
        """提示词不应承诺自动拆分 parallel_group。"""
        prompt = get_supervisor_system_prompt()
        # 不应包含"将同组步骤拆为独立子任务"这种承诺
        assert "拆为独立子任务" not in prompt
