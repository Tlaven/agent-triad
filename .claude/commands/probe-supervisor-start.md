---
description: Start the nightly probe loop by registering a CronCreate job that fires /probe-supervisor every 3 minutes. Replaces the old /loop-driven approach so /probe-supervisor-stop can actually terminate it.
argument-hint: [budget_hours]
allowed-tools: CronCreate, Read(logs/probes/**)
---

# Probe Supervisor Start

Use `$ARGUMENTS` as `budget_hours` if a positive number (default 8).

## Why this exists

`/probe-supervisor-stop` can only delete jobs that `CronList` can see. The old `/loop`-driven launch used `ScheduleWakeup`, which lives in Claude Code's in-process memory and is invisible to `CronList` — so stop appeared to succeed while wakeups kept firing. This command registers the probe via `CronCreate` instead, making the loop fully stoppable.

## Steps

1. **Read state**: Read `logs/probes/state.json` (create `logs/probes/` tree first if missing).
   - If file exists and `state.status == "running"`: reply `probe start: already running (started_at=<state.started_at>, deadline=<state.deadline_at>)` and exit. Do not register a second cron job.
   - Otherwise (missing, stopped, paused_*): proceed.

2. **Compute cron prompt**: `prompt = "/probe-supervisor <budget_hours>"` (e.g. `/probe-supervisor 8`). The budget is consumed only on the first fire when `state.json` is missing; later fires ignore it.

3. **Register cron**: call `CronCreate` with:
   - `cron`: `"*/3 * * * *"` (every 3 minutes; the runtime applies jitter automatically)
   - `prompt`: the prompt from step 2
   - `recurring`: true
   - `durable`: false (nightly/temporary — survives only this Claude Code process)

4. **Reply** with a single line:
   ```
   probe started: budget=<H>h job_id=<id> cron=*/3 * * * *
   ```
   Add one short note: `to stop: /probe-supervisor-stop`. Mention that recurring cron jobs auto-expire after 7 days if not stopped.

Do not write `state.json` here — the first `/probe-supervisor` fire bootstraps it. Do not invoke any other tool.
