# V4 涌现式知识树 — P1 技术规格

> 状态：初版（2026-04-17）
> 前置：概念对齐（`v4-knowledge-tree-concepts.md`）、架构决策 18-25（`architecture-decisions.md`）
> 范围：P1 最小闭环实现

---

## 1. 概览

### 1.1 P1 目标

跑通端到端闭环：**Bootstrap 建树 → LLM 路由检索 → Agent merge/split 编辑 → 三层同步 → Change Mapping → 向量重嵌入 → 检索日志 → 异步优化 → 知识摄入管道**。

### 1.2 P1 范围约束

- 信息类型：仅领域知识
- 编辑操作：内容编辑 + merge/split
- Delta 格式：JSON Patch (RFC 6902)
- DAG 遍历：主路径单遍历
- 图数据库：Kùzu v0.11.x
- Agent 接口：Supervisor 工具注册

### 1.3 最小闭环流程

```
┌─────────────┐     ┌──────────────┐     ┌──────────────────┐
│  Bootstrap   │────►│  Retrieve    │────►│  Agent Edit      │
│  (种子数据)   │     │ (LLM+RAG)    │     │ (merge/split)    │
└─────────────┘     └──────┬───────┘     └────────┬─────────┘
                           │                       │
                    ┌──────▼───────┐        ┌──────▼─────────┐
                    │  Retrieval   │        │  Change Mapping │
                    │  Log (JSON)  │        │ (JSON Patch)    │
                    └──────┬───────┘        └──────┬─────────┘
                           │                       │
                    ┌──────▼───────────────────────▼──────┐
                    │          Async Optimization          │
                    │  (信号检测 → 频率控制 → 执行优化)     │
                    └──────────────┬───────────────────────┘
                                   │
                    ┌──────────────▼───────────────────────┐
                    │        Ingestion Pipeline (新增)       │
                    │  事件触发 → 切分 → 过滤 → 去重 → 入树   │
                    └──────────────────────────────────────┘
```

---

## 2. 模块结构

```
src/common/knowledge_tree/
    __init__.py              # 公共 API（KnowledgeTree 门面类）
    config.py                # KnowledgeTreeConfig dataclass
    bootstrap.py             # 从种子数据建树
    storage/
        __init__.py
        markdown_store.py    # Layer 1: Markdown 文件 CRUD
        graph_store.py       # Layer 2: 图数据库抽象接口 + Kùzu 实现
        vector_store.py      # Layer 3: 向量索引操作
        sync.py              # 跨层同步（Markdown ↔ Graph ↔ Vector）
    dag/
        __init__.py
        node.py              # KnowledgeNode dataclass
        edge.py              # KnowledgeEdge dataclass
    retrieval/
        __init__.py
        router.py            # LLM 路由树导航
        rag_fallback.py      # 向量相似度检索
        fusion.py            # 结果融合（tree/tree+rag/rag/none）
        log.py               # RetrievalLog 结构化日志
    editing/
        __init__.py
        merge_split.py       # merge/split 操作
        change_map.py        # JSON Patch Delta 生成与校验
        re_embed.py          # 受影响节点局部重嵌入
    optimization/
        __init__.py
        signals.py           # 4 种信号检测
        optimizer.py         # 异步批量优化器
        anti_oscillation.py  # 频率控制（独立阈值 + 全局限额）
    ingestion/
        __init__.py
        chunker.py           # 原子切分（P1: \n\n + 对话轮边界）
        filter.py            # 轻量规则过滤
        ingest.py            # ingest_nodes() 增量嫁接
```

---

## 3. 数据模型

### 3.1 KnowledgeNode（`dag/node.py`）

```python
from dataclasses import dataclass, field
from typing import Any

@dataclass
class KnowledgeNode:
    """知识树叶子/中间节点"""
    node_id: str                           # UUID 或确定性哈希
    title: str                             # 节点标题
    content: str                           # 节点正文内容
    source: str                            # 来源标识
    created_at: str                        # ISO 8601 时间戳
    summary: str = ""                      # 摘要（用于树导航路由）
    embedding: list[float] | None = None   # 向量嵌入（由系统填充）
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_frontmatter_md(self) -> str:
        """序列化为带 YAML frontmatter 的 Markdown"""
        ...

    @classmethod
    def from_frontmatter_md(cls, text: str) -> "KnowledgeNode":
        """从带 YAML frontmatter 的 Markdown 反序列化"""
        ...

    def to_dict(self) -> dict:
        """序列化为字典（不含 embedding）"""
        ...

    @classmethod
    def from_dict(cls, d: dict) -> "KnowledgeNode":
        """从字典反序列化"""
        ...
```

