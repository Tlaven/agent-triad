# 测试注意事项

---

## 1. 环境与代理

- Python 3.11+，包管理器 `uv`。
- `.env` 文件在项目根目录，包含 API Key 等敏感配置。
- `make setup` 安装依赖；`make dev` 启动 LangGraph 开发服务器（端口 2024）。
- HTTP 代理：如需代理，在 `.env` 中设置 `HTTP_PROXY` / `HTTPS_PROXY`。

---

## 2. 测试分层

| 层 | 路径 | 依赖 | 命令 |
|----|------|------|------|
| 单元测试 | `tests/unit_tests/` | 无外部依赖 | `make test_unit` |
| 集成测试 | `tests/integration/` | Mock LLM | `make test_integration` |
| E2E 测试 | `tests/e2e/` | 真实 LLM + API Key | `make test_e2e` |
| **E2E Server 测试** | `tests/e2e/test_*.py` | **运行中的 dev server** | 见下文 |

自动化回归（不含 E2E）：`make test_automated` = 单元 + 集成。

---

## 3. E2E Server 测试

### 3.1 概念

通过 LangGraph SDK 异步客户端连接运行中的 dev server，发送消息后等待 run 完成，从 thread state 中提取工具调用信息进行验证。

**与普通 E2E 的区别**：
- 普通 E2E（`make test_e2e`）用 pytest + 真实 LLM，但不经过 ASGI 中间件。
- Server 测试经过完整的 ASGI 中间件栈，能捕获阻塞调用、进程管理等问题。

### 3.2 前置条件

```bash
# 1. 确保 .env 配置正确
# 2. 启动 dev server
make dev

# 3. 等待 server 就绪
curl http://localhost:2024/ok
# → {"ok": true}
```

### 3.3 可用测试脚本

| 脚本 | 用途 | 用例数 | 覆盖工具 |
|------|------|--------|---------|
| `test_comprehensive_server.py` | 进阶综合测试，三级验证 | 20 | 全部 10 个 |
| `test_all_tools_server.py` | 全工具快速测试 | 10 | 全部 10 个 |
| `test_kt_via_server.py` | 知识树专项测试 | 5 | 7 个 KT 工具 |

运行方式（从项目根目录）：

```bash
# 综合测试（~15min，取决于 LLM 速度）
uv run python -u tests/e2e/test_comprehensive_server.py

# 全工具测试（~10min）
uv run python -u tests/e2e/test_all_tools_server.py

# 知识树测试（~3min）
uv run python -u tests/e2e/test_kt_via_server.py
```

### 3.4 综合测试设计（test_comprehensive_server.py）

#### 三级验证

| 级别 | 验证内容 | 判定 |
|------|---------|------|
| **L1** 工具调用 | 期望的工具是否被调用 | 不匹配 → FAIL |
| **L2** 输出格式 | JSON 字段 / 关键词是否正确 | 不匹配 → SOFT_PASS |
| **L3** 副作用 | 文件系统 / KT 状态变化 | 不匹配 → SOFT_PASS |

#### 四组独立 Thread

| 组 | 测试范围 | 用例数 | 说明 |
|----|---------|--------|------|
| **组 A** | 知识树完整闭环 | A1-A7 | bootstrap → status → retrieve → ingest → dedup |
| **组 B** | Mode 2 + Mode 3 执行流 | B1-B6 | executor / list / get / planner+executor / check_progress |
| **组 C** | 异步 + 停止流程 | C1-C4 | async dispatch / stop / list / get_result |
| **组 D** | 重规划 + 边界 | D1-D3 | plan / execute+replan / kt_status |

每组独立 thread，避免 `MAX_REPLAN` 等状态污染。

#### 判定策略

| 判定 | 条件 |
|------|------|
| **PASS** | L1 匹配 |
| **SOFT_PASS** | L1 匹配但 L2/L3 有问题 |
| **FAIL** | L1 不匹配（期望工具未被调用） |

