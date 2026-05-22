# 元认知设计规格

> 状态：2026-05-22 设计完成
> 定位：V4 知识树元认知能力，三阶段渐进式实现
> 前置：P1/P2/P3 已完成，元知识自举双通道注入已完成

---

## 目标

让 Agent 从"有记忆但不从中学习"进化到"能从自身操作经验中提取可复用教训，并主动检索和运用这些教训"。

三个阶段解决三个痛点，按 ROI 排序：

| 阶段 | 痛点 | 一句话 |
|------|------|--------|
| 1 经验沉淀 | 失败经验不沉淀 | Executor 结果 → 结构化经验节点 → KT 自动存储和检索 |
| 2 操作种子 | KT 操作知识缺失 | 种子一组"何时/如何用 KT"的元规则 |
| 3 置信度评估 | Agent 不知道自己不知道 | Supervisor 评估检索充分性，不充分时主动补充 |

---

## 阶段 1：失败经验沉淀

### 经验节点格式

```markdown
[经验] {情境关键词}
情境：{什么任务/什么条件下}
行动：{做了什么}
结果：{成功|失败} — {具体结果}
教训：{下次应该怎样}
适用：{什么类型的任务应参考此经验}
```

- 存储：普通 KT 节点（markdown 文件），`metadata.node_type = "experience"`
- 目录归属：走正常 ingest → embed → 聚类。不强绑 `experience/` 目录，由向量空间自然决定
- 检索：和普通知识节点一起参与 RAG，靠内容相关性竞争

### Extractor 增强

**改动文件**：`src/common/knowledge_tree/ingestion/extractor.py`

现有流程：提取 summary + result_summary + failure_reason → 过滤 → ingest

增强流程（在过滤之后加一步）：

```
ExecutorResult
  → 现有提取（summary/result_summary/failure_reason）
  → 过滤通用模板
  → [新增] 经验提炼（LLM prompt 输出四元组）
  → ingest 作为 experience 节点
```

**提取条件**：
- `status=failed`：始终提取经验
- `status=completed`：仅当 summary 含可复用模式时提取。判断标准：summary 中包含"发现/找到/确认/正确的 X 是 Y/需要先 Z"等知识发现性表述，且长度 > 50 字符。简单确认性结果（"已读取文件"）跳过
- 通用模板/过于具体的细节：沿袭现有 filter 过滤

**经验提炼 prompt 核心结构**：

```
从以下任务执行结果中提取一条可复用的经验教训。
输出格式：
情境：<一句话描述任务场景>
行动：<执行中采取了什么关键行动>
结果：<成功或失败，具体结果>
教训：<下次遇到类似情况应该怎么做>
适用：<什么类型的任务应参考此经验>

执行结果：
{ExecutorResult 内容}
```

**入口不变**：`_try_auto_ingest_executor_result()` 在 Supervisor graph 中的调用位置、异常处理完全不变。

---

## 阶段 2：KT 操作知识种子

**改动文件**：`src/common/knowledge_tree/bootstrap.py`

新增 `seed_meta_rules()` 函数，在建树流程中自动写入操作元规则。

**种子元规则（5 条）**：

1. **主动沉淀**：当用户分享了项目特定信息（路径、配置、约定、偏好）时，用 knowledge_tree_ingest 沉淀
2. **失败前查**：遇到重复出现的错误模式时，先用 knowledge_tree_retrieve 查看是否有历史经验可参考
3. **先查后答**：当任务涉及不熟悉的技术栈或领域时，先 retrieve 查知识树再回答
4. **失败后学**：执行失败后重规划前，先检索相关失败经验避免重复踩坑
5. **成功也记**：完成任务后如果发现新的可复用知识（工具用法、配置技巧、排错方法），主动 ingest

**验证**：建树后 `list_meta_rules` 确认种子存在。

---

## 阶段 3：检索置信度评估

**改动文件**：`src/supervisor_agent/prompts.py`（系统提示词中的 KT 指导部分）

纯提示词工程，不新增代码逻辑。在 Supervisor 系统提示的 KT 指导中增加检索质量评估指令：

```
当你使用了 [相关知识] 中的内容来回答时：
- 检索结果直接回答了用户问题 → 正常回答，标注 [基于记忆]
- 检索结果部分相关但有明显缺口 → 回答但标注 [部分记忆]，提示用户可能需要更具体的信息
- 检索结果不相关或为空，但凭自身知识有把握 → 直接回答，不提及 KT
- 检索结果不相关且没有把握 → 用 knowledge_tree_retrieve 换关键词再检索一次，或升级到 Planner
```

**闭环信号**：Supervisor 的"部分记忆"和"不相关"判断通过 `record_feedback` 写回，形成 P3 优化闭环的输入信号。

---

## 外部可观测性：KT 状态快照

**目的**：面向人类开发者的快速诊断视图，一眼看懂 KT 如何影响 AgentTriad、又如何被 AgentTriad 改变。

**不是给 Agent 的工具，是给人看的报告。**

### 输出位置

每次 Supervisor 任务完成后，在 `logs/` 目录下追加一行 JSON 摘要（如果启用）。

环境变量控制：`KT_SNAPSHOT_ENABLED=true`（默认 false）。

### 快照内容

```json
{
  "timestamp": "2026-05-22T23:30:00",
  "task_summary": "用户要求分析项目依赖关系",
  "kt_influence": {
    "auto_retrieve_hits": 2,
    "retrieved_nodes": ["configuration/env-setup.md", "experience/dependency-resolution-001.md"],
    "agent_used_kt": true,
    "confidence_level": "sufficient",
    "manual_retrieve_count": 0,
    "manual_ingest_count": 0
  },
  "kt_mutations": {
    "auto_ingest_count": 1,
    "ingested_nodes": ["experience/dependency-resolution-002.md"],
    "ingest_triggers": ["executor_result_failed"],
    "meta_rules_active": 6,
    "optimization_suggestions": 0
  },
  "kt_health": {
    "total_nodes": 48,
    "total_directories": 25,
    "experience_nodes": 3,
    "avg_retrieval_score": 0.52,
    "false_positive_rate_7d": 0.12
  }
}
```

### 字段说明

| 区块 | 含义 |
|------|------|
| `kt_influence` | KT 本轮如何影响了 Agent 行为（检索了什么、Agent 用了没、置信度如何） |
| `kt_mutations` | Agent 本轮如何改变了 KT（摄入了什么、触发了什么优化） |
| `kt_health` | KT 当前全局状态快照（规模、质量指标） |

### 实现方式

在 `kt_retrieve` 节点和 `_try_auto_ingest_executor_result()` 中累积数据到 State 临时字段，任务完成时写入日志文件。不阻塞主流程，try/except 包裹。

---

## 改动量总结

| 阶段 | 改什么 | 新增什么 |
|------|--------|----------|
| 1 经验沉淀 | `extractor.py` | `experience` 节点类型、经验提炼 prompt |
| 2 操作种子 | `bootstrap.py` | `seed_meta_rules()` 函数、5 条元规则文本 |
| 3 置信度评估 | `prompts.py` | 检索质量评估指令文本 |
| 可观测性 | `graph.py`（kt_retrieve + Entry A） | State 临时字段、JSON 快照写入 |

无新工具、无新存储结构、无新架构组件。全部复用现有基础设施。
