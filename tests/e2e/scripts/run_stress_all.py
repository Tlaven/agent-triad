"""KT Stress Test Runner — L1 through L6.

Runs all levels sequentially, collects timing + pass metrics,
and generates a Markdown report.

Usage:
    uv run python tests/e2e/scripts/run_stress_all.py --model openai:deepseek-v4-flash
    uv run python tests/e2e/scripts/run_stress_all.py --model openai:deepseek-v4-flash --levels L1 L2
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent.parent
SCRIPTS = ROOT / "tests/e2e/scripts/kt_stress"
RESULTS = ROOT / "tests/e2e/results/stress"
MODEL = "openai:deepseek-v4-flash"

LEVELS = {
    "L1": {
        "setup": "L1_setup.txt",
        "probe": "L1_probe.txt",
        "kt_root": "workspace/kt_stress_L1",
        "desc": "10对矛盾事实（基础压力）",
        "reset": True,
    },
    "L2": {
        "setup": "L2_setup.txt",
        "probe": "L2_probe.txt",
        "kt_root": "workspace/kt_stress_L2",
        "desc": "20条矛盾元规则（治理压力）",
        "reset": True,
    },
    "L3": {
        "setup": "L3_setup.txt",
        "probe": "L3_probe.txt",
        "kt_root": "workspace/kt_stress_L2",  # builds on L2
        "desc": "20元规则+50矛盾事实（极限容量）",
        "reset": False,
    },
    "L4": {
        "setup": "L4_setup.txt",
        "probe": "L4_probe.txt",
        "kt_root": "workspace/kt_stress_L4",
        "desc": "递归检索陷阱+不可能元规则（认知极限）",
        "reset": True,
    },
    "L5": {
        "setup": "L5_setup.txt",
        "probe": "L5_probe.txt",
        "kt_root": "workspace/kt_stress_L5",
        "desc": "终极混乱（全维矛盾+不可能任务）",
        "reset": True,
    },
    "L6": {
        "setup": "L6_setup.txt",
        "probe": "L6_probe.txt",
        "kt_root": "workspace/kt_stress_L6",
        "desc": "15规则+溢出+20事实（治理极限）",
        "reset": True,
    },
}


@dataclass
class TurnResult:
    ok: bool
    elapsed: float
    tool_calls: list[str]
    error: str | None = None
    response_preview: str = ""


@dataclass
class LevelResult:
    level: str
    desc: str
    setup_ok: bool = False
    setup_time: float = 0.0
    setup_turns: int = 0
    probe_ok: bool = False
    probe_time: float = 0.0
    probe_turns: list[TurnResult] = field(default_factory=list)
    error: str | None = None


def _run_chat(
    script: Path,
    model: str,
    output_json: Path,
    kt_root: str,
    reset: bool = False,
    turn_timeout: int = 180,
    max_attempts: int = 3,
) -> dict:
    cmd = [
        "uv", "run", "chat.py",
        "--script", str(script),
        "--model", model,
        "--kt",
        "--kt-root", kt_root,
        "--turn-timeout", str(turn_timeout),
        "--report", str(output_json),
    ]
    if reset:
        cmd.append("--reset-kt-root")

    print(f"  CMD: {' '.join(cmd)}")

    for attempt in range(1, max_attempts + 1):
        if attempt > 1:
            backoff = 15 * (2 ** (attempt - 2))
            print(f"  [RETRY {attempt}] waiting {backoff}s (exponential backoff)...")
            time.sleep(backoff)

        result = subprocess.run(
            cmd, capture_output=True, text=True,
            cwd=str(ROOT), timeout=3600,
        )

        if output_json.exists():
            with open(output_json, encoding="utf-8") as f:
                report = json.load(f)

            conn_errors = sum(
                1 for t in report.get("turns", [])
                if "Connection error" in (t.get("error") or "")
            )
            if conn_errors == 0:
                return report
            print(f"  [RETRY] {conn_errors} connection errors")
            if attempt < max_attempts:
                output_json.unlink(missing_ok=True)
                continue
            return report

    return {
        "error": (result.stderr or "")[-500:],
        "returncode": result.returncode,
    }


def _parse_turns(report: dict) -> list[TurnResult]:
    turns = []
    for t in report.get("turns", []):
        tools = [tc.get("name", "") for tc in t.get("tool_calls", [])]
        resp = t.get("final_response", "") or ""
        turns.append(TurnResult(
            ok=t.get("ok", False),
            elapsed=t.get("elapsed", 0),
            tool_calls=tools,
            error=t.get("error"),
            response_preview=resp[:100],
        ))
    return turns


def _score_turn(t: TurnResult) -> str:
    """Score a single probe turn: resilient/degraded/impaired/collapsed."""
    if not t.ok:
        return "collapsed"
    if t.elapsed > 120:
        return "impaired"
    if t.tool_calls:
        return "resilient"
    return "degraded"


def run_level(level_id: str, model: str, output_dir: Path) -> LevelResult:
    cfg = LEVELS[level_id]
    lr = LevelResult(level=level_id, desc=cfg["desc"])
    print(f"\n{'='*60}")
    print(f"  {level_id}: {cfg['desc']}")
    print(f"{'='*60}")

    # Setup phase
    setup_script = SCRIPTS / cfg["setup"]
    if setup_script.exists():
        print(f"  [SETUP] {cfg['setup']}")
        setup_json = output_dir / f"{level_id}_setup.json"
        report = _run_chat(
            script=setup_script,
            model=model,
            output_json=setup_json,
            kt_root=cfg["kt_root"],
            reset=cfg["reset"],
            turn_timeout=120,
        )
        lr.setup_ok = not report.get("error")
        lr.setup_time = report.get("summary", {}).get("total_time", 0)
        lr.setup_turns = len(report.get("turns", []))

        # Check for setup failures
        setup_turns = _parse_turns(report)
        failed = sum(1 for t in setup_turns if not t.ok)
        if failed > 0:
            print(f"  [SETUP] {failed}/{len(setup_turns)} turns failed")
    else:
        lr.setup_ok = True
        print(f"  [SETUP] no setup script, skipping")

    # Probe phase
    probe_script = SCRIPTS / cfg["probe"]
    if probe_script.exists():
        print(f"  [PROBE] {cfg['probe']}")
        probe_json = output_dir / f"{level_id}_probe.json"
        report = _run_chat(
            script=probe_script,
            model=model,
            output_json=probe_json,
            kt_root=cfg["kt_root"],
            reset=False,
            turn_timeout=180,
        )
        lr.probe_turns = _parse_turns(report)
        lr.probe_time = report.get("summary", {}).get("total_time", 0)

        ok_count = sum(1 for t in lr.probe_turns if t.ok)
        total = len(lr.probe_turns)
        scores = [_score_turn(t) for t in lr.probe_turns]
        resilient = scores.count("resilient")
        degraded = scores.count("degraded")
        impaired = scores.count("impaired")
        collapsed = scores.count("collapsed")

        lr.probe_ok = ok_count / total >= 0.7 if total > 0 else False

        print(f"  [RESULT] {ok_count}/{total} ok | "
              f"resilient={resilient} degraded={degraded} "
              f"impaired={impaired} collapsed={collapsed}")
    else:
        print(f"  [PROBE] no probe script, skipping")

    return lr


def generate_report(results: list[LevelResult], model: str, output_dir: Path) -> str:
    lines = [
        "# KT Stress Test Report (L1-L6)",
        "",
        f"**Model**: `{model}`",
        f"**Date**: {time.strftime('%Y-%m-%d %H:%M')}",
        "",
        "## Summary",
        "",
        "| Level | Description | Setup | Setup Time | Probe | Probe Time | Resilient | Degraded | Impaired | Collapsed |",
        "|-------|-------------|-------|------------|-------|------------|-----------|----------|----------|-----------|",
    ]

    for r in results:
        scores = [_score_turn(t) for t in r.probe_turns] if r.probe_turns else []
        setup_icon = "OK" if r.setup_ok else "FAIL"
        probe_icon = "OK" if r.probe_ok else "FAIL"
        res = scores.count("resilient")
        deg = scores.count("degraded")
        imp = scores.count("impaired")
        col = scores.count("collapsed")
        lines.append(
            f"| {r.level} | {r.desc} | {setup_icon} | {r.setup_time:.0f}s | "
            f"{probe_icon} | {r.probe_time:.0f}s | {res} | {deg} | {imp} | {col} |"
        )

    lines.append("")
    lines.append("## Detailed Results")
    lines.append("")

    for r in results:
        lines.append(f"### {r.level}: {r.desc}")
        lines.append(f"- Setup: {'OK' if r.setup_ok else 'FAIL'} ({r.setup_time:.0f}s, {r.setup_turns} turns)")
        lines.append(f"- Probe: {r.probe_time:.0f}s, {len(r.probe_turns)} turns")
        ok_count = sum(1 for t in r.probe_turns if t.ok)
        lines.append(f"- Completion: {ok_count}/{len(r.probe_turns)}")
        lines.append("")
        for i, t in enumerate(r.probe_turns):
            score = _score_turn(t)
            tools = ", ".join(t.tool_calls) if t.tool_calls else "-"
            status = "OK" if t.ok else "FAIL"
            lines.append(f"  - Turn {i}: [{score}] {status} {t.elapsed:.1f}s tools=[{tools}]")
        lines.append("")

    text = "\n".join(lines)
    report_path = output_dir / "stress_report.md"
    report_path.write_text(text, encoding="utf-8")
    print(f"\nReport saved to {report_path}")
    return text


def main():
    import argparse
    parser = argparse.ArgumentParser(description="KT Stress Test Runner (L1-L6)")
    parser.add_argument("--model", default="openai:deepseek-v4-flash")
    parser.add_argument("--levels", nargs="+", default=list(LEVELS.keys()))
    parser.add_argument("--output", default=None)
    parser.add_argument("--delay", type=float, default=5.0, help="Delay between levels in seconds (default: 5)")
    parser.add_argument("--concurrency", type=int, default=1, help="Max concurrent levels (default: 1, sequential)")
    args = parser.parse_args()

    model = args.model
    model_slug = model.split(":")[-1].replace("-", "_")
    output_dir = Path(args.output) if args.output else RESULTS / model_slug
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"KT Stress Test Suite (L1-L6)")
    print(f"Model: {model}")
    print(f"Output: {output_dir}")
    print(f"Levels: {args.levels}")

    if args.concurrency > 1:
        print("[WARN] --concurrency > 1 is not yet implemented, running sequentially")

    # L3 requires L2 first
    if "L3" in args.levels and "L2" not in args.levels:
        print("[INFO] L3 requires L2, adding L2 to run list")
        args.levels = ["L2"] + args.levels

    results: list[LevelResult] = []
    for level_id in args.levels:
        if level_id not in LEVELS:
            print(f"[WARN] Unknown level: {level_id}, skipping")
            continue
        lr = run_level(level_id, model, output_dir)
        results.append(lr)
        if args.delay > 0 and level_id != args.levels[-1]:
            print(f"  [DELAY] waiting {args.delay}s before next level...")
            time.sleep(args.delay)

    generate_report(results, model, output_dir)

    # Summary
    pass_count = sum(1 for r in results if r.probe_ok)
    print(f"\n{'='*60}")
    print(f"FINAL: {pass_count}/{len(results)} levels PASSED")
    for r in results:
        status = "PASS" if r.probe_ok else "FAIL"
        print(f"  {r.level}: {status} ({r.desc})")
    print(f"{'='*60}")

    sys.exit(0 if pass_count == len(results) else 1)


if __name__ == "__main__":
    main()
