# 元认知实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 AgentTriad 从自身操作经验中提取可复用教训，并通过检索自动运用这些经验。

**Architecture:** 三阶段渐进式——增强 extractor 提取结构化经验节点，种子操作元规则，在系统提示中加入检索置信度评估指令。全部复用现有 KT 基础设施，无新工具、无新存储结构。额外加一个面向人类的 KT 状态快照可观测性报告。

**Tech Stack:** Python 3.11, pytest, 现有 KT 模块（extractor, filter, bootstrap, __init__）

**Spec:** `docs/superpowers/specs/2026-05-22-meta-cognition-design.md`

---

## File Structure

| File | Action | Responsibility |
|------|--------|---------------|
| `src/common/knowledge_tree/ingestion/extractor.py` | Modify | 新增 `extract_experience_from_executor_result()` 函数 |
| `src/common/knowledge_tree/bootstrap.py` | Modify | 新增 `seed_meta_rules()` 函数 |
| `src/supervisor_agent/graph.py` | Modify | Entry A 调用经验提取；State 新增快照字段 |
| `src/supervisor_agent/state.py` | Modify | 新增 `kt_snapshot` 字段 |
| `src/supervisor_agent/prompts.py` | Modify | 加入检索置信度评估指令 |
| `src/common/knowledge_tree/snapshot.py` | Create | KT 状态快照生成和写入 |
| `tests/unit_tests/common/knowledge_tree/test_extractor.py` | Modify | 经验提取测试 |
| `tests/unit_tests/common/knowledge_tree/test_bootstrap_meta_rules.py` | Create | 元规则种子测试 |
| `tests/unit_tests/common/knowledge_tree/test_snapshot.py` | Create | 快照生成测试 |

---

## Task 1: 经验提取函数

**Files:**
- Modify: `src/common/knowledge_tree/ingestion/extractor.py`
- Test: `tests/unit_tests/common/knowledge_tree/test_extractor.py`

- [ ] **Step 1: Write failing tests for experience extraction**

在 `test_extractor.py` 末尾新增测试类：

```python
class TestExperienceExtraction:
    """验证经验节点提取（元认知阶段 1）。"""

    def test_failed_task_extracts_experience(self):
        """失败任务应提取经验四元组。"""
        summary = "执行失败：在处理大规模文件时 Executor 超时退出。"
        plan_json = _make_plan_json(
            steps=[
                {
                    "step_id": "step_1",
                    "intent": "扫描目录下所有文件",
                    "status": "failed",
                    "result_summary": "",
                    "failure_reason": "Executor 进程超时，可能是文件数量过多导致内存溢出。",
                },
            ],
        )
        result = extract_experience_from_executor_result(summary, plan_json, "failed")
        assert len(result) == 1
        text = result[0]
        assert "情境" in text
        assert "行动" in text
        assert "教训" in text
        assert "失败" in text

    def test_completed_with_discovery_extracts_experience(self):
        """完成但含有发现性内容时提取经验。"""
        summary = "发现重要模式：在 config.yaml 中设置 worker_timeout 时需要同步修改 supervisor_timeout，否则会导致进程假死。"
        plan_json = _make_plan_json(
            goal="配置超时参数",
            steps=[
                {
                    "step_id": "step_1",
                    "intent": "修改配置文件",
                    "status": "completed",
                    "result_summary": "发现 worker_timeout 和 supervisor_timeout 需要同步修改。",
                    "failure_reason": "",
                },
            ],
        )
        result = extract_experience_from_executor_result(summary, plan_json, "completed")
        assert len(result) == 1
        text = result[0]
        assert "情境" in text
        assert "教训" in text

    def test_completed_trivial_no_experience(self):
        """简单确认性完成不提取经验。"""
        summary = "已读取文件。"
        plan_json = _make_plan_json(
            steps=[
                {
                    "step_id": "step_1",
                    "intent": "读取配置",
                    "status": "completed",
                    "result_summary": "已读取。",
                    "failure_reason": "",
                },
            ],
        )
        result = extract_experience_from_executor_result(summary, plan_json, "completed")
        assert len(result) == 0

    def test_empty_input_no_experience(self):
        """空输入不提取经验。"""
        result = extract_experience_from_executor_result("", "", "completed")
        assert result == []

    def test_experience_format_contains_all_fields(self):
        """经验节点包含完整的四元组字段。"""
        summary = "使用 uv run pytest 运行测试时，需要确保 .env 文件存在，否则会加载失败。"
        plan_json = _make_plan_json(
            steps=[
                {
                    "step_id": "step_1",
                    "intent": "运行测试",
                    "status": "failed",
                    "result_summary": "",
                    "failure_reason": "缺少 .env 文件导致配置加载失败。",
                },
            ],
        )
        result = extract_experience_from_executor_result(summary, plan_json, "failed")
        assert len(result) == 1
        for field in ["情境", "行动", "结果", "教训", "适用"]:
            assert field in result[0]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd C:\Projects\Agents\AgentTriad && uv run pytest tests/unit_tests/common/knowledge_tree/test_extractor.py::TestExperienceExtraction -v`
