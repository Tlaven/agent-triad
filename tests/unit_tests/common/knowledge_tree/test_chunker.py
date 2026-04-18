"""Chunker 测试。"""

from src.common.knowledge_tree.ingestion.chunker import (
    _estimate_tokens,
    chunk_conversation,
    chunk_text,
)


class TestEstimateTokens:
    def test_english_text(self):
        assert _estimate_tokens("hello world") > 0

    def test_chinese_text(self):
        # 中文 1.5 字/token，所以估算为 len * 0.67
        text = "这是一段中文文本"
        result = _estimate_tokens(text)
        assert result == int(len(text) * 0.67)

    def test_empty_text(self):
        assert _estimate_tokens("") == 0


class TestChunkText:
    def test_single_paragraph(self):
        result = chunk_text("这是一段短文本。")
        assert result == ["这是一段短文本。"]

    def test_split_on_double_newline(self):
        text = "第一段内容，" * 30 + "\n\n" + "第二段内容，" * 30 + "\n\n" + "第三段内容，" * 30
        result = chunk_text(text, max_tokens=50)
        # 长段落不会被合并
        assert len(result) >= 2

    def test_merge_short_chunks(self):
        chunks = "短1\n\n短2\n\n短3"
        result = chunk_text(chunks, max_tokens=1000)
        # 短片段应该合并
        assert len(result) == 1

    def test_empty_input(self):
        assert chunk_text("") == []
        assert chunk_text("   ") == []

    def test_max_tokens_respected(self):
        # 生成一个超长文本
        long_text = "内容。" * 500  # ~1000 字
        result = chunk_text(long_text, max_tokens=100)
        for chunk in result:
            assert _estimate_tokens(chunk) <= 100

    def test_single_line_no_newlines(self):
        result = chunk_text("这是一行没有换行的文本内容。")
        assert result == ["这是一行没有换行的文本内容。"]


class TestChunkConversation:
    def test_single_message(self):
        messages = [{"role": "user", "content": "你好"}]
        result = chunk_conversation(messages)
        assert len(result) == 1
        assert "[user]" in result[0]

    def test_multiple_messages(self):
        messages = [
            {"role": "user", "content": "你好"},
            {"role": "assistant", "content": "你好！"},
        ]
        result = chunk_conversation(messages, max_tokens=10000)
        # 短消息合并
        assert len(result) == 1

    def test_empty_messages(self):
        assert chunk_conversation([]) == []

    def test_skips_empty_content(self):
        messages = [
            {"role": "user", "content": "有内容"},
            {"role": "assistant", "content": ""},
        ]
        result = chunk_conversation(messages)
        # 只有非空内容被包含
        assert len(result) == 1
        assert "[user]" in result[0]

    def test_long_conversation_splits(self):
        messages = [
            {"role": "user", "content": f"消息{i}，" * 50}
            for i in range(10)
        ]
        result = chunk_conversation(messages, max_tokens=100)
        assert len(result) > 1
