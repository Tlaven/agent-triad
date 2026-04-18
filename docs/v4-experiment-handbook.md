# V4 知识树实验手册

> 解决核心痛点：**LLM/Embedding 调用贵、闭环周期长、三层一致性难验证**。
> 原则：能用 mock 验证的不用真实模型；能单层验证的不做全链路。

---

## 1. 成本分层

| 层级 | 依赖 | 单次耗时 | 单次成本 | 适用场景 |
|------|------|---------|---------|---------|
| L0 纯内存 | 无 | <10ms | 0 | 数据模型、存储 CRUD、过滤规则 |
| L1 Mock 闭环 | mock LLM + mock embedder | <50ms | 0 | 检索路由、融合、编辑、优化信号 |
| L2 真实 Embed | sentence-transformers | 1-3s | 0（本地） | 聚类质量、去重精度、检索相关性 |
| L3 真实 LLM | API 调用 | 5-30s | ¥0.01-0.5 | 路由准确率、闭环收敛性 |
| L4 完整闭环 | LLM + Embed + 多轮 | 1-5min | ¥1-10 | 端到端验收、回归 |

**日常开发停在 L0-L1，L2+ 仅在关键节点前执行。**

---

## 2. Mock 策略矩阵

| 组件 | L0 | L1 | L2 | L3-L4 |
|------|----|----|----|----|
| GraphStore | InMemory | InMemory | InMemory | InMemory（P1 无 Kùzu） |
| VectorStore | InMemory | InMemory | InMemory | InMemory |
| MarkdownStore | tmp_path | tmp_path | tmp_path | tmp_path |
| Embedder | 哈希 embedder | 哈希 embedder | **真实模型** | **真实模型** |
| LLM | — | `make_mock_llm` | — | **真实 API** |
| 时间 | — | 手动注入 | 手动注入 | 真实 |

### 哈希 Embedder（L0-L1 默认）

```python
def mock_embedder(dim=16):
    """字符位置加权 → 不同文本产生正交向量。"""
    def embed(text):
        vec = [0.0] * dim
        for i, c in enumerate(text):
            vec[(ord(c) + i) % dim] += 1.0
        mag = sum(x*x for x in vec) ** 0.5
        return [x/mag for x in vec] if mag > 0 else vec
    return embed
```

### Mock LLM 路由决策（L1）

```python
def mock_router(child_index: int, confidence: float):
    """固定路由：返回指定的子节点和置信度。"""
    from unittest.mock import MagicMock
    import json
    llm = MagicMock()
    llm.invoke.return_value = json.dumps({
        "selected_index": child_index,
        "confidence": confidence,
    })
    return llm
```

### 快速建树 Fixture

```python
@pytest.fixture
def tree_with_data(tmp_path):
    """3 层 × 3 叶节点的预构建知识树（<5ms）。"""
    config = KnowledgeTreeConfig(
        markdown_root=tmp_path/"md", db_path=tmp_path/"db",
        tree_nav_confidence=0.5,
    )
    kt = KnowledgeTree(config, embedder=mock_embedder())
    # ... bootstrap seed files ...
    return kt
```

---

## 3. 确定性测试配方

### 3.1 检索路由 — 不需要 LLM

**痛点**：每次检索都调 LLM 太贵。
**解法**：Mock LLM 返回固定决策，只测路由逻辑。

```python
kt.llm = mock_router(child_index=0, confidence=0.9)
result, log = kt.retrieve("任意查询")
assert result.fusion_mode == "tree"
assert log.tree_confidence == 0.9
```

**覆盖场景**（全部 L1，<1ms/条）：

| 场景 | mock 参数 | 期望 fusion_mode |
|------|----------|-----------------|
| 高置信命中 | `index=0, conf=0.9` | `tree` |
| 低置信兜底 | `index=-1, conf=0.1` | `rag` 或 `none` |
| 树空 | 不设 llm | `rag` 或 `none` |

### 3.2 三层一致性 — 不需要任何模型

**痛点**：编辑后三层可能不同步。
**解法**：操作后断言三个 store。

