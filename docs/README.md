# `docs/` 文档索引

> 新用户上手请先看根目录 [`README.md`](../README.md)。
> **开发与协作 Agent 的硬规则**在 [`../CLAUDE.md`](../CLAUDE.md)；本目录供**按需深读**，可由 `CLAUDE.md` 指向此处。

---

## 按主题找内容

### 基础架构

| 主题 | 文件 | 大概位置 |
|------|------|----------|
| **执行契约、三种模式、失败状态机、Session 同步、硬约束、模块速查** | [`../CLAUDE.md`](../CLAUDE.md) | 全文按 Markdown 二级标题分节（定位与架构 → 运行与环境） |
| **产品定位、三种模式、Plan 字段、工具分层、NFR、不做清单** | [`product-roadmap.md`](product-roadmap.md) | §1–§6 |
| **版本里程碑、V1/V2/V3 验收与状态** | [`product-roadmap.md`](product-roadmap.md) | §7（含 7.1–7.4） |
| **设计决策全文（为何这样定、细节与边界）** | [`architecture-decisions.md`](architecture-decisions.md) | 文首说明后 → **决策 1** 起分节编号 |

### V3 进程分离

| 主题 | 文件 | 大概位置 |
|------|------|----------|
| **Mermaid 总览、执行序列、组件与旧架构对比** | [`v3-architecture-diagrams.md`](v3-architecture-diagrams.md) | §1 系统总览；§2 起为流程与专题图 |
| **完整执行流分析：派发、双路径返回、超时全景、异常矩阵** | [`v3-execution-flow.md`](v3-execution-flow.md) | 按执行阶段分节 |

### V4 知识树（按阅读顺序排列）

> **V4 有 4 篇文档，按以下顺序阅读。** `core-design` 是当前开发的唯一权威参考。

| # | 文件 | 状态 | 定位 |
|---|------|------|------|
| 1 | [`v4-knowledge-tree-concepts.md`](v4-knowledge-tree-concepts.md) | 历史参考 | 概念对齐：两层存储 + Overlay、向量映射公式、P1-P3 路线图。部分内容已被 core-design 取代（如 stored_vector 混合公式降为 P2，P1 纯用 content_embedding + 锚点自动放置） |
| 2 | [`v4-knowledge-tree-spec.md`](v4-knowledge-tree-spec.md) | 历史参考 | P1 技术规格：数据模型、接口契约、测试策略。core-design 简化了部分接口（如移除 bootstrap/status 工具、精简为 2 工具） |
| 3 | [`v4-kt-core-design.md`](v4-kt-core-design.md) | **当前权威** | 核心设计：向量-结构互塑闭环、养料入口、检索、管理权归属。**实现以本文档为准** |
| 4 | [`v4-experiment-handbook.md`](v4-experiment-handbook.md) | 活跃维护 | 实验手册：成本分层 L0-L4、Mock 策略、确定性测试配方、诊断工具 |

### 测试

| 主题 | 文件 | 大概位置 |
|------|------|----------|
| **改代码后跑哪条命令、`make test_*` 含义** | [`../tests/README.md`](../tests/README.md) | 命令表与文档分工 |
| **环境、代理、测试分层、E2E Server 测试方法论、三级验证、已知 LLM 行为问题、FAQ** | [`../tests/TESTING.md`](../tests/TESTING.md) | E2E Server 测试 §3 起；已知问题 §4 |
| **V2-a/b/c 专项测试文件与命令** | [`../tests/V2_TESTING.md`](../tests/V2_TESTING.md) | 各节按 V2-a / V2-b / V2-c 分块 |

---

## 根目录与 `docs/` 的分工（速记）

| 路径 | 谁读 | 内容 |
|------|------|------|
| `README.md` | 新用户 | 安装、环境、启动、项目树摘要 |
| `CLAUDE.md` | 开发者 / 协作 Agent | 契约、状态机、模块速查、环境指针 |
| `docs/README.md` | 所有人（导航） | 本索引 |
| `docs/*.md` | 需要细节时 | 产品路线、ADR、架构图 |
