# KT 清理与 Entry A 摄入过滤加固 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 清理 KT 中 65 个垃圾节点并将 Entry A 自动摄入过滤加固到不再摄入 BlockingError / MagicMock / 测试任务残留。

**Architecture:** 沿用 V4 KT 现有两层存储（MarkdownStore + InMemoryVectorStore）+ Embedding 派生索引（`title:` / `alias:` / `stored:` 前缀键）+ 目录锚点。在 `KnowledgeTree` 门面新增一站式 `delete_node`；在 `filter.py` 顶部加 infra_error / test_task 前置 reject；在 `extractor.py:154` 修复门控 bug；在 `_try_auto_ingest_executor_result` 注入 `goal`/`primary_intent` metadata 让 `core.py:354` title fallback 用 plan goal；`dedup_threshold` 0.95→0.88；一次性脚本白名单批量删除。全程 TDD。

**Tech Stack:** Python 3.11+ / pytest / ruff / mypy --strict / LangGraph 旁路模块（本次不动）/ V4 KnowledgeTree（hash embedder）。

**Spec:** `docs/superpowers/specs/2026-07-04-kt-cleanup-and-filter-hardening-design.md` (commit 220a0c7)

---

## File Structure

| 文件 | 角色 | 本次动作 |
|------|------|---------|
| `src/common/knowledge_tree/ingestion/extractor.py:144-155` | 经验提炼 framework 错误过滤门控 | 修门控 bug（Task 1） |
| `src/common/knowledge_tree/ingestion/filter.py` | `should_remember` 规则过滤器 | 加 `_INFRA_ERROR_PATTERNS`（Task 2）+ `_TEST_TASK_PATTERNS`（Task 3），插入位置在 `user_explicit` 早返回之后 |
| `src/supervisor_agent/graph.py:1142-1200` | `_try_auto_ingest_executor_result` Entry A | 注入 `goal`/`primary_intent` 到 ingest metadata（Task 4）|
| `src/common/knowledge_tree/core.py:325-381` | `KnowledgeTree.ingest` | 改 `title=chunk[:50]` 为 goal→primary_intent→chunk fallback（Task 4）|
| `src/common/knowledge_tree/core.py`（新增方法 ~line 380 后） | `KnowledgeTree.delete_node` | 一站式删除 API（Task 6）|
| `src/common/knowledge_tree/config.py:43` | `KnowledgeTreeConfig.dedup_threshold` | 0.95 → 0.88（Task 5）|
| `src/common/context.py:343` | `Context.kt_dedup_threshold` | 0.95 → 0.88（Task 5）|
| `docs/environment-variables.md:146`、`docs/architecture-decisions.md:786` | 环境变量文档 | 同步阈值改动（Task 5）|
| `scripts/cleanup_kt.py`（新建） | 一次性清理脚本 | 调 `kt.delete_node` 批删 10 目录（Task 7）|
| `tests/unit_tests/common/knowledge_tree/test_extractor.py` | extractor 单元测试 | 加 4 例（Task 1，部分跨 Task 2/3）|
| `tests/unit_tests/common/knowledge_tree/test_filter.py` | filter 单元测试 | 加 2 例（Task 2 + Task 3）|
| `tests/unit_tests/common/knowledge_tree/test_core.py`（新建） | KnowledgeTree API 单元测试 | `test_delete_node` 用例（Task 6）|
| `workspace/knowledge_tree/meta_rules/*.md`（7 个） | meta_rules 治理内容 | 审计 + 必要时删除（Task 10）|

---

## Task 1: 修复 extractor framework error 门控 bug

**Files:**
- Modify: `src/common/knowledge_tree/ingestion/extractor.py:150-155`
- Test: `tests/unit_tests/common/knowledge_tree/test_extractor.py`

**背景：** 当前 `extract_experience_from_executor_result` 在 status=failed 路径用 `if _FRAMEWORK_ERRORS.search(combined) and not step_failures: return []` 过滤基础设施错误。门控条件写反——失败任务**几乎都有** step_failures，导致整个过滤被 bypass。MagicMock 残留 7+ 节点由此造成。

- [ ] **Step 1: 写失败测试（先红）**

追加到 `tests/unit_tests/common/knowledge_tree/test_extractor.py` 末尾：

```python
class TestFrameworkErrorBypassFix:
    """回归：framework error 即使有 step_failures 也必须被拦，原 bug 被 `and not step_failures` bypass。"""

    def test_mock_with_step_failures_filtered(self):
        """MagicMock 型错误含 step_failures 时仍不应产经验节点（原 bug 会让其漏过）。"""
        summary = "Executor internal error: object MagicMock cannot be used in await expression."
        plan_json = _make_plan_json(
            goal="探测 Executor 启动",
            steps=[
                {
                    "step_id": "step_1",
                    "intent": "调用 Executor",
                    "status": "failed",
                    "result_summary": "",
                    "failure_reason": "TypeError: object MagicMock can't be used in 'await' expression",
                },
            ],
        )
        result = extract_experience_from_executor_result(summary, plan_json, "failed")
        assert result == [], f"应被 framework error 过滤，但得到: {result}"

    def test_blocking_error_with_step_failures_filtered(self):
        summary = "Executor 启动失败：BlockingError raised in scope."
        plan_json = _make_plan_json(
            goal="启动 Executor 子进程",
            steps=[
                {
                    "step_id": "step_1",
                    "intent": "spawn",
                    "status": "failed",
                    "result_summary": "",
                    "failure_reason": "BlockingError: ... raised in scope",
                },
            ],
        )
        result = extract_experience_from_executor_result(summary, plan_json, "failed")
        assert result == [], f"应被 framework error 过滤，但得到: {result}"

    def test_legit_business_failure_still_extracts_experience(self):
        """真实业务失败（非基础设施错误）应仍提取经验。"""
        summary = "任务失败：发现部署脚本中端口冲突导致服务启动失败。"
        plan_json = _make_plan_json(
            goal="部署 v2 服务",
            steps=[
                {
                    "step_id": "step_1",
                    "intent": "启动服务",
                    "status": "failed",
                    "result_summary": "",
                    "failure_reason": "端口 8080 被占用，部署脚本未做端口冲突检测。",
                },
            ],
        )
        result = extract_experience_from_executor_result(summary, plan_json, "failed")
        assert len(result) == 1
        assert "教训" in result[0]
```

- [ ] **Step 2: 跑测试验证失败**

```bash
uv run pytest tests/unit_tests/common/knowledge_tree/test_extractor.py::TestFrameworkErrorBypassFix -v
```

预期：`test_mock_with_step_failures_filtered` 与 `test_blocking_error_with_step_failures_filtered` FAIL（当前 `and not step_failures` 让其漏过，返回非空 list）。`test_legit_business_failure_still_extracts_experience` PASS。

- [ ] **Step 3: 改 extractor.py:150-155**

把：

```python
        combined = f"{summary} {' '.join(step_failures)}"
        _FRAMEWORK_ERRORS = re.compile(
            r"(mock|MagicMock|TypeError|await\s+expression|import\s+error|module\s+not\s+found)",
            re.IGNORECASE,
        )
        if _FRAMEWORK_ERRORS.search(combined) and not step_failures:
            return []
```

改为：