#### 超时配置

每个测试用例可配 `timeout_s`（默认 480s）。超时后自动 cancel run 并标记为超时。

---

## 4. 已知 LLM 行为问题

以下现象在测试中反复出现，属于 LLM 自主决策导致的不确定性，**非代码 bug**：

### 4.1 Executor 文件路径嵌套

Executor 子进程的 CWD 是 `workspace/`。当用户消息说"在 workspace 下创建文件"时，LLM 可能在 CWD 下再加一层 `workspace/`，导致文件出现在 `workspace/workspace/` 下。

**应对**：测试消息中不提 "workspace" 前缀；L3 副作用检查同时搜索两个路径。

### 4.2 Supervisor 忽略"只规划不执行"指令

Supervisor 有时会自主决定 plan+execute 一步到位，即使消息说"只需要规划"。

**应对**：Mode 3 测试的 `expected_tools` 同时包含 `call_planner` 和 `call_executor`。

### 4.3 manage_executor(action="check_progress") 触发难

Supervisor 倾向于执行任务而非仅查看进度。需要非常明确的消息（"只使用 manage_executor(action=check_progress)，不要执行任务"）。

### 4.4 Hash Embedder 无语义匹配

P1 默认的 n-gram hash embedder 不具备语义理解能力。摄入新知识后用不同措辞检索，可能无法命中。

**应对**：A6 测试（ingest 后 retrieve）标记为预期可能失败；这是 P1→P2 升级的已知驱动力。

---

## 5. E2E 前检查清单

1. `make dev` 已启动且 `curl localhost:2024/ok` 返回 `{"ok": true}`
2. `.env` 中 API Key 有效（先跑 `make test_llm_health` 验证）
3. 上次测试的残留进程已清理（`taskkill //F //IM langgraph.exe` 等）
4. 残留 workspace 文件已清理（`rm -rf workspace/b1_test.txt` 等）

---

## 6. FAQ

**Q: 测试卡住不动？**
A: 检查 dev server 日志。可能原因：LLM API 超时、dev server 需重启、残留 executor 子进程未清理。

**Q: B4 耗时 500s？**
A: Mode 3 规划+执行链路涉及多轮 LLM 调用，正常现象。确保 `executor_wait_timeout` >= 300s。

**Q: 工具覆盖率不满？**
A: `manage_executor(action="check_progress")` 和 `manage_executor(action="stop")` 需要 executor 在运行状态才容易被触发。重新运行或调整消息措辞。

---

## 6. chat.py 脚本模式 — 刁钻 E2E 回归

除了通过 LangGraph Dev Server 测试，`chat.py` 还支持**脚本模式**：直接调用 `graph.ainvoke()`（无需启动 dev server），按顺序执行一组预定义消息，输出 JSON 报告。

### 6.1 基本用法

```bash
# 单组测试
uv run chat.py --kt --kt-embedding-model hash \
    --script tests/e2e/scripts/dedup_stress.txt \
    --report tests/e2e/results/dedup_stress.json \
    --turn-timeout 120 --reset-kt-root --kt-root workspace/kt_test

# 批量运行（bash runner）
bash tests/e2e/scripts/run_adversarial.sh [dedup|noise|collision|executor|all]
```

### 6.2 关键 flag

| Flag | 说明 |
|------|------|
| `--script <file>` | 脚本文件：JSON 字符串数组或逐行文本（`#` 开头跳过，空行跳过） |
| `--report <path>` | JSON 报告输出路径（含每轮 input/tool_calls/tool_outputs/final_response/elapsed） |
| `--turn-timeout <s>` | 单轮超时秒数（建议：纯 KT 90-120s，含 executor 180s） |
| `--reset-kt-root` | 脚本运行前清空 KT 根目录（仅允许 workspace/ 下的子目录） |
| `--reset-each-turn` | 每轮清空对话上下文（隔离压测，避免长上下文放大效应） |
| `--kt-embedding-model hash` | 使用 hash embedder（零外部依赖，PRC 网络下必须） |

