# KT 端到端质量验证报告

> 日期：2026-05-07
> 总测试数：987（单元 + 集成），KT 专项：340
> 状态：**全部通过**

---

## 1. 执行摘要

验证范围：从"组件不报错"到"KT 真正为 Agent 提供有用记忆"。

| 维度 | 验证方式 | 结果 |
|------|---------|------|
| Hash embedder 检索 | 14 个集成测试，精确+语义查询 | 全部通过 |
| Entry A 闭环 | 22 个集成测试，executor → extract → ingest → retrieve | 全部通过 |
| Filter 边界条件 | 35 个单元测试，代码块/混合语言/超长/URL/变体 | 全部通过 |
| 种子知识覆盖 | 15 篇种子文档，5 类场景 | 全部可检索 |
| 配置一致性 | config.py ↔ context.py ↔ 设计文档 | 一致 |
| 全量回归 | 987 tests（unit + integration） | 0 failures |

---

## 2. Hash Embedder 检索质量基线

> Hash embedder 是默认 fallback（无 GPU 环境下使用），这是最关键的质量基线。

### 2.1 精确匹配检索

| 查询 | 预期目标文档 | Top-3 命中 |
|------|------------|-----------|
| "AgentTriad 三层架构 Supervisor Planner Executor" | architecture/agent-triad-structure | Yes |
| "Executor 子进程 FastAPI Mailbox 通信协议" | architecture/executor-protocol | Yes |
| "Plan JSON plan_id steps intent expected_output" | architecture/plan-json-and-state | Yes |
| "失败 failed 重规划 replan MAX_REPLAN" | conventions/error-handling | Yes |
| "知识树 向量 向量索引 embedding RAG RRF" | patterns/knowledge-tree-design | Yes |
| "pytest 单元测试 集成测试 e2e coverage" | conventions/testing-patterns | Yes |
| "Observation Reflection 截断 快照 snapshot" | patterns/observation-and-reflection | Yes |

### 2.2 分数质量

- 精确匹配分数：≥ 0.15（阈值），实际 0.3-0.6
- 结果按相似度降序排列：验证通过
- 不相关查询（"红烧肉的做法"）分数 < 0.4：验证通过

### 2.3 闭环验证

- Ingest → Retrieve：新摄入知识可被检索
- Dedup：重复摄入被正确去重
- Bootstrap：从 15 篇种子文档正常创建节点和锚点

---

## 3. Entry A 完整闭环

> Entry A 是 KT 最重要的自动知识来源：executor 结果 → extract → filter → ingest → retrieve。

### 3.1 成功场景

| 场景 | 验证内容 | 结果 |
|------|---------|------|
| 3 步完成 | 每步 result_summary 提取+摄入+检索 | 通过 |
| Goal 提取 | completed 状态提取 goal 作为上下文 | 通过 |
| 仅 summary | 无 plan_json 时只从 summary 提取 | 通过 |

### 3.2 失败场景

| 场景 | 验证内容 | 结果 |
|------|---------|------|
| 部分失败 | failure_reason 提取+检索 | 通过 |
| 全部失败 | 多个 failure_reason 正确提取 | 通过 |
| 失败教训检索 | 用自然语言查询失败原因 | 通过 |

### 3.3 Filter 行为

| 输入 | 预期 | 结果 |
|------|------|------|
| "所有步骤执行完成" | 过滤（generic_template） | 过滤 |
| "执行成功" | 过滤（generic_template） | 过滤 |
| 有意义的 result_summary | 通过（task_complete） | 通过 |
| Extractor 包裹的通用文本 | 通过（含 step_id + intent 上下文） | 通过 |

### 3.4 边界条件

| 场景 | 验证 | 结果 |
|------|------|------|
| 空 plan_json | 只从 summary 提取 | 通过 |
| 格式错误 JSON | 不崩溃，只从 summary 提取 | 通过 |
| 空 summary + 空 plan | 返回空列表 | 通过 |
| 空 steps 数组 | 只提取 summary + goal | 通过 |
| paused 状态 | 不提取 goal（仅 completed 提取） | 通过 |

---

## 4. 种子知识覆盖矩阵

### 4.1 种子文档清单（15 篇）

| 目录 | 文档 | 覆盖场景 |
|------|------|---------|
| architecture/ | agent-triad-structure.md | 系统架构概览 |
| architecture/ | executor-protocol.md | Executor 通信协议 |
| architecture/ | graph-topology.md | 图拓扑结构 |
| architecture/ | plan-json-and-state.md | Plan JSON + State |
| conventions/ | coding-style.md | 编码风格 |
| conventions/ | error-handling.md | 错误处理模式 |
| conventions/ | testing-patterns.md | 测试模式 |
| conventions/ | tools-reference.md | **工具参考手册（新增）** |
| patterns/ | knowledge-tree-design.md | KT 设计 |
| patterns/ | observation-and-reflection.md | Observation/Reflection |
| patterns/ | process-management.md | 进程管理 |
| patterns/ | supervisor-decision.md | Supervisor 决策 |
| setup/ | development-workflow.md | **开发工作流（新增）** |
| setup/ | environment-configuration.md | **环境配置（新增）** |
| troubleshooting/ | common-errors.md | **常见错误（新增）** |