### 3.2 KnowledgeEdge（`dag/edge.py`）

```python
@dataclass
class KnowledgeEdge:
    """知识树边"""
    edge_id: str           # UUID
    parent_id: str
    child_id: str
    is_primary: bool       # True = 主父节点，用于遍历
    edge_type: str = "parent_child"  # "parent_child" | "association"
```

### 3.3 RetrievalLog（`retrieval/log.py`）

```python
@dataclass
class RetrievalLog:
    """单次检索的结构化日志"""
    query_id: str                          # UUID
    query_text: str
    query_vector: list[float] | None = None
    tree_path: list[str] = field(default_factory=list)     # 导航经过的 node_id 列表
    tree_confidence: float | None = None   # 最终导航置信度
    tree_success: bool = False             # 树导航是否成功
    rag_triggered: bool = False            # 是否触发了 RAG
    rag_results: list[tuple[str, float]] = field(default_factory=list)  # (node_id, similarity)
    fusion_mode: str = "none"              # "tree" | "tree+rag" | "rag" | "none"
    final_node_ids: list[str] = field(default_factory=list)
    agent_satisfaction: bool | None = None
    agent_feedback: str | None = None
    timestamp: str = ""                    # ISO 8601
```

### 3.4 ChangeDelta（`editing/change_map.py`）

```python
@dataclass
class ChangeDelta:
    """一次编辑的结构化 Delta"""
    delta_id: str
    operation: str                         # "update_content" | "merge" | "split"
    patches: list[dict]                    # JSON Patch (RFC 6902) 操作列表
    affected_node_ids: list[str]           # 受影响的节点 ID
    before_snapshot: dict                  # 编辑前快照（用于审计）
    after_snapshot: dict                   # 编辑后快照
    timestamp: str = ""
```

### 3.5 OptimizationSignal（`optimization/signals.py`）

```python
@dataclass
class OptimizationSignal:
    """优化信号"""
    signal_type: str        # "nav_failure" | "rag_false_positive" | "total_failure" | "content_insufficient"
    node_id: str | None     # 关联的节点 ID（可能为空）
    evidence: dict[str, Any]  # 支撑证据
    priority: int           # 1-4，1 最高
    detected_at: str = ""   # ISO 8601
```

---

## 4. 接口契约

### 4.1 Bootstrap（`bootstrap.py`）

```python
def bootstrap_from_directory(
    seed_dir: str,               # 种子 Markdown 文件目录
    config: KnowledgeTreeConfig,
    embedder: Embedder,          # 嵌入函数
    llm,                         # LLM 实例（用于生成摘要和聚类）
) -> BootstrapReport:
    """
    从种子数据构建初始知识树。

    流程：
    1. 读取 seed_dir 下所有 .md 文件 → KnowledgeNode 列表
    2. 为每个节点生成摘要（如无）和向量嵌入
    3. 通过语义聚类算法构建 DAG 层级
    4. 写入 Markdown + 图数据库 + 向量索引

    返回 BootstrapReport 包含：节点数、边数、层级深度、耗时等统计。
    """
```

### 4.2 Retrieve（`retrieval/` 组合）

```python
def retrieve(
    query: str,
    config: KnowledgeTreeConfig,
    graph_store: BaseGraphStore,
    embedder: Embedder,
    llm,
) -> tuple[RetrievalResult, RetrievalLog]:
    """
    主检索入口。

    流程（见决策 21）：
    1. query → embedder → query_vector
    2. router.py: LLM 路由树导航
    3. rag_fallback.py: 向量兜底（条件触发）
    4. fusion.py: 结果融合
    5. 返回结果 + 完整 RetrievalLog
    """
```

### 4.3 Apply Edit（`editing/` 组合）

