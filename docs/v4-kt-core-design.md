# V4 知识树核心设计 — 向量-结构互塑闭环

> 状态：设计确认（2026-04-25）
> 前置：`v4-knowledge-tree-spec.md`（P1 技术规格，已实现）
> 定位：知识树是 AgentTriad 与其他 Agent 项目的核心差异

---

## 1. 核心愿景

**一个向量-结构互塑闭环**：

```
信息自底向上涌现成动态层级树
  → Agent 自主编辑树结构后产生结构化 Change Mapping
  → Change Mapping 反向实时校准向量空间映射
  → 形成记忆的持续自我进化
```

关键约束：**组织靠向量不靠 LLM，降低 token 消耗**。Agent 只在必要时参与，日常聚类和放置全部由向量空间自动完成。

---

## 2. 向量-结构互塑闭环

### 2.1 向量 → 结构（自底向上涌现，零 LLM）

**核心概念：文件夹 = 向量空间中一个正在生长、动态覆盖的语义区域。**

目录不是文件系统的容器概念。每个目录的锚点向量定义了向量空间中的一个语义覆盖区域。目录里的每个节点是这个区域内的一个点。新信息 embed 后落入哪个区域，就属于哪个目录——这不是"文件归类"，而是"向量空间中的自然归属"。

- **生长**：节点增多 → 锚点漂移 → 覆盖区域扩展或收缩 → 越来越精确
- **动态覆盖**：区域边界不是固定的，随着新知识加入自然演变
- **涌现**：不需要预定义分类体系，区域本身从数据中涌现

```
新信息 → embed → 向量聚类找到最近目录锚点 → 自动放入该目录
             没有匹配的锚点 → 自动创建新目录（P1: 临时无语义名，P2: Agent 命名）
```

向量空间本身的聚类能力驱动树结构的形成。Agent 不需要决定"这个知识该放哪"——向量自动处理。

新目录命名策略：
- **P1**：临时无语义名（如 UUID 或递增编号），不花 token 思考命名
- **P2**：Agent 为目录赋予有意义的名称（仅在 Agent 主动重组时触发，日常不消耗 token）

**前提**：语义 embedder 必须可靠工作。hash embedder 没有聚类能力，整个涌现机制会失效。

### 2.2 结构 → 向量（Change Mapping，实时自动闭环）

```
任何文件系统结构变更（写入/删除/移动节点、创建/删除目录）
  → 实时触发 → 重算受影响目录的锚点向量 → 向量空间立即被结构校准
```

Change Mapping 是知识树的心跳，**必须实时、自动、任何结构变更都触发**：

- **节点写入** → 该目录锚点重算
- **节点删除** → 该目录锚点重算（目录为空则删除锚点）
- **节点移动** → 源目录 + 目标目录锚点都重算
- **目录创建/删除** → 锚点集合更新

锚点向量 = 目录内所有节点 embedding 的质心。这不是可选的优化，而是闭环的基本约束——结构变了，向量空间必须同步变。

**零 LLM 调用**：锚点重算是纯数学操作（向量均值 + 归一化）。

### 2.3 闭环效果

```
更好的向量聚类 → 更准确的目录归属 → 更合理的树结构
  → 更准确的锚点 → 更好的向量空间校准 → 更好的检索
    → 更好的摄入决策 → 继续循环
```

---

## 3. 养料入口（什么进入知识树）

### 3.1 入口 A：自动 — Executor 完成计划后

**触发时机**：Executor 完成整个 plan 的执行后（不论成功或失败）。

**输入**：完整的执行记录，包括：
- Plan JSON（goal、steps、intents、expected_outputs）
- 每步的执行结果（result_summary、failure_reason）
- 最终 summary
- 失败原因（如果失败）

**处理**：从执行记录中提取值得记忆的内容，embed 后走向量→结构路径自动放置。

**为什么这最丰富**：3-Agent 架构的特殊之处在于每个 Agent 层对同一任务产生不同分辨率的信号——Planner 产生意图（WHY），Executor 产生行动（HOW），Supervisor 产生评估（WHAT）。执行记录包含完整弧线。

**实现优先级**：P2（先做好入口 B 验证基础，再接 Executor 记录）。

### 3.2 入口 B：主动 — Supervisor 调用记忆工具

**触发时机**：
- 用户主动要求（"记住这个"）
- 正在使用的 skill 触发记忆
- Supervisor 自主判断值得记住

**输入**：`ingest(text)` — Supervisor 提供原始文本。

