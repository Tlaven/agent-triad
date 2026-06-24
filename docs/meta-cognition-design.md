# 元认知设计（Meta-Cognition Design）

> 定位：V4 知识树的元认知能力参考文档。本文是 [`v4-kt-core-design.md`](v4-kt-core-design.md) 的配套，聚焦"Agent 如何从自身经验中学习"这一闭环。
> 硬规则见 [`CLAUDE.md`](../CLAUDE.md) §Session 同步；架构决策见 [`architecture-decisions.md`](architecture-decisions.md) 决策 28。

---

## 1. 目标与边界

让 Agent 从"有记忆但不从中学习"进化到"能从自身操作经验中提取可复用教训，并主动检索和运用这些教训"。

| 能力 | 实现状态 | 入口 |
|------|---------|------|
| 经验沉淀 | ✅ 已上线 | `extract_experience_from_executor_result()` |
| 操作种子（元规则） | ✅ 已上线 | `bootstrap.seed_meta_rules()` |
| 置信度评估 | ✅ 已上线 | Supervisor 系统提示 + snapshot 字段 |
| Alias RRF 检索扩展 | ✅ 已上线 | `retrieval/rag_search.py` |
| P3 自动优化闭环 | ✅ 已上线 | `optimization/signals.py` + `anti_oscillation.py` |

**不做的事**：不新增工具、不新增存储结构、不新增架构组件。全部复用现有 KT 基础设施。

---

## 2. 经验提取契约

**入口函数**：`src/common/knowledge_tree/ingestion/extractor.py:103` `extract_experience_from_executor_result(summary, updated_plan_json, status)`

**输出格式**（每个元素是一个完整经验节点内容）：

```markdown
[经验] {情境关键词}
情境：{什么任务/什么条件下}
行动：{做了什么}
结果：{成功|失败} — {具体结果}
教训：{下次应该怎样}
适用：{什么类型的任务应参考此经验}
```

**提取条件**（决策 29 加固后的门控）：

| status | 提取条件 |
|--------|---------|
| `failed` | 必须有 `step_failures` 或 summary ≥ 20 字；排除框架错误（mock/TypeError/await/import error） |
| `completed` | combined text 必须命中发现性模式（`发现/确认/需要先/必须/关键/导致…原因` 等）且 ≥ 50 字 |
| `paused` | 不提取（快照已结构化，无需经验化） |

**存储**：普通 KT 节点（markdown 文件），`metadata.node_type = "experience"`。目录归属由向量空间自然聚类决定，不强绑 `experience/` 目录。检索时与普通知识节点一起参与 RAG，靠内容相关性竞争。

---

## 3. Auto-ingest 链路

**入口函数**：`src/supervisor_agent/graph.py:1099` `_try_auto_ingest_executor_result(content, ctx, exec_status)`

**触发时机**：Executor 结果状态为 `completed` 或 `failed` 后，由 `dynamic_tools_node` 自动调用。

**关键设计：BlockingError 规避**

LangGraph dev server 中，同步 KT 操作会抛 `BlockingError`（asyncio 事件循环检测到同步阻塞调用）。解决方案是用 `asyncio.to_thread` 包裹整个 auto-ingest 调用：

```python
# graph.py:743 附近
await asyncio.to_thread(
    _try_auto_ingest_executor_result, content, runtime.context, exec_status
)
```

**错误隔离**：整个 auto-ingest 链路 try/except 包裹，KT 失败**绝不影响**主图执行路径。日志级别 `logger.debug`，非关键错误不告警。

**双重提取**：auto-ingest 同时调用两个提取器：
1. `extract_knowledge_from_executor_result` → 常规知识块（summary + result_summary + failure_reason）
2. `extract_experience_from_executor_result` → 结构化经验节点（`node_type=experience`）

两者都经过决策 29 的 Filter 质量门槛（通用模板过滤、低信息量过滤、密度门控）。

---

## 4. 元规则播种与治理

**播种入口**：`src/common/knowledge_tree/bootstrap.py:198` `seed_meta_rules(kt)`

**调用时机**：KT 初始化时（`core.py:307`），首次建树自动写入操作元规则。

**治理架构（决策 28）**：四层从存储到注入逐层收紧。

