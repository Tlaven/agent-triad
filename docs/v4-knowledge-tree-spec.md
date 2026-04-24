# V4 涌现式知识树 — P1 技术规格

> 状态：v4（2026-04-22）
> 前置：概念对齐（`v4-knowledge-tree-concepts.md`）、架构决策 18-26（`architecture-decisions.md`）
> 范围：P1 最小闭环实现

---

## 1. 概览

### 1.1 P1 目标

跑通端到端闭环：**文件系统种子建树 → 向量检索 → 增量摄入 → Agent 手动搜索**。

P1 先不加 structural_vector（纯 content_embedding 验证基础流程），P2 引入混合向量。

### 1.2 P1 范围约束

- 存储：文件系统 + 内存向量索引 + Overlay JSON
- Bootstrap：从种子目录建树（目录结构直接成为树结构）
- 检索：纯 content_embedding 向量检索（RAG）
- 手动搜索：Agent 直接使用现有工作区工具（`read_workspace_text_file`、`list_workspace_entries`、`search_files`、`grep_content`）
- 摄入：增量嫁接（新知识 → RAG 定位 → 放入对应目录）
- 不含：Agent 重组工具、structural_vector、优化信号、Leiden 聚类

### 1.3 最小闭环流程

```
┌─────────────┐     ┌──────────────┐     ┌──────────────────┐
│  Bootstrap   │────►│  Retrieve    │────►│  Agent 手动搜索   │
│  (种子目录)   │     │ (RAG 向量)    │     │ (工作区工具)      │
└─────────────┘     └──────┬───────┘     └──────────────────┘
                           │
                    ┌──────▼───────┐
                    │  Ingest      │
                    │ (增量摄入)    │
                    └──────────────┘
```

---

## 2. 模块结构

```
src/common/knowledge_tree/
    __init__.py              # 公共 API（KnowledgeTree 门面类）
    config.py                # KnowledgeTreeConfig dataclass
    bootstrap.py             # 从种子目录建树
    storage/
        __init__.py
        markdown_store.py    # 文件系统读写（Layer 1 SoT）
        vector_store.py      # 向量索引操作（Layer 2）
        overlay.py           # Overlay JSON 跨目录关联边读写
        sync.py              # 文件系统 → 向量派生同步
    dag/
        __init__.py
        node.py              # KnowledgeNode dataclass（保留）
        edge.py              → 移入 overlay.py（仅关联边用）
    retrieval/
        __init__.py
        rag_search.py        # 向量相似度检索
        log.py               # RetrievalLog 结构化日志
    ingestion/
        __init__.py
        chunker.py           # 原子切分
        filter.py            # 轻量规则过滤
        ingest.py            # ingest_nodes() 增量嫁接
    editing/
        __init__.py
        re_embed.py          # 受影响节点局部重嵌入
    optimization/
        __init__.py
        signals.py           # 优化信号检测（P3）
```

### 与旧模块的对应关系

| 旧模块 | 新模块 | 说明 |
|--------|--------|------|
| `storage/graph_store.py` | **删除** | 文件系统替代 Graph 层 |
| `storage/markdown_store.py` | `markdown_store.py` | 保留，但改为支持目录层级读写 |
| `storage/vector_store.py` | `vector_store.py` | 保留，增加目录锚点管理 |
| `dag/edge.py` | `overlay.py` | 仅保留 `is_primary=False` 的关联边 |
| `storage/sync.py` | `sync.py` | 简化为文件系统 → 向量单向派生 |
| `retrieval/router.py` | **P1 删除** | LLM 路由树导航不再需要 |
| `retrieval/rag_fallback.py` | `rag_search.py` | 重命名，RAG 成为主检索路径 |
| `retrieval/fusion.py` | **P1 删除** | 只有单一 RAG 路径，无融合 |
| `bootstrap.py` | `bootstrap.py` | 重写：从聚类建树改为目录继承 |
| `editing/merge_split.py` | **P2** | P2 Agent 重组工具 |
| `editing/change_map.py` | **P2** | P2 重组 Delta 追踪 |

---

## 3. 数据模型

### 3.1 KnowledgeNode（`dag/node.py`）

```python
from dataclasses import dataclass, field
from typing import Any

@dataclass
class KnowledgeNode:
    """知识树节点——对应文件系统中的一个 Markdown 文件。"""

    node_id: str                           # 文件相对路径（如 "development/debugging.md"）
    title: str                             # 节点标题
    content: str                           # 节点正文内容
    source: str                            # 来源标识
    created_at: str                        # ISO 8601 时间戳
    summary: str = ""                      # 摘要
    embedding: list[float] | None = None   # content_embedding（纯内容语义，永不变）
    stored_vector: list[float] | None = None  # stored_vector（P2: α·content + β·structural）
    metadata: dict[str, Any] = field(default_factory=dict)

    # -- 目录锚点相关（P2）--
    directory: str = ""                    # 所属目录路径
    anchor: list[float] | None = None      # 所属目录的锚点向量
```

