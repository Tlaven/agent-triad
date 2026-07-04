# KT 清理与 Entry A 摄入过滤加固设计

- 日期：2026-07-04
- 状态：Draft（待 review）
- 作者：opencode + 用户
- 关联：决策 29（宁缺毋滥）/ 决策 32（failed 节点带 [失败教训] 前缀）/ `docs/architecture-decisions.md`

---

## 1. 背景与问题

知识树（KT）现有 91 节点 / 20 目录，其中约 65 节点为垃圾，根因是оном `src/supervisor_agent/graph.py:_try_auto_ingest_executor_result` 的 Entry A 自动摄入严重失效：

1. **不过滤测试任务**：hello world / test.txt / echo 等明显测试任务全部被当知识摄入。
2. **基础设施错误被当知识**：BlockingError / MagicMock / TypeError 不属于业务知识但被摄入。诊断注明方向居然相反——`filter.py:_DECISION_KEYWORDS` 把 `失败/错误/异常/崩溃` 列入加分关键词，使 BlockingError 文本反而被加分。
3. **`extractor.py:154` 的过滤门控写反**：`if _FRAMEWORK_ERRORS.search(combined) and not step_failures: return []`——失败任务几乎都有 step_failures，门控被 bypassed，MagicMock 残留 7+ 节点由此产生。
4. **标题用 chunk 前 N 字符**：`core.py:354` `title=chunk[:50]`，目录名 `_sanitize_dirname(title)` 仅保留 ASCII，导致怪异目录名（`executor_executor_blockin`）与检索质量差。
5. **同主题重复摄入**：`dedup_threshold=0.95` 过高，BlockingError 触发 N 次产生 N 个相似节点。
6. 决策 29「宁缺毋滥」三级垃圾检测没识别这些明显模式。

## 2. 目标与成功标准

| | 标准 |
|---|---|
| 清理 | 节点数 91 → ≤ 30；删除 10 个已知垃圾目录；向量索引无 stale embedding（含 `title:` / `alias:` / `stored:`） |
| 加固 | 重跑一段会摄入垃级任务的会话后，KT 节点数不增长 |
| 误伤 | 合法业务（含 `hello.py` 修改、查询 `data_users.json` 等场景）仍被摄入 |
| 标题 | 新摄入节点 title 优先使用 plan goal / primary intent，可读 |
| 副作用 | `mass_actions` 失败节点（节点无主题）不应被摄入 |

非目标：不重构 V4 KT 的存储/检索；不上 LLM 标题生成；不引入 RAG 加权打分模型。

## 3. 现状分类（已人工核实）

### KEEP 目录（9 个，真实知识 ~26 节点）

`architecture/`（5）、`conventions/`（3）、`patterns/`（4）、`setup/`（2）、`meta_rules/`（7，需审计）、`misc/`（2）、`ingest/`（1）、`knowledge_tree_ingest/`（1）、`knowledge_tree_retrieve/`（1）

### DELETE 整目录（10 个，65 节点）

| 目录 | 节点数 | 类别 |
|------|------:|------|
| `these_blocking_operations/` | 9 | A BlockingError 诊断残留 |
| `executor_executor_blockin/` | 6 | A |
| `executor_executor_typeerr/` | 17 | A + B MagicMock 残留 |
| `executor_echo_executor_te/` | 1 | C 测试任务残留 |
| `created_file_hello_py_wit/` | 11 | C |
| `python_open_tmp_test_txt_/` | 3 | C |
| `1_data_2_data_users_j/` | 8 | C |
| `step_1/` | 1 | E 截断无意义标题 |
| `step_1_nonexistent_file_y/` | 3 | C + E |
| `2026-07-02-001_t3_-_2026-/` | 6 | D 探测会话残留 |

合计 65 节点（=91 − 保留 26）。

## 4. 设计

### 4.1 新增删除 API

`src/common/knowledge_tree/core.py` 新增方法：

```python
def delete_node(self, node_id: str) -> dict[str, Any]:
    """一站式删除：md 文件 + content/title/stored/alias embeddings + 空目录锚点。"""
```

实现要点：
- 调用 `md_store.delete_node(node_id)` 删 `.md`
- 调用 `vector_store.delete_embedding(node_id)` 删 content embedding
- 调用 `vector_store.delete_embedding(f"title:{node_id}")` 删 title（容错：不存在静默）
- 调用 `vector_store.delete_embedding(f"stored:{node_id}")` 删 stored（容错）
- 扫 `f"alias:{node_id}:"` 前缀删全部 alias embedding（meta_rule 才有）
- 若删后目录为空：`vector_store.delete_anchor(directory)`
- 全程 try/except 单点失败不中断；返回 `{"ok": bool, "deleted": [...], "skipped": [...], "errors": [...]}`
- 末尾 `self.mark_dirty()`（与 `ingest` 一致）

