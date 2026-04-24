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
| `test_kt_via_server.py` | 知识树专项测试 | 5 | 4 个 KT 工具 |

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

### 4.3 check_executor_progress 触发难

Supervisor 倾向于执行任务而非仅查看进度。需要非常明确的消息（"只使用 check_executor_progress，不要执行任务"）。

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

**Q: 工具覆盖率不满 10/10？**
A: `check_executor_progress` 和 `stop_executor` 需要 executor 在运行状态才容易被触发。重新运行或调整消息措辞。
