"""Filter 测试。"""

from src.common.knowledge_tree.ingestion.filter import should_remember


class TestShouldRemember:
    def test_user_explicit_always_passes(self):
        result = should_remember("任何内容", trigger="user_explicit")
        assert result.passed is True
        assert result.confidence == 1.0

    def test_task_complete_always_passes(self):
        result = should_remember("任务完成了", trigger="task_complete")
        assert result.passed is True
        assert result.confidence == 0.9

    def test_decision_keyword_passes(self):
        result = should_remember("发现了一个重要的规则需要记住。")
        assert result.passed is True
        assert "keyword:" in result.reason

    def test_conclusion_keyword(self):
        result = should_remember("得出的结论是系统需要重构。")
        assert result.passed is True

    def test_experience_keyword(self):
        result = should_remember("这次的经验值得记录下来。")
        assert result.passed is True

    def test_has_number_passes(self):
        result = should_remember("系统有3个主要组件。")
        assert result.passed is True
        assert result.reason == "has_number"

    def test_sufficient_length_passes(self):
        result = should_remember("这是一段足够长的文本，包含了一些信息但没有什么特别的关键词和数字，仅仅是一段普通的描述性文字而已，用来测试长度阈值。")
        assert result.passed is True
        assert result.reason == "sufficient_length"

    def test_short_no_keywords_fails(self):
        result = should_remember("好的")
        assert result.passed is False
        assert result.reason == "too_short_no_keyword"

    def test_empty_fails(self):
        result = should_remember("")
        assert result.passed is False
        assert result.reason == "empty_chunk"

    def test_whitespace_only_fails(self):
        result = should_remember("   \n  ")
        assert result.passed is False

    def test_low_confidence_for_numbers(self):
        result = should_remember("第3步")
        assert result.passed is True
        assert result.confidence == 0.5

    def test_high_confidence_for_explicit(self):
        result = should_remember("记住这个重要决定", trigger="user_explicit")
        assert result.confidence == 1.0