### 4.2 过滤器加固（最小集，3 处）

**4.2.1 extractor.py:154 修门控 bug**

```python
# Before
if _FRAMEWORK_ERRORS.search(combined) and not step_failures:
    return []
# After
if _FRAMEWORK_ERRORS.search(combined):
    return []
```

无论有无 step_failures，基础设施错误一律不摄入经验节点。失败任务的经验提炼在前一句 `if not step_failures and len(summary)<20: return []` 已经保护。

**4.2.2 filter.py 加 _INFRA_ERROR_PATTERNS 前置过滤**

```python
_INFRA_ERROR_PATTERNS = re.compile(
    r"(BlockingError|blocking\s+call|object\s+MagicMock|"
    r"Traceback\s+\(most\s+recent|await\s+expression|"
    r"ImportError|ModuleNotFoundError|"
    r"TypeError:.*awaitable)",
    re.IGNORECASE,
)
```

在 `should_remember` 中插入位置 **必须** 在 `user_explicit` 早返回之后（line 126），确保用户显式指令（"记录这场 BlockingError 教训"）不被新过滤拦截：

```python
if trigger == "task_complete" and _INFRA_ERROR_PATTERNS.search(text):
    return FilterResult(passed=False, reason="infra_error", confidence=0.0)
```

仅对自动摄入触发。

**4.2.3 filter.py 加 _TEST_TASK_PATTERNS 结构判据**

```python
_TEST_TASK_PATTERNS = re.compile(
    r"(创建.*hello\.(?:py|js|txt)|write\s+hello\s+world|"
    r"创建.*test\.txt|创建.*test_runner\.py|"
    r"tmp_test_|_test_\d+|"
    r"echo\s+(hello|hi|test)\s*$|"
    r"\battempt\s+\d+\b)",
    re.IGNORECASE,
)
```

紧跟 _INFRA_ERROR_PATTERNS 之后（同样在 `user_explicit` 早返回之后）：

```python
if trigger == "task_complete" and _TEST_TASK_PATTERNS.search(text):
    return FilterResult(passed=False, reason="test_task_residual", confidence=0.0)
```

不引入词面黑名单（test/echo/mock 等单字），仅匹配结构化模式以减少误伤。

### 4.3 标题生成 fallback

**4.3.1 graph.py``_try_auto_ingest_executor_result` 注入 plan metadata**

解析 updated_plan_json 后把 `goal` 与首步 `intent` 注入 metadata：

```python
goal = ""
primary_intent = ""
if updated_plan:
    try:
        plan_obj = json.loads(updated_plan)
        goal = plan_obj.get("goal", "")
        steps = plan_obj.get("steps", [])
        if steps:
            primary_intent = steps[0].get("intent", "")
    except (json.JSONDecodeError, TypeError):
        pass

common_meta = {"executor_status": exec_status, "goal": goal, "primary_intent": primary_intent}
# 知识块与经验节点分别合并 common_meta
```

**4.3.2 core.py:354 改 title 优先顺序**

```python
title = (
    metadata.get("goal", "")[:60]
    or metadata.get("primary_intent", "")[:60]
    or chunk[:50]
)
```

经验节点的 extractor.py:189 已用 `[经验] {context[:30]}`（context=goal），无需修改。

### 4.4 dedup_threshold 调整

`KnowledgeTreeConfig.from_context` 的默认值 0.95 → 0.88。env `KT_DEDUP_THRESHOLD` 已存在（见 `src/common/context.py:343` 与 `docs/environment-variables.md:146`），仅需同步：`config.py:43` 默认值 + `context.py:343` 默认值 + `docs/environment-variables.md:146` 表格 + `docs/architecture-decisions.md:786` 表格。

### 4.5 一次性清理脚本

`scripts/cleanup_kt.py`：

```
USAGE
  uv run python scripts/cleanup_kt.py --dry-run       # 仅列出
  uv run python scripts/cleanup_kt.py --diff          # 与预期清单对比
  uv run python scripts/cleanup_kt.py --yes           # 真删