**处理**：text → chunk → filter → embed → 向量聚类自动放置 → Change Mapping。

**实现优先级**：P1（当前已有基础，优化质量即可）。

### 3.3 两个入口共用同一条路径

```
入口 A 或 B 的文本
  → embed
  → 向量搜索找最近锚点
  → 放入对应目录（或创建新目录）
  → 写入节点
  → 更新锚点（Change Mapping）
```

路径完全相同，只是"什么文本进入"的来源不同。

---

## 4. 检索：Recall

### 4.1 自动注入（已实现）

```
用户消息 → kt_retrieve 节点（高阈值 RAG）
  → 检索到 → 拼接到用户消息 → call_model
  → 没检索到 → 原消息直接进入 call_model
```

高阈值（≥0.6）避免注入噪声。仅在 `__start__` 入口执行，工具循环不重复注入。

### 4.2 主动检索工具

`knowledge_tree_retrieve(query)` — Supervisor 主动查询知识树，获取详细信息。

### 4.3 检索增强方向

当前检索只有向量信号。未来加入结构信号：
- 向量信号：query embedding vs content embeddings
- 结构信号：命中节点所在目录的锚点向量也参与评分

结构信号让"同一主题下的相关知识"互相增强。纯向量可能只命中一条，但结构信号可以扩展到同目录的其他相关节点。

---

## 5. 知识管理权归属

**唯一管理者：Supervisor**（掌握完整消息和上下文）

- Supervisor 拥有完整的用户对话历史
- Supervisor 可以看到 Executor 的执行结果
- Supervisor 可以看到 Planner 的规划输出
- Planner 和 Executor 通过 Supervisor 传递的上下文间接获得 KT 信息

未来扩展：
- Planner 被 Supervisor 调用时，Supervisor 可以将 KT 检索结果注入 Planner 的 prompt
- Executor 执行 Plan 的每个 step 时，可以按 step intent 做 RAG 检索（但当前不做，先打磨好基础）

---

## 6. 当前状态与下一步

### 已完成

- [x] P1 基础架构（两层存储 + Overlay + Bootstrap）
- [x] 语义 embedder 集成（BAAI/bge-small-zh-v1.5，降级到 hash）
- [x] 节点缓存（消除重复文件 I/O）
- [x] 工具精简（bootstrap/status 移除，10→8 工具）
- [x] Graph 集成（kt_retrieve 节点，用户消息自动 RAG 注入）
- [x] `get_or_create_kt()` 模块级接口
- [x] RAG 结果拼接到用户消息（不是 system prompt）

### 待实现（按优先级）

1. **清理垃圾测试节点** — 测试时写入的低质量节点在污染检索结果
2. **验证语义 embedder 端到端质量** — 确认聚类和检索在语义空间下有效
3. **入口 A：Executor 完成后的执行记录提取** — 自动从执行记录中提取知识
4. **检索增强：结构信号** — 目录锚点参与检索评分
5. **Change Mapping 实时闭环** — 确保任何文件系统结构变更都实时触发锚点重算

### 文件结构（不变）

```
src/common/knowledge_tree/
    __init__.py              # KnowledgeTree 门面 + get_or_create_kt()
    config.py                # KnowledgeTreeConfig
    bootstrap.py             # 种子目录建树
    embedding/
        semantic.py          # sentence-transformers embedder
    storage/
        markdown_store.py    # 文件系统存储（含节点缓存）
        vector_store.py      # 向量索引 + 目录锚点
        overlay.py           # Overlay JSON
        sync.py              # 文件系统 → 向量同步
    retrieval/
        rag_search.py        # RAG 检索（content + title 双路 + RRF）
        query_expander.py    # 查询扩展（保留，未来使用）
        log.py               # 检索日志
    ingestion/
        chunker.py           # 文本切分
        filter.py            # 轻量过滤
        ingest.py            # 增量嫁接
    dag/
        node.py              # KnowledgeNode
```

---

## 7. Supervisor 工具列表

| 工具 | 类型 | 说明 |
|------|------|------|
| `knowledge_tree_retrieve` | 主动工具 | Supervisor 主动查询知识树 |
| `knowledge_tree_ingest` | 主动工具 | Supervisor 主动记忆（用户要求、skill 触发、自主判断） |
| `kt_retrieve` graph 节点 | 自动注入 | 用户消息进入时自动 RAG 检索，拼接到用户消息 |

bootstrap 和 status 已从工具列表移除（内部自动处理）。
