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
    ├── generate_plan ──▶ Planner Agent   ← 生成意图层 JSON 计划（不含工具名）
    └── execute_plan ───▶ Executor Agent  ← ReAct 循环自主选工具执行
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
| Supervisor 模型 | Qwen (qwen-flash) |
| Planner / Executor 模型 | DeepSeek-V3.2 (via SiliconFlow) |
| 代码质量 | Ruff + MyPy |
| 测试 | pytest + pytest-asyncio |
| 可观测性 | LangSmith（可选） |

---

## 分阶段路线

| 版本 | 目标 | 状态 |
|---|---|---|
| V1 | 单线程闭环 MVP | 开发中 |
| V2 | Executor Reflection + Snapshot 上报 | 计划中 |
| V3 | 多 Executor 并行 + Memory 归档 | 计划中 |