```

行为：
1. 实例化 `KnowledgeTreeConfig.from_context(Context.from_env())` + `get_or_create_kt(config)` + `kt.bootstrap()`（幂等）
2. 内置**白名单**（DELETE 整目录列表，硬编码于脚本顶部常量），不靠关键词扫描——避免误删。
3. 列出每个目录的全部 `.md` node_id，逐个调 `kt.delete_node(node_id)`
4. 全部完成后调 `kt.save(force=True)` 刷新 `.vector_index.json`
5. 输出统计：删除数、保留数、最终节点数、目录数
6. `--diff` 与硬编码预期列表（10 目录 65 节点）做集合对称差，差集非空则 non-zero exit

### 4.6 meta_rules 审计

`meta_rules/` 7 条逐条审：

- 含"如何过滤 BlockingError / MagicMock"这类本应在代码固化的治理内容 → 把规则内容提取为 `filter.py` 常量并删除元规则
- 含过期作废规则（如决策 31 已撤销后残留的 strip 逻辑 hint）→ 删除
- 配额保护：保留 ≤ 10 条（远离 MAX_META_RULES=15 上限）

## 5. 测试设计

### 5.1 单元测试（无 LLM）

**`tests/unit_tests/common/knowledge_tree/test_extractor.py`** 新增 4 例：

1. `test_framework_error_with_step_failures_filtered`：summary 含 "object MagicMock"，steps 含 failure_reason → `extract_experience_from_executor_result` 返回 `[]`（回归门控 bug）
2. `test_blocking_error_summary_filtered`：summary 含 "BlockingError: ... raised in scope" → 返回 `[]`
3. `test_legit_hello_modification_kept`：goal="修改 hello.py 的 greet 函数为接受 name 参数"; summary 含决策性内容 → 经验节点被提炼
4. `test_structural_test_runner_task_filtered`：goal="执行一个简单测试"; steps[0].intent="Run test_runner.py"; summary="test_runner executed" → 被拦

**`tests/unit_tests/common/knowledge_tree/test_filter.py`** 新增：

5. `test_infra_error_pattern_rejects_task_complete`：BlockingError 文本 trigger=task_complete → reject；trigger=user_explicit → still pass
6. `test_test_task_pattern_rejects_structural`：hello.py 创建 + test_runner → reject；含 hello.py 的业务修改 summary → pass

**`tests/unit_tests/common/knowledge_tree/test_core.py`**（新建）新增 `test_delete_node`：

7. 准备一个 KT 实例（mock embedder），ingest 1 节点 + 1 meta_rule 节点
8. 调 `kt.delete_node(node_id)` → 断言 md 不存在、`title:` / `stored:` / `alias:` / 主 embedding 全无、空目录 anchor 被清
9. 调 `kt.delete_node("not/exist")` → 返回 `{"ok": False, "skipped": [...]}` 不抛

### 5.2 脚本验证

`uv run python scripts/cleanup_kt.py --dry-run --diff` 与硬编码预期集合（"these_blocking_operations;executor_executor_blockin;..."）做对称差，零偏差才算通过。

不写自动化 e2e 验证（避免引真 LLM）。

## 6. 实施顺序

1. 代码层加固（4.2.1 / 4.2.2 / 4.2.3 / 4.3 / 4.4）
2. 单元测试更新（5.1 全部 9 例）
3. `delete_node` API（4.1）+ 单元测试
4. `scripts/cleanup_kt.py`（4.5）
5. `--dry-run --diff` 验证 → `--yes` 真删 → `kt.save`
6. meta_rules 审计（4.6）
7. `make lint` + `make test_unit`

## 7. 风险与缓解

| 风险 | 缓解 |
|------|------|
| 新过滤器误伤合法业务 | `_TEST_TASK_PATTERNS` 仅匹配结构化模式；加正向测试用例 ③ ④ |
| `delete_node` 漏删 alias/stored 留 stale embedding | 单元测试 8 覆盖；脚本 `kt.save(force=True)` 后下次 bootstrap 用 manifest freshness 强制重建 |
| dedup 0.88 过于激进 | env 可调；先单元小步验证；保留通过 `--diff` 即可视测 |
| 修 extractor.py:154 后失败任务经验提炼减少 | 失败任务仍有 `extract_knowledge_from_executor_result` 路径（也走 should_remember 过滤），只是经验结构化节点不再生成无关 MagicMock 经验；这是期望行为 |
| 用户回测后再跑会话产生新垃圾 | `--diff` 可重复验证 |

## 8. 验收清单

- [ ] 91 → ≤30 节点（脚本输出确认）
- [ ] `these_blocking_operations/` 等 10 目录全部清空并从状态返回中消失
- [ ] `kt.delete_node` 单元测试全绿
- [ ] 9 例新单元测试全绿
- [ ] `--dry-run --diff` 零偏差
- [ ] `make lint` 通过
- [ ] `make test_unit` 通过
- [ ] meta_rules 节点数 ≤10
- [ ] 新会话不再产生 hello/test_runner/MagicMock/BlockingError 类节点（手动验证一次）

## 9. 后续工作（不在本次范围）

- RAG 加权打分式过滤器
- LLM 提取主题作 title（若后续 fallback 仍不满意）
- `knowledge_tree_bulk_delete` 暴露给 Agent（需权限审计）
- 周期性 KT 自检任务（cron / langgraph schedule）