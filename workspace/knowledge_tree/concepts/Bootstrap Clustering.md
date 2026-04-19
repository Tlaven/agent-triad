---
type: concept
title: "Bootstrap Clustering"
complexity: advanced
domain: knowledge-management
aliases:
  - "GMM Clustering"
  - "Tree Construction"
  - "Auto Clustering"
created: 2026-04-19
updated: 2026-04-19
tags:
  - concept
  - v4
  - bootstrap
  - clustering
  - gmm
  - umap
status: developing
related:
  - "[[Knowledge Tree]]"
  - "[[Change Mapping]]"
sources: []
---

# Bootstrap Clustering

Bootstrap 聚类是从扁平节点自动构建层级 DAG 树结构的算法。支持双策略，通过 `cluster_method` 配置选择。

---

## Dual Strategy Design

| Strategy | Trigger | Depth | Dependencies |
|----------|---------|-------|-------------|
| **GMM+UMAP** | `cluster_method="gmm"` or `"auto"` + sklearn available + nodes >= cluster_size | Auto multi-layer | scikit-learn, umap-learn (optional) |
| **Simple Cosine BFS** | `cluster_method="simple"` or auto-fallback | Fixed 3 layers | None |

Config: `cluster_method: str = "auto"`, `cluster_size: int = 20`

---

## GMM+UMAP Algorithm (LeanRAG-inspired)

```
Leaf node embedding matrix
  -> UMAP reduce to 2D (optional, auto-enabled when sklearn available)
  -> BIC criterion selects optimal cluster count k
  -> GMM clustering -> k clusters
  -> Each cluster creates summary intermediate node (heuristic title, no LLM)
  -> Intermediate node embeddings as next layer input
  -> Recurse until cluster_count <= 1 or insufficient nodes
  -> Create root node connecting all top-level nodes
```

Key parameters:
- **BIC selects k**: Iterate [2, max_k], pick lowest BIC; max_k = n / cluster_size
- **UMAP**: n_neighbors=min(15, n-1), metric=cosine, random_state=42
- **P1 summary**: Heuristic (child title common prefix), no LLM call
- **P2 summary**: LLM-generated summary description

---

## Simple Cosine BFS (zero-dependency fallback)

```
Leaf nodes -> Cosine similarity adjacency matrix (threshold=0.6)
  -> BFS find connected components -> each component is a group
  -> root -> group -> leaf (fixed 3 layers)
```

---

## Trigger Conditions

- **Regular**: Batch (periodic or threshold-triggered)
- **Special**: Agent file system edit captured in real-time -> Change Mapping optimization, but with noise filtering (batch + threshold combined)

---

## Connections

See [[Knowledge Tree]] for how bootstrap fits the overall pipeline.
See [[Change Mapping]] for post-bootstrap edit handling.