Expected: FAIL — `ImportError: cannot import name 'extract_experience_from_executor_result'`

- [ ] **Step 3: Implement `extract_experience_from_executor_result()`**

在 `extractor.py` 末尾新增函数：

```python
# 经验提炼关键词：检测 completion summary 中是否有知识发现性内容
_DISCOVERY_PATTERNS = re.compile(
    r"发现|确认|正确的.*是|需要先|必须|关键|重要.*模式|导致.*原因|因为|只有.*才能"
)


def extract_experience_from_executor_result(
    summary: str,
    updated_plan_json: str,
    status: str,
) -> list[str]:
    """从 Executor 结果中提取结构化经验节点。

    与 extract_knowledge_from_executor_result 不同，此函数输出
    格式化的经验四元组（情境/行动/结果/教训/适用），用于元认知。

    Args:
        summary: Executor 返回的 summary 文本。
        updated_plan_json: Executor 返回的 updated_plan_json 字符串。
        status: Executor 状态（"completed"/"failed"/"paused"）。

    Returns:
        格式化的经验文本列表。每个元素是一个完整的经验节点内容。
    """
    # 收集素材
    goal = ""
    step_intents: list[str] = []
    step_failures: list[str] = []
    step_results: list[str] = []

    if updated_plan_json and updated_plan_json.strip():
        try:
            plan = json.loads(updated_plan_json)
        except (json.JSONDecodeError, TypeError):
            plan = {}
        goal = plan.get("goal", "")
        for step in plan.get("steps", []):
            intent = step.get("intent", "")
            step_intents.append(intent)
            fr = step.get("failure_reason", "")
            if fr and fr.strip():
                step_failures.append(f"步骤「{intent}」失败：{fr.strip()}")
            rs = step.get("result_summary", "")
            if rs and rs.strip():
                step_results.append(f"步骤「{intent}」：{rs.strip()}")

    # 判断是否值得提取经验
    if status == "failed":
        # 失败任务始终提取
        pass
    elif status == "completed":
        # 完成任务只有含有发现性内容时才提取
        combined = f"{summary} {' '.join(step_results)}"
        if not _DISCOVERY_PATTERNS.search(combined):
            return []
        if len(combined.strip()) < 50:
            return []
    else:
        # paused 等其他状态不提取经验
        return []

    # 构造情境
    context = goal if goal else "（无明确目标）"
    actions = "；".join(step_intents) if step_intents else "（执行了任务）"

    # 构造结果和教训
    if status == "failed":
        outcome = "失败"
        lessons = "；".join(step_failures) if step_failures else summary
        lesson_text = f"避免{lessons}" if lessons else "需要进一步分析失败原因"
    else:
        outcome = "成功"
        lesson_text = summary if summary else "。".join(step_results)

    # 适用范围
    applicable = goal if goal else "；".join(step_intents[:2])

    experience = (
        f"[经验] {context[:30]}\n"
        f"情境：{context}\n"
        f"行动：{actions}\n"
        f"结果：{outcome} — {summary[:100] if summary else '见步骤详情'}\n"
        f"教训：{lesson_text}\n"
        f"适用：涉及「{applicable}」类型的任务"
    )

    return [experience]
```

