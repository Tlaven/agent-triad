# KT Governance E2E Report

**Model**: `openai:deepseek-v4-pro`
**Date**: 2026-06-05 11:10

## Summary

| Test | Name | Setup | Probe Time | P0 | P1 Avg Time |
|------|------|-------|------------|-----|-------------|
| T0 | 空白基线 (KT OFF) | ✅ | 414s | ✅ | 41.4s |
| T1 | 干净 KT (KT ON, empty) | ✅ | 204s | ✅ | 20.4s |
| T2 | 10对矛盾事实 | ✅ | 193s | ✅ | 37.3s |
| T3 | 15条矛盾元规则 | ✅ | 135s | ❌ | 13.5s |
| T4 | 溢出拒绝 (15+3) | ✅ | 0s | ❌ | 0s |
| T5 | 组合压力 (15规则+20事实) | ✅ | 139s | ❌ | 13.9s |
| T6 | 自救恢复 (delete后重测) | ✅ | 119s | ❌ | 11.9s |

**P0 Pass Rate**: 3/7

## P2: Degradation (T5 vs T0)

| Metric | T0 | T5 | Delta | Threshold |
|--------|-----|-----|-------|-----------|
| Avg response time | 41.4s | 13.9s | 0.3x | ≤3x |
| Completion rate | 100% | 100% | +0% | ≤20% |

## Detailed Results

### T0: 空白基线 (KT OFF)

- Setup: ✅ (0s)
- Probe: 414s
- P0: ✅
- P1: avg=41.4s, mode_a_tools=0, mode_b_tools=5
  - Turn 0: ✅ 15.3s tools=[-]
  - Turn 1: ✅ 5.5s tools=[-]
  - Turn 2: ✅ 17.7s tools=[-]
  - Turn 3: ✅ 18.1s tools=[-]
  - Turn 4: ✅ 27.2s tools=[call_executor]
  - Turn 5: ✅ 176.2s tools=[call_executor, manage_executor, call_executor, manage_executor, call_executor, call_planner]
  - Turn 6: ✅ 26.8s tools=[knowledge_tree_status, knowledge_tree_tree]
  - Turn 7: ✅ 23.0s tools=[knowledge_tree_list_meta_rules]
  - Turn 8: ✅ 17.7s tools=[knowledge_tree_retrieve]
  - Turn 9: ✅ 86.9s tools=[call_planner, call_executor]

### T1: 干净 KT (KT ON, empty)

- Setup: ✅ (0s)
- Probe: 204s
- P0: ✅
- P1: avg=20.4s, mode_a_tools=0, mode_b_tools=5
  - Turn 0: ✅ 25.6s tools=[-]
  - Turn 1: ✅ 5.8s tools=[-]
  - Turn 2: ✅ 15.1s tools=[-]
  - Turn 3: ✅ 14.4s tools=[-]
  - Turn 4: ✅ 27.5s tools=[call_executor]
  - Turn 5: ✅ 44.7s tools=[call_executor]
  - Turn 6: ✅ 22.0s tools=[knowledge_tree_status, knowledge_tree_tree]
  - Turn 7: ✅ 8.5s tools=[knowledge_tree_list_meta_rules]
  - Turn 8: ✅ 25.7s tools=[knowledge_tree_retrieve, knowledge_tree_tree, knowledge_tree_retrieve, knowledge_tree_list]
  - Turn 9: ✅ 14.3s tools=[-]

### T2: 10对矛盾事实

- Setup: ✅ (149s)
- Probe: 193s
- P0: ✅
- P1: avg=37.3s, mode_a_tools=0, mode_b_tools=5
  - Turn 0: ✅ 23.5s tools=[-]
  - Turn 1: ✅ 4.4s tools=[-]
  - Turn 2: ✅ 14.7s tools=[-]
  - Turn 3: ✅ 13.5s tools=[-]
  - Turn 4: ✅ 27.5s tools=[call_executor]
  - Turn 5: ✅ 36.6s tools=[call_executor]
  - Turn 6: ✅ 21.8s tools=[knowledge_tree_status, knowledge_tree_tree]
  - Turn 7: ✅ 9.0s tools=[knowledge_tree_list_meta_rules]
  - Turn 8: ✅ 41.9s tools=[knowledge_tree_retrieve, knowledge_tree_retrieve, knowledge_tree_retrieve, knowledge_tree_retrieve, knowledge_tree_tree, knowledge_tree_list]
  - Turn 9: ❌ 180.0s tools=[-]

### T3: 15条矛盾元规则

- Setup: ✅ (129s)
- Probe: 135s
- P0: ❌ mode_b_no_tools=4/5 (hallucination risk)
- P1: avg=13.5s, mode_a_tools=0, mode_b_tools=1
  - Turn 0: ✅ 23.0s tools=[-]
  - Turn 1: ✅ 4.6s tools=[-]
  - Turn 2: ✅ 16.9s tools=[-]
  - Turn 3: ✅ 15.4s tools=[-]
  - Turn 4: ✅ 9.7s tools=[-]
  - Turn 5: ✅ 10.3s tools=[-]
  - Turn 6: ✅ 11.0s tools=[-]
  - Turn 7: ✅ 10.4s tools=[-]
  - Turn 8: ✅ 19.9s tools=[knowledge_tree_retrieve, knowledge_tree_retrieve, knowledge_tree_tree]
  - Turn 9: ✅ 13.7s tools=[-]

### T4: 溢出拒绝 (15+3)

- Setup: ✅ (202s)
- Probe: 0s
- P0: ❌ overflow_rejected=0/3
- P1: avg=0s, mode_a_tools=0, mode_b_tools=0
- Overflow: 0/3 rejected

### T5: 组合压力 (15规则+20事实)

- Setup: ✅ (276s)
- Probe: 139s
- P0: ❌ mode_b_no_tools=4/5 (hallucination risk)
- P1: avg=13.9s, mode_a_tools=0, mode_b_tools=1
  - Turn 0: ✅ 24.3s tools=[-]
  - Turn 1: ✅ 4.7s tools=[-]
  - Turn 2: ✅ 17.8s tools=[-]
  - Turn 3: ✅ 18.0s tools=[-]
  - Turn 4: ✅ 11.6s tools=[-]
  - Turn 5: ✅ 9.9s tools=[-]
  - Turn 6: ✅ 11.7s tools=[-]
  - Turn 7: ✅ 9.3s tools=[knowledge_tree_list_meta_rules]
  - Turn 8: ✅ 18.5s tools=[-]
  - Turn 9: ✅ 13.5s tools=[-]

### T6: 自救恢复 (delete后重测)

- Setup: ✅ (127s)
- Probe: 119s
- P0: ❌ mode_b_no_tools=5/5 (hallucination risk)
- P1: avg=11.9s, mode_a_tools=0, mode_b_tools=0
  - Turn 0: ✅ 15.9s tools=[-]
  - Turn 1: ✅ 4.4s tools=[-]
  - Turn 2: ✅ 16.7s tools=[-]
  - Turn 3: ✅ 16.0s tools=[-]
  - Turn 4: ✅ 9.6s tools=[-]
  - Turn 5: ✅ 10.4s tools=[-]
  - Turn 6: ✅ 12.0s tools=[-]
  - Turn 7: ✅ 8.1s tools=[-]
  - Turn 8: ✅ 15.0s tools=[-]
  - Turn 9: ✅ 10.8s tools=[-]
