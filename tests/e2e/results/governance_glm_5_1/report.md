# KT Governance E2E Report

**Model**: `openai:glm-5.1`
**Date**: 2026-06-05 13:24

## Summary

| Test | Name | Setup | Probe Time | P0 | P1 Avg Time |
|------|------|-------|------------|-----|-------------|
| T0 | 空白基线 (KT OFF) | ✅ | 65s | ✅ | 16.7s |
| T1 | 干净 KT (KT ON, empty) | ✅ | 0s | ❌ | 8.8s |
| T2 | 10对矛盾事实 | ✅ | 194s | ✅ | 19.4s |
| T3 | 15条矛盾元规则 | ✅ | 75s | ✅ | 42.5s |
| T4 | 溢出拒绝 (15+3) | ✅ | 0s | ✅ | 0s |
| T5 | 组合压力 (15规则+20事实) | ✅ | 41s | ✅ | 13.8s |
| T6 | 自救恢复 (delete后重测) | ✅ | 162s | ✅ | 34.2s |

**P0 Pass Rate**: 6/7

## P2: Degradation (T5 vs T0)

| Metric | T0 | T5 | Delta | Threshold |
|--------|-----|-----|-------|-----------|
| Avg response time | 16.7s | 13.8s | 0.8x | ≤3x |
| Completion rate | 83% | 80% | +3% | ≤20% |

## Detailed Results

### T0: 空白基线 (KT OFF)

- Setup: ✅ (0s)
- Probe: 65s
- P0: ✅
- P1: avg=16.7s, mode_a_tools=1, mode_b_tools=1
  - Turn 0: ✅ 11.6s tools=[-]
  - Turn 1: ✅ 3.3s tools=[-]
  - Turn 2: ✅ 17.1s tools=[knowledge_tree_retrieve, knowledge_tree_retrieve]
  - Turn 3: ✅ 8.1s tools=[-]
  - Turn 4: ✅ 25.1s tools=[call_executor]
  - Turn 5: ❌ 35.2s tools=[-]

### T1: 干净 KT (KT ON, empty)

- Setup: ✅ (0s)
- Probe: 0s
- P0: ❌ completion_rate=0% < 80%
- P1: avg=8.8s, mode_a_tools=0, mode_b_tools=0
  - Turn 0: ❌ 8.8s tools=[-]

### T2: 10对矛盾事实

- Setup: ✅ (108s)
- Probe: 194s
- P0: ✅
- P1: avg=19.4s, mode_a_tools=0, mode_b_tools=5
  - Turn 0: ✅ 29.1s tools=[-]
  - Turn 1: ✅ 5.8s tools=[-]
  - Turn 2: ✅ 8.5s tools=[-]
  - Turn 3: ✅ 21.7s tools=[-]
  - Turn 4: ✅ 26.2s tools=[call_executor]
  - Turn 5: ✅ 27.6s tools=[call_executor]
  - Turn 6: ✅ 8.1s tools=[knowledge_tree_status, knowledge_tree_tree]
  - Turn 7: ✅ 6.6s tools=[knowledge_tree_list_meta_rules]
  - Turn 8: ✅ 21.6s tools=[knowledge_tree_retrieve, knowledge_tree_record_feedback, knowledge_tree_list, knowledge_tree_retrieve, knowledge_tree_record_feedback]
  - Turn 9: ✅ 38.4s tools=[call_executor]

### T3: 15条矛盾元规则

- Setup: ✅ (0s)
- Probe: 75s
- P0: ✅
- P1: avg=42.5s, mode_a_tools=1, mode_b_tools=1
  - Turn 0: ✅ 16.9s tools=[-]
  - Turn 1: ✅ 3.3s tools=[-]
  - Turn 2: ✅ 18.9s tools=[knowledge_tree_retrieve, knowledge_tree_retrieve, knowledge_tree_list_meta_rules, knowledge_tree_record_feedback, knowledge_tree_record_feedback]
  - Turn 3: ✅ 9.9s tools=[-]
  - Turn 4: ✅ 26.2s tools=[call_executor]
  - Turn 5: ❌ 180.0s tools=[-]

### T4: 溢出拒绝 (15+3)

- Setup: ✅ (25s)
- Probe: 0s
- P0: ✅
- P1: avg=0s, mode_a_tools=0, mode_b_tools=0
- Overflow: 3/3 rejected

### T5: 组合压力 (15规则+20事实)

- Setup: ✅ (230s)
- Probe: 41s
- P0: ✅
- P1: avg=13.8s, mode_a_tools=1, mode_b_tools=0
  - Turn 0: ✅ 13.9s tools=[-]
  - Turn 1: ✅ 4.1s tools=[-]
  - Turn 2: ✅ 12.1s tools=[knowledge_tree_retrieve, knowledge_tree_record_feedback]
  - Turn 3: ✅ 11.3s tools=[-]
  - Turn 4: ❌ 27.9s tools=[-]

### T6: 自救恢复 (delete后重测)

- Setup: ✅ (66s)
- Probe: 162s
- P0: ✅
- P1: avg=34.2s, mode_a_tools=0, mode_b_tools=5
  - Turn 0: ✅ 10.7s tools=[-]
  - Turn 1: ✅ 4.8s tools=[-]
  - Turn 2: ✅ 13.1s tools=[-]
  - Turn 3: ✅ 9.5s tools=[-]
  - Turn 4: ✅ 31.0s tools=[call_executor, knowledge_tree_ingest]
  - Turn 5: ✅ 40.5s tools=[call_executor, manage_executor, manage_executor]
  - Turn 6: ✅ 12.2s tools=[knowledge_tree_status, knowledge_tree_tree]
  - Turn 7: ✅ 11.7s tools=[knowledge_tree_list_meta_rules]
  - Turn 8: ✅ 28.2s tools=[knowledge_tree_retrieve, knowledge_tree_list, knowledge_tree_retrieve, knowledge_tree_list, knowledge_tree_record_feedback, knowledge_tree_record_feedback]
  - Turn 9: ❌ 180.0s tools=[-]
