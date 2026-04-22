# V4 涌现式知识树 — 概念对齐文档

> 状态：v4（2026-04-22）
> 前置：架构决策 18-26（`architecture-decisions.md`）
> 范围：AgentTriad 内部，Supervisor 内嵌组件
> 目标：Agent 信息的自组织存储、高效检索、持续进化

---

## 1. 核心思想

**文件系统即树，向量服务于树，Agent 驱动演化。**

不是"树结构 vs 向量搜索"二选一，而是 Agent 主导的双向闭环：

- **文件系统**就是知识树——目录层级 = 父子关系，文件 = 知识节点
- **向量**跟随树结构——同目录文件的向量聚成簇，结构变化驱动向量重组
- **Agent** 是决策者——检索时决定用 RAG 还是手动搜索，主动重组树时表达结构意图

```
文件系统（主结构）
  ↕ 双向
向量索引（语义索引，簇与目录对齐）
  ↕ 辅助
轻量 Overlay（跨目录关联边，JSON）
```

---

## 2. 两层存储 + Overlay 架构

```
┌─────────────────────────────────────────────────────┐
│  Layer 1: 文件系统（Source of Truth + 结构）          │
│                                                     │
│  目录层级 = 树的父子关系（primary edges）              │
│  目录内的 Markdown 文件 = 叶子/中间节点               │
│  README.md = 目录摘要（可选，Agent 维护，有则更好）    │
│  叶子节点可包含可执行代码/脚本（被 Markdown 引用解释）  │
│  人类可直接读写、git 可版本化                         │
├─────────────────────────────────────────────────────┤
│  Layer 2: 向量索引（语义检索层）                      │
│                                                     │
│  stored_vector = normalize(α · content_embedding     │
│                          + β · structural_vector)    │
│                                                     │
│  content_embedding  — 纯内容语义，永不变              │
│  structural_vector  — 来自目录锚点，跟随重组更新       │
│  同一目录的文件共享锚点 → stored_vector 自然聚簇       │
├─────────────────────────────────────────────────────┤
│  Overlay: 轻量关联图（跨目录 is_primary=False 边）    │
│                                                     │
│  单个 JSON 文件，仅存跨目录关联关系                    │
│  不替代文件系统的 primary 结构                         │
└─────────────────────────────────────────────────────┘
```

### 与旧架构（三层分离）的关键区别

| 对比维度 | 旧架构（三层） | 新架构（两层 + Overlay） |
|---------|--------------|----------------------|
| 结构存储 | 独立 Graph 数据库（Kùzu/内存） | 文件系统目录层级 |
| 主父子关系 | `KnowledgeEdge.is_primary=True` | 目录包含关系 |
| 向量生成 | 纯内容 embedding | content + structural 混合 |
| 同步负担 | Markdown ↔ Graph ↔ Vector 三方同步 | 文件系统 → Vector 单向派生 |
| 结构变更 | 修改 Graph 边 + sync | 移动文件 + 重算向量 |

---

## 3. 向量映射机制

### 3.1 核心公式

```
content_embedding  — 纯内容语义，文件创建时算一次，永不变
structural_vector  — 来自所属目录的锚点
stored_vector      — normalize(α · content_embedding + β · structural_vector)
```

- `α` 控制内容语义的权重（主要因素）
- `β` 控制结构位置的权重（辅助聚集）
- 默认 α ≈ 0.8, β ≈ 0.2~0.3

### 3.2 目录锚点（Directory Anchor）

每个目录有一个锚点向量，代表该目录在语义空间中的位置：

```
anchor_D = normalize(mean(content_embedding(目录 D 下所有文件)))
```

锚点 = 目录内所有文件内容向量的质心。

目录内每个文件的 `structural_vector = anchor_D`。

**效果**：同一目录的文件共享锚点 → stored_vector 被拉向同一方向 → 自然聚成簇。但 α 权重保证内容匹配仍是主要检索因素。