```python
        combined = f"{summary} {' '.join(step_failures)}"
        _FRAMEWORK_ERRORS = re.compile(
            r"(mock|MagicMock|TypeError|await\s+expression|import\s+error|module\s+not\s+found)",
            re.IGNORECASE,
        )
        if _FRAMEWORK_ERRORS.search(combined):
            return []
```

仅删去行尾 ` and not step_failures`。

- [ ] **Step 4: 跑测试验证通过**

```bash
uv run pytest tests/unit_tests/common/knowledge_tree/test_extractor.py -v
```

预期：全部 PASS（包括原有 `TestExperienceExtraction`、`TestEntryAExperienceIngestion`、新增三例）。

- [ ] **Step 5: 跑全套单元测试确认无回归**

```bash
uv run pytest tests/unit_tests/common/knowledge_tree -v
```

预期：全绿。

- [ ] **Step 6: Commit**

```bash
git add src/common/knowledge_tree/ingestion/extractor.py tests/unit_tests/common/knowledge_tree/test_extractor.py
git commit -m "fix(kt/extractor): framework error 门控不再被 step_failures bypass

原逻辑 `if _FRAMEWORK_ERRORS.search(combined) and not step_failures: return []`
把门控方向写反——失败任务几乎都有 step_failures，过滤被 bypass，导致
MagicMock 残留 7+ 节点。直接去掉 `and not step_failures`，基础
设施错误一律不产经验节点。"
```

---

## Task 2: filter.py 加 _INFRA_ERROR_PATTERNS 前置过滤

**Files:**
- Modify: `src/common/knowledge_tree/ingestion/filter.py`
- Test: `tests/unit_tests/common/knowledge_tree/test_filter.py`

**背景：** `filter.py:_DECISION_KEYWORDS` 把 `失败/错误/异常/崩溃` 列为加分关键词，导致 BlockingError 文本反而被加分通过。新增 `_INFRA_ERROR_PATTERNS` 前置 reject——插入位置必须**在 `user_explicit` 早返回（line 126）之后**，确保用户显式指令"记录这场 BlockingError 教训"仍能通过。

- [ ] **Step 1: 写失败测试**

追加到 `tests/unit_tests/common/knowledge_tree/test_filter.py`：

```python
class TestInfraErrorPreFilter:
    """基础设施错误文本应在 task_complete 路径前置 reject（user_explicit 仍通过）。"""

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
```

- [ ] **Step 2: 跑测试验证失败**

```bash
uv run pytest tests/unit_tests/common/knowledge_tree/test_filter.py::TestInfraErrorPreFilter -v
```

预期：前 4 例中前 3 例 FAIL（当前 BlockingError 因含"失败"关键词被加分通过），第 4、5 例 PASS。

- [ ] **Step 3: 在 filter.py 加新常量与过滤逻辑**

在 `_NEGATIVE_FACT_PATTERNS = re.compile(...)` 之后新增：

```python
# 基础设施错误模式（自动摄入前置 reject）。
# 这些是框架/运行时错误，不属于业务知识。用户显式指令不走此过滤
# （在 user_explicit 早返回之后才检查）。
_INFRA_ERROR_PATTERNS = re.compile(
    r"(BlockingError|blocking\s+call|object\s+MagicMock|"
    r"Traceback\s+\(most\s+recent|"
    r"await\s+expression|"
    r"ImportError|ModuleNotFoundError|"
    r"TypeError:.*await)",
    re.IGNORECASE,
)
```

在 `should_remember` 函数体中找到 `if trigger == "user_explicit": return FilterResult(...)`（line 125-126）之后，立即插入：

```python
    # 基础设施错误前置过滤（仅对自动摄入触发，user_explicit 已早返回通过）。
    if trigger == "task_complete" and _INFRA_ERROR_PATTERNS.search(text):
        return FilterResult(passed=False, reason="infra_error", confidence=0.0)
```

注意：必须**在** user_explicit 早返回**之后**，否则用户显式覆盖权被破坏。

- [ ] **Step 4: 跑 filter 全套测试**

```bash
uv run pytest tests/unit_tests/common/knowledge_tree/test_filter.py -v
```

预期：全绿。如已有 calibration 测试失败需逐个复查（很可能本就标定到 BlockingError 通过——如确实标定为通过，那是旧 bug 的固化，应连同 calibration 一起改）。

- [ ] **Step 5: 跑 calibration 与全套 KT 测试**

```bash
uv run pytest tests/unit_tests/common/knowledge_tree -v
```

预期：全绿。如果 `test_filter_calibration.py` 出现失败，定位该 test 用例文本是否含 BlockingError 等关键词被旧 calibration 标定通过——若被标为通过，则该 calibration 用例本身是 bug 固化，请删除该用例并 commit 注释 `# 旧 calibration 把 BlockingError 误标为通过`。

- [ ] **Step 6: Commit**

```bash
git add src/common/knowledge_tree/ingestion/filter.py tests/unit_tests/common/knowledge_tree/test_filter.py
git commit -m "feat(kt/filter): _INFRA_ERROR_PATTERNS 前置过滤基础设施错误

filter.py 的 _DECISION_KEYWORDS 把 失败/错误/异常/崩溃 列为加分
关键词，导致 BlockingError / MagicMock / Traceback 等基础设施错误
被加分通过、污染 KT。新增 _INFRA_ERROR_PATTERNS 在 user_explicit
早返回之后前置 reject，仅对 auto task_complete 触发，用户显式
覆盖权保留。"
```

---

## Task 3: filter.py 加 _TEST_TASK_PATTERNS 结构判据

**Files:**
- Modify: `src/common/knowledge_tree/ingestion/filter.py`
- Test: `tests/unit_tests/common/knowledge_tree/test_filter.py`

**背景：** hello world / test.txt / test_runner.py / tmp_test / attempt N 等明显测试任务被作为知识摄入，产生 `created_file_hello_py_wit/`、`python_open_tmp_test_txt_/` 等垃圾目录。新增结构判据（不引入词面黑名单 `test`/`echo`/`mock` 等单字避免误伤合法业务）。

- [ ] **Step 1: 写失败测试**

追加到 `tests/unit_tests/common/knowledge_tree/test_filter.py`：

```python
class TestTestTaskPreFilter:
    """测试任务的结构性模式应在 task_complete 路径前置 reject。"""

    def test_hello_world_creation_rejected(self):
        text = "Create a file named hello.py with the greet function that prints hello world."
        result = should_remember(text, trigger="task_complete")
        assert result.passed is False
        assert result.reason == "test_task_residual"

    def test_test_runner_rejected(self):
        text = "在工作区创建 test_runner.py 并执行它，输出 ok 表示测试通过。"
        result = should_remember(text, trigger="task_complete")
        assert result.passed is False
        assert result.reason == "test_task_residual"

    def test_tmp_test_txt_rejected(self):
        text = "在 workspace 目录下创建 test.txt 文件写入 hello world。"
        result = should_remember(text, trigger="task_complete")
        assert result.passed is False
        assert result.reason == "test_task_residual"

    def test_attempt_n_rejected(self):
        text = "attempt 1: 在终端执行 echo hello 测试 Executor。"
        result = should_remember(text, trigger="task_complete")
        assert result.passed is False
        assert result.reason == "test_task_residual"

    def test_legit_hello_modification_passes(self):
        """合法业务——修改 hello.py 的 greet 函数——应通过（结构判据不误伤）。"""
        text = "发现修改 hello.py 的 greet 函数为接受 name 参数时，"
        "需要同步更新调用方传入 'World'，否则会触发 TypeError。"
        result = should_remember(text, trigger="task_complete")
        assert result.passed is True

    def test_test_task_user_explicit_passes(self):
        """用户显式指令仍通过。"""
        text = "记录这次 test_runner 调试经验"
        result = should_remember(text, trigger="user_explicit")
        assert result.passed is True
        assert result.reason == "user_explicit"
```