### 6.3 测试组

脚本位于 `tests/e2e/scripts/`，结果写入 `tests/e2e/results/`。

#### 组 1: Dedup 压力测试 (`dedup_stress.txt`, 10 轮)

验证去重机制在边界条件下的行为：完全相同内容、改标点、换表述、跨主题干扰。

| 轮次 | 测试点 | 期望 |
|------|--------|------|
| T1 | 摄入基准知识 | `nodes_ingested=1` |
| T3 | 重复摄入完全相同内容 | 自动去重 |
| T4 | 仅改标点/空格 | 相似度 < threshold → 自动过滤 |
| T5 | 换表述但语义相同 | 视阈值可能摄入或过滤 |
| T9-T10 | 跨主题检索 | 多条命中，不混淆 |

#### 组 2: Noise/抗幻觉 (`noise_antihallucination.txt`, 10 轮)

验证系统对无关查询、误导性知识、存在性陷阱的处理。

| 轮次 | 测试点 | 期望 |
|------|--------|------|
| T3 | 荒谬查询（"火星章鱼量子锅铲"） | "No results found" |
| T6 | 摄入误导知识（"GIL 让所有多线程都变慢"） | 允许摄入，检索到时如实返回 |
| T9 | 英文查中文知识 | 跨语言检索 |
| T10 | 检索不存在的内容（"量子计算模块"） | 低质量/无结果 |

#### 组 3: Collision/路径安全 (`collision_path.txt`, 11 轮)

验证同标题碰撞、路径注入、特殊字符、代码块处理。

| 轮次 | 测试点 | 期望 |
|------|--------|------|
| T1-T3 | 3 条同标题"测试策略" | 全部摄入，无覆盖 |
| T5 | 内容包含 `../escape.md`、`C:/Windows/` | 按普通文本存储，不写文件 |
| T7 | 表情符号 + 中日韩阿拉伯文 | 正确存储和检索 |
| T10 | 代码块内容（含 Python 代码） | 正确摄入 |

#### 组 4: Executor 压力 (`executor_notk.txt`, 10 轮)

验证 Supervisor/Executor 的模式切换、错误路径、跨轮引用。

| 轮次 | 测试点 | 期望 |
|------|--------|------|
| T1 | Mode 1 直接回复 | 无工具调用 |
| T2 | Mode 2 文件创建 | executor 成功 |
| T4 | 读取不存在的文件 | executor 返回失败 |
| T7 | 从工具使用切回直接回复 | 模式切换 |
| T9 | 极短输入"嗯" | 不崩溃 |

### 6.4 结果解读

JSON 报告结构：

```json
{
  "script": "tests/e2e/scripts/dedup_stress.txt",
  "context": { "kt_embedding_model": "hash", ... },
  "turns": [
    {
      "ok": true,
      "elapsed": 3.6,
      "input": "请检索...",
      "tool_calls": [{"name": "knowledge_tree_retrieve", "args": {...}}],
      "tool_outputs": [{"name": "knowledge_tree_retrieve", "json": {...}}],
      "final_response": "检索结果：...",
      "error": null
    }
  ],
  "summary": { "turns_completed": 10, "ok": true, "total_time": 38.6 }
}
```

- `ok: true/false` — 本轮是否正常完成（超时或异常为 false）
- `tool_calls` — 验证 LLM 是否调用了期望的工具
- `tool_outputs.json` — 验证工具返回格式（如 `nodes_ingested`, `nodes_deduplicated`）
- 失败时优先看 `tool_calls`/`tool_outputs` 确认是**工具逻辑失败**还是**模型超时**

### 6.5 编写新测试脚本

格式：每行一条消息，`#` 开头的行为注释，空行跳过。