```python
def apply_edit(
    operation: str,              # "update_content" | "merge" | "split"
    params: dict,                # 操作参数（节点 ID、内容等）
    config: KnowledgeTreeConfig,
    graph_store: BaseGraphStore,
    md_root: Path,
    embedder: Embedder,
) -> ChangeDelta:
    """
    应用编辑操作。

    流程：
    1. 解析并验证操作参数
    2. 执行操作（merge: 合并节点 + 继承边；split: 拆分节点 + 创建子节点）
    3. Markdown 先写（SoT）
    4. sync.py: 同步到图数据库
    5. change_map.py: 生成 JSON Patch Delta
    6. re_embed.py: 局部重嵌入受影响节点
    7. 返回 ChangeDelta（含审计快照）
    """
```

### 4.4 Run Optimization（`optimization/` 组合）

```python
def run_optimization_cycle(
    logs: list[RetrievalLog],
    config: KnowledgeTreeConfig,
    graph_store: BaseGraphStore,
    md_root: Path,
    llm,
    history: OptimizationHistory,
) -> OptimizationReport:
    """
    执行一轮优化。

    流程：
    1. signals.py: 从检索日志检测信号
    2. anti_oscillation.py: 频率控制过滤
    3. optimizer.py: 按优先级执行优化动作
    4. 返回 OptimizationReport
    """
```

---

## 5. 配置

### 5.1 Context 新增字段（`src/common/context.py`）

```python
# 在 Context dataclass 中添加：

# --- V4: Knowledge Tree ---
enable_knowledge_tree: bool = False
knowledge_tree_root: str = "workspace/knowledge_tree"
knowledge_tree_db_path: str = "workspace/knowledge_tree/.kuzu"
kt_tree_nav_confidence: float = 0.7
kt_rag_similarity_threshold: float = 0.85
kt_optimization_window: int = 3600           # 秒
kt_max_optimizations_per_window: int = 10
kt_nav_failure_threshold: int = 5            # 次/时间窗口
kt_rag_false_positive_threshold: int = 3
kt_total_failure_threshold: int = 3
kt_content_insufficient_threshold: int = 5
kt_embedding_model: str = "BAAI/bge-small-zh-v1.5"
kt_embedding_dimension: int = 512
kt_max_tree_depth: int = 5
```

所有字段遵循现有 env-var 覆盖模式（字段名大写化为环境变量）。

### 5.2 KnowledgeTreeConfig（`config.py`）

```python
@dataclass(kw_only=True)
class KnowledgeTreeConfig:
    """知识树运行时配置，由 Context 字段构造"""
    markdown_root: Path
    db_path: Path
    tree_nav_confidence: float = 0.7
    rag_similarity_threshold: float = 0.85
    optimization_window: int = 3600
    max_optimizations_per_window: int = 10
    nav_failure_threshold: int = 5
    rag_false_positive_threshold: int = 3
    total_failure_threshold: int = 3
    content_insufficient_threshold: int = 5
    embedding_model: str = "BAAI/bge-small-zh-v1.5"
    embedding_dimension: int = 512
    max_tree_depth: int = 5

    @classmethod
    def from_context(cls, ctx: "Context") -> "KnowledgeTreeConfig":
        """从 Context 构造"""
        ...
```

---

## 6. 工具接口

### 6.1 Supervisor 工具注册

在 `src/supervisor_agent/tools.py` 的 `get_tools()` 中条件注册：

```python
if runtime_context.enable_knowledge_tree:
    from src.common.knowledge_tree import build_knowledge_tree_tools
    tools.extend(build_knowledge_tree_tools(runtime_context))
```

### 6.2 暴露的工具

| 工具名 | 签名 | 说明 |
|--------|------|------|
| `knowledge_tree_retrieve` | `(query: str) -> str` | 主检索工具，返回 JSON 格式结果 |
| `knowledge_tree_edit` | `(operation: str, params_json: str) -> str` | 编辑操作，operation 限定为 update_content/merge/split |
| `knowledge_tree_status` | `() -> str` | 树结构概览（节点数、深度、最近编辑、健康指标） |

### 6.3 工具输出格式

所有工具返回 JSON 字符串，遵循现有 `{ok: bool, ...}` 模式：

```json
{
  "ok": true,
  "source": "tree",
  "node_id": "abc123",
  "title": "...",
  "content": "...",
  "confidence": 0.85
}
```