- [ ] **Step 2: 跑测试验证失败**

```bash
uv run pytest tests/unit_tests/common/knowledge_tree/test_filter.py::TestTestTaskPreFilter -v
```

预期：4 例 reject 用例 FAIL（当前会通过），2 例 passes 用例 PASS。

- [ ] **Step 3: 在 filter.py 加新常量与过滤逻辑**

在 `_INFRA_ERROR_PATTERNS = re.compile(...)` 常量之后追加：

```python
# 测试任务结构判据：hello world / test_runner / tmp_test / attempt N 等
# 明显测试任务模式。仅匹配结构化模式，避免词面黑名单（test/echo/mock
# 等单字）误伤合法业务。仅在 task_complete 路径前置 reject。
_TEST_TASK_PATTERNS = re.compile(
    r"(hello\s+world|hello\.(?:py|js|txt)\b|"
    r"test_runner\.py|tmp_test_|_test_\d+|"
    r"\battempt\s+\d+\b)",
    re.IGNORECASE,
)
```

在 `should_remember` 中插入 infra_error 过滤**之后**：

```python
    # 测试任务结构判据（仅 auto task_complete；user_explicit 已早返回）。
    if trigger == "task_complete" and _TEST_TASK_PATTERNS.search(text):
        return FilterResult(passed=False, reason="test_task_residual", confidence=0.0)
```

- [ ] **Step 4: 跑测试验证通过**

```bash
uv run pytest tests/unit_tests/common/knowledge_tree/test_filter.py::TestTestTaskPreFilter -v
uv run pytest tests/unit_tests/common/knowledge_tree/test_filter.py -v
```

预期：全绿。若 `test_legit_hello_modification_passes` 失败，回查正则——合法业务文本应**不**含"hello world"短语（"修改 hello.py"不命中 `\bhello\.(?:py|js|txt)\b` 应是匹配的，需调整为不匹配仅创建动作）。调整方式：如失败，把 `hello\.(?:py|js|txt)\b` 收紧为 `(?:创建|create|write).*hello\.(?:py|js|txt)\b`。

- [ ] **Step 5: 跑全套 KT 测试**

```bash
uv run pytest tests/unit_tests/common/knowledge_tree -v
```

- [ ] **Step 6: Commit**

```bash
git add src/common/knowledge_tree/ingestion/filter.py tests/unit_tests/common/knowledge_tree/test_filter.py
git commit -m "feat(kt/filter): _TEST_TASK_PATTERNS 结构判据过滤测试任务残留

hello world / test_runner.py / tmp_test / attempt N 等测试任务被当
知识摄入造成 created_file_hello_py_wit/ 等 30+ 垃圾节点。新增结构
判据正则匹配测试任务模式，前置 reject 仅对 task_complete 触发，
user_explicit 保留覆盖权。仅匹配结构化模式，避免词面黑名单误伤合法业务（如修改 hello.py）。"
```

---

## Task 4: ingest metadata 注入 goal/primary_intent + title fallback

**Files:**
- Modify: `src/supervisor_agent/graph.py:1142-1200`（`_try_auto_ingest_executor_result`）
- Modify: `src/common/knowledge_tree/core.py:325-381`（`KnowledgeTree.ingest`）
- Test: 视情况在 `test_ingest.py` 或 `test_core.py` 加 title fallback 用例

**背景：** `core.py:354` 当前 `title=chunk[:50]`，目录名 `_sanitize_dirname(title)` 仅留 ASCII 导致怪异目录名 `executor_executor_blockin`。plan_json 已有结构化 `goal` 字段，优先用它当 title，零成本可读。

- [ ] **Step 1: 写 title fallback 测试**

新建 `tests/unit_tests/common/knowledge_tree/test_core.py`：

```python
"""KnowledgeTree API 单元测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.common.knowledge_tree.config import KnowledgeTreeConfig
from src.common.knowledge_tree.core import KnowledgeTree
from src.common.knowledge_tree.storage.markdown_store import MarkdownStore
from src.common.knowledge_tree.storage.overlay import OverlayStore
from src.common.knowledge_tree.storage.vector_store import InMemoryVectorStore


def _diverse_embedder(dim: int = 16):
    """多样性 embedder。"""
    def embed(text: str) -> list[float]:
        vec = [0.0] * dim
        for i, c in enumerate(text):
            idx = (ord(c) + i) % dim
            vec[idx] += 1.0
        mag = sum(x * x for x in vec) ** 0.5
        if mag > 0:
            vec = [x / mag for x in vec]
        return vec
    return embed


@pytest.fixture
def kt(tmp_path: Path) -> KnowledgeTree:
    """构造一个最简 KnowledgeTree 实例。"""
    md_store = MarkdownStore(tmp_path / "md")
    vector_store = InMemoryVectorStore(dimension=16)
    overlay_store = OverlayStore(tmp_path / "md" / ".overlay.json")
    embedder = _diverse_embedder(16)
    config = KnowledgeTreeConfig(markdown_root=tmp_path / "md")
    return KnowledgeTree(
        config=config,
        md_store=md_store,
        vector_store=vector_store,
        overlay_store=overlay_store,
        embedder=embedder,
    )


class TestIngestTitleFallback:
    def test_title_prefers_goal_metadata(self, kt: KnowledgeTree):
        """ingest 时若 metadata 含 goal，title 用 goal 前 60 字符。"""
        text = "发现 Worker 进程配置：worker_timeout 与 supervisor_timeout 必须同步修改否则进程假死。"
        report = kt.ingest(
            text,
            trigger="task_complete",
            source="auto:executor",
            metadata={"goal": "配置超时参数同步修改", "primary_intent": "修改 config.toml"},
        )
        assert report.nodes_ingested == 1
        nodes = kt.md_store.list_nodes()
        assert len(nodes) == 1
        # title 应来自 goal 而非 chunk
        assert "配置超时参数同步修改" in nodes[0].title
        # chunk 前 50 字符不应作为 title
        assert "Worker 进程" not in nodes[0].title

    def test_title_falls_back_to_primary_intent(self, kt: KnowledgeTree):
        """无 goal 但有 primary_intent 时，title 用 primary_intent 前 60 字符。"""
        text = ("发现 LangGraph 已知陷阱：在 async node 内调 os.getcwd 会触发 BlockingError。"
                "该调用需挪到 worker thread 包 asyncio.to_thread。")
        report = kt.ingest(
            text,
            trigger="task_complete",
            source="auto:executor",
            metadata={"primary_intent": "在 async 节点内规避 os.getcwd"},
        )
        assert report.nodes_ingested == 1
        nodes = kt.md_store.list_nodes()
        assert len(nodes) == 1
        assert "在 async 节点内规避 os.getcwd" in nodes[0].title

    def test_title_falls_back_to_chunk_when_no_metadata(self, kt: KnowledgeTree):
        """无 goal/primary_intent 时维持原逻辑（chunk[:50]）。"""
        text = "发现" + ("x" * 80) + "总结"  # 50+ 字符触发 sufficient_length
        report = kt.ingest(text, trigger="user_explicit", source="test")
        assert report.nodes_ingested == 1
        nodes = kt.md_store.list_nodes()
        assert len(nodes) == 1
        assert nodes[0].title == text[:50]
```