**关键变更**：`node_id` 从 UUID 改为**文件相对路径**。文件系统的路径天然唯一且包含结构信息。

### 3.2 OverlayEdge（`storage/overlay.py`）

```python
@dataclass
class OverlayEdge:
    """跨目录关联边（is_primary=False）。"""
    source_path: str          # 源文件相对路径
    target_path: str          # 目标文件相对路径
    relation: str = "related" # 关系类型
    strength: float = 1.0     # 关联强度 0.0-1.0
    created_by: str = ""      # "agent" | "wiki_link" | "rag_co_occurrence"
    note: str = ""
```

### 3.3 DirectoryAnchor（`storage/vector_store.py`）

```python
@dataclass
class DirectoryAnchor:
    """目录锚点——目录内所有文件 content_embedding 的质心。"""
    directory: str                    # 目录路径
    anchor_vector: list[float]        # 质心向量
    file_count: int                   # 目录内文件数
    last_updated: str = ""            # ISO 8601
```

### 3.4 RetrievalLog（`retrieval/log.py`）

```python
@dataclass
class RetrievalLog:
    """单次检索的结构化日志。"""
    query_id: str
    query_text: str
    query_vector: list[float] | None = None
    rag_results: list[tuple[str, float]] = field(default_factory=list)  # (path, similarity)
    agent_satisfaction: bool | None = None
    agent_feedback: str | None = None
    manual_search_triggered: bool = False  # Agent 是否触发了手动搜索
    timestamp: str = ""
```

---

## 4. 接口契约

### 4.1 Bootstrap（`bootstrap.py`）

```python
def bootstrap_from_directory(
    seed_dir: Path,               # 种子目录（如 workspace/knowledge_tree/）
    md_store: MarkdownStore,      # 文件系统存储
    vector_store: BaseVectorStore,
    overlay_store: OverlayStore,  # Overlay 存储（bootstrap 时保留已有边）
    embedder: Callable[[str], list[float]],
) -> BootstrapReport:
    """
    从种子目录构建初始知识树。

    流程：
    1. 递归扫描 seed_dir，读取目录层级 = 树结构
    2. 解析每个 .md 文件 → KnowledgeNode（node_id = 相对路径）
    3. 为每个文件生成 content_embedding + title_embedding
    4. 计算每个目录的锚点 = 目录内文件 content_embedding 的质心
    5. 写入向量索引
    注意：不清空 overlay 边，保留已有跨目录关联。
    返回 BootstrapReport：节点数、目录数、锚点数、深度等。
    """
```

### 4.2 Retrieve（`retrieval/rag_search.py`）

```python
def rag_search(
    query_vector: list[float],
    vector_store: BaseVectorStore,
    md_store: MarkdownStore,       # 用于加载完整节点
    embedder: object = None,       # 可选，用于 title embedding 路径
    top_k: int = 5,
    threshold: float = 0.15,       # hash embedder 用 0.15，语义 embedder 用 0.7
) -> list[tuple[KnowledgeNode, float]]:
    """
    向量相似度检索（content + title 双路融合）。

    检索策略：
    1. content embedding 路径：query vs content embeddings
    2. title embedding 路径：query vs title: 前缀的 embeddings
    3. 两路结果用倒数秩融合（RRF）合并
    4. 最终按实际相似度降序返回

    Returns:
        (node, similarity) 列表，按相似度降序。
    """
```

### 4.3 Ingest（`ingestion/ingest.py`）

```python
def ingest_nodes(
    candidates: list[KnowledgeNode],
    vector_store: BaseVectorStore,
    md_store: MarkdownStore,        # 文件系统存储
    overlay_store: OverlayStore,    # Overlay 存储
    embedder: Callable[[str], list[float]],
    dedup_threshold: float = 0.95,
    attach_threshold: float = 0.7,  # 目录锚点相似度阈值
) -> IngestReport:
    """
    增量嫁接候选节点到知识树。

    对每个候选节点：
    1. embed → content_embedding
    2. vector_store.search(top-1) → 去重检查
    3. 找最相似的目录锚点 → 确定放置目录
    4. 写入 Markdown 文件到对应目录
    5. 更新向量索引（content + title embedding）
    6. 刷新目录锚点
    """
```

---

## 5. 配置