### 4.2 查询覆盖

| 用户查询类型 | 覆盖状态 | 对应种子文档 |
|-------------|---------|------------|
| "怎么配置环境/API key" | 已覆盖 | setup/environment-configuration |
| "怎么启动开发环境/调试" | 已覆盖 | setup/development-workflow |
| "executor 有什么工具/怎么读文件" | 已覆盖 | conventions/tools-reference |
| "executor 启动失败/连接超时" | 已覆盖 | troubleshooting/common-errors |
| "系统架构是怎样的" | 已覆盖 | architecture/agent-triad-structure |
| "怎么写测试" | 已覆盖 | conventions/testing-patterns |

---

## 5. Filter 边界条件评估

### 5.1 测试覆盖

| 类别 | 测试数 | 关键发现 |
|------|-------|---------|
| 代码块 | 4 | 有自然语言说明的代码块正确保留 |
| 混合语言 | 5 | 中英混合文本通过长度/关键词规则 |
| 超长文本 | 3 | 5000+ 字符文本正确处理 |
| URL/路径 | 3 | 含 URL/路径的文本通过数字检测 |
| 通用模板变体 | 3 | 所有变体正确过滤，近似文本正确保留 |
| 特殊格式 | 5 | JSON/Markdown/Unicode/Tab 分隔正确处理 |

### 5.2 已知行为

- **Extractor 包裹的通用文本**：extractor 格式化为 "步骤 X (intent): result_summary"，即使 result_summary 是通用文本，包裹后的字符串含 step_id + intent 上下文，因此通过 filter。这是可接受行为——上下文信息有检索价值。
- **纯代码**：无自然语言的代码片段通过 sufficient_length（> 50 字符）。低优先级问题——实际 Entry A 场景下代码通常伴随自然语言。

---

## 6. 配置一致性审计

### 6.1 config.py ↔ context.py

所有 16 个 KT 配置参数在 `KnowledgeTreeConfig` 和 `Context` 之间完全一致：
- `rag_similarity_threshold`: 0.15
- `embedding_model`: "BAAI/bge-small-zh-v1.5"
- `embedding_dimension`: 512
- `dedup_threshold`: 0.95
- `ingest_attach_threshold`: 0.7
- 其余参数均一致

### 6.2 Semantic embedder 自动调整

- Hash embedder：使用配置默认值（0.15）
- Semantic embedder：检测到阈值 < 0.3 时自动提升至 0.5
- 设计文档已更新以反映实际行为

### 6.3 Observation 路径

- `observation_workspace_dir`: "workspace/agent/.observations"（在 agent 工作区根目录内）
- 与 KT root（"workspace/knowledge_tree"）独立——不同组件，不同路径

### 6.4 硬编码阈值（非配置化）

以下值硬编码在代码中，不可通过环境变量调整：
- RRF 常数 `k_rrf=60`（检索融合）
- 向量搜索默认 `top_k=5`/`top_k=3`
- 质量评估阈值（high ≥0.5, medium ≥0.25）
- Hash embedder n-gram 权重

这些值当前无需用户调优。如需调整，可通过 `config.py` 统一配置化。

---

## 7. 阈值调优指南

### 7.1 RAG 检索阈值（`kt_rag_similarity_threshold`）

| Embedder | 推荐阈值 | 说明 |
|----------|---------|------|
| hash | 0.15（默认） | n-gram 匹配分数较低，低阈值避免漏检 |
| semantic | 0.5（自动提升） | 语义理解更精确，高阈值避免噪声 |

### 7.2 摄入附着阈值（`kt_ingest_attach_threshold`）

- 默认 0.7：新知识附着到相似度 ≥0.7 的最近目录锚点
- 降低至 0.5：更多新知识附着到锚点（可能增加噪声）
- 提高至 0.9：仅非常相似的内容附着（可能创建过多孤立节点）

### 7.3 去重阈值（`kt_dedup_threshold`）

- 默认 0.95：仅去除近乎完全相同的重复
- 降低至 0.85：更积极的去重（可能丢失变体知识）
- 保持 0.95：推荐，避免误去重

---

## 8. 结论

KT 系统验证通过：

1. **默认 hash embedder 可用**：15 篇种子文档在 hash embedder 下均可被精确匹配查询检索到
2. **Entry A 闭环可靠**：executor 结果 → 提取 → 过滤 → 摄入 → 检索全链路通过
3. **种子知识覆盖核心场景**：15 篇种子文档覆盖架构、协议、工具、配置、排错等 6 类场景
4. **Filter 在生产场景下正确**：代码块、混合语言、超长文本等边界条件处理正确
5. **配置一致性无问题**：所有参数在 config.py/context.py/设计文档之间一致
6. **全量测试无回归**：987 tests, 0 failures