注意：先确认 `KnowledgeTree.__init__` 的参数名（见 `core.py:67`），若与上面 fixture 不符需对齐。

- [ ] **Step 2: 跑测试验证失败**

```bash
uv run pytest tests/unit_tests/common/knowledge_tree/test_core.py -v
```

预期：3 例都 FAIL（当前 `title=chunk[:50]`）。

- [ ] **Step 3: 改 core.py:354 title fallback**

`src/common/knowledge_tree/core.py` 在 `KnowledgeTree.ingest` 内找到：

```python
                node = KnowledgeNode.create(
                    node_id="",
                    title=chunk[:50],
                    content=chunk,
                    source=source,
                    metadata=meta,
                )
```

改为：

```python
                title = (
                    (metadata.get("goal") or "")[:60]
                    or (metadata.get("primary_intent") or "")[:60]
                    or chunk[:50]
                )
                node = KnowledgeNode.create(
                    node_id="",
                    title=title,
                    content=chunk,
                    source=source,
                    metadata=meta,
                )
```

- [ ] **Step 4: 改 graph.py 注入 goal/primary_intent**

`src/supervisor_agent/graph.py:_try_auto_ingest_executor_result`，将整段：

```python
    try:
        from src.common.knowledge_tree import get_or_create_kt
        from src.common.knowledge_tree.config import KnowledgeTreeConfig
        from src.common.knowledge_tree.ingestion.extractor import (
            extract_experience_from_executor_result,
            extract_knowledge_from_executor_result,
        )

        summary = _extract_executor_summary(content)
        updated_plan = _extract_updated_plan_from_executor(content) or ""

        chunks = extract_knowledge_from_executor_result(
            summary, updated_plan, exec_status
        )

        # 即使无常规知识块，也继续提取经验
        config = KnowledgeTreeConfig.from_context(ctx)
        kt = get_or_create_kt(config)
        total_ingested = 0
        for chunk in chunks:
            report = kt.ingest(
                chunk,
                trigger="task_complete",
                source="auto:executor",
                metadata={"executor_status": exec_status},
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
                metadata={
                    "node_type": "experience",
                    "executor_status": exec_status,
                },
            )
            total_ingested += exp_report.nodes_ingested
```

改为（注入 goal/primary_intent 解析 + 合并 metadata）：

```python
    try:
        from src.common.knowledge_tree import get_or_create_kt
        from src.common.knowledge_tree.config import KnowledgeTreeConfig
        from src.common.knowledge_tree.ingestion.extractor import (
            extract_experience_from_executor_result,
            extract_knowledge_from_executor_result,
        )

        summary = _extract_executor_summary(content)
        updated_plan = _extract_updated_plan_from_executor(content) or ""

        # 解析 plan_json 提取 goal / primary_intent 用于 title fallback
        goal = ""
        primary_intent = ""
        if updated_plan:
            try:
                import json as _json
                _plan_obj = _json.loads(updated_plan)
                goal = (_plan_obj.get("goal") or "").strip()
                _steps = _plan_obj.get("steps") or []
                if _steps:
                    primary_intent = (_steps[0].get("intent") or "").strip()
            except (ValueError, TypeError):
                pass

        common_meta = {
            "executor_status": exec_status,
            "goal": goal,
            "primary_intent": primary_intent,
        }

        chunks = extract_knowledge_from_executor_result(
            summary, updated_plan, exec_status
        )

        # 即使无常规知识块，也继续提取经验
        config = KnowledgeTreeConfig.from_context(ctx)
        kt = get_or_create_kt(config)
        total_ingested = 0
        for chunk in chunks:
            report = kt.ingest(
                chunk,
                trigger="task_complete",
                source="auto:executor",
                metadata=common_meta,
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
                metadata={
                    "node_type": "experience",
                    **common_meta,
                },
            )
            total_ingested += exp_report.nodes_ingested
```

注意保持原有的 logger.info 输出与 try/except 包围不变。

- [ ] **Step 5: 跑测试验证通过**

```bash
uv run pytest tests/unit_tests/common/knowledge_tree/test_core.py -v
uv run pytest tests/unit_tests/common/knowledge_tree -v
```

预期：全绿。如果有 supervisor 测试触及 `_try_auto_ingest_executor_result`，应仍通过——本次改动不影响对外行为。

- [ ] **Step 6: Commit**

```bash
git add src/common/knowledge_tree/core.py src/supervisor_agent/graph.py tests/unit_tests/common/knowledge_tree/test_core.py
git commit -m "feat(kt): title fallback 用 plan.goal 优先于 chunk[:50]

core.py:354 原 title=chunk[:50] 导致怪异目录名（executor_executor_blockin）
与检索质量差。改为优先 metadata.goal → primary_intent → chunk[:50]
fallback chain。_try_auto_ingest_executor_result 解析 plan_json 把
goal/primary_intent 注入 ingest metadata，零 LLM 成本可读。"
```

---

## Task 5: dedup_threshold 0.95 → 0.88

**Files:**
- Modify: `src/common/knowledge_tree/config.py:43`
- Modify: `src/common/context.py:343`
- Modify: `docs/environment-variables.md:146`
- Modify: `docs/architecture-decisions.md:786`

**背景：** `dedup_threshold=0.95` 阈值过高，BlockingError 文本字面差异即逃逸去重，导致同一主题多次摄入 N 个相似节点。降到 0.88 让结构高度相似的节点被合并，仍保留语义差异节点的独立性。

- [ ] **Step 1: 改 config.py:43**

```python
    dedup_threshold: float = 0.95
```

改为：

```python
    dedup_threshold: float = 0.88
```

- [ ] **Step 2: 改 context.py:343**

```python
    kt_dedup_threshold: float = field(
        default=0.95,
        metadata={
            "description": "Cosine similarity threshold for deduplication (skip if above)."
        },
    )
```

改为：

```python
    kt_dedup_threshold: float = field(
        default=0.88,
        metadata={
            "description": "Cosine similarity threshold for deduplication (skip if above)."
        },
    )
```

- [ ] **Step 3: 改 environment-variables.md**

找到 `docs/environment-variables.md:146` 那一行：

```
| `KT_DEDUP_THRESHOLD` | 0.95 | 去重阈值 |
```

改为：

```
| `KT_DEDUP_THRESHOLD` | 0.88 | 去重阈值（0.88 = 结构高度相似即合并，保留语义差异节点）|
```

- [ ] **Step 4: 改 architecture-decisions.md**

找到 `docs/architecture-decisions.md:786` 那一行：

```
| `kt_dedup_threshold` | 0.95 | 去重阈值 |
```

改为：

```
| `kt_dedup_threshold` | 0.88 | 去重阈值（0.88 = 结构高度相似即合并，保留语义差异节点）|
```

- [ ] **Step 5: 跑相关单元测试**

