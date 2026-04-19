---
type: meta
title: "AgentTriad Knowledge Tree Index"
updated: 2026-04-19
tags:
  - meta
  - index
status: evergreen
related:
  - "[[overview]]"
  - "[[concepts/_index]]"
  - "[[entities/_index]]"
  - "[[sources/_index]]"
---

# AgentTriad Knowledge Tree Index

Last updated: 2026-04-19

Navigation: [[overview]] | [[concepts/_index]] | [[entities/_index]] | [[sources/_index]]

---

## Concepts

- [[Three-Agent Architecture]] — Supervisor + Planner + Executor 的三层协作架构 (status: mature)
- [[Plan JSON]] — 意图层任务计划的结构化格式规范 (status: mature)
- [[Execution Modes]] — Mode 1/2/3 三种执行模式的决策逻辑 (status: mature)
- [[V3 Process Isolation]] — V3 进程分离架构：独立子进程 + 双路径通信 (status: mature)
- [[Knowledge Tree]] — V4 涌现式知识树：三层存储 + 双路径检索 + 闭环自进化 (status: developing)
- [[Change Mapping]] — 编辑 Delta 提取与向量校准机制 (status: seed)
- [[Bootstrap Clustering]] — GMM+UMAP / 简单余弦 BFS 双策略建树算法 (status: developing)

---

## Entities

- [[Supervisor Agent]] — ReAct 主循环，负责模式决策和工具派发 (status: mature)
- [[Planner Agent]] — 意图层规划器，输出 Plan JSON (status: mature)
- [[Executor Agent]] — 自主选工具的任务执行器 (status: mature)

---

## Sources

- [[architecture-decisions]] — docs/architecture-decisions.md 的知识提取 (status: seed)

---

## Questions

- [[How does tree-first retrieval compare to pure RAG]] — 树优先检索 vs 纯 RAG 的优劣对比 (status: seed)
