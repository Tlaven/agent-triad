# 环境变量参考（Environment Variables）

> 定位：AgentTriad 全部环境变量的集中参考。数据源为 `src/common/context.py`（字段定义）+ `.env.example`（示例）。
> 硬规则摘要见 [`CLAUDE.md`](../CLAUDE.md) §运行与环境；本文是完整列表。
>
> **加载机制**：`load_dotenv(override=True)`，`.env` 覆盖系统环境变量。所有变量可选。

---

## 1. Provider 接口

多 provider 通过 `load_chat_model("provider:model")` 统一加载。`.env` 中配置两套接口：

| 变量 | 默认值 | 作用域 | 说明 |
|------|--------|--------|------|
| `OPENAI_API_KEY` | — | Supervisor + Executor | OpenAI 兼容接口密钥 |
| `OPENAI_BASE_URL` | — | Supervisor + Executor | OpenAI 兼容接口 base URL（如 `https://opencode.ai/zen/go/v1`） |
| `ANTHROPIC_API_KEY` | — | Planner | Anthropic 兼容接口密钥 |
| `ANTHROPIC_BASE_URL` | — | Planner | Anthropic 兼容接口 base URL（如 `https://opencode.ai/zen/go`） |
| `SILICONFLOW_API_KEY` | — | KT Embedding | SiliconFlow embedding API 密钥 |
| `REGION` | — | 通用 | 区域标识（如 `prc`），影响部分 API 端点选择 |
| `NO_PROXY` | — | 网络 | 绕过本地代理的域名列表（如 `api.siliconflow.cn,opencode.ai`） |

**模型格式**：`provider:model_name`，如 `openai:kimi-k2.6`、`anthropic:qwen3.7-max`。

---

## 2. Agent 模型与 LLM 参数

三 Agent 各自独立配置模型和 LLM 参数。

### 模型选择

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `SUPERVISOR_MODEL` | `openai:kimi-k2.6` | Supervisor 模型（`provider:model` 格式） |
| `PLANNER_MODEL` | `anthropic:qwen3.7-max` | Planner 模型 |
| `EXECUTOR_MODEL` | `openai:deepseek-v4-flash` | Executor 模型 |

### Supervisor LLM 参数

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `SUPERVISOR_TEMPERATURE` | — | 采样温度 |
| `SUPERVISOR_TOP_P` | — | nucleus sampling |
| `SUPERVISOR_MAX_TOKENS` | — | 最大输出 token |
| `SUPERVISOR_SEED` | — | 随机种子（支持时） |
| `SUPERVISOR_MAX_HISTORY_MESSAGES` | 100 | 历史消息上限（0 = 不限制） |

### Planner LLM 参数

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `PLANNER_TEMPERATURE` | — | 采样温度 |
| `PLANNER_TOP_P` | — | nucleus sampling |
| `PLANNER_MAX_TOKENS` | — | 最大输出 token |
| `PLANNER_SEED` | — | 随机种子 |

### Executor LLM 参数

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `EXECUTOR_TEMPERATURE` | — | 采样温度 |
| `EXECUTOR_TOP_P` | — | nucleus sampling |
| `EXECUTOR_MAX_TOKENS` | — | 最大输出 token |
| `EXECUTOR_SEED` | — | 随机种子 |

### Thinking（思维链）

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `ENABLE_IMPLICIT_THINKING` | true | 启用隐式思维链（兼容旧名 `THINKING_VISIBILITY`） |
| `SUPERVISOR_THINKING_VISIBILITY` | `implicit` | `visible` 时把推理拼入 `content`；`implicit` 不拼。仅 Supervisor 生效，Planner/Executor 不拼 |

### Streaming

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `ENABLE_LLM_STREAMING` | false | 启用流式输出聚合 |

---

## 3. 超时保护（决策 30）

三 Agent 各自独立的单次 LLM 调用超时 + Executor 工具/等待超时。

| 变量 | 默认值（秒） | 作用域 | 超时行为 |
|------|-------------|--------|---------|
| `SUPERVISOR_CALL_MODEL_TIMEOUT` | 120 | Supervisor 单次 LLM | 返回友好提示，不崩溃 |
| `PLANNER_CALL_MODEL_TIMEOUT` | 120 | Planner 单次 LLM | 抛 `RuntimeError`，由 Supervisor 捕获 |
| `EXECUTOR_CALL_MODEL_TIMEOUT` | 180 | Executor 单次 LLM | 终止子进程 |
| `EXECUTOR_TOOL_TIMEOUT` | 300 | Executor tools_node | 返回部分结果让 LLM 摘要 |
| `EXECUTOR_WAIT_TIMEOUT` | 300 | Supervisor 等待 Executor | 终止 executor 进程并标记失败 |
| `EXECUTOR_STARTUP_TIMEOUT` | 30 | Executor 子进程 `/health` | spawn 失败 |

**约束**：`EXECUTOR_WAIT_TIMEOUT` 应 > `EXECUTOR_CALL_MODEL_TIMEOUT`，否则 Supervisor 会提前终止尚在正常执行的 Executor。

**0 禁用**：所有超时值为 0 时禁用该层超时保护。

---