```python
# 编辑后检查一致性
delta = kt.edit("split", {"node_id": nid, "splits": [...]})
assert delta is not None

# Layer 1: Markdown
assert kt.md_store.node_exists(new_child_id)
# Layer 2: Graph
assert kt.graph_store.get_node(new_child_id) is not None
# Layer 3: Vector
assert kt.vector_store.get_embedding(new_child_id) is not None
```

### 3.3 摄入管道 — 分段验证

**痛点**：完整摄入链太长，失败难定位。
**解法**：每一步独立可测。

```python
# ① 切分（L0，无依赖）
chunks = chunk_text(long_text, max_tokens=512)
assert all(_estimate_tokens(c) <= 512 for c in chunks)

# ② 过滤（L0，无依赖）
result = should_remember(chunk, trigger="task_complete")
assert result.passed is True

# ③ 去重 + 嫁接（L1，需预构建树）
report = ingest_nodes([node], graph_store, vector_store, md_store, embedder)
assert report.nodes_ingested == 1

# ④ 完整管道（L1）
report = kt.ingest(text, trigger="task_complete")
```

### 3.4 优化信号 — 时间控制

**痛点**：信号积累需要长时间运行。
**解法**：直接构造 RetrievalLog 列表。

```python
# 不需要等待，直接注入失败日志
for _ in range(5):
    _, log = kt.retrieve("查询")
    kt.record_feedback(log.query_id, satisfaction=False)

report = kt.optimize()
assert report.signals_detected > 0
```

### 3.5 去重精度 — 需要真实 Embedder（L2）

这是**唯一必须用真实 embedder** 的测试：

```python
# pytest -m needs_embedding
from sentence_transformers import SentenceTransformer
model = SentenceTransformer("BAAI/bge-small-zh-v1.5")

embedder = lambda text: model.encode(text).tolist()
kt = KnowledgeTree(config, embedder=embedder)
# ... bootstrap ...

# 测试语义相似但不同的文本
r1 = kt.ingest("LangGraph 状态管理使用 TypedDict 定义。")
r2 = kt.ingest("LangGraph 通过 TypedDict 管理状态模式。")  # 语义相似
assert r2.nodes_deduplicated == 1 or r2.nodes_ingested == 1  # 取决于阈值
```

---

## 4. 实验协议

### 4.1 闭环收敛验证（L3）

**目的**：验证 ingest → retrieve → edit → optimize 能收敛。
**前置**：真实 LLM + mock embedder（省 embed 成本，保留路由能力）。
**预算**：约 5-10 次 LLM 调用，¥0.1-0.5。

```
1. Bootstrap 种子树（5+ 叶节点）
2. Ingest 3 条新知识（trigger=task_complete）
3. 检索每条新知识 → 验证能命中
4. edit(merge) 两个相似节点
5. optimize() → 验证无报错
6. 重新检索 → 验证合并后仍可命中
```

**断言清单**：
- [ ] ingest 后 total_nodes 增加
- [ ] retrieve 命中新节点或其父 group
- [ ] merge 后两节点合并为一个
- [ ] optimize 返回 signals_detected ≥ 0
- [ ] merge 后 retrieve 仍可检索到内容

### 4.2 聚类质量验证（L2）

**目的**：种子数据的聚类是否产生有意义的分组。
**前置**：真实 embedder + mock LLM。
**预算**：首次需下载模型（~100MB），后续 0 成本。

```
1. 准备 10+ 条领域知识种子
2. Bootstrap
3. 检查 group 分组是否语义合理
4. 注入边界情况（完全不同领域）→ 验证创建新 group
```

**断言清单**：
- [ ] 相关知识在同一 group
- [ ] 不同领域在不同 group
- [ ] 新领域知识自动创建新 group

### 4.3 长期运行模拟（L1）

**目的**：模拟 100+ 次检索后的优化信号和防震荡。
**前置**：全部 mock，零成本。

```python
# 快速模拟 100 次检索
for i in range(100):
    _, log = kt.retrieve(f"查询{i}")
    kt.record_feedback(log.query_id, satisfaction=(i % 3 != 0))  # 1/3 不满意

report = kt.optimize()
# 验证防震荡：优化次数不超过限额
assert report.actions_planned <= config.max_optimizations_per_window
```