```bash
uv run pytest tests/unit_tests/common/knowledge_tree -v
uv run pytest tests/unit_tests/common -q
```

预期：全绿。如果 `test_ingest.py` 中有断言 `dedup_threshold=0.95` 的硬编码用例失败，那是固化了旧默认值——改为读取 config 当前值或显式传入 `dedup_threshold=0.88`。

- [ ] **Step 6: Commit**

```bash
git add src/common/knowledge_tree/config.py src/common/context.py docs/environment-variables.md docs/architecture-decisions.md
git commit -m "chore(kt): dedup_threshold 0.95 -> 0.88

0.95 阈值过高，BlockingError 文本字面差异即逃逸去重，导致同一
主题重复摄入 N 节点。0.88 让结构高度相似节点被合并，仍保留语义
差异节点的独立性。env KT_DEDUP_THRESHOLD 仍可覆盖。"
```

---

## Task 6: 新增 KnowledgeTree.delete_node 一站式删除 API

**Files:**
- Modify: `src/common/knowledge_tree/core.py`（新增 `delete_node` 方法）
- Test: `tests/unit_tests/common/knowledge_tree/test_core.py`

**背景：** `vector_store.delete_embedding(node_id)` 在 `vector_store.py:184-207` 已经一站式清理主 embedding + `title:` + `stored:` + `alias:` 前缀键。但需要再调 `md_store.delete_node` + 空目录的 `md_store.remove_directory_if_empty` + `vector_store.delete_anchor`。封装到 `KnowledgeTree.delete_node` 即可。

- [ ] **Step 1: 写失败测试**

追加到 `tests/unit_tests/common/knowledge_tree/test_core.py`：

```python
class TestDeleteNode:
    def test_delete_node_removes_md_and_embeddings(self, kt: KnowledgeTree):
        """删除节点后 md 文件、主 embedding、title:/stored:/alias: 全无。"""
        # 准备：先 ingest 一个 meta_rule 节点（带 alias）+ 一个普通节点
        common_embedder = kt.embedder
        from src.common.knowledge_tree.dag.node import KnowledgeNode
        from src.common.knowledge_tree.storage.vector_store import DirectoryAnchor
        from datetime import UTC, datetime

        # 普通节点
        kt.ingest(
            "发现重要模式：异步路径上的同步 syscall 必须包 asyncio.to_thread。",
            trigger="user_explicit",
            source="test",
        )

        # meta_rule 节点（手动注入带 alias）
        meta_node = KnowledgeNode.create(
            node_id="",
            title="异步路径禁同步 syscall",
            content="meta rule: async 节点内禁止 os.getcwd / Path.resolve() 等同步 syscall。",
            source="meta_rule",
            metadata={
                "node_type": "meta_rule",
                "priority": 1,
                "aliases": ["no sync in async", "禁同步调用"],
            },
        )
        # 模拟 ingest_nodes 的 alias 索引建立
        from src.common.knowledge_tree.ingestion.ingest import _unique_node_id, _sanitize_dirname
        dir_name = _sanitize_dirname(meta_node.title)
        kt.md_store.ensure_directory(dir_name)
        meta_node.node_id = _unique_node_id(kt.md_store, dir_name, meta_node.title)
        meta_node.directory = dir_name
        meta_node.embedding = common_embedder(meta_node.content or meta_node.title)
        kt.md_store.write_node(meta_node)
        kt.vector_store.upsert_embedding(meta_node.node_id, meta_node.embedding)
        kt.vector_store.upsert_embedding(
            f"title:{meta_node.node_id}",
            common_embedder(meta_node.title),
        )
        for i, alias in enumerate(meta_node.metadata["aliases"]):
            kt.vector_store.upsert_embedding(
                f"alias:{meta_node.node_id}:{i}",
                common_embedder(alias),
            )
        kt.vector_store.upsert_anchor(DirectoryAnchor(
            directory=dir_name,
            anchor_vector=meta_node.embedding,
            file_count=1,
            last_updated=datetime.now(UTC).isoformat(),
        ))

        node_id = meta_node.node_id
        # 前置：节点存在 + 全部索引存在
        assert kt.md_store.node_exists(node_id)
        stored_key = f"stored:{node_id}"
        # 先 mock 一个 stored 向量（debug 用：通常 stored 由 stored_vector 模块生成）
        # 测试中直接 upsert 一个 stored 占位向量验证删除覆盖
        kt.vector_store.upsert_embedding(stored_key, common_embedder("any"))
        assert node_id in kt.vector_store._embeddings
        assert f"title:{node_id}" in kt.vector_store._embeddings
        assert stored_key in kt.vector_store._embeddings

        # 调 delete_node
        result = kt.delete_node(node_id)
        assert result["ok"] is True

        # 验证：md 不存在
        assert not kt.md_store.node_exists(node_id)
        # 主 embedding 已删
        assert node_id not in kt.vector_store._embeddings
        # title/stored/alias 已删
        assert f"title:{node_id}" not in kt.vector_store._embeddings
        assert stored_key not in kt.vector_store._embeddings
        for k in kt.vector_store._embeddings:
            assert not k.startswith(f"alias:{node_id}:")

    def test_delete_node_clears_empty_directory_anchor(self, kt: KnowledgeTree):
        """删完目录最后一个节点后，目录锚点应被清。"""
        # 准备：手动写一个节点到一个独占目录并建锚点
        from src.common.knowledge_tree.dag.node import KnowledgeNode
        from src.common.knowledge_tree.storage.vector_store import DirectoryAnchor
        from datetime import UTC, datetime
        from src.common.knowledge_tree.ingestion.ingest import _unique_node_id, _sanitize_dirname

        title = "test独占目录节点"
        dir_name = _sanitize_dirname(title)
        kt.md_store.ensure_directory(dir_name)
        node = KnowledgeNode.create(
            node_id="",
            title=title,
            content="测试独占目录的删除",
            source="test",
        )
        node.node_id = _unique_node_id(kt.md_store, dir_name, title)
        node.embedding = kt.embedder(node.content or node.title)
        kt.md_store.write_node(node)
        kt.vector_store.upsert_embedding(node.node_id, node.embedding)
        kt.vector_store.upsert_embedding(f"title:{node.node_id}", kt.embedder(node.title))
        kt.vector_store.upsert_anchor(DirectoryAnchor(
            directory=dir_name,
            anchor_vector=node.embedding,
            file_count=1,
            last_updated=datetime.now(UTC).isoformat(),
        ))

        result = kt.delete_node(node.node_id)
        assert result["ok"] is True
        # 目录锚点已清
        assert kt.vector_store.get_anchor(dir_name) is None
        # 物理目录已删（可选，依赖 md_store.remove_directory_if_empty）
        # 不强制断言物理目录——只要 anchor 清了就不影响检索

    def test_delete_nonexistent_node_returns_skipped(self, kt: KnowledgeTree):
        """删除不存在的 node_id 不抛异常，返回 skipped 列表。"""
        result = kt.delete_node("not/exist_node.md")
        # 不抛异常；ok 可 True（无害）或 False，但 skipped 列表非空
        assert "not/exist_node.md" in result.get("skipped", [])
        assert result.get("deleted", []) == []
```

- [ ] **Step 2: 跑测试验证失败**

```bash
uv run pytest tests/unit_tests/common/knowledge_tree/test_core.py::TestDeleteNode -v
```

