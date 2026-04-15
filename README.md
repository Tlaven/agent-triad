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

详见：[CLAUDE.md](./CLAUDE.md)（工程文档）| [PRD.md](./PRD.md)（产品需求）| [ROADMAP.md](./ROADMAP.md)（路线图）

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
└── ROADMAP.md               # 路线图
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

## 功能配置

### 启用 Reflection

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

### MCP 只读工具

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

### 工具输出治理

自动管理工具输出长度，防止上下文爆炸：

```bash
# .env（可选配置）
MAX_OBSERVATION_CHARS=6500                  # 单条观察最大长度
OBSERVATION_OFFLOAD_THRESHOLD_CHARS=28000   # 超长内容外置阈值
ENABLE_OBSERVATION_OFFLOAD=true            # 启用外置存储
ENABLE_OBSERVATION_SUMMARY=false           # 启用智能摘要（额外成本）
```

### LangSmith 分布式追踪

Supervisor 调用 Executor 时会通过 HTTP header 把当前 trace 上下文传递给 Executor 子进程，Executor 内部的所有 LangGraph 节点（ReAct 循环、LLM 调用、tool node 等）会作为子节点挂在 Supervisor trace 下，在 LangSmith UI 中形成一棵完整的 trace 树。

#### 前提条件

`.env` 中必须配置：

```bash
LANGCHAIN_TRACING_V2=true
LANGCHAIN_API_KEY=lsv2_xxxxxxxx   # 你的 LangSmith API Key
LANGCHAIN_PROJECT=your-project-name
```

（项目默认 `.env.example` 已包含这三项，填入有效 Key 即可）

#### 手动验证步骤

1. **确认 `.env` 配置**：检查 `LANGCHAIN_TRACING_V2=true` 且 `LANGCHAIN_API_KEY` 已填入有效值。

2. **启动服务**：

   ```bash
   make dev
   ```

3. **发送一个需要 Executor 的任务**（通过 LangGraph Studio 或 API），例如：`"创建一个 hello.txt 文件，内容写 Hello World"`。

4. **打开 LangSmith UI**（[smith.langchain.com](https://smith.langchain.com)）→ 进入对应项目 → 找到最新的 Supervisor trace。

5. **展开 trace 树**：点开 `call_executor` 工具调用 → 应看到 Executor Graph 的全部节点（`call_executor` LLM 节点、`tools` ToolNode、各轮 ReAct 循环）作为子节点展开。

6. **按 plan_id 搜索**：在 LangSmith 搜索栏输入 `plan_id:plan_xxxxxxxx` 可一次找到 Supervisor 和 Executor 的所有相关记录。

#### 工作原理

```
Supervisor (call_executor tool)
  └─ run_tree.to_headers()  →  HTTP header "langsmith-trace: ..."
                                        ↓  POST /execute
                               Executor FastAPI (/execute 路由)
                                 └─ tracing_context(parent=headers)
                                      └─ run_executor(plan_json)
                                           ├─ call_executor (LLM)
                                           ├─ tools (ToolNode)
                                           └─ ...
```

#### 排查技巧

- **trace 没有连成树** → 在 Supervisor 侧打印 `trace_headers`，确认 `langsmith-trace` key 存在；在 Executor 侧确认 `tracing_context` 套住了 `run_executor()` 调用。
- **Executor 节点出现在独立 trace 里** → 检查 Executor 进程的 `LANGCHAIN_PROJECT` 是否和 Supervisor 一致。
- **LangSmith 未安装或 Key 无效** → trace 传递会静默跳过，不影响业务功能，只是无法在 UI 中看到嵌套结构。

---

## 运行测试

### 测试命令

```bash
make test_unit    # 单元测试 (~1 min)
make test_all     # 全部测试 (~3 min)
```

### 测试覆盖

**总测试数**: 331 项（266 单元 + 65 集成）

| 功能 | 测试数 | 覆盖内容 |
|-----|--------|----------|
| 工具输出治理 | 多项 | 截断、外置、边界处理 |
| Planner 工具 + MCP | 62 项 | 权限分层、并发、错误处理 |
| Reflection/Snapshot | 46 项 | 触发逻辑、快照结构、Supervisor 集成 |

---
