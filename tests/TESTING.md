# 测试注意事项

## 快速参考

```
make test_unit          # 纯函数 + Mock 节点，无网络，秒级
make test_integration   # 图级集成，Mock LLM，秒级
make test_llm_health    # LLM 连通性诊断，真实 API，~15s
make test_e2e           # 全链路验收，真实 LLM，分钟级
make test_all           # unit + integration（不含 e2e）
```

### 本次变更：按 Agent 独立 LLM 参数（回归建议）

```bash
# 仅跑本次相关用例（Context + 三层 call_model 参数透传）
uv run pytest \
  tests/unit_tests/common/test_context.py \
  tests/unit_tests/supervisor_agent/test_call_model.py \
  tests/unit_tests/planner_agent/test_call_planner.py \
  tests/unit_tests/executor_agent/test_call_executor.py -q
```

可选：本地临时验证 Executor 更稳定（低随机）

```env
EXECUTOR_TEMPERATURE=0
EXECUTOR_TOP_P=1
# EXECUTOR_SEED=42         # 模型支持时可开启
# EXECUTOR_MAX_TOKENS=2048 # 视任务复杂度调整
```

---

## 测试分层结构

```
tests/
├── conftest.py                  # 全局 fixture（mock_context, make_mock_llm 等）
│
├── unit_tests/                  # Layer 1+2：无 LLM、无网络
│   ├── common/                  # Context / utils 纯函数
│   ├── executor_agent/          # 输出解析、工具验证、capabilities
│   ├── planner_agent/           # plan_id 生成、输出规范化
│   └── supervisor_agent/        # 辅助函数、call_model、dynamic_tools_node
│
├── integration/                 # Layer 3：图级集成，Mock LLM
│   ├── test_executor_graph.py   # run_executor() 全流程
│   ├── test_planner_graph.py    # run_planner() 全流程
│   └── test_supervisor_graph.py # 完整 Supervisor StateGraph
│
└── e2e/                         # Layer 4：真实 LLM
    ├── test_llm_health.py       # ★ 必须先跑：连通性 + 延迟诊断
    └── test_v1_acceptance.py    # V1 验收场景（A/B/C/D）
```

---

## 运行 E2E 前的必做检查

**每次跑 E2E 之前，先执行：**

```bash
make test_llm_health
```

这个命令 ~15 秒内能诊断出以下所有问题，避免 E2E 挂几分钟后才报错：

| 诊断项 | 如果失败说明 |
|--------|-------------|
| API Key 格式 | `.env` 里 key 拼写有误 |
| TCP 连通 | 防火墙 / DNS 解析失败 |
| HTTPS 握手 | TLS 证书问题 / 代理拦截 |
| HTTP 200 | Key 无效（401）或服务端故障（5xx）|
| 首 token 延迟 < 15s | 代理配置有问题（见下方代理说明）|
| 完整响应 < 20s | 模型过载或超时阈值太小 |

---

## 代理注意事项（重要）

**症状**：`make test_llm_health` 的 TCP / HTTPS 测试通过，但 LLM ping 超时（>20s）。

**原因**：本机系统代理（如 Clash、V2Ray，通常监听 `127.0.0.1:7890`）会被
httpx（OpenAI SDK 底层）自动接管。代理对 SSE 长连接处理不稳定，导致 LLM 响应永久挂起。

**解法**：在 `.env` 中加入（已默认配置）：

```env
NO_PROXY=api.siliconflow.cn,api.siliconflow.com
```

`test_print_network_info` 测试会打印当前代理状态，可用于确认配置是否生效：

```
System proxies    : {'no': 'api.siliconflow.cn,api.siliconflow.com'}   ← 正确
System proxies    : {'https': 'http://127.0.0.1:7890', ...}            ← 需要加 NO_PROXY
```

---

## 环境配置

必须在 `.env` 文件中配置：

```env
SILICONFLOW_API_KEY=sk-...          # Planner / Executor / Supervisor 使用
NO_PROXY=api.siliconflow.cn,api.siliconflow.com   # 绕过本机代理
REGION=prc                          # prc = api.siliconflow.cn，international = api.siliconflow.com
```

可选：

```env
DASHSCOPE_API_KEY=sk-...            # 如使用 Qwen 模型
LANGCHAIN_TRACING_V2=true           # 启用 LangSmith 追踪
LANGCHAIN_API_KEY=lsv2_...
```

`.env` 由 `tests/conftest.py` 在 pytest 启动时自动加载（`load_dotenv`），
**不需要手动 export 环境变量**。

---

## 常见问题

### Q: `make test_unit` 报 `ImportError`

确认已安装依赖：

```bash
uv sync --dev
```

### Q: 单元测试 / 集成测试出现 `ModuleNotFoundError: src`

`conftest.py` 会把项目根目录加入 `sys.path`。如果仍然报错，
检查是否从项目根目录运行（而非 `tests/` 子目录）。

### Q: E2E 测试被跳过（`SKIPPED`）

检查 `.env` 是否存在且包含有效的 `SILICONFLOW_API_KEY`，
或运行 `make test_llm_health` 查看详情。

### Q: `test_scenario_*` 失败且无错误信息，只是超时

大概率是代理问题，参考上方「代理注意事项」。

### Q: `test_llm_health.py` 报 `openai.InternalServerError` / HTTP 500（例如 code `50507`）

这通常是 **SiliconFlow 上游短暂不可用**（或模型实例瞬时故障），不一定是你本地网络问题。

建议：

- 先重跑一次 `make test_llm_health`（该文件已对常见 5xx 做**少量自动重试**）
- 若只有 `Step-3.5-Flash` 失败、其他模型正常：多半是 **该模型路由/实例抖动**，等几分钟或换同系列模型验证
- 若持续失败：检查账号配额/账单状态，并对比 `REGION` 指向的域名是否与你账号可用区域一致

### Q: 单元测试中 Mock LLM 的 `ainvoke` 耗尽了响应列表

`make_mock_llm(responses)` 是按顺序消费的。如果某个测试多调用了
一次 LLM，后续测试可能收到 `StopIteration`。每个测试创建独立的
`make_mock_llm` 实例，不要复用跨 test 的 mock。

### Q: 集成测试 `plan_id` 不可预测，断言失败

集成测试里 `plan_id` 由 `_generate_plan_id()` 在运行时生成。
如需精确断言，可在测试内 `patch("src.planner_agent.graph._generate_plan_id", return_value="plan_test_fixed")`，
或只断言格式（`assert re.match(r"^plan_v\d{8}_[0-9a-f]{4}$", plan_id)`）。

---

## 添加新测试的约定

| 测试类型 | 放在 | 标记 |
|---------|------|------|
| 纯函数、无 I/O | `unit_tests/` | 无 |
| Mock LLM 节点/图 | `unit_tests/` 或 `integration/` | 无 |
| 真实 LLM 调用 | `e2e/` | `@pytest.mark.live_llm` |

- **单元测试**：使用 `conftest.py` 中的 `make_mock_llm` 创建 Mock LLM，
  使用 `mock_context` fixture 控制 `max_replan` 等参数。
- **异步测试**：函数名以 `async def test_` 开头，pytest-anyio 自动识别。
- **真实 LLM 测试**：加 `@pytest.mark.live_llm` 并在开头 `skipif` 检查 API key，
  保证没有 key 时优雅跳过而不是报错。