预期：3 例都 FAIL（`KnowledgeTree` 无 `delete_node` 属性）。

- [ ] **Step 3: 在 core.py 加 delete_node 方法**

`src/common/knowledge_tree/core.py`，在 `ingest` 方法结束（约 line 381）之后追加：

```python
    def delete_node(self, node_id: str) -> dict[str, Any]:
        """一站式删除节点：md 文件 + content/title/stored/alias embeddings + 空目录锚点。

        Args:
            node_id: 节点相对路径 ID（如 "patterns/async_care.md"）。

        Returns:
            结构化报告：{"ok": bool, "deleted": [node_id], "skipped": [...],
            "errors": [...]}。单点失败不中断，errors 列表收集异常。
        """
        deleted: list[str] = []
        skipped: list[str] = []
        errors: list[str] = []

        # 1. 删 md 文件
        try:
            if not self.md_store.node_exists(node_id):
                skipped.append(node_id)
                # 仍要清残留 embeddings（防御性）
                self.vector_store.delete_embedding(node_id)
                return {"ok": True, "deleted": deleted, "skipped": skipped, "errors": errors}
            self.md_store.delete_node(node_id)
            deleted.append(node_id)
        except Exception as e:
            errors.append(f"md_store.delete_node failed for {node_id}: {e}")

        # 2. 删 content/title/stored/alias embeddings（vector_store.delete_embedding 一次性）
        try:
            self.vector_store.delete_embedding(node_id)
        except Exception as e:
            errors.append(f"vector_store.delete_embedding failed for {node_id}: {e}")

        # 3. 删空目录锚点
        directory = node_id.rsplit("/", 1)[0] if "/" in node_id else ""
        if directory:
            try:
                files = self.md_store.get_directory_files(directory)
                if not files:
                    self.vector_store.delete_anchor(directory)
                    # 物理目录清理（可选，失败无害）
                    try:
                        self.md_store.remove_directory_if_empty(directory)
                    except Exception:
                        pass
            except Exception as e:
                errors.append(f"anchor/dir cleanup failed for {directory}: {e}")

        if deleted:
            self.mark_dirty()
        return {"ok": len(errors) == 0, "deleted": deleted, "skipped": skipped, "errors": errors}
```

- [ ] **Step 4: 跑测试验证通过**

```bash
uv run pytest tests/unit_tests/common/knowledge_tree/test_core.py -v
uv run pytest tests/unit_tests/common/knowledge_tree -v
```

预期：全绿。

- [ ] **Step 5: 跑 mypy 严格检查**

```bash
uv run mypy --strict src/common/knowledge_tree/core.py
```

预期：无新增错误。

- [ ] **Step 6: Commit**

```bash
git add src/common/knowledge_tree/core.py tests/unit_tests/common/knowledge_tree/test_core.py
git commit -m "feat(kt): KnowledgeTree.delete_node 一站式删除 API

封装 md_store.delete_node + vector_store.delete_embedding（已含
title:/stored:/alias: 前缀）+ 空目录 delete_anchor + remove_directory_if_empty。
单点失败不中断，返回结构化报告。供一次性脚本与未来 KT 治理工具复用。"
```

---

## Task 7: 一次性清理脚本 scripts/cleanup_kt.py

**Files:**
- Create: `scripts/cleanup_kt.py`

**背景：** 硬编码 DELETE 白名单的 10 目录（来自 spec 已核实），不靠关键词扫描避免误删。脚本支持 `--dry-run` 列出、`--diff` 与预期清单对比、`--yes` 真删。删完调 `kt.save(force=True)` 刷新 `.vector_index.json`。

- [ ] **Step 1: 写脚本**

新建 `scripts/cleanup_kt.py`：

```python
"""一次性 KT 垃圾节点清理脚本。

DELETE 白名单（spec §3 已人工核实）：10 个目录共 65 节点。
不靠关键词扫描——避免误删。脚本调 KnowledgeTree.delete_node 一站式删除，
删完 kt.save(force=True) 刷新 .vector_index.json。

Usage:
  uv run python scripts/cleanup_kt.py --dry-run       # 仅列出待删
  uv run python scripts/cleanup_kt.py --diff           # 与预期清单对比
  uv run python scripts/cleanup_kt.py --yes            # 真删
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# 允许脚本直接 uv run python scripts/xxx.py 时 import src 包
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# ruff: noqa: T201 — 脚本使用 print 输出进度

# DELETE 白名单：10 个目录（spec §3）
DELETE_DIRECTORIES = {
    "these_blocking_operations",
    "executor_executor_blockin",
    "executor_executor_typeerr",
    "executor_echo_executor_te",
    "created_file_hello_py_wit",
    "python_open_tmp_test_txt_",
    "1_data_2_data_users_j",
    "step_1",
    "step_1_nonexistent_file_y",
    "2026-07-02-001_t3_-_2026-",
}

# KEEP 白名单（spec §3）：以下目录**不**删，含真实知识
KEEP_DIRECTORIES = {
    "architecture",
    "conventions",
    "patterns",
    "setup",
    "meta_rules",
    "misc",
    "ingest",
    "knowledge_tree_ingest",
    "knowledge_tree_retrieve",
}


def list_nodes_in_delete_dirs(kt: object) -> list[str]:
    """收集 DELETE 白名单目录下所有 node_id。"""
    nodes: list[str] = []
    all_dirs = kt.md_store.list_directories()  # type: ignore[attr-defined]
    for d in all_dirs:
        if d in DELETE_DIRECTORIES:
            nodes.extend(kt.md_store.get_directory_files(d))  # type: ignore[attr-defined]
    return nodes


def cmd_dry_run(kt: object) -> int:
    nodes = list_nodes_in_delete_dirs(kt)
    print(f"[dry-run] 待删 {len(nodes)} 个节点，分布在以下目录:")
    for d in sorted(DELETE_DIRECTORIES):
        files = kt.md_store.get_directory_files(d)  # type: ignore[attr-defined]
        if files:
            print(f"  {d}/ ({len(files)})")
            for f in files:
                print(f"    - {f}")
    # 显示 KEEP 下的节点（不删，仅报告）
    keep_count = len(kt.md_store.list_node_ids()) - len(nodes)  # type: ignore[attr-defined]
    print(f"\n[KEEP] {keep_count} 个节点将保留")
    return 0


def cmd_diff(kt: object) -> int:
    """与硬编码预期集合对比。返回 0 表示零偏差。"""
    nodes = list_nodes_in_delete_dirs(kt)
    actual_dirs: set[str] = set()
    for n in nodes:
        d = n.rsplit("/", 1)[0] if "/" in n else ""
        if d:
            actual_dirs.add(d)
    expected_dirs = set(DELETE_DIRECTORIES)

    extra = actual_dirs - expected_dirs  # 实际有但白名单没列出
    missing = expected_dirs - actual_dirs  # 白名单有但实际已空
    expected_count = 65
    actual_count = len(nodes)
    count_diff = actual_count - expected_count

    print(f"期望目录: {sorted(expected_dirs)}")
    print(f"实际命中: {sorted(actual_dirs)}")
    print(f"额外目录（白名单应补）: {sorted(extra) or '<none>'}")
    print(f"预期缺失（白名单已空）: {sorted(missing) or '<none>'}")
    print(f"期望节点数: {expected_count}，实际: {actual_count}，差: {count_diff:+d}")

    if extra or missing or count_diff != 0:
        print("偏差非零，请检查白名单或 KT 状态。")
        return 1
    print("零偏差。")
    return 0


def cmd_yes(kt: object) -> int:
    nodes = list_nodes_in_delete_dirs(kt)
    if not nodes:
        print("无节点可删。")
        return 0
    print(f"开始删除 {len(nodes)} 个节点...")
    ok_count = 0
    err_count = 0
    for i, node_id in enumerate(nodes, 1):
        result = kt.delete_node(node_id)  # type: ignore[attr-defined]
        if result.get("ok"):
            ok_count += 1
            print(f"  [{i}/{len(nodes)}] deleted: {node_id}")
        else:
            err_count += 1
            print(f"  [{i}/{len(nodes)}] FAILED: {node_id} -- {result.get('errors')}")
    # 强制保存向量索引
    saved = kt.save(force=True)  # type: ignore[attr-defined]
    remaining = len(kt.md_store.list_node_ids())  # type: ignore[attr-defined]
    print(f"\n清理完成：删除 {ok_count} 个，失败 {err_count} 个。")
    print(f"向量索引保存: {'ok' if saved else 'FAILED'}")
    print(f"剩余节点: {remaining}")
    return 0 if err_count == 0 else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="KT 垃圾节点清理脚本")
    g = parser.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true", help="仅列出待删节点")
    g.add_argument("--diff", action="store_true", help="与硬编码预期清单对比")
    g.add_argument("--yes", action="store_true", help="真删")
    args = parser.parse_args()

    from src.common.knowledge_tree import get_or_create_kt
    from src.common.knowledge_tree.config import KnowledgeTreeConfig

    config = KnowledgeTreeConfig()
    kt = get_or_create_kt(config)
    # bootstrap 是幂等的，确保锚点 / 索引已就绪
    kt.bootstrap()

    if args.dry_run:
        return cmd_dry_run(kt)
    if args.diff:
        return cmd_diff(kt)
    if args.yes:
        return cmd_yes(kt)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: 跑 dry-run 验证脚本工作**

```bash
uv run python scripts/cleanup_kt.py --dry-run
```

预期：列出 65 个左右待删节点，分布在 10 个目录。

- [ ] **Step 3: 跑 diff 验证白名单与现状一致性**

```bash
uv run python scripts/cleanup_kt.py --diff
```

预期：零偏差（exit 0）。如果偏差非零：

- `额外目录`：spec §3 核实遗漏，**不要**真删——确认该目录确属垃圾后把它加入 `DELETE_DIRECTORIES`。
- `预期缺失`：当前 KT 已无该目录，跳过即可。
- `count_diff`：超出预期数说明 spec 统计低于实际，记录差异后继续。

- [ ] **Step 4: ruff 检查**

```bash
uv run ruff check scripts/cleanup_kt.py
```

预期：无错误。如有 `T201 print` 警告，确认顶部 `# ruff: noqa: T201` 已存在。

