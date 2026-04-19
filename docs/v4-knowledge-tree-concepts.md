# V4 涌现式知识树 — 概念对齐文档

> 状态：概念对齐 v3（2026-04-19）
> 范围：AgentTriad 内部，Supervisor 内嵌组件
> 目标：Agent 信息的自组织存储、高效检索、持续进化

---

## 1. 核心思想

三层机制形成闭环：**信息片段 → 语义聚类 → DAG 结构 → Agent 编辑 → Change Mapping → 向量校准 → 结构重组**。

不是"树结构 vs 向量搜索"二选一，而是双向塑造：
- 树提供精确路径导航，向量提供模糊兜底
- Agent 编辑通过 Change Mapping 提供可解释的信号校准向量空间
- 向量统计特征引导树的重组方向

---

## 2. 三层分离存储架构

```
┌─────────────────────────────────────────────────┐
│  Layer 1: Markdown 文件（Source of Truth）        │
│  - Agent 直接读写、人类可审查、git 可版本化       │
├─────────────────────────────────────────────────┤
│  Layer 2: 图数据库（结构层）                      │
│  - DAG 结构：节点元数据 + 关系边                 │
│  - 主父节点标记（用于遍历）+ 关联边（多父引用）   │
│  - 内置/外挂向量索引（Layer 3）                  │
├─────────────────────────────────────────────────┤
│  Layer 3: 向量索引（检索层）                      │
│  - 语义相似度计算、模糊召回                       │
│  - 受树结构约束排序                               │
└─────────────────────────────────────────────────┘
```

### 图数据库选型（2026-04-16 决策）

| 角色 | 方案 | 说明 |
|------|------|------|
| **P1 原型** | **Kùzu v0.11.x** | 已归档但功能完整，嵌入+Cypher+向量一体，原型验证最快 |
| **长期首选** | **DuckDB + 自定义图层** | 生态成熟、向量扩展活跃，图遍历自研但完全可控 |
| **积极备选** | **RyuGraph** | Kùzu 直接继承者，理念匹配，需持续关注社区活跃度 |
| **长期观察** | FalkorDBLite / Coffy | Python 原生方案，原型快速但生产成熟度待验证 |

**关键**：保留抽象接口，Kùzu → DuckDB 迁移路径在设计时就考虑。

---

## 3. 检索流程（详细设计见 `用户意见.md`）

```
查询 → ① 向量化
     → ② LLM 路由导航（主路径，置信度 ≥ 0.7 继续）
     → ③ RAG 兜底（树导航失败时，相似度 ≥ 0.85）
     → ④ 结果融合（tree / tree+rag / rag / none）
     → ⑤ Agent 反馈（满意度标注）
     → ⑥ 完整检索日志
```

**关键决策：树优先，RAG 兜底**

- 树导航使用 LLM 做路由决策（不是向量匹配），利用 LLM 语义理解处理歧义
- RAG 仅在树导航失败时触发，高阈值过滤确保质量
- 检索日志是闭环优化的数据燃料
- Agent 反馈结构化字段：`{satisfaction: bool, reason: str}`，便于后续小模型路由和树质量评估
- **检索日志从 P1 起输出结构化 JSON**，为后续小模型路由器训练预留数据基础
- 全链路日志覆盖：检索路径 + Agent 反馈 + Change Mapping + 优化效果

---

## 4. 异步优化闭环（4 种信号）

| 信号类型 | 触发条件 | 优化动作 |
|----------|----------|----------|
| 导航失败 | 某父节点下频繁导航失败 | 标记"结构薄弱点"，Agent 分裂/重组/摘要重写 |
| RAG 假阳性 | RAG 返回节点被标记为不相关 | 对比学习负样本，调整相似度权重/微调嵌入 |
| 整体失败 | 树 + RAG 均无结果，累积达阈值 | Agent 创建新节点，失败查询作为种子 |
| 内容不足 | 树导航成功但内容不充分 | Agent 更新节点内容/摘要 |

所有优化动作**异步批量**执行，不阻塞检索路径。

### 防震荡机制：分层控制

- **独立阈值**：每种信号类型独立配置触发条件（如导航失败 N 次/时间窗口、假阳性 M 次等）
- **全局频率上限**：无论信号类型，总优化动作受全局限额约束，超出排队到下个窗口。初期保守值（如每小时最多 N 次），后续结合检索日志满意度反馈动态调整
- **优先级排序**：整体失败 > 导航失败 > RAG 假阳性 > 内容不足
- 允许不同信号竞争有限优化资源，防止单一信号霸占

