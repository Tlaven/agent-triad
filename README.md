# Multi-Agent Framework

通用三层 Multi-Agent 框架：自然语言 → **Supervisor**（调度）→ **Planner**（意图层 Plan）→ **Executor**（工具执行）→ 结果融合；Planner 与 Executor 工具集解耦。

**读什么**：[`CLAUDE.md`](./CLAUDE.md) 开发与协作 Agent 的硬规则 · [`docs/product-roadmap.md`](./docs/product-roadmap.md) 产品与里程碑 · [`docs/README.md`](./docs/README.md) `docs/` 索引导航 · [`tests/README.md`](./tests/README.md) 改代码后跑哪条测试 · [`tests/TESTING.md`](./tests/TESTING.md) 环境、代理与分层细节。

---

## 快速上手

```bash
uv sync --dev
cp .env.example .env   # 填入 Key，至少：
# SILICONFLOW_API_KEY  — Planner / Executor
# DASHSCOPE_API_KEY    — Supervisor（若用默认配置）
make dev_ui            # LangGraph Studio（推荐）；无 UI：`make dev`
make test_automated    # 单元+集成（Mock LLM），日常回归；`make test_all` 同义
```

跑 E2E / 真实 LLM 前请先读 [`tests/TESTING.md`](./tests/TESTING.md)（含 `make test_llm_health`）。

---

## 默认工作区

- `write_file` / `run_local_command` 受 `AGENT_WORKSPACE_DIR` 约束（默认 `workspace`）。
- 可选只读 MCP：`ENABLE_FILESYSTEM_MCP=true`、`FILESYSTEM_MCP_ROOT_DIR=…`（见 `.env.example`）。

---

## 仓库结构（摘要）

```
src/
  common/           配置、模型加载、Observation 等
  supervisor_agent/ 主图与工具（call_planner / call_executor）
  planner_agent/    规划 ReAct
  executor_agent/   执行 ReAct + 工具
tests/
  README.md    TESTING.md    unit_tests/  integration/  e2e/
docs/            产品路线、ADR、V3 架构图、索引
CLAUDE.md        硬规则（给开发者与 AI）
```

---

## 技术栈

| 项 | 说明 |
|----|------|
| 编排 | LangGraph（StateGraph + ReAct） |
| 语言 / 包管理 | Python 3.11+ · uv |
| 质量 | Ruff · MyPy · pytest |

默认模型与提供商见 `config/agent_models.toml`；可选 Reflection、MCP、Observation 预算、LangSmith 等环境变量见 **`.env.example`**，行为边界见 **`CLAUDE.md`**。
