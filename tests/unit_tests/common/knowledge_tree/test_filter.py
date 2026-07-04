"""Filter 测试：覆盖基础场景 + 生产边界条件。

边界条件分类：
- 代码块：```python ... ``` 包裹的技术文本
- 混合语言：中英混合的技术描述
- 超长文本：5000+ 字符
- URL/路径：包含 URL 或文件路径的文本
- 纯代码：无自然语言的代码片段
- 通用模板变体：试图绕过过滤的变体
"""

from src.common.knowledge_tree.ingestion.filter import should_remember


class TestShouldRemember:
    def test_user_explicit_always_passes(self):
        result = should_remember("任何内容", trigger="user_explicit")
        assert result.passed is True
        assert result.confidence == 1.0

    def test_task_complete_with_substance_passes(self):
        result = should_remember("发现任务完成中存在超时配置的问题需要修复", trigger="task_complete")
        assert result.passed is True

    def test_decision_keyword_passes(self):
        result = should_remember("发现了一个重要的规则需要记住。")
        assert result.passed is True
        assert "keyword" in result.reason

    def test_conclusion_keyword(self):
        result = should_remember("得出的结论是系统需要重构。")
        assert result.passed is True

    def test_experience_keyword(self):
        result = should_remember("这次的经验值得记录下来。")
        assert result.passed is True

    def test_has_number_passes(self):
        result = should_remember("系统有3个主要组件，分别是 Supervisor、Planner 和 Executor。")
        assert result.passed is True

    def test_sufficient_length_passes(self):
        text = "这是一段足够长的文本，没有任何决策关键词和数字。" * 5  # > 100 chars
        result = should_remember(text)
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
        result = should_remember("第3步执行发现错误")
        assert result.passed is True
        assert result.confidence >= 0.5

    def test_high_confidence_for_explicit(self):
        result = should_remember("记住这个重要决定", trigger="user_explicit")
        assert result.confidence == 1.0


# ---------------------------------------------------------------------------
# 边界条件：代码块
# ---------------------------------------------------------------------------


class TestFilterCodeBlocks:
    """包含代码块的文本过滤。"""

    def test_code_block_with_explanation_passes(self):
        """有自然语言说明的代码块应通过（含关键词/长度）。"""
        text = (
            "发现错误处理模式：使用 try/except 包裹外部调用。\n"
            "```python\n"
            "try:\n"
            "    result = external_api.call()\n"
            "except TimeoutError:\n"
            "    logger.error('API timeout')\n"
            "```\n"
            "这个模式确保超时不会导致整个流程崩溃。"
        )
        result = should_remember(text, trigger="task_complete")
        assert result.passed is True

    def test_pure_code_no_natural_language(self):
        """纯代码无自然语言，通过技术内容模式。"""
        text = (
            "def process(data):\n"
            "    for item in data:\n"
            "        result = transform(item)\n"
            "        if result.is_valid():\n"
            "            yield result\n"
            "        else:\n"
            "            continue\n"
        )
        result = should_remember(text, trigger="")
        assert result.passed is True

    def test_inline_code_with_numbers(self):
        """含内联代码和数字的文本应通过。"""
        text = "配置项 `executor_call_model_timeout=180` 和 `executor_tool_timeout=300`。"
        result = should_remember(text, trigger="")
        assert result.passed is True
        assert result.reason == "has_number"

    def test_code_block_with_error_keyword(self):
        """含错误关键词的代码块应通过。"""
        text = (
            "常见超时错误处理：\n"
            "```python\n"
            "try:\n"
            "    await asyncio.wait_for(coro, timeout=30)\n"
            "except asyncio.TimeoutError:\n"
            "    raise ExecutorTimeoutError()\n"
            "```"
        )
        result = should_remember(text, trigger="")
        assert result.passed is True
        assert "keyword" in result.reason


# ---------------------------------------------------------------------------
# 边界条件：混合语言
# ---------------------------------------------------------------------------