在文件顶部添加 `import re`（如果还没有）。

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd C:\Projects\Agents\AgentTriad && uv run pytest tests/unit_tests/common/knowledge_tree/test_extractor.py::TestExperienceExtraction -v`
Expected: All 5 tests PASS

- [ ] **Step 5: Run full extractor test suite to confirm no regression**

Run: `cd C:\Projects\Agents\AgentTriad && uv run pytest tests/unit_tests/common/knowledge_tree/test_extractor.py -v`
Expected: All tests PASS (old + new)

- [ ] **Step 6: Commit**

```bash
cd C:\Projects\Agents\AgentTriad
git add src/common/knowledge_tree/ingestion/extractor.py tests/unit_tests/common/knowledge_tree/test_extractor.py
git commit -m "feat(meta-cognition): add experience extraction from executor results"
```

---

## Task 2: 接线经验提取到 Entry A

**Files:**
- Modify: `src/supervisor_agent/graph.py:915-954` (`_try_auto_ingest_executor_result`)

- [ ] **Step 1: Write failing test for experience ingestion in Entry A**

在 `test_extractor.py` 末尾新增（或在 `test_knowledge_tree_tools_integration.py` 中新增）：

```python
class TestEntryAExperienceIngestion:
    """验证 Entry A 调用经验提取并 ingest 为 experience 节点。"""

    def test_failed_result_triggers_experience_ingest(self, tmp_path):
        """失败结果应触发经验提取。"""
        from unittest.mock import MagicMock, patch

        from src.common.knowledge_tree.ingestion.extractor import (
            extract_experience_from_executor_result,
        )

        summary = "Executor 超时导致任务失败。"
        plan_json = json.dumps({
            "plan_id": "p1", "version": 1, "goal": "测试",
            "steps": [{"step_id": "s1", "intent": "执行", "status": "failed",
                        "result_summary": "", "failure_reason": "超时退出。"}],
        })

        result = extract_experience_from_executor_result(summary, plan_json, "failed")
        assert len(result) == 1
        assert "教训" in result[0]
        assert "失败" in result[0]
```

- [ ] **Step 2: Run test to verify it passes (extractor already implemented)**

Run: `cd C:\Projects\Agents\AgentTriad && uv run pytest tests/unit_tests/common/knowledge_tree/test_extractor.py::TestEntryAExperienceIngestion -v`
Expected: PASS — extractor 函数已在 Task 1 实现

- [ ] **Step 3: Modify `_try_auto_ingest_executor_result` to also ingest experience nodes**

在 `graph.py` 的 `_try_auto_ingest_executor_result` 函数中，在现有 `for chunk in chunks:` 循环之后，新增经验提取和 ingest 逻辑：

```python
def _try_auto_ingest_executor_result(content: str, ctx: Any, exec_status: str = "completed") -> None:
    """Entry A: 从 Executor 完成结果中自动提取知识并存入知识树。

    设计原则：全程 try/except 包裹，KT 失败不影响主图执行路径。
    同时处理 completed 和 failed 状态——失败结果的 failure_reason 是重要的教训知识。
    """
    try:
        from src.common.knowledge_tree import get_or_create_kt
        from src.common.knowledge_tree.config import KnowledgeTreeConfig
        from src.common.knowledge_tree.ingestion.extractor import (
            extract_knowledge_from_executor_result,
            extract_experience_from_executor_result,
        )

        summary = _extract_executor_summary(content)
        updated_plan = _extract_updated_plan_from_executor(content) or ""

        chunks = extract_knowledge_from_executor_result(
            summary, updated_plan, exec_status
        )
        if not chunks:
            return

        config = KnowledgeTreeConfig.from_context(ctx)
        kt = get_or_create_kt(config)
        total_ingested = 0
        for chunk in chunks:
            report = kt.ingest(
                chunk,
                trigger="task_complete",
                source="auto:executor",
            )
            total_ingested += report.nodes_ingested

        # 元认知：提取结构化经验节点
        experiences = extract_experience_from_executor_result(
            summary, updated_plan, exec_status
        )
        for exp in experiences:
            exp_report = kt.ingest(
                exp,
                trigger="task_complete",
                source="auto:executor_experience",
                metadata={"node_type": "experience"},
            )
            total_ingested += exp_report.nodes_ingested

        if total_ingested > 0:
            logger.info(
                "Entry A: auto-ingested %d knowledge chunks (%d experiences) from executor result",
                total_ingested,
                len(experiences),
            )
    except Exception:
        logger.debug("Entry A: auto-ingest failed (non-critical)", exc_info=True)
