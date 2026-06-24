# KT Stress Test Report (L1-L6)

**Model**: `openai:deepseek-v4-flash`
**Date**: 2026-06-05 19:38

## Summary

| Level | Description | Setup | Setup Time | Probe | Probe Time | Resilient | Degraded | Impaired | Collapsed |
|-------|-------------|-------|------------|-------|------------|-----------|----------|----------|-----------|
| L1 | 10对矛盾事实（基础压力） | OK | 112s | OK | 230s | 0 | 5 | 1 | 0 |
| L2 | 20条矛盾元规则（治理压力） | OK | 374s | OK | 68s | 1 | 2 | 0 | 1 |
| L3 | 20元规则+50矛盾事实（极限容量） | OK | 695s | FAIL | 119s | 0 | 1 | 0 | 1 |
| L4 | 递归检索陷阱+不可能元规则（认知极限） | OK | 56s | OK | 176s | 3 | 4 | 0 | 0 |
| L5 | 终极混乱（全维矛盾+不可能任务） | OK | 100s | FAIL | 0s | 0 | 0 | 0 | 1 |
| L6 | 15规则+溢出+20事实（治理极限） | OK | 963s | FAIL | 68s | 2 | 0 | 0 | 1 |

## Detailed Results

### L1: 10对矛盾事实（基础压力）
- Setup: OK (112s, 10 turns)
- Probe: 230s, 6 turns
- Completion: 6/6

  - Turn 0: [degraded] OK 32.0s tools=[-]
  - Turn 1: [degraded] OK 12.4s tools=[-]
  - Turn 2: [degraded] OK 10.7s tools=[-]
  - Turn 3: [degraded] OK 24.4s tools=[-]
  - Turn 4: [degraded] OK 18.5s tools=[-]
  - Turn 5: [impaired] OK 131.8s tools=[call_planner, call_executor, manage_executor]

### L2: 20条矛盾元规则（治理压力）
- Setup: OK (374s, 20 turns)
- Probe: 68s, 4 turns
- Completion: 3/4

  - Turn 0: [degraded] OK 34.8s tools=[-]
  - Turn 1: [degraded] OK 5.6s tools=[-]
  - Turn 2: [resilient] OK 27.8s tools=[call_executor]
  - Turn 3: [collapsed] FAIL 149.5s tools=[-]

### L3: 20元规则+50矛盾事实（极限容量）
- Setup: OK (695s, 50 turns)
- Probe: 119s, 2 turns
- Completion: 1/2

  - Turn 0: [degraded] OK 119.0s tools=[-]
  - Turn 1: [collapsed] FAIL 180.0s tools=[-]

### L4: 递归检索陷阱+不可能元规则（认知极限）
- Setup: OK (56s, 4 turns)
- Probe: 176s, 7 turns
- Completion: 7/7

  - Turn 0: [resilient] OK 57.5s tools=[knowledge_tree_retrieve, knowledge_tree_retrieve, knowledge_tree_tree, knowledge_tree_list, knowledge_tree_retrieve, knowledge_tree_retrieve, knowledge_tree_retrieve, knowledge_tree_record_feedback, knowledge_tree_list]
  - Turn 1: [resilient] OK 20.0s tools=[knowledge_tree_retrieve, knowledge_tree_retrieve]
  - Turn 2: [resilient] OK 61.1s tools=[knowledge_tree_retrieve, knowledge_tree_retrieve, knowledge_tree_list, knowledge_tree_tree, knowledge_tree_list, knowledge_tree_retrieve, knowledge_tree_retrieve, knowledge_tree_retrieve, knowledge_tree_retrieve, knowledge_tree_retrieve, knowledge_tree_retrieve, knowledge_tree_list, knowledge_tree_retrieve, knowledge_tree_retrieve, knowledge_tree_retrieve, knowledge_tree_list]
  - Turn 3: [degraded] OK 23.4s tools=[-]
  - Turn 4: [degraded] OK 6.5s tools=[-]
  - Turn 5: [degraded] OK 3.7s tools=[-]
  - Turn 6: [degraded] OK 3.4s tools=[-]

### L5: 终极混乱（全维矛盾+不可能任务）
- Setup: OK (100s, 6 turns)
- Probe: 0s, 1 turns
- Completion: 0/1

  - Turn 0: [collapsed] FAIL 180.0s tools=[-]

### L6: 15规则+溢出+20事实（治理极限）
- Setup: OK (963s, 38 turns)
- Probe: 68s, 3 turns
- Completion: 2/3

  - Turn 0: [resilient] OK 45.7s tools=[knowledge_tree_retrieve, knowledge_tree_record_feedback]
  - Turn 1: [resilient] OK 22.2s tools=[knowledge_tree_retrieve]
  - Turn 2: [collapsed] FAIL 46.5s tools=[-]