---

## 5. 诊断工具

### 5.1 三层一致性检查器

```python
def check_consistency(kt: KnowledgeTree) -> list[str]:
    """一键检查三层存储是否一致。"""
    issues = []
    for node_id in kt.md_store.list_node_ids():
        if kt.graph_store.get_node(node_id) is None:
            issues.append(f"MD 有但 Graph 无: {node_id[:8]}")
        if kt.vector_store.get_embedding(node_id) is None:
            issues.append(f"MD 有但 Vector 无: {node_id[:8]}")
    for node_id in (kt.graph_store._nodes or {}):
        if not kt.md_store.node_exists(node_id):
            issues.append(f"Graph 有但 MD 无: {node_id[:8]}")
    return issues
```

### 5.2 检索日志分析器

```python
def analyze_retrieval_logs(kt: KnowledgeTree) -> dict:
    """分析检索日志，快速定位问题。"""
    logs = kt._retrieval_logs
    if not logs:
        return {"total": 0}
    modes = {}
    for log in logs:
        modes[log.fusion_mode] = modes.get(log.fusion_mode, 0) + 1
    satisfied = sum(1 for l in logs if l.agent_satisfaction is True)
    return {
        "total": len(logs),
        "modes": modes,
        "satisfaction_rate": satisfied / len(logs),
        "rag_rate": modes.get("rag", 0) / len(logs),
    }
```

### 5.3 树结构可视化（调试用）

```python
def print_tree(kt: KnowledgeTree, max_depth: int = 3):
    """打印树结构，快速目视检查。"""
    root_id = kt.graph_store.get_root_id()
    if not root_id:
        print("(empty tree)")
        return

    def _walk(node_id, depth=0):
        node = kt.graph_store.get_node(node_id)
        prefix = "  " * depth + ("├─ " if depth > 0 else "")
        title = node.title[:30] if node else "?"
        print(f"{prefix}{title} [{node_id[:8]}]")
        if depth < max_depth:
            for child in kt.graph_store.get_children(node_id):
                _walk(child.node_id, depth + 1)

    _walk(root_id)
```

---

## 6. Makefile 集成

```makefile
# 知识树专项测试
test_kt_unit:       ## 知识树 L0+L1（<1s, ¥0）
	uv run pytest tests/unit_tests/common/knowledge_tree/ -q

test_kt_integration: ## 知识树闭环（<2s, ¥0）
	uv run pytest tests/integration/test_knowledge_tree_loop.py -v

test_kt_embedding:  ## L2 聚类+去重精度（需模型, ~5s）
	uv run pytest tests/ -m needs_embedding -v

test_kt_e2e:        ## L3+L4 完整闭环（需 API key, ~2min）
	uv run pytest tests/e2e/ -m "kt_e2e and live_llm" -v
```

---

## 7. 速查：测试什么用什么层

| 我要验证… | 层级 | 用什么 | 耗时 |
|-----------|------|--------|------|
| KnowledgeNode 序列化 | L0 | 纯 pytest | <1ms |
| InMemoryStore CRUD | L0 | 纯 pytest | <1ms |
| 过滤规则正确性 | L0 | `should_remember()` | <1ms |
| 切分粒度 | L0 | `chunk_text()` | <1ms |
| JSON Patch 生成/应用 | L0 | 纯 pytest | <1ms |
| 检索路由逻辑 | L1 | mock LLM | <10ms |
| 融合模式切换 | L1 | mock LLM + embedder | <10ms |
| 编辑 + 三层同步 | L1 | mock embedder | <10ms |
| 优化信号检测 | L1 | 注入日志 | <10ms |
| 增量 ingest | L1 | mock embedder | <10ms |
| 去重精度 | L2 | 真实 embedder | ~1s |
| 聚类语义质量 | L2 | 真实 embedder | ~3s |
| 路由准确率 | L3 | 真实 LLM | ~10s |
| 闭环收敛 | L3 | 真实 LLM + mock embed | ~30s |
| 端到端验收 | L4 | 真实 LLM + embed | ~2min |
