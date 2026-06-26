---
description: Stop the nightly probe loop, finalize session meta and daily summary.
allowed-tools: Bash(scripts/probe_supervisor.py:health), Read(logs/probes/**), Write(logs/probes/**), Edit(logs/probes/**), CronList, CronDelete
---

# Probe Stop

Use `$ARGUMENTS` as `end_reason` if provided (one of: `manual|budget|server_unreachable|auth_error|thrashing_detected|status_not_running`). If empty, default `manual`.

## Steps

1. **Stop the loop**: call `CronList`. Find every job whose `prompt` contains `/probe-supervisor` (excluding `/probe-supervisor-stop` itself). Call `CronDelete` on each. Count deleted.

2. **Read state**: Read `logs/probes/state.json`. If missing, reply `probe stop: nothing to stop (no state.json)` and exit.

3. **Finalize current session meta**: Append to `logs/probes/<today>/session-<current_session.session_id's NNN>/meta.md`:
   ```
   - started_at: <current_session.started_at>
   - ended_at: <now>
   - turn_count: <current_session.turn_count>
   - quality_distribution: {good: N, ok: N, degrading: N, bad: N}   # count from turns.jsonl
   - end_reason: <$ARGUMENTS or "manual">
   - thread_id: <current_session.thread_id>
   - topic_thread: <current_session.topic_thread>
   - signals: <current_session.degradation_signals_observed>
   ```

4. **Append closing block to `daily-summary.md`**:
   ```markdown

   --- probe stop <YYYY-MM-DD HH:MM> ---

   - **Window**: <state.started_at> → <now>
   - **Sessions**: <state.total_sessions>
   - **Total turns**: <state.total_turns>
   - **End reason**: <$ARGUMENTS or "manual">
   - **Quality distribution (all sessions)**: {good: N, ok: N, degrading: N, bad: N}
   - **Known issues found**: <list each known_issues_found entry as `- session-NNN turn-NNN signal: description`>
   - **Top 3 notable moments**: <pick 3 across all sessions: best verdict, worst verdict, most interesting tool sequence>
   - **Morning actions**: <2-4 bullet recommendations, e.g. "session-002 turn-07 疑似 BUG-3 复现，检查 state.messages 长度"、"L3 超时在 turn-12 复现，验证 P1 元规则仲裁是否生效">
   ```

5. **Archive current session**: append `state.current_session` summary (with computed `quality_distribution` and `end_reason`) to `state.history`. Set `state.status = "stopped"`. Set `state.current_session = null`. Save `state.json`.

6. **Reply** with a single line: `probe stopped: sessions=<N> turns=<M> known_issues=<K> reason=<reason>`.

If `state.json` is corrupted or unreadable, attempt backup as `state.json.corrupt-<ts>`, then reply `probe stop: state corrupted, backed up`. The cron deletion in step 1 still happens first regardless.
