# V4 知识树核心设计 — 向量-结构互塑闭环

> 状态：P2 完成（2026-05-08），structural_vector 混合 + Agent 重组 + Overlay 管理，362 KT tests
> 前置：`v4-knowledge-tree-spec.md`（P1 技术规格，已实现）
> 定位：知识树是 AgentTriad 与其他 Agent 项目的核心差异

---

## 1. 核心愿景

V4 知识树服务于一个长期目标：**必须让 Agent 自己可以管理自己的上下文**。这里的"管理"不只是检索记忆，而是让 Agent 能主动判断什么值得沉淀、何时召回、何时裁剪、何时重组，并把上下文维护纳入任务闭环。

**一个向量-结构互塑闭环**：

```
信息自底向上涌现成动态层级树
  → Agent 自主编辑树结构后产生结构化 Change Mapping
  → Change Mapping 反向实时校准向量空间映射
  → 形成记忆的持续自我进化
```

关键约束：**组织靠向量不靠 LLM，降低 token 消耗**。Agent 只在必要时参与，日常聚类和放置全部由向量空间自动完成。

### 元知识自举：Agent 学会使用记忆

KT 不只存储任务知识，还可以存储**关于如何使用 KT 本身的决策知识**（元知识）。这包括：

- **存什么** — 什么样的信息值得沉淀（判断规则）
- **何时取** — 在什么场景下应该主动检索（召回时机）
- **怎么用** — 检索到的信息如何与当前任务结合（应用方式）

这些元知识作为普通文本节点存入 KT，与任务知识走同一条 ingest → embed → 聚类路径。当 Supervisor 需要管理 KT 时，RAG 检索到相关元知识并注入上下文，Supervisor 据此做出决策。

```
Supervisor 需要做 KT 相关决策
  → kt_retrieve 自动注入
     ├── 任务相关记忆（"上次这个项目怎么构建的"）
     └── 元知识（"遇到这类任务该怎么用 KT"）
  → Supervisor 拿到增强后的上下文 → 做出更好的决策
```

**核心洞察**：这把"让 Agent 变聪明"从"依赖模型原生判断力"转化为"让 Agent 检索到更好的知识"。LLM 遵循检索到的指令，远比自主做出正确判断容易。这像人类的学习方式——不需要天生知道怎么做笔记，学了一套方法论后遇到信息时调用即可。

**前提**：语义 embedder 必须可靠工作。元知识的检索依赖语义匹配，hash embedder 无法支撑。

**元知识自举的现实约束**（测试 F7-F9 验证）：

1. **系统提示词是前提**：RAG 注入的元知识被 LLM 当"信息"而非"指令"。只有系统提示词明确要求"遵循注入的行为规则"时，元知识才能有效影响决策。
2. **只能优化，不能触发**：元知识可以优化已有行为（如"使用 retrieve 时用更精确的查询词"），但不能触发新行为（如"首次遇到某场景时主动 ingest"）。新行为必须通过系统提示词或工具注册引入。
3. **认知集成的三层结构**：
   - 系统提示词（告知 Supervisor 有 KT、何时使用）→ 不可替代的基础
   - Auto-inject（被动记忆注入）→ 需要明确标示来源
   - 元知识 RAG（运行时策略优化）→ 需要前两层支撑才能生效

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

自动注入阈值取决于 embedder：hash embedder 使用配置默认值（0.15），semantic embedder 自动提升至 0.5 以避免注入噪声。仅在 `__start__` 入口执行，工具循环不重复注入。质量标记：`[高可信]`（≥0.5）和 `[参考]`（≥0.25）标注在注入内容前。

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

这意味着上下文自管理的责任首先落在 Supervisor：它需要决定哪些上下文进入知识树、哪些上下文被召回给 Planner/Executor、哪些过期或低质量信息应被压缩、降权或清理。

**Supervisor 行使管理权的前提**（认知集成约束）：

1. **系统提示词必须告知管理职责**：Supervisor 必须通过系统提示词知道自己有 KT 管理职责，否则不会主动使用 KT 工具。
2. **Auto-inject 必须明确标示来源**：注入的知识必须标注"来自记忆系统，非用户输入"，否则 Supervisor 无法区分用户输入和记忆注入。
3. **质量标记辅助判断**：高可信（≥0.7）和参考（0.4-0.7）的标记帮助 Supervisor 判断检索结果的可靠程度。

未来扩展：
- Planner 被 Supervisor 调用时，Supervisor 可以将 KT 检索结果注入 Planner 的 prompt
- Executor 执行 Plan 的每个 step 时，可以按 step intent 做 RAG 检索（但当前不做，先打磨好基础）

---

## 6. 当前状态与下一步

### 已完成

