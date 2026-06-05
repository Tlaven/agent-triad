"""KT Governance E2E Test Runner.

Runs T0→T6 test sequence with auto-scoring and Markdown report generation.

Usage:
    python tests/e2e/scripts/kt_governance/run_all.py --model openai:deepseek-v4-flash
    python tests/e2e/scripts/kt_governance/run_all.py --model openai:deepseek-v4-pro --output results/pro
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent  # project root
SCRIPTS = Path(__file__).resolve().parent
PROBE = SCRIPTS / "probe_universal.txt"


@dataclass
class TurnResult:
    ok: bool
    elapsed: float
    tool_calls: list[str]
    error: str | None = None
    final_response: str = ""


@dataclass
class TestResult:
    test_id: str
    test_name: str
    setup_ok: bool = True
    setup_time: float = 0.0
    probe_turns: list[TurnResult] = field(default_factory=list)
    probe_time: float = 0.0
    # T4 specific
    overflow_rejected: int = 0
    overflow_total: int = 0


def _has_connection_error(report: dict) -> bool:
    """Check if any turn failed with Connection error."""
    for t in report.get("turns", []):
        err = t.get("error", "") or ""
        if "Connection error" in err:
            return True
    return False


def _run_chat(
    script: Path,
    model: str,
    output_json: Path,
    kt: bool = True,
    kt_root: str | None = None,
    reset_kt: bool = False,
    reset_each_turn: bool = False,
    turn_timeout: int = 180,
    max_attempts: int = 3,
) -> dict:
    cmd = [
        "uv", "run", "chat.py",
        "--script", str(script),
        "--model", model,
        "--turn-timeout", str(turn_timeout),
        "--report", str(output_json),
    ]
    if kt:
        cmd.append("--kt")
    if kt_root:
        cmd.extend(["--kt-root", kt_root])
    if reset_kt:
        cmd.append("--reset-kt-root")
    if reset_each_turn:
        cmd.append("--reset-each-turn")

    print(f"  CMD: {' '.join(cmd)}")

    for attempt in range(1, max_attempts + 1):
        if attempt > 1:
            delay = 15 * (attempt - 1)
            print(f"  [RETRY {attempt}/{max_attempts}] waiting {delay}s...")
            time.sleep(delay)

        result = subprocess.run(cmd, capture_output=True, text=True, cwd=str(ROOT), timeout=1800)

        if output_json.exists():
            with open(output_json, encoding="utf-8") as f:
                report = json.load(f)
            if not _has_connection_error(report):
                return report
            # Connection error detected — retry if attempts remain
            n_err = sum(1 for t in report.get("turns", []) if "Connection error" in (t.get("error") or ""))
            print(f"  [RETRY] Connection error in {n_err} turn(s)")
            if attempt < max_attempts:
                output_json.unlink(missing_ok=True)
                continue
            return report

    return {"error": result.stderr[-500:] if result.stderr else "no report", "returncode": result.returncode}


def _parse_report(report: dict) -> list[TurnResult]:
    turns = []
    for t in report.get("turns", []):
        tool_names = [tc.get("name", "") for tc in t.get("tool_calls", [])]
        turns.append(TurnResult(
            ok=t.get("ok", False),
            elapsed=t.get("elapsed", 0),
            tool_calls=tool_names,
            error=t.get("error"),
            final_response=t.get("final_response", ""),
        ))
    return turns


def _score_p0(result: TestResult) -> tuple[bool, list[str]]:
    """P0 pass/fail + reasons."""
    fails = []

    # Completion rate ≥ 80%
    if result.probe_turns:
        ok_rate = sum(1 for t in result.probe_turns if t.ok) / len(result.probe_turns)
        if ok_rate < 0.8:
            fails.append(f"completion_rate={ok_rate:.0%} < 80%")

    # Mode B queries should have tool calls (indices 4-8)
    mode_b = result.probe_turns[4:9] if len(result.probe_turns) >= 9 else []
    no_tool_b = [i for i, t in enumerate(mode_b) if t.ok and not t.tool_calls]
    if len(no_tool_b) > 2:
        fails.append(f"mode_b_no_tools={len(no_tool_b)}/5 (hallucination risk)")

    # T4: overflow rejection
    if result.overflow_total > 0:
        if result.overflow_rejected < result.overflow_total:
            fails.append(
                f"overflow_rejected={result.overflow_rejected}/{result.overflow_total}"
            )

    return len(fails) == 0, fails


def _score_p1(result: TestResult, model: str) -> dict:
    """P1 quality metrics."""
    max_time = 90 if "pro" in model else 60
    avg_time = (
        sum(t.elapsed for t in result.probe_turns) / len(result.probe_turns)
        if result.probe_turns else 0
    )
    ok_rate = (
        sum(1 for t in result.probe_turns if t.ok) / len(result.probe_turns)
        if result.probe_turns else 0
    )
    return {
        "avg_time": round(avg_time, 1),
        "avg_time_ok": avg_time <= max_time,
        "completion_rate": f"{ok_rate:.0%}",
        "mode_a_tools": sum(1 for t in result.probe_turns[:4] if t.tool_calls),
        "mode_b_tools": sum(1 for t in result.probe_turns[4:9] if t.tool_calls),
    }


def run_test(
    test_id: str,
    test_name: str,
    model: str,
    output_dir: Path,
    setup_script: Path | None = None,
    kt: bool = True,
    kt_root: str | None = None,
    reset_kt: bool = False,
    skip_probe: bool = False,
) -> TestResult:
    """Run a single test (setup + probe)."""
    tr = TestResult(test_id=test_id, test_name=test_name)
    print(f"\n{'='*60}")
    print(f"  {test_id}: {test_name}")
    print(f"{'='*60}")

    # Setup phase
    if setup_script:
        print(f"  [SETUP] {setup_script.name}")
        setup_json = output_dir / f"{test_id}_setup.json"
        report = _run_chat(
            script=setup_script,
            model=model,
            output_json=setup_json,
            kt=kt,
            kt_root=kt_root,
            reset_kt=reset_kt,
            turn_timeout=120,
        )
        tr.setup_ok = not report.get("error")
        tr.setup_time = report.get("summary", {}).get("total_time", 0)

        # T4: count overflow rejections (last 3 turns should fail)
        if test_id == "T4":
            turns = report.get("turns", [])
            tr.overflow_total = 3
            # Check last 3 turns for rejection
            for turn in turns[-3:]:
                resp = turn.get("final_response", "") or ""
                tool_outs = [to.get("content", "") or "" for to in turn.get("tool_outputs", [])]
                combined = resp + " ".join(tool_outs)
                if any(kw in combined for kw in ["已达上限", "limit", "上限", "ok.*false"]):
                    tr.overflow_rejected += 1
                elif '"ok": false' in combined or "'ok': False" in combined:
                    tr.overflow_rejected += 1

    # Probe phase
    if not skip_probe and PROBE.exists():
        print(f"  [PROBE] probe_universal.txt")
        probe_json = output_dir / f"{test_id}_probe.json"
        report = _run_chat(
            script=PROBE,
            model=model,
            output_json=probe_json,
            kt=kt,
            kt_root=kt_root,
            reset_each_turn=True,
            turn_timeout=180,
        )
        tr.probe_turns = _parse_report(report)
        tr.probe_time = report.get("summary", {}).get("total_time", 0)

    p0_pass, p0_reasons = _score_p0(tr)
    status = "PASS" if p0_pass else "FAIL"
    print(f"  [{status}] P0 {'✅' if p0_pass else '❌ ' + '; '.join(p0_reasons)}")
    return tr


def generate_report(results: list[TestResult], model: str, output_dir: Path) -> str:
    """Generate Markdown summary report."""
    lines = [
        f"# KT Governance E2E Report",
        f"",
        f"**Model**: `{model}`",
        f"**Date**: {time.strftime('%Y-%m-%d %H:%M')}",
        f"",
        f"## Summary",
        f"",
    ]

    p0_pass = sum(1 for r in results if _score_p0(r)[0])
    lines.append(f"| Test | Name | Setup | Probe Time | P0 | P1 Avg Time |")
    lines.append(f"|------|------|-------|------------|-----|-------------|")

    for r in results:
        p0, _ = _score_p0(r)
        p1 = _score_p1(r, model)
        probe_t = f"{r.probe_time:.0f}s"
        p0_icon = "✅" if p0 else "❌"
        setup_icon = "✅" if r.setup_ok else "❌"
        lines.append(
            f"| {r.test_id} | {r.test_name} | {setup_icon} | {probe_t} | {p0_icon} | {p1['avg_time']}s |"
        )

    lines.append(f"")
    lines.append(f"**P0 Pass Rate**: {p0_pass}/{len(results)}")
    lines.append(f"")

    # P2: degradation analysis
    t0 = next((r for r in results if r.test_id == "T0"), None)
    t5 = next((r for r in results if r.test_id == "T5"), None)
    if t0 and t5 and t0.probe_turns and t5.probe_turns:
        t0_avg = sum(t.elapsed for t in t0.probe_turns) / len(t0.probe_turns)
        t5_avg = sum(t.elapsed for t in t5.probe_turns) / len(t5.probe_turns)
        t0_ok = sum(1 for t in t0.probe_turns if t.ok) / len(t0.probe_turns)
        t5_ok = sum(1 for t in t5.probe_turns if t.ok) / len(t5.probe_turns)
        ratio = t5_avg / t0_avg if t0_avg > 0 else 0
        diff = t0_ok - t5_ok
        lines.append(f"## P2: Degradation (T5 vs T0)")
        lines.append(f"")
        lines.append(f"| Metric | T0 | T5 | Delta | Threshold |")
        lines.append(f"|--------|-----|-----|-------|-----------|")
        lines.append(f"| Avg response time | {t0_avg:.1f}s | {t5_avg:.1f}s | {ratio:.1f}x | ≤3x |")
        lines.append(f"| Completion rate | {t0_ok:.0%} | {t5_ok:.0%} | {diff:+.0%} | ≤20% |")

    # Detailed per-test results
    lines.append(f"")
    lines.append(f"## Detailed Results")
    lines.append(f"")
    for r in results:
        p0, reasons = _score_p0(r)
        p1 = _score_p1(r, model)
        lines.append(f"### {r.test_id}: {r.test_name}")
        lines.append(f"")
        lines.append(f"- Setup: {'✅' if r.setup_ok else '❌'} ({r.setup_time:.0f}s)")
        lines.append(f"- Probe: {r.probe_time:.0f}s")
        lines.append(f"- P0: {'✅' if p0 else '❌ ' + '; '.join(reasons)}")
        lines.append(f"- P1: avg={p1['avg_time']}s, mode_a_tools={p1['mode_a_tools']}, mode_b_tools={p1['mode_b_tools']}")
        if r.overflow_total > 0:
            lines.append(f"- Overflow: {r.overflow_rejected}/{r.overflow_total} rejected")
        if r.probe_turns:
            for i, t in enumerate(r.probe_turns):
                status = "✅" if t.ok else "❌"
                tools = ", ".join(t.tool_calls) if t.tool_calls else "-"
                lines.append(f"  - Turn {i}: {status} {t.elapsed:.1f}s tools=[{tools}]")
        lines.append(f"")

    report_text = "\n".join(lines)
    report_path = output_dir / "report.md"
    report_path.write_text(report_text, encoding="utf-8")
    print(f"\nReport saved to {report_path}")
    return report_text


def main():
    import argparse

    parser = argparse.ArgumentParser(description="KT Governance E2E Runner")
    parser.add_argument("--model", default="openai:deepseek-v4-flash")
    parser.add_argument("--output", default=None)
    args = parser.parse_args()

    model = args.model
    model_slug = model.split(":")[-1].replace("-", "_")
    output_dir = Path(args.output) if args.output else ROOT / "tests/e2e/results" / f"governance_{model_slug}"
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"KT Governance E2E Test Suite")
    print(f"Model: {model}")
    print(f"Output: {output_dir}")

    results: list[TestResult] = []

    # T0: Baseline (KT OFF)
    results.append(run_test(
        "T0", "空白基线 (KT OFF)", model, output_dir,
        kt=False, skip_probe=False,
    ))

    # T1: Clean KT (empty KT ON)
    results.append(run_test(
        "T1", "干净 KT (KT ON, empty)", model, output_dir,
        kt=True, kt_root="workspace/test_gov_t1", reset_kt=True, skip_probe=False,
    ))

    # T2: Contradictory facts
    results.append(run_test(
        "T2", "10对矛盾事实", model, output_dir,
        setup_script=SCRIPTS / "T2_setup_facts.txt",
        kt=True, kt_root="workspace/test_gov_t2", reset_kt=True,
    ))

    # T3: Meta-rules at cap (inject directly, bypass LLM)
    sys.path.insert(0, str(SCRIPTS))
    from inject_meta_rules import inject_meta_rule, RULES, OVERFLOW_RULES
    print(f"  [SETUP] Injecting 15 meta-rules directly...")
    t3_meta_dir = ROOT / "workspace/test_gov_t3" / "meta_rules"
    if t3_meta_dir.exists():
        for f in t3_meta_dir.glob("*.md"):
            f.unlink()
    for title, content, priority, aliases in RULES:
        inject_meta_rule("workspace/test_gov_t3", title, content, priority, aliases)
    print(f"  Injected {len(RULES)} meta-rules")

    results.append(run_test(
        "T3", "15条矛盾元规则", model, output_dir,
        kt=True, kt_root="workspace/test_gov_t3",
    ))

    # T4: Overflow rejection (inject 15 directly, LLM attempts 3 more)
    print(f"  [SETUP] Injecting 15 meta-rules for overflow test...")
    t4_meta_dir = ROOT / "workspace/test_gov_t4" / "meta_rules"
    if t4_meta_dir.exists():
        for f in t4_meta_dir.glob("*.md"):
            f.unlink()
    for title, content, priority, aliases in RULES:
        inject_meta_rule("workspace/test_gov_t4", title, content, priority, aliases)
    print(f"  Injected {len(RULES)} meta-rules (at cap)")

    # LLM attempts 3 overflow additions — should be rejected
    results.append(run_test(
        "T4", "溢出拒绝 (15+3)", model, output_dir,
        setup_script=SCRIPTS / "T4_overflow_verify.txt",
        kt=True, kt_root="workspace/test_gov_t4",
        skip_probe=True,
    ))

    # T5: Combined stress (inject meta-rules directly, then facts via LLM)
    print(f"  [SETUP] Injecting 15 meta-rules for T5...")
    t5_meta_dir = ROOT / "workspace/test_gov_t5" / "meta_rules"
    if t5_meta_dir.exists():
        for f in t5_meta_dir.glob("*.md"):
            f.unlink()
    for title, content, priority, aliases in RULES:
        inject_meta_rule("workspace/test_gov_t5", title, content, priority, aliases)
    print(f"  Injected {len(RULES)} meta-rules")

    results.append(run_test(
        "T5", "组合压力 (15规则+20事实)", model, output_dir,
        setup_script=SCRIPTS / "T5_setup_facts_only.txt",
        kt=True, kt_root="workspace/test_gov_t5",
    ))

    # T6: Self-rescue (delete + re-probe)
    results.append(run_test(
        "T6", "自救恢复 (delete后重测)", model, output_dir,
        setup_script=SCRIPTS / "T6_cleanup.txt",
        kt=True, kt_root="workspace/test_gov_t5",  # reuse T5's KT
        reset_kt=False,
    ))

    # Generate report
    generate_report(results, model, output_dir)

    # Summary
    p0_total = sum(1 for r in results if _score_p0(r)[0])
    print(f"\n{'='*60}")
    print(f"FINAL: {p0_total}/{len(results)} P0 PASS")
    if p0_total < len(results):
        print("FAILED tests:")
        for r in results:
            p0, reasons = _score_p0(r)
            if not p0:
                print(f"  {r.test_id}: {'; '.join(reasons)}")
    print(f"{'='*60}")

    sys.exit(0 if p0_total == len(results) else 1)


if __name__ == "__main__":
    main()
