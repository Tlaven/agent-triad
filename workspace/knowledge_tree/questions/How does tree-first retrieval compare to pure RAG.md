---
type: question
title: "How does tree-first retrieval compare to pure RAG"
complexity: advanced
domain: knowledge-management
created: 2026-04-19
updated: 2026-04-19
tags:
  - question
  - retrieval
  - rag
  - knowledge-tree
status: seed
related:
  - "[[Knowledge Tree]]"
  - "[[Bootstrap Clustering]]"
sources: []
---

# How does tree-first retrieval compare to pure RAG

## Question

In what scenarios does tree-first retrieval (LLM route navigation + RAG fallback) outperform pure vector-based RAG? What are the measurable differences in:

1. Retrieval precision and recall
2. Query latency and cost
3. Maintenance burden at different knowledge scales

## Context

AgentTriad's V4 Knowledge Tree uses a dual-path retrieval strategy: LLM-guided tree navigation as primary path (confidence >= 0.7), with vector similarity search as fallback (threshold >= 0.85). This is fundamentally different from pure RAG which relies solely on embedding similarity.

## Hypothesis

- **Small-medium scale (<1000 nodes)**: Tree-first should outperform due to structured context from DAG hierarchy
- **Large scale (>10000 nodes)**: Pure RAG may be more efficient; tree navigation cost grows
- **Ambiguous queries**: Tree-first with LLM routing handles ambiguity better than embedding similarity alone

## Open Questions

- What is the crossover point where tree navigation becomes slower than vector search?
- How much does the LLM routing cost add compared to embedding computation?
- Does tree structure quality significantly affect retrieval performance?