class TestFilterMixedLanguage:
    """中英混合文本过滤。"""

    def test_chinese_with_english_terms(self):
        """中文描述夹杂英文术语，通过技术内容模式。"""
        text = "Executor 子进程使用 FastAPI 启动 HTTP server，监听动态分配的 port。"
        result = should_remember(text, trigger="")
        assert result.passed is True

    def test_english_with_chinese_context(self):
        """英文内容带中文上下文应通过。"""
        text = "Important: asyncio.wait_for() 在 Windows 上使用 ProactorEventLoop，行为与 Linux 不同。"
        result = should_remember(text, trigger="")
        assert result.passed is True

    def test_pure_english_no_trigger(self):
        """纯英文短文本无 trigger 应被过滤。"""
        text = "ok done"
        result = should_remember(text, trigger="")
        assert result.passed is False

    def test_english_with_number(self):
        """纯英文含数字应通过。"""
        text = "The default timeout is 180 seconds for LLM calls."
        result = should_remember(text, trigger="")
        assert result.passed is True
        assert result.reason == "has_number"

    def test_chinese_english_code_mix(self):
        """中英代码混合文本应通过（关键词 + 长度）。"""
        text = (
            "在 Supervisor graph.py 中使用 ReAct 模式：\n"
            "每轮 call_model 后检查 tool_calls，如果有则调用 dynamic_tools_node。\n"
            "循环直到不再有 tool_calls 或达到最大轮数。"
        )
        result = should_remember(text, trigger="")
        assert result.passed is True


# ---------------------------------------------------------------------------
# 边界条件：超长文本
# ---------------------------------------------------------------------------


class TestFilterLongText:
    """超长文本过滤。"""

    def test_very_long_text_passes(self):
        """5000+ 字符文本应通过（含关键词）。"""
        text = "重要发现：系统架构设计原则。 " * 200  # ~4800 字符
        result = should_remember(text, trigger="")
        assert result.passed is True

    def test_long_text_without_keywords_or_numbers(self):
        """无关键词无数字的长文本应通过 sufficient_length (> 100)。"""
        text = ("这是一段很长的描述性文字，没有特别的决策关键词，" * 10)
        result = should_remember(text, trigger="")
        assert result.passed is True
        assert result.reason == "sufficient_length"

    def test_long_text_with_task_complete_high_confidence(self):
        """task_complete 触发的长文本应有合理置信度。"""
        text = "A" * 1000
        result = should_remember(text, trigger="task_complete")
        assert result.passed is True
        assert result.confidence >= 0.6


# ---------------------------------------------------------------------------
# 边界条件：URL 和文件路径
# ---------------------------------------------------------------------------


class TestFilterURLsAndPaths:
    """包含 URL 或文件路径的文本。"""

    def test_text_with_url_passes(self):
        """包含 URL 的文本应通过（有数字/点号）。"""
        text = "API 文档地址：https://docs.example.com/api/v2/endpoints"
        result = should_remember(text, trigger="")
        assert result.passed is True
        assert result.reason == "has_number"

    def test_text_with_file_path_passes(self):
        """包含文件路径的文本应通过（有数字）。"""
        text = "配置文件位于 src/common/context.py，定义了所有超时参数。"
        result = should_remember(text, trigger="")
        assert result.passed is True

    def test_pure_url(self):
        """纯 URL 短文本（无自然语言）行为。"""
        text = "https://github.com/example/repo/issues/123"
        result = should_remember(text, trigger="")
        # 有数字，通过
        assert result.passed is True
        assert result.reason == "has_number"


# ---------------------------------------------------------------------------
# 边界条件：通用模板变体
# ---------------------------------------------------------------------------