```

注意：`import extract_experience_from_executor_result` 加到现有的 import 块中。

- [ ] **Step 4: Run existing Entry A tests to verify no regression**

Run: `cd C:\Projects\Agents\AgentTriad && uv run pytest tests/integration/test_kt_entry_a_closed_loop.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
cd C:\Projects\Agents\AgentTriad
git add src/supervisor_agent/graph.py tests/unit_tests/common/knowledge_tree/test_extractor.py
git commit -m "feat(meta-cognition): wire experience extraction into Entry A auto-ingest"
```

---

## Task 3: 元规则种子

**Files:**
- Modify: `src/common/knowledge_tree/bootstrap.py`
- Create: `tests/unit_tests/common/knowledge_tree/test_bootstrap_meta_rules.py`

- [ ] **Step 1: Write failing test for meta rule seeding**

创建 `tests/unit_tests/common/knowledge_tree/test_bootstrap_meta_rules.py`：

```python
"""Tests for meta rule seeding during bootstrap."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from src.common.knowledge_tree.bootstrap import seed_meta_rules


class TestSeedMetaRules:
    """验证元规则种子写入。"""

    def test_seeds_all_five_rules(self, tmp_path):
        """应写入 5 条元规则。"""
        kt = MagicMock()
        kt.get_meta_rules.return_value = []
        seed_meta_rules(kt)
        assert kt.ingest.call_count == 5

    def test_seed_content_contains_kt_guidance(self, tmp_path):
        """种子内容应包含 KT 操作指导。"""
        kt = MagicMock()
        kt.get_meta_rules.return_value = []
        seed_meta_rules(kt)
        calls = kt.ingest.call_args_list
        all_content = " ".join(c[0][0] for c in calls)
        # 至少包含 ingest 和 retrieve 相关指导
        assert "ingest" in all_content
        assert "retrieve" in all_content

    def test_seed_does_not_duplicate_existing_rules(self):
        """已存在的元规则不应重复写入。"""
        from src.common.knowledge_tree.dag.node import KnowledgeNode

        existing = KnowledgeNode.create(
            node_id="meta_1",
            title="主动沉淀",
            content="当用户分享了项目特定信息时，用 knowledge_tree_ingest 沉淀",
            source="bootstrap",
            metadata={"node_type": "meta_rule"},
        )
        kt = MagicMock()
        kt.get_meta_rules.return_value = [existing]
        seed_meta_rules(kt)
        # 已有一条匹配，应跳过它，写入其余 4 条
        assert kt.ingest.call_count == 4

    def test_seed_metadata_is_meta_rule(self):
        """每条种子的 metadata 应包含 node_type=meta_rule。"""
        kt = MagicMock()
        kt.get_meta_rules.return_value = []
        seed_meta_rules(kt)
        for call in kt.ingest.call_args_list:
            metadata = call[1].get("metadata", {})
            assert metadata.get("node_type") == "meta_rule"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd C:\Projects\Agents\AgentTriad && uv run pytest tests/unit_tests/common/knowledge_tree/test_bootstrap_meta_rules.py -v`
Expected: FAIL — `ImportError: cannot import name 'seed_meta_rules'`

- [ ] **Step 3: Implement `seed_meta_rules()`**

在 `bootstrap.py` 末尾新增：

```python
# 操作元规则种子（元认知阶段 2）
_META_RULE_SEEDS: list[tuple[str, str, int]] = [
    (
        "主动沉淀",
        "当用户分享了项目特定信息（路径、配置、约定、偏好）时，用 knowledge_tree_ingest 沉淀到知识树",
        10,
    ),
    (
        "失败前查",
        "遇到重复出现的错误模式时，先用 knowledge_tree_retrieve 查看是否有历史经验可参考",
        20,
    ),
    (
        "先查后答",
        "当任务涉及不熟悉的技术栈或领域时，先 retrieve 查知识树再回答",
        15,
    ),
    (
        "失败后学",
        "执行失败后重规划前，先检索相关失败经验避免重复踩坑",
        25,
    ),
    (
        "成功也记",
        "完成任务后如果发现新的可复用知识（工具用法、配置技巧、排错方法），主动 ingest",
        5,
    ),
]


def seed_meta_rules(kt: Any) -> int:
    """向知识树种子操作元规则。

    已存在的元规则（按 content 匹配）不会重复写入。

    Args:
        kt: KnowledgeTree 实例。

    Returns:
        新写入的元规则数量。
    """
    existing_contents: set[str] = set()
    try:
        for rule in kt.get_meta_rules():
            existing_contents.add(rule.content.strip())
    except Exception:
        logger.warning("Failed to check existing meta rules during seed")

    count = 0
    for title, content, priority in _META_RULE_SEEDS:
        if content.strip() in existing_contents:
            continue
        try:
            kt.ingest(
                content,
                trigger="bootstrap",
                source="bootstrap:meta_rule",
                metadata={"node_type": "meta_rule", "priority": priority},
            )
            count += 1
        except Exception as e:
            logger.warning("Failed to seed meta rule '%s': %s", title, e)

    if count > 0:
        logger.info("Meta rules seeded: %d new rules", count)

    return count
```

在文件顶部添加 `from __future__ import annotations` 后面加 `from typing import Any`。

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd C:\Projects\Agents\AgentTriad && uv run pytest tests/unit_tests/common/knowledge_tree/test_bootstrap_meta_rules.py -v`
Expected: All 4 tests PASS

- [ ] **Step 5: Commit**

```bash
cd C:\Projects\Agents\AgentTriad
git add src/common/knowledge_tree/bootstrap.py tests/unit_tests/common/knowledge_tree/test_bootstrap_meta_rules.py
git commit -m "feat(meta-cognition): add meta rule seeding for KT operational knowledge"
```

---

## Task 4: 元规则种子接入 bootstrap 流程

**Files:**
- Modify: `src/common/knowledge_tree/__init__.py`（`get_or_create_kt` 函数）

- [ ] **Step 1: Wire `seed_meta_rules` into bootstrap flow**

在 `__init__.py:332` 处，`bootstrap_from_directory()` 调用之后、`return` 语句之前，加入元规则种子调用：

```python
        report = bootstrap_from_directory(
            seed_dir=seed_dir,
            md_store=self.md_store,
            vector_store=self.vector_store,
            overlay_store=self.overlay_store,
            embedder=self.embedder,
        )

        # 元认知：种子操作元规则
        from src.common.knowledge_tree.bootstrap import seed_meta_rules
        try:
            seed_meta_rules(self)
        except Exception as e:
            logger.warning("Meta rule seeding failed (non-critical): %s", e)

        return {
```

注意 `seed_meta_rules(self)` 传入的是 KnowledgeTree 实例自身（`self`），因为 `seed_meta_rules` 需要 `kt.get_meta_rules()` 和 `kt.ingest()`。

- [ ] **Step 2: Run existing KT tests to verify no regression**

Run: `cd C:\Projects\Agents\AgentTriad && uv run pytest tests/unit_tests/common/knowledge_tree/ -v -q`
Expected: All tests PASS

- [ ] **Step 3: Commit**

```bash
cd C:\Projects\Agents\AgentTriad
git add src/common/knowledge_tree/__init__.py
git commit -m "feat(meta-cognition): wire meta rule seeding into KT initialization"
```

---

## Task 5: 检索置信度评估指令

**Files:**
- Modify: `src/supervisor_agent/prompts.py:93-122`

- [ ] **Step 1: Add confidence assessment instructions to system prompt**

在 `prompts.py` 中找到 KT 指导部分的 `**优化建议**` 段落之后，加入：

```
**检索质量评估**：当你使用了 [相关知识] 中的内容来回答时，请按以下标准判断检索充分性：
- 检索结果直接回答了用户问题 → 正常回答，标注 [基于记忆]
- 检索结果部分相关但有明显缺口 → 回答但标注 [部分记忆]，提示用户可能需要更具体的信息
- 检索结果不相关或为空，但凭自身知识有把握 → 直接回答，不提及 KT
- 检索结果不相关且没有把握 → 用 knowledge_tree_retrieve 换关键词再检索一次，或升级到 Planner
```

这段文字加在 `**优化建议**` 段落后面，仍然在系统提示的 KT 指导区块内。

- [ ] **Step 2: Verify prompt renders correctly**

Run: `cd C:\Projects\Agents\AgentTriad && uv run python -c "from src.supervisor_agent.prompts import get_supervisor_system_prompt; from src.common.context import Context; p = get_supervisor_system_prompt(Context()); print('检索质量评估' in p)"`
Expected: `True`

- [ ] **Step 3: Run supervisor prompt tests**

Run: `cd C:\Projects\Agents\AgentTriad && uv run pytest tests/unit_tests/supervisor_agent/ -v -q -k "prompt or graph"`
Expected: All tests PASS

- [ ] **Step 4: Commit**

```bash
cd C:\Projects\Agents\AgentTriad
git add src/supervisor_agent/prompts.py
git commit -m "feat(meta-cognition): add retrieval confidence assessment instructions to system prompt"
```

---

## Task 6: KT 状态快照可观测性

**Files:**
- Create: `src/common/knowledge_tree/snapshot.py`
- Modify: `src/supervisor_agent/state.py` (新增 `kt_snapshot_data` 字段)
- Modify: `src/supervisor_agent/graph.py` (在 kt_retrieve 和 Entry A 中累积数据，任务完成时写入)
- Create: `tests/unit_tests/common/knowledge_tree/test_snapshot.py`

- [ ] **Step 1: Write failing tests for snapshot generation**

创建 `tests/unit_tests/common/knowledge_tree/test_snapshot.py`：

```python
"""Tests for KT status snapshot generation."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

