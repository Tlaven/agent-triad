---
type: concept
title: "Change Mapping"
complexity: advanced
domain: knowledge-management
aliases:
  - "Edit Delta"
  - "Vector Calibration"
created: 2026-04-19
updated: 2026-04-19
tags:
  - concept
  - v4
  - change-mapping
  - delta
  - json-patch
status: seed
related:
  - "[[Knowledge Tree]]"
  - "[[Bootstrap Clustering]]"
sources: []
---

# Change Mapping

Change Mapping 是知识树编辑能力的核心机制：当 Agent 编辑 Markdown 节点时，系统提取结构化 Delta，同步更新图数据库和向量索引，形成可追溯的闭环。

---

## Phased Implementation

| Phase | Agent Operations | Change Mapping | Delta Format |
|-------|-----------------|----------------|-------------|
| P1 Prototype | Edit content + merge/split | Re-embed affected nodes, path re-sort | JSON Patch (RFC 6902) |
| P2 Structure evolution | + move_subtree | + subtree path Delta | + Semantic layer (merge/split/move -> JSON Patch) |
| P3 Full | + Create abstraction layer, cross-layer restructure | Generic Delta description format | Complete semantic layer |

---

## Delta Format Strategy

- **P1**: Standard JSON Patch (RFC 6902) for validation
- **P2**: Domain semantic operations (merge/split/move) mapped down to JSON Patch
- Agent output is **strongly constrained**: prompt template + parsing validation to prevent hallucinated invalid Deltas
- P2 introduces **semantic Delta log** for human/Supervisor audit

---

## Vector Calibration

After Delta extraction:
1. Identify affected nodes from the Delta
2. Re-embed only the changed nodes (not the entire tree)
3. Update Kuzu graph edges if parent-child relationships changed
4. Log the calibration for traceability

---

## Connections

See [[Knowledge Tree]] for the overall knowledge tree design.
See [[Bootstrap Clustering]] for the initial tree construction.