## 4. 历史与迭代限制

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `SUPERVISOR_MAX_HISTORY_MESSAGES` | 100 | Supervisor 消息历史上限（截断算法保留工具调用序列完整性） |
| `MAX_REPLAN` | — | 最大重规划次数 |
| `MAX_EXECUTOR_ITERATIONS` | — | Executor 单次执行最大迭代 |
| `MAX_PLANNER_ITERATIONS` | — | Planner 单次规划最大迭代 |

---

## 5. Reflection（决策 10）

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `REFLECTION_INTERVAL` | 0 | Reflection 触发间隔（工具轮数）。0 = 关闭（默认），正整数启用 |
| `CONFIDENCE_THRESHOLD` | — | Reflection 置信度阈值 |

---

## 6. 知识树（V4）

### 启用与路径

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `ENABLE_KNOWLEDGE_TREE` | false | 启用 V4 知识树 |
| `KNOWLEDGE_TREE_ROOT` | `workspace/knowledge_tree` | KT 根目录 |
| `KT_SNAPSHOT_ENABLED` | false | 启用任务完成后的 JSON 状态快照写入 `logs/` |

### Embedding

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `KT_EMBEDDER_TYPE` | `api` | embedder 类型：`semantic`（本地）/ `api`（SiliconFlow 等）/ `hash` |
| `KT_EMBEDDING_MODEL` | `BAAI/bge-large-zh-v1.5` | embedding 模型名 |
| `KT_EMBEDDING_DIMENSION` | 1024 | 向量维度 |

### 检索与摄入阈值

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `KT_RAG_SIMILARITY_THRESHOLD` | 0.15 | RAG 相似度阈值（semantic embedder 时自动升至 0.5） |
| `KT_INGEST_ATTACH_THRESHOLD` | 0.7 | 目录锚点吸附阈值 |
| `KT_DEDUP_THRESHOLD` | 0.88 | 去重阈值（0.88 = 结构高度相似即合并，保留语义差异节点）|
| `KT_MAX_TREE_DEPTH` | — | 树最大深度 |

### 结构权重（stored_vector 混合）

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `KT_STRUCTURAL_WEIGHT` | — | structural 向量权重（β） |
| `KT_CONTENT_WEIGHT` | — | content 向量权重（α） |
| `KT_INGEST_ENABLED` | true | 启用摄入管道 |
| `KT_INGEST_CHUNK_MAX_TOKENS` | — | 单 chunk 最大 token |
| `KT_VECTOR_PERSISTENCE_ENABLED` | true | 向量持久化（`.vector_index.json` + manifest 新鲜度检测） |

### P3 优化闭环

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `KT_OPTIMIZATION_WINDOW` | — | 优化检测窗口大小 |
| `KT_MAX_OPTIMIZATIONS_PER_WINDOW` | — | 窗口内最大优化次数（反振荡） |
| `KT_TOTAL_FAILURE_THRESHOLD` | — | 总失败信号阈值 |
| `KT_RAG_FALSE_POSITIVE_THRESHOLD` | — | RAG 误报信号阈值 |
| `KT_CONTENT_INSUFFICIENT_THRESHOLD` | — | 内容不足信号阈值 |

---

## 7. MCP 与工具开关

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `ENABLE_DEEPWIKI` | false | 启用 DeepWiki MCP（只读，供 Planner/Executor） |
| `ENABLE_FILESYSTEM_MCP` | false | 启用文件系统 MCP |
| `FILESYSTEM_MCP_ROOT_DIR` | — | 文件系统 MCP 根目录限制 |
| `READONLY_TOOLS_ONLY` | false | 限制为只读工具（Planner 默认 true） |
| `MAX_SEARCH_RESULTS` | — | `search_files`/`grep_content` 最大返回数 |

---

## 8. 工作区与可观测性

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `AGENT_WORKSPACE_DIR` | `workspace` | 副作用工具的工作区根目录 |
| `EXECUTOR_HOST` | `localhost` | Executor 子进程 host |
| `EXECUTOR_PORT` | 0 | Executor 端口（0 = 动态分配） |
| `MAILBOX_PORT` | 0 | Mailbox HTTP server 端口（0 = 动态） |

### Observation（决策 V2-a）

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `MAX_OBSERVATION_CHARS` | — | 工具返回截断阈值 |
| `OBSERVATION_OFFLOAD_THRESHOLD_CHARS` | — | 外置阈值（超过则写文件） |
| `ENABLE_OBSERVATION_OFFLOAD` | — | 启用外置 |
| `ENABLE_OBSERVATION_SUMMARY` | — | 启用摘要 |
| `OBSERVATION_WORKSPACE_DIR` | — | 外置文件目录 |
| `SNAPSHOT_INTERVAL` | 0 | Executor 轻量快照间隔（0 禁用） |

---

## 9. LangSmith 追踪（可选）

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `LANGSMITH_API_KEY` | — | LangSmith API 密钥 |
| `LANGCHAIN_TRACING_V2` | false | 启用 v2 追踪 |
| `LANGCHAIN_PROJECT` | — | LangSmith 项目名（如 `AgentTriad`） |

---

## 关联文档

- [`CLAUDE.md`](../CLAUDE.md) §运行与环境（硬规则摘要）、§多模型环境变量
- [`.env.example`](../.env.example) — 最小配置示例
- [`architecture-decisions.md`](architecture-decisions.md) 决策 30（LLM 超时保护）、决策 27（Executor 等待超时）
- [`troubleshooting.md`](troubleshooting.md) — `.env` override 不生效等常见问题