class TestFilterGenericVariants:
    """通用模板文本的各种变体。"""

    def test_all_generic_patterns_filtered(self):
        """所有已知的通用模板模式应被过滤。"""
        generics = [
            "所有步骤执行完成",
            "执行成功",
            "任务完成",
            "已完成",
            "步骤1已成功完成",
            "步骤99已成功完成",
        ]
        for text in generics:
            result = should_remember(text, trigger="task_complete")
            assert result.passed is False, (
                f"'{text}' should be filtered as generic, got {result}"
            )
            assert result.reason == "generic_template"

    def test_generic_with_task_complete_still_filtered(self):
        """即使是 task_complete 触发，通用模板也应被过滤。"""
        result = should_remember("所有步骤执行完成", trigger="task_complete")
        assert result.passed is False
        assert result.reason == "generic_template"

    def test_similar_but_not_generic_passes(self):
        """与通用模板相似但有信息量的文本应通过。"""
        texts = [
            "所有步骤执行完成，但在 step_3 发现了编码问题",
            "执行成功，新增了 5 个重要配置项",
            "任务完成，发现覆盖率提升到 92%",
        ]
        for text in texts:
            result = should_remember(text, trigger="task_complete")
            assert result.passed is True, (
                f"'{text}' should pass, got {result}"
            )


# ---------------------------------------------------------------------------
# 边界条件：特殊字符和格式
# ---------------------------------------------------------------------------


class TestFilterSpecialContent:
    """特殊字符和格式的文本。"""

    def test_json_content_passes(self):
        """JSON 格式内容应通过（有数字/长度）。"""
        text = '{"timeout": 180, "max_retries": 3, "base_url": "http://localhost:2024"}'
        result = should_remember(text, trigger="")
        assert result.passed is True

    def test_markdown_formatted_text(self):
        """Markdown 格式文本应通过。"""
        text = (
            "## 配置说明\n"
            "- `LLM_API_KEY`: **必须**设置\n"
            "- `LLM_BASE_URL`: 可选，默认 OpenAI\n"
            "- `executor_call_model_timeout`: 默认 180s"
        )
        result = should_remember(text, trigger="")
        assert result.passed is True

    def test_newline_heavy_text(self):
        """多换行文本（去掉空白后仍应有内容）。"""
        text = "重要\n\n\n\n\n\n决定\n\n\n\n使用异步模式"
        result = should_remember(text, trigger="")
        assert result.passed is True
        assert "keyword" in result.reason

    def test_unicode_special_chars(self):
        """含 Unicode 特殊字符的文本。"""
        text = "注意：路径中不能包含 → 特殊字符，如 ★ 或 ●"
        result = should_remember(text, trigger="")
        assert result.passed is True

    def test_tab_separated_values(self):
        """Tab 分隔的值。"""
        text = "step_1\t添加配置\tcompleted\texecutor_call_model_timeout=180"
        result = should_remember(text, trigger="")
        assert result.passed is True


class TestInfraErrorPreFilter:
    """基础设施错误文本应在 task_complete 路径前置 reject（user_explicit 仍通过）。

    过滤插入位置必须在 user_explicit 早返回之后，确保用户显式覆盖权。
    """

    def test_blocking_error_rejected_task_complete(self):
        text = "Executor 启动失败：BlockingError: ... raised in scope, would block"
        result = should_remember(text, trigger="task_complete")
        assert result.passed is False
        assert result.reason == "infra_error"

    def test_magicmock_rejected_task_complete(self):
        text = "TypeError: object MagicMock can't be used in 'await' expression"
        result = should_remember(text, trigger="task_complete")
        assert result.passed is False
        assert result.reason == "infra_error"

    def test_traceback_rejected_task_complete(self):
        text = "Traceback (most recent call last): File src/foo.py line 42"
        result = should_remember(text, trigger="task_complete")
        assert result.passed is False
        assert result.reason == "infra_error"

    def test_infra_error_user_explicit_still_passes(self):
        """用户显式指令仍通过（覆盖权高于过滤）。"""
        text = "记录这场 BlockingError 教训：os.getcwd 阻塞"
        result = should_remember(text, trigger="user_explicit")
        assert result.passed is True
        assert result.reason == "user_explicit"

    def test_legit_business_failure_still_passes(self):
        """合法业务失败文本（无 infra 关键词）仍走原 keyword 路径通过。"""
        text = "任务失败：端口 8080 冲突导致服务未启动，需要在部署脚本前置端口检测。"
        result = should_remember(text, trigger="task_complete")
        assert result.passed is True
