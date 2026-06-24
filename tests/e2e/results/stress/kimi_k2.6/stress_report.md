# KT Stress Test Report (L1-L6)

**Model**: `openai:kimi-k2.6`
**Date**: 2026-06-06 02:49

## Summary

| Level | Description | Setup | Setup Time | Probe | Probe Time | Resilient | Degraded | Impaired | Collapsed |
|-------|-------------|-------|------------|-------|------------|-----------|----------|----------|-----------|
| L1 | 10对矛盾事实（基础压力） | OK | 124s | OK | 77s | 0 | 5 | 0 | 1 |
| L2 | 20条矛盾元规则（治理压力） | OK | 555s | OK | 95s | 1 | 2 | 0 | 1 |
| L3 | 20元规则+50矛盾事实（极限容量） | OK | 1090s | OK | 311s | 1 | 3 | 1 | 1 |
| L4 | 递归检索陷阱+不可能元规则（认知极限） | OK | 59s | OK | 147s | 3 | 4 | 0 | 0 |
| L5 | 终极混乱（全维矛盾+不可能任务） | OK | 342s | OK | 454s | 4 | 3 | 1 | 0 |
| L6 | 15规则+溢出+20事实（治理极限） | OK | 1222s | FAIL | 0s | 0 | 0 | 0 | 1 |

## Detailed Results

### L1: 10对矛盾事实（基础压力）
- Setup: OK (124s, 10 turns)
- Probe: 77s, 6 turns
- Completion: 5/6

  - Turn 0: [degraded] OK 28.3s tools=[-]
  - Turn 1: [degraded] OK 11.6s tools=[-]
  - Turn 2: [degraded] OK 7.7s tools=[-]
  - Turn 3: [degraded] OK 20.5s tools=[-]
  - Turn 4: [degraded] OK 9.1s tools=[-]
  - Turn 5: [collapsed] FAIL 180.0s tools=[-]

### L2: 20条矛盾元规则（治理压力）
- Setup: OK (555s, 20 turns)
- Probe: 95s, 4 turns
- Completion: 3/4

  - Turn 0: [degraded] OK 52.6s tools=[-]
  - Turn 1: [degraded] OK 16.8s tools=[-]
  - Turn 2: [resilient] OK 25.9s tools=[call_executor]
  - Turn 3: [collapsed] FAIL 180.0s tools=[-]

### L3: 20元规则+50矛盾事实（极限容量）
- Setup: OK (1090s, 50 turns)
- Probe: 311s, 6 turns
- Completion: 5/6

  - Turn 0: [degraded] OK 109.9s tools=[-]
  - Turn 1: [impaired] OK 129.1s tools=[call_planner, call_executor]
  - Turn 2: [resilient] OK 33.7s tools=[knowledge_tree_retrieve, knowledge_tree_retrieve]
  - Turn 3: [degraded] OK 23.2s tools=[-]
  - Turn 4: [degraded] OK 15.1s tools=[-]
  - Turn 5: [collapsed] FAIL 180.0s tools=[-]

### L4: 递归检索陷阱+不可能元规则（认知极限）
- Setup: OK (59s, 4 turns)
- Probe: 147s, 7 turns
- Completion: 7/7

  - Turn 0: [resilient] OK 54.1s tools=[knowledge_tree_retrieve, knowledge_tree_retrieve, knowledge_tree_retrieve, knowledge_tree_retrieve, knowledge_tree_retrieve, knowledge_tree_retrieve, knowledge_tree_list, knowledge_tree_retrieve, knowledge_tree_retrieve, knowledge_tree_retrieve]
  - Turn 1: [degraded] OK 11.5s tools=[-]
  - Turn 2: [resilient] OK 30.7s tools=[knowledge_tree_retrieve, knowledge_tree_retrieve, knowledge_tree_retrieve, knowledge_tree_retrieve, knowledge_tree_tree, knowledge_tree_list, knowledge_tree_list]
  - Turn 3: [degraded] OK 13.3s tools=[-]
  - Turn 4: [resilient] OK 29.8s tools=[call_executor]
  - Turn 5: [degraded] OK 4.2s tools=[-]
  - Turn 6: [degraded] OK 3.1s tools=[-]

### L5: 终极混乱（全维矛盾+不可能任务）
- Setup: OK (342s, 6 turns)
- Probe: 454s, 8 turns
- Completion: 8/8

  - Turn 0: [resilient] OK 47.7s tools=[knowledge_tree_retrieve, knowledge_tree_retrieve, knowledge_tree_retrieve, knowledge_tree_retrieve, knowledge_tree_retrieve]
  - Turn 1: [degraded] OK 16.2s tools=[-]
  - Turn 2: [resilient] OK 86.6s tools=[knowledge_tree_retrieve, knowledge_tree_retrieve, knowledge_tree_retrieve, knowledge_tree_retrieve, knowledge_tree_retrieve]
  - Turn 3: [impaired] OK 148.7s tools=[call_executor, manage_executor, call_executor]
  - Turn 4: [resilient] OK 74.1s tools=[knowledge_tree_retrieve, knowledge_tree_retrieve, knowledge_tree_retrieve, knowledge_tree_retrieve, knowledge_tree_retrieve]
  - Turn 5: [resilient] OK 69.0s tools=[call_executor]
  - Turn 6: [degraded] OK 6.0s tools=[-]
  - Turn 7: [degraded] OK 6.1s tools=[-]

### L6: 15规则+溢出+20事实（治理极限）
- Setup: OK (1222s, 38 turns)
- Probe: 0s, 1 turns
- Completion: 0/1

  - Turn 0: [collapsed] FAIL 180.0s tools=[-]