### 3.3 增量摄入时的向量生成

新知识到达时：

1. 计算新内容的 `content_embedding`
2. 与所有目录锚点比较相似度
3. 最相似的锚点 → 新知识放入该目录
4. `structural_vector = 该目录的锚点`
5. `stored_vector = α · content + β · structural`
6. 如果锚点相似度都不够 → 创建新目录 → 锚点 = 该文件的 content_embedding

### 3.4 Agent 重组后的向量更新

1. 文件移动到新目录
2. 新目录的锚点重新计算
3. 被移动文件的 `structural_vector` 更新为新目录锚点
4. `stored_vector` 重新计算
5. 向量空间自动跟随文件结构调整

**无循环依赖**：content_embedding 只算一次不动，锚点从 content_embedding 推导，stored_vector 是两者的合成。

---

## 4. 检索流程

```
Agent 需要知识
  → ① RAG 快速查找（stored_vector 相似度检索）
  → ② 满意？拿走结束
  → ③ 不满意？Agent 手动搜索文件系统
```

### 4.1 RAG 检索

- 查询向量化后与所有 `stored_vector` 计算相似度
- 超过阈值则返回结果
- 同目录的文件因锚点加分更容易被一起召回

### 4.2 Agent 手动搜索文件系统

RAG 不满意时，Agent 切换到**分层探索模式**：

1. **概览**：获取目录树整体结构
2. **逐级决策**：读当前目录内容（README.md 或直接列表），决定进入哪个子目录或读哪个文件
3. **精确搜索**：在目录内用文本匹配（grep）、格式搜索等方式定位

Agent 可用多种搜索方式：
- 读目录列表（类似 `ls` / `tree`）
- 读文件内容
- 文本匹配（grep）
- 格式结构搜索

这不是盲目遍历，而是 Agent 根据每一步的观察自主决策下一步。

---

## 5. 三种变更模式

### 5.1 Bootstrap（初始建树）

从人工预组织的种子目录建树：

```
种子目录（人类组织的文件系统结构）
  → 读取目录层级 = 树结构（primary edges）
  → 解析每个 Markdown 文件 = 节点内容
  → 生成 content_embedding
  → 计算目录锚点（每目录内文件的 content 质心）
  → 生成 structural_vector + stored_vector
  → 写入向量索引
  → Overlay 初始为空
```

**关键**：目录结构直接成为树结构，不需要聚类算法重新组织。种子目录里已有的 README.md 直接作为摘要使用。

### 5.2 增量摄入

新知识到达时的增量操作：

```
新知识 → 计算 content_embedding
       → 与所有目录锚点比较
       → 足够相似？
           → 是：放入对应目录，局部更新锚点
           → 否：创建新目录，挂载到语义最近的父目录下
       → 生成 structural_vector + stored_vector
       → 更新向量索引
```

不执行全局重聚类，仅局部更新。

### 5.3 Agent 驱动的主动重构

Agent 通过"编号树重组"表达结构意图：

#### Step 1：系统展示当前树

```
01 development/
    01 debugging.md
    02 async_pattern.md
02 skills/
    01 code_review.md
03 domain/
    01 architecture.md
    02 design_decisions.md
```

#### Step 2：Agent 输出重组后的树

Agent 按自己的理解输出新的目录结构和编号。

#### Step 3：系统自动执行

1. **解析差异**：对比新旧编号结构，识别文件移动、目录合并/拆分
2. **自动移动文件**：Python 程序在文件系统中实际执行移动/创建/删除
3. **提取关系信号**：从位置变化中提取 Agent 认为的节点关联性
   - 被放到同一目录的文件 → 强关联
   - 保持在一起的文件 → 确认关联
   - 被分开的文件 → 弱化关联
4. **重算向量**：
   - 更新受影响目录的锚点
   - 更新被移动文件的 structural_vector 和 stored_vector
   - 向量空间跟随结构调整

#### 触发条件