| 层 | 机制 | 位置 |
|----|------|------|
| 存储 | `MAX_META_RULES = 15` 硬上限 | `config.py` |
| 存储 | 冲突 warning（embedding sim > 0.7） | `tools.py` `_sync_add_meta_rule()` |
| 注入 | 别名互斥分组 + 同优先级全抑制 | `graph.py` `_resolve_meta_rule_conflicts()` |
| 自救 | `knowledge_tree_delete_meta_rule(title)` | Supervisor 工具 |

**为什么同优先级矛盾全抑制而非选一条**：任意选择比不选择更危险——"禁止工具" vs "必须工具"选哪条都是错的。抑制后 LLM 回退默认行为，反而最安全。

---

## 5. Alias RRF 4 路径融合

**目的**：元规则通过 alias embedding 扩展 RAG 检索可达性。一条规则可能有多个别名表述，每个别名独立 embed 后参与检索。

**实现**：`src/common/knowledge_tree/retrieval/rag_search.py`

4 路径 RRF（Reciprocal Rank Fusion）融合，`k_rrf = 60` 平滑常数：

| 路径 | embedding 来源 | 用途 |
|------|---------------|------|
| content | 节点正文 | 主语义匹配 |
| title | 节点标题 | 标题级精确匹配 |
| alias | `alias:{node_id}:{i}` | 元规则别名扩展 |
| anchor | 目录锚点 | 同目录聚簇增强 |

**融合公式**（每路径独立排序后）：

```
score(node) = Σ_path 1 / (k_rrf + rank_path(node) + 1)
```

**效果**：一条元规则即使正文用词与查询不完全匹配，只要某个别名命中，也能通过 alias 路径获得 RRF 分数进入最终结果。

---

## 6. P3 自动优化闭环

**目的**：KT 自检发现质量问题（摄入缺失、结构混乱、反馈异常）时，主动生成优化建议注入 Supervisor。

**信号检测**：`src/common/knowledge_tree/optimization/signals.py`

检测的信号类别：
- 检索结果质量异常（低相似度、高矛盾密度）
- 摄入管道异常（过滤率突变、去重命中异常）
- 结构异常（目录膨胀、孤儿节点）

**反振荡**：`src/common/knowledge_tree/optimization/anti_oscillation.py`

防止优化建议反复触发导致系统震荡——记录近期已触发的建议，冷却期内不重复注入。

**注入通道**：`graph.py` `kt_retrieve()` 节点将优化建议写入 `state.kt_optimization_suggestions`，`call_model()` 以"可选行动"措辞注入系统提示（非硬约束）。Supervisor 可选择是否采取行动（摄入缺失知识 / 重组树 / 记录反馈）。

---

## 7. 置信度评估

**目的**：让 Supervisor 对自己的回答有元认知——知道检索结果是否充分。

**实现方式**：纯提示词工程，无新增代码逻辑。Supervisor 系统提示的 KT 指导部分包含检索质量评估指令：

| 检索质量 | Supervisor 行为 | 标注 |
|---------|----------------|------|
| 直接回答了问题 | 正常回答 | `[基于记忆]` |
| 部分相关有缺口 | 回答但提示 | `[部分记忆]` |
| 不相关但有把握 | 直接回答 | 不提及 KT |
| 不相关且无把握 | 换关键词重新检索，或升级 Planner | — |

**闭环信号**：Supervisor 的"部分记忆"和"不相关"判断通过 `record_feedback` 写回 KT，形成 P3 优化闭环的输入信号。

**可观测性**：启用 `KT_SNAPSHOT_ENABLED=true` 时，每次任务完成后在 `logs/` 写入 JSON 快照，包含 `confidence_level`、`agent_used_kt`、`retrieved_nodes` 等字段，供人类开发者诊断 KT 如何影响 Agent 行为。详见 `snapshot.py`。

---

## 关联文档

- [`v4-kt-core-design.md`](v4-kt-core-design.md) — KT 核心设计（当前权威）
- [`kt-subsystems.md`](kt-subsystems.md) — KT 子包走读（optimization/embedding/editing）
- [`architecture-decisions.md`](architecture-decisions.md) 决策 28（元规则治理）、决策 29（摄入质量门槛）
- [`CLAUDE.md`](../CLAUDE.md) §Session 同步（auto-ingest 触发时机）