```text
# 这是注释，不会发送
请记录到知识树：测试知识内容。
# 检索验证
请检索关于测试的知识。
```

原则：
- 每轮只测一个关注点
- 避免需要多步骤 executor 的任务（容易超时）
- 用 `--reset-each-turn` 做隔离压测
- 每次只改一个变量（模型 / 阈值 / reset_each_turn）

---

## 7. Embedding 模型配置与排错

### 7.1 模型选项

| 模型 | 设置方式 | 依赖 | 适用场景 |
|------|---------|------|---------|
| `hash`（推荐默认） | `--kt-embedding-model hash` 或 `.env` 设 `KT_EMBEDDING_MODEL=hash` | 无 | L0-L1 测试、PRC 网络、无 GPU 环境 |
| `BAAI/bge-small-zh-v1.5` | 默认（需本地缓存） | `sentence-transformers` | L2+ 语义检索质量验证 |

### 7.2 HuggingFace 连接问题

**症状**：`graph.ainvoke()` 永久卡死，日志显示反复 retry `huggingface.co`。

**根因**：`.env` 中 `ENABLE_KNOWLEDGE_TREE=true` 且 `KT_EMBEDDING_MODEL` 未设为 `hash`，系统尝试从 HuggingFace 下载语义模型。在 PRC 网络下 `huggingface.co` 不可达，`sentence-transformers` 内置重试机制导致无限等待。

**修复**（已合入，2026-05-01）：`SentenceTransformer(model_name, local_files_only=True)` — 只使用本地缓存，无缓存时立即降级到 hash embedder。

**手动缓存模型**（在有网络的环境下执行一次即可）：

```bash
uv run python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('BAAI/bge-small-zh-v1.5')"
```

**快速解决方案**（不需要下载模型）：

```bash
# 方法 1: 运行时指定
uv run chat.py --kt --kt-embedding-model hash ...

# 方法 2: 写入 .env（永久生效）
echo "KT_EMBEDDING_MODEL=hash" >> .env
```

### 7.3 Embedder 降级行为

```
配置 embedding_model="hash"
  → 使用 n-gram hash embedder（零依赖）

配置 embedding_model="BAAI/bge-small-zh-v1.5"
  → 尝试加载语义 embedder (local_files_only=True)
  → 有本地缓存 → 加载成功，自动提高 rag_similarity_threshold 到 0.5
  → 无本地缓存 → 加载失败 → 降级到 hash embedder + 警告日志
```

---

## 8. KT 能力阶梯测试

### 8.1 测试方法

按难度递增的"能力阶梯"测试 KT 三个核心功能（被动召回、主动检索、主动摄入），从最低难度逐步升级直到失败，定位能力天花板。

### 8.2 测试结果（2026-05-06，MiniMax-M2.5 + 语义 embedder）

| Rung | 测试内容 | 结果 |
|------|---------|------|
| 1 | 显式摄入 + 显式检索 + 原文措辞 | ✅ sim=0.585 |
| 2 | + 换措辞 | ✅ sim=0.718 |
| 3 | + 隐式检索（提示性） | ✅ 自动注入直接回答 |
| 4 | + 被动召回（无提示，新会话） | ✅ 2s 内回答 |
| 5 | + 组合记忆（同时召回多条） | ✅ 同时回答两条知识 |
| 6 | + 噪声环境（8 节点中 4 条噪声） | ✅ 不影响检索质量 |
| 7 | + 跨轮次衰减（10 轮填充后） | ✅ 全部正确召回 |

**能力天花板：≥ Rung 7**（语义 embedder + 0.4 阈值）

### 8.3 已知问题

- **Hash embedder 无法支撑被动召回**：similarity 最高 ~0.63，低于 auto-inject 阈值
- **"不要使用工具"指令残留**：影响后续 ingest 轮次，需分到不同脚本
- **详细测试记录**：`docs/test-findings-kt-capability.md`