### 5.1 Context 字段（`src/common/context.py`）

```python
# --- V4: Knowledge Tree ---
enable_knowledge_tree: bool = False
knowledge_tree_root: str = "workspace/knowledge_tree"
kt_rag_similarity_threshold: float = 0.15   # hash embedder 用 0.15，语义 embedder 用 0.7
kt_embedding_model: str = "BAAI/bge-small-zh-v1.5"
kt_embedding_dimension: int = 512
kt_ingest_chunk_max_tokens: int = 512
kt_dedup_threshold: float = 0.95
kt_ingest_enabled: bool = True
kt_ingest_attach_threshold: float = 0.7     # 目录锚点相似度阈值

# P2 新增
kt_structural_weight: float = 0.2     # β：structural_vector 权重
kt_content_weight: float = 0.8        # α：content_embedding 权重
kt_max_tree_depth: int = 5

# P3 优化闭环
kt_optimization_window: int = 3600
kt_max_optimizations_per_window: int = 10
kt_total_failure_threshold: int = 3
kt_rag_false_positive_threshold: int = 3
kt_content_insufficient_threshold: int = 5
```

### 5.2 KnowledgeTreeConfig（`config.py`）

```python
@dataclass(kw_only=True)
class KnowledgeTreeConfig:
    """知识树运行时配置。"""
    markdown_root: Path                    # 种子/根目录
    rag_similarity_threshold: float = 0.15  # hash embedder 用 0.15
    embedding_model: str = "BAAI/bge-small-zh-v1.5"
    embedding_dimension: int = 512
    ingest_chunk_max_tokens: int = 512
    dedup_threshold: float = 0.95
    ingest_enabled: bool = True
    ingest_attach_threshold: float = 0.7   # 目录锚点相似度阈值
    # P2
    structural_weight: float = 0.2
    content_weight: float = 0.8
    max_tree_depth: int = 5
    # P3 优化闭环
    optimization_window: int = 3600
    max_optimizations_per_window: int = 10
    total_failure_threshold: int = 3
    rag_false_positive_threshold: int = 3
    content_insufficient_threshold: int = 5
```

---

## 6. 工具接口

### 6.1 P1 工具

| 工具名 | 签名 | 说明 |
|--------|------|------|
| `knowledge_tree_retrieve` | `(query: str) -> str` | RAG 向量检索（content + title 双路融合） |
| `knowledge_tree_ingest` | `(text: str, trigger: str, source: str) -> str` | 增量摄入新知识 |
| `knowledge_tree_status` | `() -> str` | 树概览（节点数、目录数、锚点状态） |
| `knowledge_tree_bootstrap` | `() -> str` | 从种子目录建树（已有数据时跳过） |

### 6.2 P2 新增工具

| 工具名 | 签名 | 说明 |
|--------|------|------|
| `knowledge_tree_reorganize` | `() -> str` | 返回带编号的目录树，供 Agent 重组 |
| `knowledge_tree_apply_reorganization` | `(proposed_tree: str) -> str` | Agent 输出重组后编号树，系统自动执行移动+向量调整 |

### 6.3 Agent 手动搜索

P1 直接复用现有 Supervisor 工作区工具：
- `list_workspace_entries` — 列目录
- `read_workspace_text_file` — 读文件
- `search_files` — glob 搜索
- `grep_content` — 正则搜索

不需要新增工具。

---

## 7. 测试策略

### 7.1 测试目录

```
tests/unit_tests/common/knowledge_tree/
    conftest.py              # 共享 fixture（种子目录、mock embedder）
    test_config.py
    test_node.py
    test_markdown_store.py
    test_vector_store.py
    test_overlay.py
    test_bootstrap.py
    test_rag_search.py
    test_ingest.py
    test_chunker.py
    test_filter.py
    test_retrieval_log.py
    test_re_embed.py
    test_signals.py
    test_anti_oscillation.py
    test_anchor_integration.py
    test_sync.py

tests/integration/
    test_knowledge_tree_loop.py   # 端到端闭环
```

### 7.2 关键测试用例

| 模块 | 核心测试 |
|------|---------|
| `test_bootstrap.py` | 从种子目录建树、目录层级正确解析、锚点计算正确 |
| `test_rag_search.py` | 相似度检索、阈值过滤、空结果处理 |
| `test_ingest.py` | 增量嫁接到对应目录、去重跳过、新目录创建 |
| `test_vector_store.py` | 锚点 CRUD、stored_vector 计算（P2） |
| `test_overlay.py` | 关联边读写、JSON 格式正确 |
| `test_knowledge_tree_loop.py` | Bootstrap → Retrieve → Ingest → Retrieve again |