---

## 5. 分阶段实现路线

### 5.1 Change Mapping（编辑能力）

| 阶段 | Agent 可执行操作 | Change Mapping 处理 | Delta 格式 |
|------|-----------------|-------------------|-----------|
| P1 原型验证 | 编辑内容 + merge/split | 重新嵌入受影响节点、路径重排 | JSON Patch (RFC 6902) |
| P2 结构进化 | + move_subtree | + 子树路径 Delta | + 语义层（merge/split/move 映射到 JSON Patch） |
| P3 完整实现 | + 创建抽象层/跨层重组 | 通用 Delta 描述格式 | 完整语义层 |

**Delta 格式策略**：渐进式。P1 用标准 JSON Patch 验证流程；P2 引入领域语义操作层（merge/split/move），底层仍映射到 JSON Patch 执行。Agent 强制输出结构化操作数组，确保可追溯。

**实现约束**：
- Agent 输出 Delta 时需**强约束**（prompt 模板 + 解析校验），防止幻觉导致无效 Delta
- P2 引入语义层后，同时维护**语义 Delta 日志**供人类/Supervisor 审计

### 5.2 LLM 路由策略

| 阶段 | 策略 | 依据 |
|------|------|------|
| P1 | 全量 LLM 路由，完整日志 | 端到端验证闭环 |
| P2 | 日志分析：结构稳定度、导航明确度、性能基线 | 数据驱动 |
| P3 | 高质量树 → 小模型路由；质量不足 → 优化树结构 | 数据决策 |

### 5.3 DAG 遍历

| 阶段 | 策略 | 关注点 |
|------|------|--------|
| P1 | 主路径遍历（每个节点标记一个主父节点） | 定义并缓存常见主路径 |
| P2 | 多路径并行探索 | 深度/置信度剪枝防分支爆炸 + 常见路径缓存 |

### 5.4 信息范围

| 阶段 | 信息类型 | 叶子节点模式 |
|------|---------|-------------|
| P1 | 领域知识（结构性强，边界清晰） | `{title, content, source, created_at}` |
| P2 | + Agent 记忆（碎片化、动态） | + `decay_score`, `access_count`（指数衰减：结合时间 + 重要性） |
| P3 | + 技能/Skill + 参考资料 | 技能含可执行定义（可绑定 Supervisor 工具调用）；参考资料含外部链接 |

---

## 6. Bootstrap 聚类算法

### 6.1 双策略设计

Bootstrap 支持两种聚类策略，通过 `cluster_method` 配置选择：

| 策略 | 触发条件 | 深度 | 依赖 |
|------|---------|------|------|
| **GMM+UMAP** | `cluster_method="gmm"` 或 `="auto"` + sklearn 可用 + 节点数 ≥ `cluster_size` | 自动多层 | scikit-learn, umap-learn（可选） |
| **简单余弦 BFS** | `cluster_method="simple"` 或自动回退 | 固定 3 层 | 无 |

配置项（`KnowledgeTreeConfig`）：
- `cluster_method: str = "auto"` — `"auto"` | `"gmm"` | `"simple"`
- `cluster_size: int = 20` — GMM 目标每簇节点数

依赖安装：`pip install ".[knowledge-tree]"` 或 `uv pip install scikit-learn umap-learn`

### 6.2 GMM+UMAP 算法流程（借鉴 LeanRAG）

```
叶子节点嵌入矩阵
  → UMAP 降维到 2D（可选，sklearn 可用时自动启用）
  → BIC 准则选择最优簇数 k
  → GMM 聚类 → k 个簇
  → 每簇创建摘要中间节点（启发式标题，不调用 LLM）
  → 中间节点嵌入作为下一层输入
  → 递归直到簇数 ≤ 1 或节点数不足
  → 创建根节点连接所有顶层节点
```

关键参数：
- **BIC 选 k**：遍历 `[2, max_k]`，选 BIC 最低的 k；`max_k = n / cluster_size`
- **UMAP 降维**：`n_neighbors=min(15, n-1)`，`metric=cosine`，`random_state=42`
- **P1 摘要**：启发式（子节点标题公共前缀），不调 LLM
- **P2 摘要**：LLM 生成摘要描述（与 LeanRAG 的 `aggregate_entities` prompt 类似）