- [ ] **Step 5: Commit**

```bash
git add scripts/cleanup_kt.py
git commit -m "chore(kt): scripts/cleanup_kt.py 一次性垃圾节点清理脚本

DELETE 白名单硬编码 10 个目录共 65 节点（spec §3 已核实），不靠
关键词扫描避免误删。支持 --dry-run / --diff / --yes 三个互斥模式。
调 KnowledgeTree.delete_node 一站式删除，删完 kt.save(force=True)
刷新 .vector_index.json。--diff 与预期清单对比，零偏差才退出 0。"
```

---

## Task 8: 真实清理执行（dry-run → diff → yes）

**Files:**
- Modify: `workspace/knowledge_tree/**`（删除节点）
- Modify: `workspace/knowledge_tree/.vector_index.json`（重建）

**背景：** 在 Task 1-7 全部通过后，跑真删。这是**有副作用**的步骤——确保此前所有 unit test 已绿，所有 commit 已就绪。

- [ ] **Step 1: 跑 dry-run 二次确认**

```bash
uv run python scripts/cleanup_kt.py --dry-run > /tmp/kt_dry_run.txt
```

打开 `/tmp/kt_dry_run.txt` 人工 review 列出的目录与节点。**确认无 KEEP 白名单目录的节点出现**。如有意外目录节点出现，**不要**继续——`DELETE_DIRECTORIES` 集合需校正。

- [ ] **Step 2: 跑 diff 二次确认**

```bash
uv run python scripts/cleanup_kt.py --diff
```

预期：零偏差（exit 0）。

- [ ] **Step 3: git 备份当前 workspace/knowledge_tree**

以防万一：

```bash
git add workspace/knowledge_tree/
git commit -m "chore(kt): 备份清理前 91 节点快照"
```

- [ ] **Step 4: 执行真删**

```bash
uv run python scripts/cleanup_kt.py --yes
```

预期输出末尾：`清理完成：删除 65 个，失败 0 个。剩余节点: 26`。

- [ ] **Step 5: 验证清理结果**

```bash
uv run python -c "from src.common.knowledge_tree import get_or_create_kt; from src.common.knowledge_tree.config import KnowledgeTreeConfig; kt = get_or_create_kt(KnowledgeTreeConfig()); kt.bootstrap(); s = kt.status(); print(f\"nodes={s['total_nodes']} dirs={s['total_directories']} anchors={s['total_anchors']}\"); print('dirs:', s['directories'])"
```

预期：`nodes=26`（或近 26）、`dirs=9`（与 KEEP 9 目录一致）、无 `executor_executor_blockin` / `created_file_hello_py_wit` 等。

- [ ] **Step 6: 验证向量索引无 stale**

```bash
uv run python -c "from src.common.knowledge_tree import get_or_create_kt; from src.common.knowledge_tree.config import KnowledgeTreeConfig; kt = get_or_create_kt(KnowledgeTreeConfig()); kt.bootstrap(); ks = list(kt.vector_store._embeddings.keys()); print(f'emb_count={len(ks)}'); import collections; prefixes = collections.Counter(k.split(':', 1)[0] for k in ks); print(prefixes)"
```

预期：主节点数与 `title:` / `stored:` / `alias:` 前缀数对齐，无指向已删节点的 stale key。

- [ ] **Step 7: Commit 清理结果**

```bash
git add workspace/knowledge_tree/
git commit -m "chore(kt): 批量清理 65 个垃圾节点（91 -> 26）

清理 DELETE 白名单 10 目录共 65 节点：
- A BlockingError 诊断残留：these_blocking_operations/ (9) + executor_executor_blockin/ (6)
- B MagicMock 测试残留：executor_executor_typeerr/ (17)
- C 测试任务残留：created_file_hello_py_wit/ (11) + python_open_tmp_test_txt_/ (3) +
  1_data_2_data_users_j/ (8) + executor_echo_executor_te/ (1) + step_1_nonexistent_file_y/ (3)
- D 探测会话残留：2026-07-02-001_t3_-_2026-/ (6)
- E 截断无意义标题：step_1/ (1)

向量索引已 kt.save(force=True) 重建。下一步进入 meta_rules 审计。"
```

---

## Task 9: meta_rules 审计

