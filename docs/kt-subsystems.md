# 知识树子系统走读（KT Subsystems）

> 定位：[`v4-kt-core-design.md`](v4-kt-core-design.md) 的配套走读。本文只列 `src/common/knowledge_tree/` 各子包的**模块职责与公共入口**，不重复设计原理。
> 元认知闭环见 [`meta-cognition-design.md`](meta-cognition-design.md)。

---

## 子包总览

```
src/common/knowledge_tree/
├── optimization/   P3 自动优化闭环（信号检测 + 反振荡）
├── embedding/      向量生成（语义 + API + 缓存）
├── editing/        Agent 驱动重组（编号树 + 移动 + 重嵌入）
├── retrieval/      RAG 检索（4 路径 RRF + 查询扩展）
├── ingestion/      摄入管道（chunker + extractor + filter）
├── storage/        存储（markdown + overlay + vector + persistence）
├── bootstrap.py    建树 + 元规则播种
├── config.py       KnowledgeTreeConfig + 阈值常量
├── core.py         KnowledgeTree 主类（门面）
├── factory.py      get_or_create_kt 单例工厂
├── snapshot.py     KT 状态快照（人类可观测）
└── tools.py        LangChain 工具适配层
```

本文重点走读前三个子包（optimization / embedding / editing），它们是 V4 P2/P3 阶段新增的、最缺乏文档的部分。retrieval/ingestion/storage 在核心设计文档中已有覆盖。

---

## 1. optimization/ — P3 自动优化闭环

**职责**：KT 自检发现质量问题（检索异常、摄入异常、结构异常）时，生成优化建议注入 Supervisor。带反振荡保护防止建议反复触发。

### 模块

#### `signals.py`
- **`OptimizationSignal`** 类：优化信号数据模型
- 检测的信号类别：
  - 检索质量异常（低相似度、高矛盾密度）
  - 摄入管道异常（过滤率突变、去重命中异常）
  - 结构异常（目录膨胀、孤儿节点）
- 公共入口：信号检测函数，输出 `list[OptimizationSignal]`

#### `anti_oscillation.py`
- **`OptimizationHistory`** 类：优化执行历史，用于频率控制
- **独立阈值**：每类信号有独立的冷却期
- **全局频率上限**：防止短时间内大量优化动作导致系统震荡
- 记录每次优化执行，冷却期内同源信号不再触发

### 数据流

```
KT 操作（retrieve/ingest）
  → signals.py 检测异常
  → anti_oscillation.py 过滤（冷却期检查）
  → 生成优化建议
  → graph.py kt_retrieve() 注入 state.kt_optimization_suggestions
  → call_model() 以"可选行动"措辞注入系统提示
  → Supervisor 决定是否采取行动
```

---

## 2. embedding/ — 向量生成

**职责**：提供节点文本 → 向量的多种实现（本地语义模型 / API / 缓存），供摄入和检索共用。

### 模块

#### `semantic.py`
- **`create_semantic_embedder()`**：创建本地 sentence-transformers 语义 embedder，失败返回 `None`
- 线程安全封装（多线程调用安全）
- 默认模型：`BAAI/bge-large-zh-v1.5`（可通过 `KT_EMBEDDING_MODEL` 配置）

#### `api.py`
- **`create_api_embedder()`**：SiliconFlow / OpenAI 兼容的 embedding API provider
- 用于无本地模型依赖的部署场景
- 走 `SILICONFLOW_API_KEY` 或 OpenAI 兼容接口

#### `cache.py`
- **`EmbeddingCache`** 类：Disk-backed embedding cache，JSON 持久化
- **`_content_hash(text)`**：内容哈希，用作 cache key
- 加速 bootstrap：启动时批量加载已缓存向量，避免重复计算
- 缓存文件：`{kt_root}/.embedding_cache_{model_name}.json`

### 选择逻辑

`factory.py` 按配置优先级选择 embedder：本地 semantic → API → hash fallback。选中后包装为统一接口供 `vector_store` 调用。

---

## 3. editing/ — Agent 驱动重组

**职责**：P2 阶段的 Agent 主动重组能力。Agent 通过编号树视图表达结构意图，系统自动执行移动 + 向量更新。

### 模块

#### `tree_view.py`
- **`TreeEntry`** 类：编号树中的一个条目（编号 + 路径 + 标题）
- 渲染：将 KT 目录结构渲染为带编号的缩进文本
- 解析：将 Agent 输出的"编号 → 新位置"映射解析为移动操作

#### `reorganize.py`
- **`MoveOp`** 类：一个文件移动操作（源路径 → 目标路径）
- 差异计算：对比当前结构 vs Agent 提议结构，生成最小移动集
- 移动执行：文件系统操作 + Overlay 更新 + 向量调整

#### `re_embed.py`
- **`re_embed_nodes()`**：对受影响节点重新生成嵌入并更新
- 编辑（移动/重命名/内容变更）后，受影响节点的 content embedding 和 structural（锚点）向量需要同步更新
- 局部重嵌入，不全量重建

#### `stored_vector.py`
- **`compute_stored_vector(content, structural, alpha, beta)`**：计算混合向量
- 公式：`stored_vector = normalize(alpha * content + beta * structural)`
- `alpha`/`beta` 权重在 `config.py` 配置
- structural 来自目录锚点，增强同目录聚簇

### 重组流程（决策 22 现行方案）

```
Agent 调用 knowledge_tree_reorganize
  → tree_view.py 渲染当前编号树
  → Agent 提议新结构（编号 → 新位置）
  → tree_view.py 解析提议
  → reorganize.py 计算差异 → 生成 MoveOp 列表
  → reorganize.py 执行移动（文件系统 + Overlay）
  → re_embed.py 局部重嵌入受影响节点
  → stored_vector.py 重算 stored_vector
```

**与决策 22 的关系**：决策 22 原本是 Change Mapping / JSON Patch 方案，后重定义为 Agent 主动重组。本子包是重定义后的实现。详见 [`architecture-decisions.md`](architecture-decisions.md) 决策 22。

---

## 关联文档

- [`v4-kt-core-design.md`](v4-kt-core-design.md) — KT 核心设计（当前权威，含 retrieval/ingestion/storage 的设计原理）
- [`meta-cognition-design.md`](meta-cognition-design.md) — 元认知闭环（经验提取 + auto-ingest + 元规则）
- [`architecture-decisions.md`](architecture-decisions.md) 决策 22（编辑→重组重定义）、决策 23（异步优化闭环与防震荡）、决策 29（摄入质量门槛）
