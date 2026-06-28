---
description: Probe LangGraph Supervisor (one cron fire) — read state.json, decide continue/switch, generate next message, evaluate response.
argument-hint: [budget_hours]
allowed-tools: Bash(scripts/probe_supervisor.py:*), Read(logs/probes/**), Write(logs/probes/**), Edit(logs/probes/**), CronList, CronDelete
---

# Probe Supervisor (one fire)

You are mid-automated-probe of the LangGraph Supervisor. This slash command runs once per cron fire (registered via `/probe-supervisor-start`). Execute the 9 steps below precisely. **Output discipline: never echo raw script JSON in your reply; internalize it.** Do not narrate. Working memory is unreliable across fires — `logs/probes/state.json` is the only long-term memory.

If `$ARGUMENTS` is a positive number, treat it as `budget_hours` (default 8).

## 1. Bootstrap or continue

Read `logs/probes/state.json` (create `logs/probes/` tree first if missing).

**If file missing** (first fire of a run):
1. Run `scripts/probe_supervisor.py health`. If `status != "ok"`, do NOT write `state.json` (leave it missing so the next fire retries init cleanly). Append a line to `logs/probes/<today>/daily-summary.md` (create file if missing) noting `HH:MM init paused: server down (<status>)`. Reply `probe paused: server down at init` and STOP this fire. The next fire will re-attempt init.
2. Run `scripts/probe_supervisor.py new-session` → `thread_id`.
3. Initialize `state.json` with the schema: `version=1`, `started_at=<now>`, `budget_hours` from `$ARGUMENTS` or 8, `deadline_at = started_at + budget_hours`, `status="running"`, `current_session = {session_id:"<today>-001", thread_id, started_at:<now>, turn_count:0, warmup_turns:2, min_turns_before_switch:5, topic_thread:"<seeded from opener>", next_topic_hint:"", quality_trend:[], consecutive_bad:0, consecutive_degrading:0, degradation_signals_observed:[]}`, `history:[]`, `total_sessions:1`, `total_turns:0`, `known_issues_found:[]`, `consecutive_server_failures:0`.
4. Create `logs/probes/<today>/session-001/meta.md` with one header line.
5. Proceed to step 4 with the opener message.

**If file exists**: proceed to step 2.

## 2. Budget & liveness

- Let `now = <current ISO timestamp>`. If `now > state.deadline_at` or `state.status != "running"`: invoke `/probe-supervisor-stop` (reason `budget` or `status_not_running`) and END this fire.
- Run `scripts/probe_supervisor.py health`. Branch on `status`:
  - `ok`: reset `consecutive_server_failures = 0`. Continue to step 3.
  - `server_unreachable`: increment `consecutive_server_failures`. If `>= 3`: invoke `/probe-supervisor-stop` (reason `server_unreachable`). Else: append to `daily-summary.md` one line `HH:MM paused: server unreachable (failures=N)`, set `state.status="paused_server_down"`, save, reply `probe paused: server down N/3` and END fire.
  - `auth_error`: invoke `/probe-supervisor-stop` (reason `auth_error`). Do not retry.
  - `rate_limit`: append pause line, END fire (next fire retries).

## 3. Decide: continue vs switch session

Let `s = state.current_session`. Compute switch decision:

- **Anti-thrash hard stop**: count `state.history` entries whose `ended_at` is within the last 60 minutes. If `>= 6`: invoke `/probe-supervisor-stop` (reason `thrashing_detected`) and END fire.
- **Soft switch conditions** (require `s.turn_count >= s.min_turns_before_switch`):
  - `s.consecutive_bad >= 2` → SWITCH, reason `consecutive_bad`
  - `s.consecutive_degrading >= 3` → SWITCH, reason `degradation`
- **Hard single-turn triggers** (require `s.turn_count >= s.min_turns_before_switch`):
  - Last turn had `empty_response=true`
  - Last turn `duration_s > 90`
  - Last turn `supervisor_decision.reason` contains "timed out"
  - Last turn `tool_calls == []` AND the user question plainly needed retrieval/planning (`mode_b_no_tools` — use judgment)
  → SWITCH, reason `hard_signal:<which>`
- **Warmup protection**: if `s.turn_count < s.warmup_turns`, never switch (record verdict but tolerate).

**If SWITCH**:
1. Append `s` summary to `state.history` (compute `quality_distribution` from `turns.jsonl`).
2. Finalize `logs/probes/<today>/session-<NNN>/meta.md` with `end_reason`, `ended_at`, `quality_distribution`.
3. Run `scripts/probe_supervisor.py new-session` → new `thread_id`. If fails, END fire (will retry next fire).
4. Reset `state.current_session`: new `session_id` (increment NNN), new `thread_id`, `started_at=<now>`, `turn_count=0`, `consecutive_*=0`, keep evolving `topic_thread` and `next_topic_hint` (see step 4 guidance).
5. Increment `state.total_sessions`.
6. Create new `session-<NNN+1>/meta.md` header.
7. Append `--- session-NNN start: <topic_thread> ---` to `daily-summary.md`.
8. Continue to step 4 (the new session's turn 1).

## 4. Generate next message (open exploration)

Generate ONE user message, **<= 2 sentences, Chinese OK**, that naturally advances `s.topic_thread`. Vary intent across turns (don't repeat last turn's intent): sometimes ask for retrieval, sometimes ingestion, sometimes multi-step planning, sometimes adversarial reconciliation ("but you just said X, now Y — reconcile").

- **Warmup turns** (`s.turn_count < s.warmup_turns`): use `s.next_topic_hint` if non-empty, otherwise a simple open question. Avoid adversarial until warmup passes.
- **First message of a brand-new probe run** (no `state.history`): pick one concrete opener — "查看知识树状态" / "这个项目的 ReAct 模式和普通 ReAct 有什么区别？" / "帮我规划一个三步任务：检索 X、综合 Y、报告 Z". Seed `s.topic_thread` from the chosen opener.
- **Every 3rd session** (`state.total_sessions % 3 == 0`): pick a fresh major topic from the menu (知识树 / 状态 / 工具 / 规划 / 执行) that hasn't appeared in the last 3 sessions.
- **Within a session**: drift naturally. Don't jump randomly. Update `s.topic_thread` and `s.next_topic_hint` to reflect where the conversation now is.

## 5. Execute send

Run `scripts/probe_supervisor.py send --thread <s.thread_id> --message "<msg>"`. Parse the JSON line. **Do not paste the JSON into your reply.** Branch:

- `status in (auth_error)`: invoke `/probe-supervisor-stop` (reason `auth_error`), END fire.
- `status == rate_limit`: append pause line, END fire.
- `status == server_unreachable`: same as step 2 unreachable branch.
- `status in (timeout, error, cancelled)`: still evaluate quality in step 6, but mark degradation signal.
- `status == ok`: proceed to evaluate.

## 6. Evaluate response quality

Produce **one verdict** and **a signals list**. Be honest, not generous.

Verdict scale:
- `good` — on point, specific, tools used sensibly when warranted
- `ok` — relevant and coherent, minor gaps
- `degrading` — coherent but generic, evasive, or missed the point noticeably
- `bad` — empty, garbled, totally off-topic, or self-contradictory

Dimensions to weigh: relevance / coherence / specificity / tool sanity.

Signals (pick applicable ones from this fixed set): `mode_b_no_tools, high_latency, empty_response, self_contradiction, evasion, repetition, rag_contradiction_flood, timed_out, run_error`.

Also write `notes` — a free-text hint **<= 40 chars** capturing nuance (e.g. "工具对了但答案空泛").

## 7. Write records

Append ONE line to `logs/probes/<today>/session-<NNN>/turns.jsonl`:
```json
{"turn": <int>, "ts": "<ISO>", "user": "<msg, full>", "agent": "<response, full up to 2000 chars>", "duration_s": <float>, "tool_calls": [...], "verdict": "<v>", "signals": [...], "notes": "<...>", "supervisor_decision": {...}|null, "messages_count_in_state": <int>}
```

Update `state.json`:
- `s.turn_count += 1`
- `s.quality_trend` append `verdict`
- `s.consecutive_bad = consecutive_bad+1 if verdict=="bad" else 0`
- `s.consecutive_degrading = consecutive_degrading+1 if verdict in ("degrading","bad") else 0`
- `s.last_user_msg_preview = <first 200 chars of msg>`
- `s.last_agent_response_preview = <first 200 chars of response>`
- `s.degradation_signals_observed` = union of prior + this turn's signals
- `state.last_fire_at = <now>`
- `state.total_turns += 1`
- If any signal in `{mode_b_no_tools, timed_out, empty_response, rag_contradiction_flood, run_error}`: append to `state.known_issues_found` with `{session: "<NNN>", turn: <int>, ts, signal, description: <notes or short summary>}`.

## 8. Daily summary

Append ONE line to `logs/probes/<today>/daily-summary.md`:
```
HH:MM session-NNN turn-NNN verdict=<v> signals=[...] | <12-word gist of what happened>
```

Only append a second line if `verdict == bad` or a new `known_issue` was logged — then add a single explanatory sentence.

**Every 30 turns** (when `state.total_turns % 30 == 0`): also append a 2-3 line `### checkpoint` paragraph summarizing topic evolution,KT cumulative state, and any concerns. After writing it, mentally reset working memory — trust `state.json` only.

## 9. End of fire

Reply with a single line: `probe turn NNN done: verdict=<v> next=session-<NNN>`.

Do not invoke any other tool. Do not write prose. The next cron fire continues from `state.json`.