- Agent 主动执行（通过 reorganize 工具）
- OptimizationSignal 触发（如目录内方差过高）
- 仅在子图范围内执行，非全树重建

---

## 6. 异步优化闭环

### 6.1 四种优化信号

| 信号类型 | 触发条件 | 优化动作 |
|----------|----------|----------|
| 整体失败 | RAG + 手动搜索均无结果，累积达阈值 | Agent 创建新节点/目录，失败查询作为种子 |
| 检索不满意 | RAG 返回结果但 Agent 标记不满意 | 记录信号，供 Agent 下次重组参考 |
| 目录内方差过高 | 同目录文件 content_embedding 差异过大 | Agent 考虑拆分目录或调整归属 |
| 内容不足 | 找到文件但内容不充分 | Agent 更新文件内容 |

### 6.2 防震荡机制

- **独立阈值**：每种信号类型独立配置触发条件
- **全局频率上限**：总优化动作受全局限额约束，超出排队
- **优先级排序**：整体失败 > 检索不满意 > 方差过高 > 内容不足

所有优化动作异步批量执行，不阻塞检索路径。

---

## 7. Overlay 关联图

### 7.1 定位

文件系统用目录层级表达 primary 父子关系（单亲）。但知识节点可能属于多个领域。Overlay 图用轻量 JSON 存储跨目录关联边（`is_primary=False`）。

### 7.2 存储格式

单个 JSON 文件（如 `knowledge_tree/.overlay.json`）：

```json
[
  {
    "source": "development/debugging.md",
    "target": "skills/code_review.md",
    "relation": "related",
    "strength": 0.8,
    "created_by": "agent",
    "note": "调试技巧与代码审查经验相关"
  }
]
```

### 7.3 关联边来源

- Markdown 文件内的 wiki-link 引用（`[[../other/file.md]]`）
- Agent 在重组过程中发现但未放到同一目录的关联
- RAG 检索日志中的共现模式

---

## 8. 叶子节点特殊能力

### 8.1 可执行代码/脚本

叶节点目录下可包含可执行代码文件（`.py`、`.sh` 等），被同目录的 Markdown 引用和解释：

```
skills/
  code_review.md          ← 引用 ./scripts/review_checklist.sh 并解释
  scripts/
    review_checklist.sh   ← 可执行脚本，Agent 可直接调用
```

Agent 检索到该知识时，读到脚本引用即可直接使用，无需自己编写。

---

## 9. 分阶段实现路线

### 9.1 P1：最小闭环

- **存储**：文件系统 + 内存向量索引 + Overlay JSON
- **Bootstrap**：从种子目录建树（目录结构 = 树结构）
- **检索**：RAG 向量检索（content_embedding only，P1 先不加 structural）
- **摄入**：增量嫁接（新知识 → RAG 定位 → 放入对应目录）
- **验证**：端到端闭环跑通

### 9.2 P2：混合向量 + 手动搜索

- **向量映射**：content_embedding + structural_vector（目录锚点法）
- **手动搜索**：Agent 文件系统探索工具（tree、read、grep）
- **Agent 重组**：编号树展示 → Agent 输出新结构 → 自动移动 + 向量调整
- **Overlay**：跨目录关联边管理

### 9.3 P3：完整闭环

- **优化信号**：全自动信号检测 + 防震荡
- **高级摄入**：Leiden 全局聚类（Agent 读整本书场景）
- **信息扩展**：Agent 记忆（衰减分数）、技能/Skill 绑定
- **小模型路由**：基于 P1-P2 积累的日志训练

---

## 10. 原则

1. **文件系统是 truth**：所有结构信息以文件系统为准，向量是派生物
2. **Agent 是决策者**：Agent 决定何时重组、如何搜索、知识放哪
3. **验证先行**：每个维度用最简单实现跑通端到端闭环
4. **数据驱动**：先积累日志和反馈，再基于数据做优化决策
5. **可解释性**：Agent 重组 → 文件移动 → 向量调整，全链路可追溯