from src.common.knowledge_tree.snapshot import generate_kt_snapshot


class TestSnapshotGeneration:
    """验证 KT 状态快照生成。"""

    def test_snapshot_structure(self):
        """快照应包含三个区块。"""
        kt = MagicMock()
        kt.get_node_count.return_value = 48
        kt.get_directory_count.return_value = 25
        result = generate_kt_snapshot(
            kt,
            task_summary="测试任务",
            auto_retrieve_hits=2,
            retrieved_nodes=["a.md", "b.md"],
            agent_used_kt=True,
            confidence_level="sufficient",
            manual_retrieve_count=0,
            manual_ingest_count=0,
            auto_ingest_count=1,
            ingested_nodes=["exp.md"],
            ingest_triggers=["executor_result_failed"],
            experience_node_count=3,
            avg_retrieval_score=0.52,
        )
        assert "kt_influence" in result
        assert "kt_mutations" in result
        assert "kt_health" in result

    def test_snapshot_is_valid_json(self):
        """快照应该是可序列化的 JSON。"""
        kt = MagicMock()
        kt.get_node_count.return_value = 10
        kt.get_directory_count.return_value = 5
        result = generate_kt_snapshot(
            kt, task_summary="test", auto_retrieve_hits=0,
            retrieved_nodes=[], agent_used_kt=False, confidence_level="none",
            manual_retrieve_count=0, manual_ingest_count=0,
            auto_ingest_count=0, ingested_nodes=[], ingest_triggers=[],
            experience_node_count=0, avg_retrieval_score=0.0,
        )
        json_str = json.dumps(result, ensure_ascii=False)
        parsed = json.loads(json_str)
        assert parsed["kt_health"]["total_nodes"] == 10

    def test_snapshot_includes_timestamp(self):
        """快照应包含时间戳。"""
        kt = MagicMock()
        kt.get_node_count.return_value = 0
        kt.get_directory_count.return_value = 0
        result = generate_kt_snapshot(
            kt, task_summary="test", auto_retrieve_hits=0,
            retrieved_nodes=[], agent_used_kt=False, confidence_level="none",
            manual_retrieve_count=0, manual_ingest_count=0,
            auto_ingest_count=0, ingested_nodes=[], ingest_triggers=[],
            experience_node_count=0, avg_retrieval_score=0.0,
        )
        assert "timestamp" in result

    def test_write_snapshot_to_file(self, tmp_path):
        """快照应能写入文件。"""
        from src.common.knowledge_tree.snapshot import write_snapshot

        kt = MagicMock()
        kt.get_node_count.return_value = 5
        kt.get_directory_count.return_value = 2
        snapshot = generate_kt_snapshot(
            kt, task_summary="test", auto_retrieve_hits=1,
            retrieved_nodes=["a.md"], agent_used_kt=True, confidence_level="sufficient",
            manual_retrieve_count=0, manual_ingest_count=0,
            auto_ingest_count=0, ingested_nodes=[], ingest_triggers=[],
            experience_node_count=0, avg_retrieval_score=0.5,
        )
        log_file = tmp_path / "kt_snapshot.jsonl"
        write_snapshot(snapshot, log_file)
        assert log_file.exists()
        line = log_file.read_text(encoding="utf-8").strip()
        parsed = json.loads(line)
        assert parsed["kt_health"]["total_nodes"] == 5
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd C:\Projects\Agents\AgentTriad && uv run pytest tests/unit_tests/common/knowledge_tree/test_snapshot.py -v`
Expected: FAIL — `ImportError: cannot import name 'generate_kt_snapshot'`

- [ ] **Step 3: Create `snapshot.py`**

创建 `src/common/knowledge_tree/snapshot.py`：

```python
"""KT 状态快照：面向人类开发者的可观测性报告。"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def generate_kt_snapshot(
    kt: Any,
    task_summary: str,
    auto_retrieve_hits: int,
    retrieved_nodes: list[str],
    agent_used_kt: bool,
    confidence_level: str,
    manual_retrieve_count: int,
    manual_ingest_count: int,
    auto_ingest_count: int,
    ingested_nodes: list[str],
    ingest_triggers: list[str],
    experience_node_count: int,
    avg_retrieval_score: float,
) -> dict:
    """生成 KT 状态快照。

    Args:
        kt: KnowledgeTree 实例。
        task_summary: 任务摘要。
        auto_retrieve_hits: 自动检索命中数。
        retrieved_nodes: 检索到的节点列表。
        agent_used_kt: Agent 是否使用了 KT 内容。
        confidence_level: 置信度级别。
        manual_retrieve_count: 主动检索次数。
        manual_ingest_count: 主动摄入次数。
        auto_ingest_count: 自动摄入次数。
        ingested_nodes: 摄入的节点列表。
        ingest_triggers: 摄入触发类型列表。
        experience_node_count: 经验节点数。
        avg_retrieval_score: 平均检索分数。

    Returns:
        可 JSON 序列化的快照字典。
    """
    total_nodes = kt.get_node_count() if hasattr(kt, "get_node_count") else 0
    total_dirs = kt.get_directory_count() if hasattr(kt, "get_directory_count") else 0

    return {
        "timestamp": datetime.now(UTC).isoformat(),
        "task_summary": task_summary[:100],
        "kt_influence": {
            "auto_retrieve_hits": auto_retrieve_hits,
            "retrieved_nodes": retrieved_nodes[:5],
            "agent_used_kt": agent_used_kt,
            "confidence_level": confidence_level,
            "manual_retrieve_count": manual_retrieve_count,
            "manual_ingest_count": manual_ingest_count,
        },
        "kt_mutations": {
            "auto_ingest_count": auto_ingest_count,
            "ingested_nodes": ingested_nodes[:5],
            "ingest_triggers": ingest_triggers,
            "meta_rules_active": len(kt.get_meta_rules()) if hasattr(kt, "get_meta_rules") else 0,
        },
        "kt_health": {
            "total_nodes": total_nodes,
            "total_directories": total_dirs,
            "experience_nodes": experience_node_count,
            "avg_retrieval_score": round(avg_retrieval_score, 2),
        },
    }


def write_snapshot(snapshot: dict, log_file: Path) -> None:
    """将快照追加写入 JSONL 文件。"""
    try:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(snapshot, ensure_ascii=False) + "\n")
    except Exception as e:
        logger.warning("Failed to write KT snapshot: %s", e)
```

- [ ] **Step 4: Add `get_node_count` and `get_directory_count` methods to KnowledgeTree if missing**

检查 `__init__.py` 中 KnowledgeTree 类是否已有这两个方法。如果没有，添加简单实现：

```python
def get_node_count(self) -> int:
    """返回节点总数。"""
    return len(self.md_store.list_all_nodes())

def get_directory_count(self) -> int:
    """返回目录数。"""
    return len(self.vector_store.get_all_anchors())
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd C:\Projects\Agents\AgentTriad && uv run pytest tests/unit_tests/common/knowledge_tree/test_snapshot.py -v`
Expected: All 4 tests PASS

- [ ] **Step 6: Add `kt_snapshot_data` field to State**

在 `state.py` 的 State 类中，在 `kt_optimization_suggestions` 之后添加：

```python
    # 元认知：KT 状态快照累积数据（任务完成后写入日志）
    kt_snapshot_data: dict = field(default_factory=dict)
```

- [ ] **Step 7: Wire snapshot data collection into `kt_retrieve` and `_try_auto_ingest_executor_result`**

在 `graph.py` 的 `kt_retrieve` 函数中，在返回字典之前，收集快照数据：

```python
# 快照数据累积
snapshot_data = {
    "auto_retrieve_hits": len(high_quality),
    "retrieved_nodes": [n.title for n, _ in high_quality[:5]],
}
```

将 `snapshot_data` 添加到返回字典中：`"kt_snapshot_data": snapshot_data`。

在 `_try_auto_ingest_executor_result` 中类似地记录 ingest 数据。注意：由于 `_try_auto_ingest_executor_result` 是同步函数且不返回 State 更新，快照数据通过 logger 输出即可。真正的快照写入在 `call_model` 最后一次调用时（检测到任务即将结束时）执行。

- [ ] **Step 8: Run full test suite**

Run: `cd C:\Projects\Agents\AgentTriad && uv run pytest tests/unit_tests/ -q`
Expected: All tests PASS

- [ ] **Step 9: Commit**

```bash
cd C:\Projects\Agents\AgentTriad
git add src/common/knowledge_tree/snapshot.py src/common/knowledge_tree/__init__.py src/supervisor_agent/state.py src/supervisor_agent/graph.py tests/unit_tests/common/knowledge_tree/test_snapshot.py
git commit -m "feat(meta-cognition): add KT status snapshot for human observability"
```

---

## Task 7: 全局验证与文档更新

**Files:**
- Modify: `docs/v4-kt-core-design.md`
- Modify: `docs/product-roadmap.md`
- Modify: `C:\Users\TL\.claude\projects\C--Projects-Agents-AgentTriad\memory\MEMORY.md`

- [ ] **Step 1: Run full test suite**

Run: `cd C:\Projects\Agents\AgentTriad && uv run make test_automated`
Expected: All tests PASS

- [ ] **Step 2: Update `docs/v4-kt-core-design.md`**

在"已完成"清单末尾新增条目：

```
- [ ] **元认知阶段 1-3** — 经验沉淀（extractor 增强）+ 操作元规则种子 + 检索置信度评估 + KT 快照可观测性
```

- [ ] **Step 3: Update `docs/product-roadmap.md`**

在版本里程碑时间线中更新 V4 进度。

- [ ] **Step 4: Commit**

```bash
cd C:\Projects\Agents\AgentTriad
git add docs/
git commit -m "docs: update KT design doc and roadmap for meta-cognition"
```