- [x] P1 基础架构（两层存储 + Overlay + Bootstrap）
- [x] 语义 embedder 集成（BAAI/bge-small-zh-v1.5，降级到 hash）
- [x] 节点缓存（消除重复文件 I/O）
- [x] 工具精简（bootstrap/status 移除 + 4 Executor 管理工具合并为 manage_executor，共 3 核心 + 4 KT = 7 工具）
- [x] Graph 集成（kt_retrieve 节点，用户消息自动 RAG 注入）
- [x] `get_or_create_kt()` 模块级接口
- [x] RAG 结果拼接到用户消息（不是 system prompt）
- [x] 向量-结构互塑闭环：MarkdownStore on_change 回调 + Change Mapping 自动锚点刷新
- [x] 检索结构信号：rag_search 锚点扩展路径（RRF Path 3）
- [x] **语义 embedder 端到端质量验证** — 精确匹配>=0.7，语义同义>=0.45，噪声<0.3（993 tests, 340 KT-specific）
- [x] **入口 A：Executor 结果知识提取** — `extractor.py` + Supervisor graph 自动 ingest（completed + failed 状态均触发，失败结果的 failure_reason 作为教训知识）
- [x] **项目种子知识** — 15 篇文档覆盖架构/规范/模式/配置/排错（含 Plan JSON、Observation/Reflection、工具参考、环境配置、常见错误）
- [x] **Filter 校准** — 15 种真实输出模式验证 + 通用模板垃圾过滤
- [x] **垃圾节点清理** — 通用模板文本过滤防止低质量 ingest
- [x] **P2：Agent 可见性** — `knowledge_tree_status`（概览）+ `knowledge_tree_list`（节点列表），Supervisor 可查看 KT 内部状态
- [x] **认知集成** — 系统提示词 KT 指导 + Auto-inject 来源标示 + 质量标记（[高可信]/[参考]），993 tests
- [x] **认知集成第二轮** — Reflection/paused 状态处理 + 异步派发诚实 + Observation 路径修复（workspace/agent/.observations）+ Planner KT 共享
- [x] **端到端质量验证** — Hash embedder 检索基线（14 tests）+ Entry A 闭环（22 tests）+ Filter 边界（23 tests）+ 种子增强（15 篇）+ 配置审计，993 tests 全通过，详见 `docs/kt-validation-report.md`
- [x] **P2：structural_vector 混合** — `stored_vector = normalize(α·content + β·structural)`，同目录文件聚簇增强
- [x] **P2：Agent 驱动重组** — 编号树显示 → Agent 输出新结构 → 自动迁移 + 向量调整 + Overlay 边更新
- [x] **P2：Overlay 主动管理** — `knowledge_tree_overlay` 工具：跨目录关联边的增删查
- [x] **语义 embedder (API)** — SiliconFlow API embedder (BAAI/bge-large-zh-v1.5, 1024-dim)，检索分数 +41% vs hash，同义查询从零命中到全命中。三种 embedder 可配置切换（hash/local/api）。详见 `docs/kt-validation-report.md` §9
- [x] **Auto-inject 有效性验证** — 真实 LLM 会话验证：KT ON 时 Supervisor 能引用只存在于 KT 中的知识，KT OFF 时得到通用回答。详见 `docs/kt-validation-report.md` §10

### 待实现（按优先级）

1. **语义 embedder 接入生产** — 配置 `.env` 切换 `KT_EMBEDDER_TYPE=api`，替换 hash 作为默认
2. **Change Mapping 效果验证** — 等知识库自然增长、主题多样化后，验证 stored_vector 是否有效（当前锚点区分度 0.71 太高，architecture↔patterns=0.93）
3. **P3：完全自动优化闭环** — 信号检测 + 反振荡 + Leiden 全局聚类（前置条件：Change Mapping 验证通过）

### 文件结构

```
src/common/knowledge_tree/
    __init__.py              # KnowledgeTree 门面 + get_or_create_kt()
    config.py                # KnowledgeTreeConfig（含 embedder_type 字段）
    bootstrap.py             # 种子目录建树
    embedding/
        api.py               # SiliconFlow / OpenAI-compatible API embedder
        semantic.py          # sentence-transformers 本地 embedder
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
        filter.py            # 记忆过滤（含通用模板垃圾检测）
        ingest.py            # 知识摄入管道
        extractor.py         # Entry A：Executor 结果知识提取
        filter.py            # 轻量过滤
        ingest.py            # 增量嫁接
    editing/
        re_embed.py          # 节点重嵌入
        stored_vector.py     # P2: structural_vector 混合计算
        tree_view.py         # P2: 编号树渲染/解析
        reorganize.py        # P2: 重组差异计算 + 移动执行
    optimization/
        anti_oscillation.py # 反振荡保护
        signals.py          # 优化信号检测
    dag/
        node.py              # KnowledgeNode
```

---

## 7. Supervisor 工具列表

| 工具 | 类型 | 说明 |
|------|------|------|
| `knowledge_tree_retrieve` | 主动工具 | Supervisor 主动查询知识树 |
| `knowledge_tree_ingest` | 主动工具 | Supervisor 主动记忆（用户要求、skill 触发、自主判断） |
| `knowledge_tree_status` | 可见性工具 | 返回 KT 概览（节点数、目录数、锚点数） |
| `knowledge_tree_list` | 可见性工具 | 列出节点（支持按目录过滤，含标题和内容预览） |
| `knowledge_tree_tree` | 重组工具 | 返回编号树视图，Agent 可查看当前结构 |
| `knowledge_tree_reorganize` | 重组工具 | Agent 提出编号树方案，系统自动执行移动 + 向量调整 |
| `knowledge_tree_overlay` | 关联工具 | 管理跨目录关联边（add/remove/list） |
| `kt_retrieve` graph 节点 | 自动注入 | 用户消息进入时自动 RAG 检索，拼接到用户消息 |

bootstrap 已从工具列表移除（内部自动处理）。