工具输出受现有 observation 治理（`src/common/observation.py`）截断保护。

---

## 7. 测试策略

### 7.1 测试目录

```
tests/unit_tests/common/knowledge_tree/
    conftest.py              # 共享 fixture
    test_config.py
    test_node.py
    test_edge.py
    test_markdown_store.py
    test_graph_store.py
    test_vector_store.py
    test_sync.py
    test_router.py
    test_rag_fallback.py
    test_fusion.py
    test_retrieval_log.py
    test_merge_split.py
    test_change_map.py
    test_anti_oscillation.py
    test_bootstrap.py

tests/integration/
    test_knowledge_tree_loop.py   # 端到端闭环（mock LLM + mock embedder）
```

### 7.2 测试 Fixture

```python
# tests/unit_tests/common/knowledge_tree/conftest.py

@pytest.fixture
def kt_config(tmp_path) -> KnowledgeTreeConfig:
    """使用临时目录的配置"""

@pytest.fixture
def temp_kuzu_db(tmp_path) -> kuzu.Database:
    """临时 Kùzu 数据库（测试后自动清理）"""

@pytest.fixture
def mock_embedder() -> Callable[[str], list[float]]:
    """确定性 mock embedder（不下载模型）"""
    def embed(text: str) -> list[float]:
        return [hash(text) % 100 / 100.0] * 512
    return embed

@pytest.fixture
def sample_tree(temp_kuzu_db, mock_embedder) -> dict:
    """预构建的 5-10 节点小型树，供检索测试使用"""
```

### 7.3 Mock 策略

- **LLM**：复用现有 `make_mock_llm` 模式，路由测试返回固定子节点选择
- **Embedder**：`mock_embedder` fixture，确定性输出，不依赖 sentence-transformers
- **图数据库**：使用临时目录的 Kùzu 实例，测试后清理
- **无 E2E（P1）**：闭环验证通过集成测试 + mock 完成

### 7.4 关键测试用例

| 模块 | 核心测试 |
|------|---------|
| `test_router.py` | 高置信度继续导航、低置信度停止、无子节点停止、到达叶子节点 |
| `test_fusion.py` | 四种融合模式的输入输出、空结果处理 |
| `test_merge_split.py` | merge 后边继承正确、split 后子节点创建正确 |
| `test_change_map.py` | JSON Patch 生成正确、校验拒绝非法操作 |
| `test_anti_oscillation.py` | 频率上限阻止过多优化、优先级排序正确 |
| `test_bootstrap.py` | 从种子文件建树、聚类生成层级、向量嵌入完成 |
| `test_knowledge_tree_loop.py` | 端到端闭环：Bootstrap → Retrieve → Edit → Sync → Re-embed → Log → Optimize |
| `test_chunker.py` | 切分粒度 ≤512 tokens、边界落在 \n\n、空/超长输入处理 |
| `test_filter.py` | 规则过滤正确判断"值得记忆"、低阈值不漏关键内容 |
| `test_ingest.py` | 增量嫁接到现有 group / 创建新 group、去重跳过、来源元数据完整 |

---

## 8. 知识摄入管道（决策 26）

### 8.1 概览

知识树的"涌现"核心——Agent 执行中产生的新知识自动回流到树中，再经 edit/optimize 自主整理，形成自进化闭环。

```
事件触发 (P1: 任务完成 / 用户指令)
      ↓
1. 原子切分 (< 512 tokens, \n\n + 对话轮边界)
      ↓
2. 轻量过滤 (规则, 低阈值)
      ↓
3. 向量去重 (cosine > 0.95 → 跳过)
      ↓
4. ingest_nodes() — 增量嫁接
   embed → search → attach or new group → sync
      ↓
5. 现有闭环 (edit / optimize / Agent 自主整理)
```

### 8.2 原子切分（`ingestion/chunker.py`）

```python
def chunk_text(
    text: str,
    max_tokens: int = 512,
) -> list[str]:
    """按 \n\n 和对话轮边界切分文本。

    P1 策略：split on double-newline, then merge short chunks.
    P2 升级为 SemanticChunker。
    """

def chunk_conversation(
    messages: list[dict],  # {"role": str, "content": str}
    max_tokens: int = 512,
) -> list[str]:
    """按对话轮切分（每轮独立或合并短轮）。"""
```

