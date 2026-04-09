# Multi-Agent Framework

通用目的三层 Multi-Agent 自主任务系统框架。  
接受自然语言查询，自动完成**任务理解 → 计划生成 → 工具执行 → 结果融合**全流程。

---

## 架构简介

```
用户 Query
    │
    ▼
Supervisor Agent          ← 主循环：评估复杂度、调度、重规划、合成答案
    ├── call_planner ──▶ Planner Agent   ← 生成意图层 JSON 计划（不含工具名）
    └── call_executor ───▶ Executor Agent  ← ReAct 循环自主选工具执行
```

三个 Agent 职责分离，通过结构化 Plan JSON 传递意图，Planner 与 Executor 工具集完全解耦。

详见：[CLAUDE.md](./CLAUDE.md)（工程文档）| [PRD.md](./PRD.md)（产品需求）| [ROADMAP.md](./ROADMAP.md)（版本计划）

---

## 快速上手

### 1. 安装依赖

```bash
uv sync --dev
```

### 2. 配置环境变量

```bash
cp .env.example .env
# 编辑 .env，填入 API Key
```

最少需要配置：

```
SILICONFLOW_API_KEY=sk-...   # Planner / Executor
DASHSCOPE_API_KEY=sk-...     # Supervisor
```

### 3. 启动开发服务器

```bash
make dev       # 无 UI
make dev_ui    # LangGraph Studio（推荐）
```

### 4. 运行测试

```bash
make test_unit    # 单元测试
make test_all     # 全部测试
```

### 5. Agent 默认工作区（简版）

- Executor 的 `write_file` / `run_local_command` 默认在 `workspace` 内运行。
- 首次执行本地命令时，会自动创建 `workspace/.venv` 并注入到命令环境。
- 可通过环境变量覆盖：
  - `AGENT_WORKSPACE_DIR`（默认 `workspace`）
  - `AGENT_VENV_DIRNAME`（默认 `.venv`）
- 若希望给 Planner / Executor 开启只读文件浏览能力，可启用：
  - `ENABLE_FILESYSTEM_MCP=true`
  - `FILESYSTEM_MCP_ROOT_DIR=workspace`

---

## 项目结构

```
├── src/
│   ├── common/              # 共用工具（模型加载、配置、基类）
│   ├── supervisor_agent/    # Supervisor 主循环
│   ├── planner_agent/       # Planner（生成意图层 Plan）
│   └── executor_agent/      # Executor（ReAct 工具执行）
├── tests/
│   ├── unit_tests/
│   ├── integration_tests/
│   └── e2e_tests/
├── PRD.md                   # 产品需求文档
├── CLAUDE.md                # 工程设计文档（给 AI 助手读）
└── ROADMAP.md               # 版本路线图
```

---

## 技术栈

| 组件 | 技术 |
|---|---|
| Agent 框架 | LangGraph (StateGraph + ReAct) |
| 语言 | Python 3.11+ |
| 包管理 | uv |
| Supervisor 模型 | Step-3.5-Flash (via SiliconFlow) |
| Planner 模型 | GLM-5 (via SiliconFlow) |
| Executor 模型 | Step-3.5-Flash (via SiliconFlow) |
| 代码质量 | Ruff + MyPy |
| 测试 | pytest + pytest-asyncio |
| 可观测性 | LangSmith（可选） |

---

## 分阶段路线

| 版本 | 目标 | 状态 |
|---|---|---|
| V1 | 单线程闭环 MVP | ✅ 已完成 |
| V2 | 运行时边界 + Planner 扩展 + Reflection/Snapshot 精简 | ✅ 已完成 (2026-04-09) |
| V3 | 多 Executor 并行 + fan-in 融合 + 并行治理 | 🔄 规划中 |

---

## V2 功能使用

### 启用 Reflection（V2-c）

Reflection 默认关闭（`REFLECTION_INTERVAL=0`），如需启用执行中反思：

```bash
# .env
REFLECTION_INTERVAL=3        # 每3个工具调用触发一次反思
CONFIDENCE_THRESHOLD=0.6     # 置信度低于此值时额外触发
```

**Reflection 行为**：
- Executor 在指定间隔自动暂停执行
- 评估任务进度和偏离程度
- 向 Supervisor 提供 `continue`/``replan`/`abort` 建议
- Supervisor 决定下一步行动，保持决策权集中

### MCP 只读工具（V2-b）

启用 Planner/Executor 共享的 MCP 只读工具：

```bash
# .env
ENABLE_DEEPWIKI=true                  # 启用 DeepWiki 检索
ENABLE_FILESYSTEM_MCP=true            # 启用文件系统只读访问
FILESYSTEM_MCP_ROOT_DIR=workspace     # MCP 文件访问根目录
```

**权限分层**：
- **Planner**: 只能使用只读工具（文件读取、检索等）
- **Executor**: 可使用副作用工具（写文件、执行命令）+ 只读工具
- 保证规划层不会意外执行破坏性操作

### 工具输出治理（V2-a）

V2-a 自动管理工具输出长度，防止上下文爆炸：

```bash
# .env（可选配置）
MAX_OBSERVATION_CHARS=6500                  # 单条观察最大长度
OBSERVATION_OFFLOAD_THRESHOLD_CHARS=28000   # 超长内容外置阈值
ENABLE_OBSERVATION_OFFLOAD=true            # 启用外置存储
ENABLE_OBSERVATION_SUMMARY=false           # 启用智能摘要（额外成本）
```

---

## 运行测试

### 测试命令

```bash
make test_unit    # 单元测试 (~1 min)
make test_all     # 全部测试 (~3 min)
```

### 测试覆盖（V2）

**总测试数**: 331 项（266 单元 + 65 集成）

| 功能 | 测试数 | 覆盖内容 |
|-----|--------|----------|
| V2-a: 工具输出治理 | 多项 | 截断、外置、边界处理 |
| V2-b: Planner 工具 + MCP | 62 项 | 权限分层、并发、错误处理 |
| V2-c: Reflection/Snapshot | 46 项 | 触发逻辑、快照结构、Supervisor 集成 |

**测试改进**：从 V1 基线 224 项增至 331 项（+107 项 V2 专项测试）

---
