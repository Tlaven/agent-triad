# `docs/` 文档索引

> 新用户上手请先看根目录 [`README.md`](../README.md)。  
> **开发与协作 Agent 的硬规则**在 [`../CLAUDE.md`](../CLAUDE.md)；本目录供**按需深读**，可由 `CLAUDE.md` 指向此处。

---

## 按主题找内容

| 主题 | 文件 | 大概位置 |
|------|------|----------|
| **执行契约、三种模式、失败状态机、Session 同步、硬约束、模块速查** | [`../CLAUDE.md`](../CLAUDE.md) | 全文按 Markdown 二级标题分节（定位与架构 → 运行与环境） |
| **产品定位、三种模式、Plan 字段、工具分层、NFR、不做清单** | [`product-roadmap.md`](product-roadmap.md) | §1–§6 |
| **版本里程碑、V1/V2/V3 验收与状态** | [`product-roadmap.md`](product-roadmap.md) | §7（含 7.1–7.4） |
| **设计决策全文（为何这样定、细节与边界）** | [`architecture-decisions.md`](architecture-decisions.md) | 文首说明后 → **决策 1** 起分节编号 |
| **V3 进程分离：Mermaid 总览、执行序列、组件与旧架构对比** | [`v3-architecture-diagrams.md`](v3-architecture-diagrams.md) | §1 系统总览；§2 起为流程与专题图 |
| **V4 知识树概念对齐：三层存储、检索流程、分阶段路线** | [`v4-knowledge-tree-concepts.md`](v4-knowledge-tree-concepts.md) | 全文 |
| **V4 知识树技术规格：P1 数据模型、接口契约、模块结构、测试策略** | [`v4-knowledge-tree-spec.md`](v4-knowledge-tree-spec.md) | 按模块分节 |
| **改代码后跑哪条命令、`make test_*` 含义** | [`../tests/README.md`](../tests/README.md) | 命令表与文档分工 |
| **环境、代理、测试分层、E2E 前检查、FAQ** | [`../tests/TESTING.md`](../tests/TESTING.md) | 「命令一览」起至分层与约定 |
| **V2-a/b/c 专项测试文件与命令** | [`../tests/V2_TESTING.md`](../tests/V2_TESTING.md) | 各节按 V2-a / V2-b / V2-c 分块 |

---

## 根目录与 `docs/` 的分工（速记）

| 路径 | 谁读 | 内容 |
|------|------|------|
| `README.md` | 新用户 | 安装、环境、启动、项目树摘要 |
| `CLAUDE.md` | 开发者 / 协调 Agent | 契约、状态机、模块速查、环境指针 |
| `docs/README.md` | 所有人（导航） | 本索引 |
| `docs/*.md` | 需要细节时 | 产品路线、ADR、架构图 |