Token 估算：中文约 1.5 字/token，英文约 0.75 词/token。P1 用 `len(text) * 0.67` 简单估算。

### 8.3 轻量过滤（`ingestion/filter.py`）

```python
@dataclass
class FilterResult:
    passed: bool
    reason: str = ""
    confidence: float = 0.0

def should_remember(chunk: str, trigger: str = "") -> FilterResult:
    """规则判断是否值得记忆。低阈值（宁多勿漏）。

    通过条件（满足任一）：
    - 含决策/结论关键词
    - 含数字或专有名词
    - trigger == "user_explicit"（用户显式指令）
    - trigger == "task_complete"（任务完成的 summary）
    """
```

### 8.4 增量摄入（`ingestion/ingest.py`）

```python
@dataclass
class IngestReport:
    nodes_ingested: int = 0
    nodes_deduplicated: int = 0
    nodes_filtered: int = 0
    errors: list[str] = field(default_factory=list)

def ingest_nodes(
    candidates: list[KnowledgeNode],
    graph_store: BaseGraphStore,
    vector_store: BaseVectorStore,
    md_store: MarkdownStore,
    embedder: Callable[[str], list[float]],
    config: KnowledgeTreeConfig,
) -> IngestReport:
    """增量嫁接候选节点到知识树。

    对每个候选节点：
    1. embed → 向量
    2. vector_store.search(top-1) → 去重检查
    3. 找最匹配的现有 group → 嫁接或创建新 group
    4. sync 三层存储
    """
```

### 8.5 新增配置字段

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `kt_ingest_chunk_max_tokens` | int | 512 | 切分粒度上限 |
| `kt_dedup_threshold` | float | 0.95 | 去重相似度阈值 |
| `kt_cluster_attach_threshold` | float | 0.7 | 嫁接到现有 group 的相似度阈值 |
| `kt_ingest_enabled` | bool | True | 摄入管道开关 |

### 8.6 集成方式

P1：Supervisor 在 `call_executor` 结果处理中，当 `status == "completed"` 时，内部调用 `ingest_nodes()`。不暴露新工具。

P2：暴露 `knowledge_tree_ingest` 工具，让 Agent 自主判断。

---

## 9. 依赖

### 9.1 新增依赖（`pyproject.toml`）

| 包 | 版本约束 | 用途 |
|----|---------|------|
| `kuzu` | `>=0.11.0,<0.12.0` | 嵌入式图数据库 + 内置向量索引 |
| `sentence-transformers` | `>=3.0.0` | 文本嵌入生成 |
| `numpy` | `>=1.26.0` | 向量运算 |
| `jsonpatch` | `>=1.33` | RFC 6902 JSON Patch 操作 |
| `pyyaml` | `>=6.0` | Markdown frontmatter 解析（如尚未在依赖中） |

### 9.2 安装验证

Phase C 第一步执行 `uv sync --dev`，验证 Kùzu 在 Windows 上的构建。如失败，回退方案：内存邻接表实现 `BaseGraphStore`，不依赖 Kùzu。

---

## 10. 风险与缓解

| 风险 | 影响 | 缓解 |
|------|------|------|
| Kùzu Windows 构建失败 | 无法使用图数据库 | `BaseGraphStore` 抽象允许替换为内存实现 |
| sentence-transformers 模型大 | 开发环境下载慢 | 测试用 mock embedder，仅 E2E 用真实模型 |
| 工具输出膨胀 Supervisor 上下文 | 影响对话质量 | 复用 observation 治理截断 |
| LLM 路由 Token 成本 | 每次检索 3-5 次 LLM 调用 | P1 限制树深 ≤5，全量日志供 P2 优化分析 |
| 聚类质量差导致导航失败 | 闭环无法收敛 | Bootstrap 种子用高质量领域知识；P1 验证标准包含导航成功率 |
| 摄入噪声爆炸 | 树膨胀、检索质量下降 | 轻量过滤 + 向量去重 + 低阈值策略；P2 可升级为 LLM 过滤 |
| 去重误杀 | 有差异的知识被跳过 | 阈值 0.95 可调；被跳过的知识生成轻量 ChangeDelta 供后续 merge |