**Files:**
- Modify: `workspace/knowledge_tree/meta_rules/*.md`（人工删/调整）
- 视情况：提取治理内容到 `src/common/knowledge_tree/ingestion/filter.py` 常量

**背景：** `meta_rules/` 7 条已接近 `MAX_META_RULES=15` 上限的近半。审计目的是：(1) 移除本应在代码固化的治理内容；(2) 移除过时作废规则；(3) 保留配额 ≤ 10。

- [ ] **Step 1: 逐条阅读 7 个 meta_rules**

```bash
for f in auto_ingest check_before_retry learn_from_failure proactive_ingest remember_success retrieve_before_answer smart_questioning; do
  echo "=== $f ==="
  cat "workspace/knowledge_tree/meta_rules/$f.md"
  echo
done
```

- [ ] **Step 2: 分类标记**

为每条 meta_rule 标记：
- **保留**：仍是行为级指引（不是代码实现细节）
- **删除**：内容应是 filter.py 常量或代码逻辑（治理固化）
- **删除**：决策已撤销（如决策 31）后残留的过期 hint

判断准则：
- 若规则描述的是「Agent 该做什么」（如"先检索 KT 再回答"）—— 保留
- 若规则描述的是「Entry A 应该过滤哪些内容」（如"不要记住 BlockingError"）—— 删除并提取到 filter.py 常量
- 若规则描述的是已撤销决策的执行步骤 —— 删除

- [ ] **Step 3: 对要删的 meta_rule 调 kt.delete_node**

对每条要删的规则，调脚本：

```bash
uv run python -c "
from src.common.knowledge_tree import get_or_create_kt
from src.common.knowledge_tree.config import KnowledgeTreeConfig
kt = get_or_create_kt(KnowledgeTreeConfig())
kt.bootstrap()
# 例：删除 auto_ingest.md
# 先确认实际 node_id：
for nid in kt.md_store.list_node_ids():
    if 'meta_rules/' in nid:
        print(nid)
"
```

找到对应 node_id 后：

```bash
uv run python -c "
from src.common.knowledge_tree import get_or_create_kt
from src.common.knowledge_tree.config import KnowledgeTreeConfig
kt = get_or_create_kt(KnowledgeTreeConfig())
kt.bootstrap()
result = kt.delete_node('meta_rules/<rule_filename>.md')
print(result)
kt.save(force=True)
"
```

逐条人工执行（**不要**自动化这一步，因为每条 meta_rule 内容可能需要重新组织）。

- [ ] **Step 4: 验证 meta_rules 数量 ≤ 10**

```bash
uv run python -c "
from src.common.knowledge_tree import get_or_create_kt
from src.common.knowledge_tree.config import KnowledgeTreeConfig
kt = get_or_create_kt(KnowledgeTreeConfig()); kt.bootstrap()
meta = [n for n in kt.md_store.list_node_ids() if n.startswith('meta_rules/')]
print(f'meta_rules count: {len(meta)}')
for m in meta: print(' -', m)
"
```

预期：`count ≤ 10`。

- [ ] **Step 5: 视情况：把"如何过滤 BlockingError"提取到 filter.py 常量**

如果某条被删的 meta_rule 内容是"Entry A 应过滤基础设施错误"——这部分**已经在** Task 2 的 `_INFRA_ERROR_PATTERNS` 中被代码固化。无需额外动作。

如果 meta_rule 内容是其它应固化但 Task 2-3 未覆盖的——评估是否需要再加 filter 常量。如不需要，仅删除即可。

- [ ] **Step 6: Commit**

```bash
git add workspace/knowledge_tree/meta_rules/
git commit -m "chore(kt/meta_rules): 审计删除过期/治理内容 meta_rule

逐条审 7 条 meta_rule：
- 删除：[列出删除的规则名]——[简短理由]
- 保留：[列出保留的规则名]
meta_rules 节点数 X -> Y，远离 MAX_META_RULES=15 上限。"
```

---

## Task 10: 最终验收 — lint + 全套单元测试

**Files:**
- 无新文件，仅运行检查

- [ ] **Step 1: ruff check + format**

```bash
uv run ruff check src/ tests/ scripts/
uv run ruff format --check src/ tests/ scripts/
```

预期：全绿。如有问题跑 `uv run ruff format src/ tests/ scripts/` 再跑 `uv run ruff check --fix src/ tests/ scripts/`。

- [ ] **Step 2: mypy 严格检查**

```bash
uv run mypy --strict src/
```

预期：无新增错误。Task 6 的 `delete_node` 可能引入类型告警，必要时加 `-> dict[str, Any]` 类型注解（已在代码中）。

- [ ] **Step 3: 全套单元测试**

```bash
uv run pytest tests/unit_tests -v
```

预期：全绿。

- [ ] **Step 4: 集成测试（无 LLM 部分）**

```bash
uv run pytest tests/integration_tests -v -m "not live_llm"
```

预期：全绿。

- [ ] **Step 5: 最终 KT 状态快照**

```bash
uv run python -c "
from src.common.knowledge_tree import get_or_create_kt
from src.common.knowledge_tree.config import KnowledgeTreeConfig
kt = get_or_create_kt(KnowledgeTreeConfig()); kt.bootstrap()
s = kt.status()
print(f'nodes={s[\"total_nodes\"]} dirs={s[\"total_directories\"]} anchors={s[\"total_anchors\"]}')
print('directories:')
for d in s['directories']: print(' -', d)
print('anchor_dirs:')
for a in s['anchor_directories']: print(' -', a)
"
```

预期：`nodes=26`（或近 26）、`dirs=9`、`anchors=9`、目录列表与 KEEP 白名单一致。

- [ ] **Step 6: 验收清单逐条确认**

对照 spec §8 验收清单：

- [ ] 91 → ≤30 节点（Task 8 Step 5 已验证）
- [ ] 10 目录全部清空并从 status 中消失（Task 8 Step 5 已验证）
- [ ] `kt.delete_node` 单元测试全绿（Task 6 Step 4）
- [ ] 9 例新单元测试全绿（Task 1-3 + Task 4）
- [ ] `--dry-run --diff` 零偏差（Task 7 Step 3 / Task 8 Step 2）
- [ ] `make lint` 通过（Task 10 Step 1-2）
- [ ] `make test_unit` 通过（Task 10 Step 3）
- [ ] meta_rules 节点数 ≤10（Task 9 Step 4）
- [ ] 新会话不再产生 hello/test_runner/MagicMock/BlockingError 类节点（手动验证一次：跑个 hello.py 测试任务，看 KT 是否增长——本任务非自动验证）

- [ ] **Step 7: 最终 commit（如有 lint 修复）**

```bash
git status
# 若有未提交的修复
git add -A
git commit -m "chore: lint + 验收收尾"
```

---

## 整体顺序图

```
Task 1 (extractor 门控 bug)
  -> Task 2 (filter infra_error)
  -> Task 3 (filter test_task)
  -> Task 4 (title fallback + graph 注入)
  -> Task 5 (dedup 0.88)
  -> Task 6 (delete_node API)
  -> Task 7 (cleanup script)
  -> Task 8 (真删 65 节点)
  -> Task 9 (meta_rules 审计)
  -> Task 10 (lint + 验收)
```

每个 Task 完成即 commit。Task 8 是唯一有副作用步骤，确保 Task 1-7 全绿后再跑。