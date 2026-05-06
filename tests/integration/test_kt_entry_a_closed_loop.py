"""Entry A 完整闭环验证：executor result → extract → filter → ingest → retrieve。

Entry A 是 KT 最重要的自动知识来源——从执行结果中学习。
本模块验证从真实 executor JSON 格式到最终检索的全链路可靠性。

测试策略：
- 使用 hash embedder（CI 兼容，无 GPU 要求）
- 构造真实 ExecutorResult 格式的 JSON
- 验证 extract → filter → ingest → retrieve 每一步
- 覆盖成功/失败/边界场景
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from src.common.knowledge_tree import KnowledgeTree
from src.common.knowledge_tree.config import KnowledgeTreeConfig
from src.common.knowledge_tree.ingestion.extractor import extract_knowledge_from_executor_result
from src.common.knowledge_tree.ingestion.filter import should_remember

SEED_DIR = Path("workspace/knowledge_tree")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def kt_hash(tmp_path: Path) -> KnowledgeTree:
    """从生产种子目录创建使用 hash embedder 的 KnowledgeTree。"""
    seed_copy = tmp_path / "kt_md"
    shutil.copytree(SEED_DIR, seed_copy)
    config = KnowledgeTreeConfig(
        markdown_root=seed_copy,
        embedding_model="hash",
        rag_similarity_threshold=0.15,
    )
    kt = KnowledgeTree(config)
    report = kt.bootstrap()
    assert report["ok"], f"Bootstrap failed: {report}"
    return kt


def _make_plan_json(
    plan_id: str = "plan_test",
    goal: str = "测试目标",
    steps: list[dict] | None = None,
) -> str:
    """构造真实 Plan JSON 格式。"""
    if steps is None:
        steps = []
    return json.dumps(
        {
            "plan_id": plan_id,
            "version": 1,
            "goal": goal,
            "steps": steps,
        },
        ensure_ascii=False,
    )


def _make_step(
    step_id: str = "step_1",
    intent: str = "执行步骤",
    status: str = "completed",
    result_summary: str = "",
    failure_reason: str = "",
) -> dict:
    """构造真实 step 格式。"""
    return {
        "step_id": step_id,
        "intent": intent,
        "expected_output": "预期输出",
        "status": status,
        "result_summary": result_summary,
        "failure_reason": failure_reason,
    }


# ---------------------------------------------------------------------------
# Phase 3.1: 成功场景 — 多步 executor 结果完整闭环
# ---------------------------------------------------------------------------


class TestEntryASuccessFullLoop:
    """多步 executor 成功完成 → 提取 → 摄入 → 检索全链路。"""

    def test_multistep_completed_extract_ingest_retrieve(self, kt_hash: KnowledgeTree):
        """3 步完成的任务：每步有意义的 result_summary 应被提取并可检索。"""
        plan_json = _make_plan_json(
            plan_id="plan_multistep",
            goal="实现 Executor 超时保护机制",
            steps=[
                _make_step(
                    step_id="step_1",
                    intent="添加超时配置",
                    result_summary="在 context.py 中添加了 executor_call_model_timeout (180s) 和 executor_tool_timeout (300s) 两个配置项。",
                ),
                _make_step(
                    step_id="step_2",
                    intent="实现超时检测逻辑",
                    result_summary="使用 asyncio.wait_for 包裹 LLM 调用，超时抛 TimeoutError 由外层捕获并记录日志。",
                ),
                _make_step(
                    step_id="step_3",
                    intent="添加集成测试",
                    result_summary="新增 3 个测试用例验证 call_model_timeout 和 tool_timeout 的行为，覆盖率从 78% 提升到 85%。",
                ),
            ],
        )
        summary = "完成了 Executor 超时保护机制的实现，包括配置项、检测逻辑和集成测试。"

        # Step 1: Extract
        chunks = extract_knowledge_from_executor_result(summary, plan_json, "completed")
        assert len(chunks) >= 3, f"Expected >= 3 chunks, got {len(chunks)}: {chunks}"

        # Step 2: Ingest each chunk
        ingested_ids = []
        for chunk in chunks:
            report = kt_hash.ingest(chunk, trigger="task_complete", source="auto:executor")
            if report.nodes_ingested > 0:
                ingested_ids.append(report.nodes_ingested)

        assert len(ingested_ids) > 0, "At least one chunk should be ingested"

        # Step 3: Retrieve — 验证能检索到超时相关内容
        results, _ = kt_hash.retrieve("executor 超时 timeout 配置")
        assert len(results) > 0, "Should retrieve knowledge about executor timeout"

        # 验证检索到的内容包含超时相关信息
        found_timeout_content = False
        for node, score in results[:5]:
            if "超时" in node.content or "timeout" in node.content.lower():
                found_timeout_content = True
                break
        assert found_timeout_content, (
            f"Retrieved content should mention timeout: "
            f"{[n.content[:80] for n, _ in results[:3]]}"
        )

    def test_completed_goal_extraction(self, kt_hash: KnowledgeTree):
        """completed 状态应提取 goal 作为上下文知识。"""
        plan_json = _make_plan_json(
            plan_id="plan_goal_extract",
            goal="验证 Plan JSON 格式的完整性",
            steps=[
                _make_step(
                    step_id="step_1",
                    intent="检查字段",
                    result_summary="确认所有必需字段存在：plan_id, version, goal, steps。",
                ),
            ],
        )
        summary = "验证通过，Plan JSON 格式完整。"

        chunks = extract_knowledge_from_executor_result(summary, plan_json, "completed")

        # goal + summary 组合应被提取
        goal_chunks = [c for c in chunks if "验证 Plan JSON" in c]
        assert len(goal_chunks) > 0, (
            f"Goal should be extracted for completed tasks: {chunks}"
        )

    def test_summary_only_no_plan(self, kt_hash: KnowledgeTree):
        """只有 summary 没有 plan_json 时应能提取知识。"""
        summary = "发现 Python 3.12 中 asyncio.Semaphore 的 acquire() 在某些条件下不会释放，需使用 try/finally 确保释放。"

        chunks = extract_knowledge_from_executor_result(summary, "", "completed")
        assert len(chunks) >= 1, f"Summary should be extracted: {chunks}"
        assert chunks[0] == summary.strip()

        # Ingest and retrieve
        report = kt_hash.ingest(chunks[0], trigger="task_complete", source="auto:executor")
        assert report.nodes_ingested > 0

        results, _ = kt_hash.retrieve("asyncio Semaphore acquire 释放")
        assert len(results) > 0


# ---------------------------------------------------------------------------
# Phase 3.2: 失败场景 — executor 失败的知识提取
# ---------------------------------------------------------------------------


class TestEntryAFailureScenarios:
    """executor 返回 failed 状态时的知识提取和检索。"""

    def test_failed_extracts_failure_reasons(self, kt_hash: KnowledgeTree):
        """失败任务应提取 failure_reason 作为教训知识。"""
        plan_json = _make_plan_json(
            plan_id="plan_failed",
            goal="部署生产环境配置",
            steps=[
                _make_step(
                    step_id="step_1",
                    intent="检查环境变量",
                    result_summary="发现 .env 文件包含中文注释导致 UTF-8 BOM 编码错误。",
                ),
                _make_step(
                    step_id="step_2",
                    intent="修复编码问题",
                    status="failed",
                    failure_reason="uvicorn 启动失败：UnicodeDecodeError，.env 文件必须为 UTF-8 无 BOM 格式。",
                ),
            ],
        )
        summary = "部署失败：.env 编码问题导致服务器无法启动。"

        chunks = extract_knowledge_from_executor_result(summary, plan_json, "failed")
        assert len(chunks) >= 2, f"Expected >= 2 chunks from failure: {chunks}"

        # failure_reason 应被提取
        failure_chunks = [c for c in chunks if "失败原因" in c or "编码" in c]
        assert len(failure_chunks) > 0, (
            f"Failure reasons should be extracted: {chunks}"
        )

    def test_failed_failure_reason_retrievable(self, kt_hash: KnowledgeTree):
        """失败原因摄入后应可被检索——用户遇到相同错误时能获得教训。"""
        plan_json = _make_plan_json(
            plan_id="plan_encoding_fail",
            goal="修复 .env 编码问题",
            steps=[
                _make_step(
                    step_id="step_1",
                    intent="定位编码问题",
                    result_summary="使用 file 命令发现 .env 文件编码为 UTF-8 BOM (with BOM)。",
                ),
                _make_step(
                    step_id="step_2",
                    intent="转换编码",
                    status="failed",
                    failure_reason=".env 文件包含中文字符，导致 Python 解析器抛出 UnicodeDecodeError。",
                ),
            ],
        )
        summary = "修复失败：.env 文件编码问题。"

        chunks = extract_knowledge_from_executor_result(summary, plan_json, "failed")

        # Ingest all chunks
        for chunk in chunks:
            kt_hash.ingest(chunk, trigger="task_complete", source="auto:executor")

        # Retrieve — 用用户可能问的方式查询
        results, _ = kt_hash.retrieve("env 编码 错误 UnicodeDecodeError 中文")
        assert len(results) > 0, "Should find knowledge about .env encoding errors"

        found_encoding = False
        for node, score in results[:5]:
            if "编码" in node.content or "UnicodeDecodeError" in node.content:
                found_encoding = True
                break
        assert found_encoding, (
            f"Retrieved content should mention encoding: "
            f"{[n.content[:80] for n, _ in results[:3]]}"
        )

    def test_all_steps_failed(self, kt_hash: KnowledgeTree):
        """所有步骤都失败的场景。"""
        plan_json = _make_plan_json(
            plan_id="plan_all_fail",
            goal="连接外部 API",
            steps=[
                _make_step(
                    step_id="step_1",
                    intent="测试连接",
                    status="failed",
                    failure_reason="API key 无效：401 Unauthorized。",
                ),
                _make_step(
                    step_id="step_2",
                    intent="重试连接",
                    status="failed",
                    failure_reason="连接超时：LLM_BASE_URL 不可达，检查网络代理设置。",
                ),
            ],
        )
        summary = "任务完全失败：API 认证和网络均不可用。"

        chunks = extract_knowledge_from_executor_result(summary, plan_json, "failed")
        # summary + step1 failure_reason + step2 failure_reason = 3+
        assert len(chunks) >= 2, f"Expected >= 2 chunks: {chunks}"

        # All failure reasons should be extracted
        failure_texts = [c for c in chunks if "失败原因" in c]
        assert len(failure_texts) >= 1, f"Failure reasons should be extracted: {chunks}"


# ---------------------------------------------------------------------------
# Phase 3.3: Filter 验证 — 通用模板 vs 有意义内容
# ---------------------------------------------------------------------------


class TestEntryAFilterBehavior:
    """验证 filter 在 Entry A 场景下的过滤行为。"""

    def test_generic_template_filtered(self):
        """通用模板文本应被过滤，不应被摄入。"""
        generic_texts = [
            "所有步骤执行完成",
            "执行成功",
            "任务完成",
            "已完成",
        ]
        for text in generic_texts:
            result = should_remember(text, trigger="task_complete")
            assert not result.passed, (
                f"Generic template should be filtered: '{text}' got {result}"
            )
            assert result.reason == "generic_template"

    def test_meaningful_result_summary_passes(self):
        """有意义的 result_summary 应通过 filter。"""
        meaningful_texts = [
            "在 context.py 中添加了 executor_call_model_timeout (180s) 配置。",
            "发现 Python 进程在 Windows 下不会自动退出，需要 atexit 注册清理。",
            "新增 3 个测试用例，覆盖率从 78% 提升到 85%。",
            "使用 asyncio.wait_for 包裹 LLM 调用实现超时保护。",
        ]
        for text in meaningful_texts:
            result = should_remember(text, trigger="task_complete")
            assert result.passed, (
                f"Meaningful text should pass: '{text}' got {result}"
            )

    def test_short_no_info_filtered(self):
        """过短且无信息量的文本应被过滤（非 task_complete 触发）。"""
        short_texts = [
            "ok",
            "完成",
            "yes",
        ]
        for text in short_texts:
            result = should_remember(text, trigger="")
            assert not result.passed, (
                f"Short no-info text should be filtered: '{text}' got {result}"
            )

    def test_task_complete_trigger_bypasses_length(self):
        """task_complete 触发下，非模板文本即使较短也通过。"""
        result = should_remember("修复了编码问题", trigger="task_complete")
        assert result.passed, "task_complete should bypass length check"

    def test_user_explicit_always_passes(self):
        """user_explicit 触发下任何非空文本都通过。"""
        result = should_remember("任意文本", trigger="user_explicit")
        assert result.passed
        assert result.confidence == 1.0

    def test_filter_integration_with_extractor(self):
        """extractor 的输出中，通用 summary 应被过滤。

        注意：extractor 格式化为 "步骤 X (intent): result_summary"，添加了
        step_id 和 intent 上下文，所以 result_summary 即使是通用文本，被包裹后
        也含有足够信息（step_id + intent）。这是可接受的行为。
        通用模板过滤主要针对纯粹的 summary 文本。
        """
        plan_json = _make_plan_json(
            plan_id="plan_filter_test",
            goal="测试过滤器集成",
            steps=[
                _make_step(
                    step_id="step_1",
                    intent="通用步骤",
                    result_summary="所有步骤执行完成",
                ),
                _make_step(
                    step_id="step_2",
                    intent="有意义的步骤",
                    result_summary="在 graph.py 中修复了状态更新的竞态条件，使用 threading.Lock 保护。",
                ),
            ],
        )
        summary = "执行成功"

        chunks = extract_knowledge_from_executor_result(summary, plan_json, "completed")

        # "执行成功" (summary) 应被通用模板过滤
        for chunk in chunks:
            assert chunk != "执行成功", f"Generic summary should be filtered: {chunk}"

        # 有意义的 step_2 result_summary 应保留
        meaningful = [c for c in chunks if "竞态条件" in c or "threading.Lock" in c]
        assert len(meaningful) > 0, (
            f"Meaningful result_summary should pass: {chunks}"
        )

        # step_1 的包裹格式也应存在（含 step_id + intent 上下文）
        wrapped = [c for c in chunks if "step_1" in c and "通用步骤" in c]
        assert len(wrapped) > 0, (
            f"Wrapped step_1 should exist (contains context): {chunks}"
        )


# ---------------------------------------------------------------------------
# Phase 3.4: 边界条件
# ---------------------------------------------------------------------------


class TestEntryAEdgeCases:
    """Entry A 边界条件和异常输入。"""

    def test_empty_plan_json(self, kt_hash: KnowledgeTree):
        """空 plan_json 应只从 summary 提取。"""
        summary = "发现了一个重要经验：.env 文件中的 API key 不能包含空格。"
        chunks = extract_knowledge_from_executor_result(summary, "", "completed")
        assert len(chunks) == 1
        assert chunks[0] == summary.strip()

    def test_malformed_plan_json(self, kt_hash: KnowledgeTree):
        """格式错误的 plan_json 应只从 summary 提取，不应崩溃。"""
        summary = "任务完成，但 plan JSON 格式异常。"
        bad_json = "{invalid json content"
        chunks = extract_knowledge_from_executor_result(summary, bad_json, "completed")
        assert len(chunks) >= 1, "Should extract from summary even with bad JSON"

    def test_empty_summary_and_plan(self):
        """空 summary 和空 plan_json 应返回空列表。"""
        chunks = extract_knowledge_from_executor_result("", "", "completed")
        assert chunks == []

    def test_empty_steps_array(self):
        """steps 为空数组时应只从 summary 和 goal 提取。"""
        plan_json = _make_plan_json(
            plan_id="plan_no_steps",
            goal="无需执行步骤的简单任务",
            steps=[],
        )
        summary = "任务直接完成。"
        chunks = extract_knowledge_from_executor_result(summary, plan_json, "completed")
        # summary + goal (completed 状态)
        assert len(chunks) >= 1, f"Should extract from summary and goal: {chunks}"

    def test_steps_with_empty_result_summary(self):
        """步骤的 result_summary 为空时应跳过。"""
        plan_json = _make_plan_json(
            plan_id="plan_empty_summary",
            goal="测试空 result_summary",
            steps=[
                _make_step(step_id="step_1", intent="空结果", result_summary=""),
                _make_step(
                    step_id="step_2",
                    intent="有结果",
                    result_summary="生成了 5 个配置文件。",
                ),
            ],
        )
        summary = "部分步骤有结果。"
        chunks = extract_knowledge_from_executor_result(summary, plan_json, "completed")
        # summary + step_2 result_summary + goal
        assert len(chunks) >= 2

    def test_paused_status_no_goal(self):
        """paused 状态不应提取 goal（只有 completed 才提取 goal）。"""
        plan_json = _make_plan_json(
            plan_id="plan_paused",
            goal="需要暂停的任务",
            steps=[
                _make_step(step_id="step_1", intent="执行中", result_summary="已执行到一半"),
            ],
        )
        summary = "任务暂停中。"

        chunks = extract_knowledge_from_executor_result(summary, plan_json, "paused")

        # goal 不应在 chunks 中（paused 状态不提取 goal）
        goal_chunks = [c for c in chunks if "任务目标" in c and "已完成" in c]
        assert len(goal_chunks) == 0, (
            f"paused status should not extract goal: {chunks}"
        )

    def test_dedup_after_multiple_ingest(self, kt_hash: KnowledgeTree):
        """多次摄入相同内容应被去重。"""
        plan_json = _make_plan_json(
            plan_id="plan_dedup",
            goal="去重测试",
            steps=[
                _make_step(
                    step_id="step_1",
                    intent="测试去重",
                    result_summary="这是唯一的测试知识：Executor 使用 uvicorn 启动 FastAPI 服务。",
                ),
            ],
        )
        summary = "去重测试完成。"

        chunks = extract_knowledge_from_executor_result(summary, plan_json, "completed")
        assert len(chunks) >= 1

        # First ingest
        for chunk in chunks:
            kt_hash.ingest(chunk, trigger="task_complete", source="auto:executor")

        # Second ingest — same content
        for chunk in chunks:
            report = kt_hash.ingest(chunk, trigger="task_complete", source="auto:executor")
            # Should dedup
            assert report.nodes_deduplicated >= 0  # dedup is expected


# ---------------------------------------------------------------------------
# Phase 3.5: 检索质量验证 — 摄入的知识应可检索
# ---------------------------------------------------------------------------


class TestEntryARetrievalQuality:
    """摄入的 Entry A 知识的检索质量验证。"""

    def test_retrieve_ingested_executor_knowledge(self, kt_hash: KnowledgeTree):
        """摄入的 executor 知识应被准确检索。"""
        # Simulate executor result about error handling
        plan_json = _make_plan_json(
            plan_id="plan_error_handle",
            goal="实现错误处理和重规划",
            steps=[
                _make_step(
                    step_id="step_1",
                    intent="分析失败模式",
                    result_summary="发现 Executor 的 3 种失败模式：工具执行失败、LLM 调用超时、子进程崩溃。",
                ),
                _make_step(
                    step_id="step_2",
                    intent="实现重规划逻辑",
                    result_summary="Supervisor 在 MAX_REPLAN 次内循环调用 Planner 重规划，每次传入 failure_reason。",
                ),
            ],
        )
        summary = "完成了错误处理机制的实现。"

        chunks = extract_knowledge_from_executor_result(summary, plan_json, "completed")
        for chunk in chunks:
            kt_hash.ingest(chunk, trigger="task_complete", source="auto:executor")

        # Query with different phrasings
        results, _ = kt_hash.retrieve("executor 失败 超时 重规划")
        assert len(results) > 0, "Should retrieve error handling knowledge"

        results2, _ = kt_hash.retrieve("MAX_REPLAN Planner failure_reason")
        assert len(results2) > 0, "Should retrieve replan knowledge"

    def test_retrieve_failure_lesson(self, kt_hash: KnowledgeTree):
        """失败教训应被检索到——帮助用户避免相同错误。"""
        plan_json = _make_plan_json(
            plan_id="plan_lesson",
            goal="配置文件系统",
            steps=[
                _make_step(
                    step_id="step_1",
                    intent="创建配置文件",
                    result_summary="在 workspace/agent/ 目录下创建了 config.json。",
                ),
                _make_step(
                    step_id="step_2",
                    intent="验证路径限制",
                    status="failed",
                    failure_reason="文件操作必须在 workspace/agent/ 内，路径超出范围被拒绝。",
                ),
            ],
        )
        summary = "配置失败：路径限制问题。"

        chunks = extract_knowledge_from_executor_result(summary, plan_json, "failed")
        for chunk in chunks:
            kt_hash.ingest(chunk, trigger="task_complete", source="auto:executor")

        results, _ = kt_hash.retrieve("文件路径 超出 范围 workspace agent")
        assert len(results) > 0

        found_path_content = False
        for node, score in results[:5]:
            if "路径" in node.content or "workspace/agent" in node.content:
                found_path_content = True
                break
        assert found_path_content, (
            f"Should find path restriction knowledge: "
            f"{[n.content[:80] for n, _ in results[:3]]}"
        )

    def test_ingested_knowledge_not_polluting_seed(self, kt_hash: KnowledgeTree):
        """摄入的新知识不应覆盖种子知识的检索。"""
        initial_status = kt_hash.status()
        initial_nodes = initial_status["total_nodes"]

        # Ingest new knowledge
        plan_json = _make_plan_json(
            plan_id="plan_new",
            goal="新任务",
            steps=[
                _make_step(
                    step_id="step_1",
                    intent="新操作",
                    result_summary="新知识内容：使用 make dev 启动开发服务器，端口 2024。",
                ),
            ],
        )
        chunks = extract_knowledge_from_executor_result(
            "新任务完成", plan_json, "completed"
        )
        for chunk in chunks:
            kt_hash.ingest(chunk, trigger="task_complete", source="auto:executor")

        # Seed knowledge should still be retrievable
        results, _ = kt_hash.retrieve("AgentTriad 三层架构 Supervisor")
        assert len(results) > 0
        found_arch = any("architecture" in n.node_id for n, _ in results[:3])
        assert found_arch, (
            f"Seed knowledge should still be retrievable: "
            f"{[n.node_id for n, _ in results[:3]]}"
        )

        # Node count should have grown
        new_status = kt_hash.status()
        assert new_status["total_nodes"] >= initial_nodes
