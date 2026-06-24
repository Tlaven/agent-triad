# KT Governance E2E Report

**Model**: `openai:deepseek-v4-flash`
**Date**: 2026-06-05 12:10

## Summary

| Test | Name | Setup | Probe Time | P0 | P1 Avg Time |
|------|------|-------|------------|-----|-------------|
| T0 | 空白基线 (KT OFF) | ✅ | 86s | ✅ | 44.3s |
| T1 | 干净 KT (KT ON, empty) | ✅ | 65s | ✅ | 40.8s |
| T2 | 10对矛盾事实 | ✅ | 343s | ✅ | 34.3s |
| T3 | 15条矛盾元规则 | ✅ | 47s | ✅ | 38.0s |
| T4 | 溢出拒绝 (15+3) | ✅ | 0s | ✅ | 0s |
| T5 | 组合压力 (15规则+20事实) | ✅ | 80s | ✅ | 24.9s |
| T6 | 自救恢复 (delete后重测) | ✅ | 149s | ❌ | 14.9s |

**P0 Pass Rate**: 6/7

## P2: Degradation (T5 vs T0)

| Metric | T0 | T5 | Delta | Threshold |
|--------|-----|-----|-------|-----------|
| Avg response time | 44.3s | 24.9s | 0.6x | ≤3x |
| Completion rate | 83% | 83% | +0% | ≤20% |

## Detailed Results

### T0: 空白基线 (KT OFF)

- Setup: ✅ (0s)
- Probe: 86s
- P0: ✅
- P1: avg=44.3s, mode_a_tools=1, mode_b_tools=1
  - Turn 0: ✅ 21.1s tools=[-]
  - Turn 1: ✅ 4.4s tools=[-]
  - Turn 2: ✅ 21.5s tools=[knowledge_tree_retrieve, knowledge_tree_retrieve]
  - Turn 3: ✅ 9.0s tools=[-]
  - Turn 4: ✅ 29.8s tools=[call_executor]
  - Turn 5: ❌ 180.0s tools=[-]

### T1: 干净 KT (KT ON, empty)

- Setup: ✅ (0s)
- Probe: 65s
- P0: ✅
- P1: avg=40.8s, mode_a_tools=0, mode_b_tools=1
  - Turn 0: ✅ 12.2s tools=[-]
  - Turn 1: ✅ 4.1s tools=[-]
  - Turn 2: ✅ 12.0s tools=[-]
  - Turn 3: ✅ 8.9s tools=[-]
  - Turn 4: ✅ 27.5s tools=[call_executor]
  - Turn 5: ❌ 180.0s tools=[-]

### T2: 10对矛盾事实

- Setup: ✅ (112s)
- Probe: 343s
- P0: ✅
- P1: avg=34.3s, mode_a_tools=0, mode_b_tools=4
  - Turn 0: ✅ 15.2s tools=[-]
  - Turn 1: ✅ 3.6s tools=[-]
  - Turn 2: ✅ 7.3s tools=[-]
  - Turn 3: ✅ 10.6s tools=[-]
  - Turn 4: ✅ 8.2s tools=[-]
  - Turn 5: ✅ 128.9s tools=[call_executor, manage_executor, manage_executor]
  - Turn 6: ✅ 14.3s tools=[knowledge_tree_status, knowledge_tree_tree]
  - Turn 7: ✅ 6.7s tools=[knowledge_tree_list_meta_rules]
  - Turn 8: ✅ 42.0s tools=[knowledge_tree_retrieve, knowledge_tree_retrieve, knowledge_tree_tree, knowledge_tree_list, knowledge_tree_list, knowledge_tree_retrieve, knowledge_tree_retrieve, knowledge_tree_retrieve, knowledge_tree_retrieve, knowledge_tree_record_feedback]
  - Turn 9: ✅ 106.2s tools=[call_planner, call_executor, manage_executor]

### T3: 15条矛盾元规则

- Setup: ✅ (0s)
- Probe: 47s
- P0: ✅
- P1: avg=38.0s, mode_a_tools=1, mode_b_tools=0
  - Turn 0: ✅ 10.1s tools=[-]
  - Turn 1: ✅ 3.5s tools=[-]
  - Turn 2: ✅ 16.3s tools=[knowledge_tree_retrieve]
  - Turn 3: ✅ 11.9s tools=[-]
  - Turn 4: ✅ 5.2s tools=[-]
  - Turn 5: ❌ 181.1s tools=[-]

### T4: 溢出拒绝 (15+3)

- Setup: ✅ (85s)
- Probe: 0s
- P0: ✅
- P1: avg=0s, mode_a_tools=0, mode_b_tools=0
- Overflow: 3/3 rejected

### T5: 组合压力 (15规则+20事实)

- Setup: ✅ (326s)
- Probe: 80s
- P0: ✅
- P1: avg=24.9s, mode_a_tools=1, mode_b_tools=0
  - Turn 0: ✅ 27.1s tools=[-]
  - Turn 1: ✅ 3.7s tools=[-]
  - Turn 2: ✅ 28.6s tools=[knowledge_tree_retrieve, knowledge_tree_retrieve, knowledge_tree_retrieve]
  - Turn 3: ✅ 12.7s tools=[-]
  - Turn 4: ✅ 8.1s tools=[-]
  - Turn 5: ❌ 69.2s tools=[-]

### T6: 自救恢复 (delete后重测)

- Setup: ✅ (85s)
- Probe: 149s
- P0: ❌ mode_b_no_tools=4/5 (hallucination risk)
- P1: avg=14.9s, mode_a_tools=0, mode_b_tools=1
  - Turn 0: ✅ 18.4s tools=[-]
  - Turn 1: ✅ 4.9s tools=[-]
  - Turn 2: ✅ 13.1s tools=[-]
  - Turn 3: ✅ 8.9s tools=[-]
  - Turn 4: ✅ 9.4s tools=[-]
  - Turn 5: ✅ 21.1s tools=[-]
  - Turn 6: ✅ 13.8s tools=[-]
  - Turn 7: ✅ 24.7s tools=[knowledge_tree_list_meta_rules]
  - Turn 8: ✅ 14.2s tools=[-]
  - Turn 9: ✅ 20.4s tools=[-]