### 6.3 简单余弦 BFS（零依赖回退）

```
叶子节点 → 余弦相似度邻接矩阵（threshold=0.6）
  → BFS 找连通分量 → 每个分量一个 group
  → root → group → leaf（固定 3 层）
```

### 6.4 聚类触发

- **常规**：批量式（定期/阈值触发）
- **特殊**：Agent 主动修改文件系统时即时捕获 → 优化 Change Mapping，但需防噪声（批量 + 阈值结合过滤）

---

## 7. 原则

1. **验证先行**：每个维度都用最简单的实现跑通端到端闭环
2. **数据驱动**：先积累日志，再基于数据做优化决策
3. **正确顺序**：先验证流程可行性（P1），再优化性能（P2-P3）
4. **可解释性**：Agent 编辑 → Change Mapping → 向量校准，全链路可追溯，结构化 JSON 日志从 P1 起强制输出

---

## 8. P1 最小闭环验证目标

跑通以下端到端流程，用小规模领域知识测试：

```
Bootstrap 建树（领域知识种子，GMM+UMAP 多层聚类）
  → LLM 路由检索（全量 LLM，完整日志）
  → Agent merge/split 编辑
  → Markdown → 图数据库同步
  → Change Mapping（JSON Patch）
  → 向量局部重嵌入
  → 检索日志积累
  → 异步优化触发
```

验证通过标准：
1. 建树后 LLM 路由能正确导航到目标节点
2. Agent 编辑后 Change Mapping 正确提取 Delta
3. 向量重嵌入后检索结果有可观测的改善（**核心度量指标**）
4. 闭环优化信号链完整（日志 → 信号 → 动作 → 效果可度量）
5. 全链路结构化 JSON 日志完整输出（检索 + 反馈 + Delta + 优化效果）

---

## 9. Wiki 种子格式

P1 使用 `workspace/knowledge_tree/` 作为种子输入目录，采用 claude-obsidian 风格的 wiki 格式。

### 9.1 目录结构

```
workspace/knowledge_tree/
  index.md                    ← 导航中心（type: meta）
  overview.md                 ← 系统概述
  concepts/
    _index.md                 ← 分类索引（type: meta）
    Three-Agent Architecture.md
    Plan JSON.md
    Execution Modes.md
    ...
  entities/
    _index.md
    Supervisor Agent.md
    Planner Agent.md
    Executor Agent.md
  sources/
    _index.md
    architecture-decisions.md
  questions/
    ...
  comparisons/
    ...
  _templates/
    concept.md / entity.md / source.md / question.md / comparison.md
```

### 9.2 Frontmatter 规范

每个 Markdown 文件使用 YAML frontmatter：

```yaml
type: concept | entity | source | question | comparison | meta
title: "Page Title"
tags: [tag1, tag2]
status: seed | developing | mature | evergreen
related:
  - "[[Other Page]]"          # wiki-link 关系提示
aliases: ["Alternative Name"]
domain: "domain-name"
complexity: intermediate | advanced
```

### 9.3 解析管线

`WikiFolderAdapter`（`src/common/knowledge_tree/ingestion/wiki_adapter.py`）负责：

1. **扫描** `workspace/knowledge_tree/` 下所有 `.md` 文件（排除 `_templates/`）
2. **解析** YAML frontmatter → 节点元数据（`page_type`, `tags`, `status` 等）
3. **提取** `[[wiki-links]]` → `RelationHint`（关系边提示）
4. **输出** `list[KnowledgeNode]` + `list[RelationHint]`
5. `type=meta` 页面跳过节点创建，但仍提取其 `[[wiki-links]]` 关系

### 9.4 与 Bootstrap 集成

```
workspace/knowledge_tree/ (Markdown 种子)
  → WikiFolderAdapter.parse_wiki_folder()
  → list[KnowledgeNode] + list[RelationHint]
  → embedder(node.content) 生成嵌入
  → Bootstrap 聚类（GMM+UMAP 或 简单余弦 BFS）
  → 写回 Markdown（Layer 1）+ Kùzu（Layer 2）+ Vector（Layer 3）
```

RelationHint 可在 P2 阶段用于辅助 DAG 边的构建（优先使用语义聚类边，wiki-link 作为辅助参考）。
