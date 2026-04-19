---
type: concept
title: "Knowledge Tree"
complexity: advanced
domain: knowledge-management
aliases:
  - "V4 Knowledge Tree"
  - "Emergent Knowledge Tree"
  - "EKT"
created: 2026-04-19
updated: 2026-04-19
tags:
  - concept
  - v4
  - knowledge-tree
  - rag
  - dag
status: developing
related:
  - "[[Change Mapping]]"
  - "[[Bootstrap Clustering]]"
  - "[[overview]]"
sources: []
---

# Knowledge Tree

V4 涌现式知识树是一个自组织存储、高效检索、持续进化的知识管理系统，内嵌于 Supervisor Agent。核心思想：**信息片段 -> 语义聚类 -> DAG 结构 -> Agent 编辑 -> Change Mapping -> 向量校准 -> 结构重组**。

---

## Three-Layer Storage

```
Layer 1: Markdown Files (Source of Truth)
  - Agent directly reads/writes, human-reviewable, git-versionable

Layer 2: Graph Database (Structure Layer)
  - DAG: node metadata + relationship edges
  - Primary parent markers (for traversal) + association edges (multi-parent refs)
  - Built-in/extensible vector index (Layer 3)

Layer 3: Vector Index (Retrieval Layer)
  - Semantic similarity computation, fuzzy recall
  - Constrained ranking by tree structure
```

---

## Retrieval Flow

```
Query -> vectorize
     -> LLM route navigation (primary, confidence >= 0.7)
     -> RAG fallback (when tree nav fails, similarity >= 0.85)
     -> Result fusion (tree / tree+rag / rag / none)
     -> Agent feedback (satisfaction annotation)
     -> Structured retrieval log
```

**Key decision: Tree-first, RAG fallback**

---

## Optimization Loop (4 Signal Types)

| Signal | Trigger | Action |
|--------|---------|--------|
| Navigation failure | Frequent nav failures under a parent | Mark as structural weak point, split/restructure |
| RAG false positive | RAG returns irrelevant nodes | Contrastive learning negative sample |
| Total failure | Tree + RAG both fail, accumulated threshold | Create new node from failed query seed |
| Content insufficient | Nav succeeds but content inadequate | Update node content/summary |

---

## Anti-Oscillation Controls

- Independent thresholds per signal type
- Global frequency cap (max N optimizations per time window)
- Priority ordering: total failure > nav failure > RAG false positive > content insufficient

---

## Connections

See [[Bootstrap Clustering]] for tree construction algorithms.
See [[Change Mapping]] for the edit delta and vector calibration mechanism.
