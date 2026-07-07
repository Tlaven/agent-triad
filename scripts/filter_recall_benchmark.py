"""filter.py recall/precision 离线压测（spec §B8 后续工单）。

目的：用历史 probe session 的真实 Supervisor 输出文本，验证
`should_remember(trigger="task_complete")` 过滤器在生产语料上的实际表现。
找出假阳性（bad turns 文本漏过过滤）和假阴性（good turns 文本被误杀），
暴露 `_INFRA_ERROR_PATTERNS` / `_TEST_TASK_PATTERNS` 等规则的 recall 缺口。

数据源：logs/probes/<date>/session-<NNN>/turns.jsonl（19 session, 174 turn）。

Ground truth 标签（保守，宁缺毋滥）：
  NEGATIVE (不应摄入): verdict in {bad, degrading, error}
                      或 signals 含 {timed_out, run_error, repetition}
                      或 agent 文本以 [timeout / [error / [熔断模板 开头
  POSITIVE (应摄入)  : verdict == good 且 notes 非空
  NEUTRAL (不计入)   : verdict == ok（保守跳过，避免噪音）

输出：
  - TP/FN/FP/TN 矩阵
  - precision / recall
  - 假阳性样本（filter 漏掉的 bad turns，附 reason）—— 暴露规则缺口
  - 假阴性样本（filter 误杀的 good turns，附 reason）—— 暴露规则过严

Usage:
  uv run python scripts/filter_recall_benchmark.py
  uv run python scripts/filter_recall_benchmark.py --strict  # ok 也算 positive
  uv run python scripts/filter_recall_benchmark.py --show-fp 10  # 显示前 10 个 FP
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# ruff: noqa: T201, D103, D101, D415, D401 — 脚本工具放行 print / docstring 规则

PROBES_DIR = Path(__file__).resolve().parent.parent / "logs" / "probes"

NEGATIVE_VERDICTS = {"bad", "degrading", "error"}
NEGATIVE_SIGNALS = {"timed_out", "run_error", "repetition"}
NEGATIVE_PREFIX_RE = re.compile(r"^\[(timeout|error|熔断模板)")


@dataclass
class Turn:
    session: str
    turn: int
    verdict: str
    signals: list[str]
    agent: str
    notes: str
    label: str  # "POSITIVE" / "NEGATIVE" / "NEUTRAL"


def load_turns() -> list[Turn]:
    turns: list[Turn] = []
    for f in sorted(PROBES_DIR.rglob("*.jsonl")):
        for line in f.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                t = json.loads(line)
            except Exception:
                continue
            verdict = t.get("verdict", "?")
            signals = t.get("signals", []) or []
            agent = t.get("agent", "") or ""
            notes = t.get("notes", "") or ""
            label = label_of(verdict, signals, agent)
            turns.append(
                Turn(
                    session=f.parent.name,
                    turn=t.get("turn", 0),
                    verdict=verdict,
                    signals=signals,
                    agent=agent,
                    notes=notes,
                    label=label,
                )
            )
    return turns


def label_of(verdict: str, signals: list[str], agent: str) -> str:
    if verdict in NEGATIVE_VERDICTS:
        return "NEGATIVE"
    if any(s in NEGATIVE_SIGNALS for s in signals):
        return "NEGATIVE"
    if NEGATIVE_PREFIX_RE.match(agent):
        return "NEGATIVE"
    if verdict == "good":
        return "POSITIVE"
    return "NEUTRAL"  # ok / unknown


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strict", action="store_true", help="ok 也算 POSITIVE")
    parser.add_argument("--show-fp", type=int, default=10, help="显示前 N 个假阳性样本")
    parser.add_argument("--show-fn", type=int, default=5, help="显示前 N 个假阴性样本")
    args = parser.parse_args()

    from src.common.knowledge_tree.ingestion.filter import should_remember

    turns = load_turns()
    print(f"\n[filter recall/precision benchmark] 加载 {len(turns)} turns")

    if args.strict:
        for t in turns:
            if t.label == "NEUTRAL":
                t.label = "POSITIVE"
        print("  --strict 模式：ok 视为 POSITIVE")

    label_counts = Counter(t.label for t in turns)
    print(f"  ground truth: {dict(label_counts)}")

    # 对每 turn 的 agent 文本跑 should_remember(trigger=task_complete)
    tp = fn = fp = tn = 0
    fp_samples: list[tuple[Turn, str, str]] = []  # (turn, filter_reason, agent_preview)
    fn_samples: list[tuple[Turn, str, str]] = []
    reject_reasons: Counter[str] = Counter()
    pass_reasons: Counter[str] = Counter()

    for t in turns:
        if t.label == "NEUTRAL":
            continue
        result = should_remember(t.agent, trigger="task_complete")
        if result.passed:
            pass_reasons[result.reason] += 1
        else:
            reject_reasons[result.reason] += 1

        if t.label == "POSITIVE":
            if result.passed:
                tp += 1
            else:
                fn += 1
                if len(fn_samples) < args.show_fn:
                    fn_samples.append((t, result.reason, t.agent[:120]))
        else:  # NEGATIVE
            if result.passed:
                fp += 1
                if len(fp_samples) < args.show_fp:
                    fp_samples.append((t, result.reason, t.agent[:200]))
            else:
                tn += 1

    precision = tp / (tp + fp) if (tp + fp) > 0 else float("nan")
    recall = tp / (tp + fn) if (tp + fn) > 0 else float("nan")
    f1 = (
        2 * precision * recall / (precision + recall)
        if (precision + recall) > 0
        else float("nan")
    )

    print()
    print(f"  {'':>10} | {'PRED=PASS':>10} | {'PRED=REJECT':>11} | {'total':>6}")
    print("  " + "-" * 50)
    print(f"  {'POSITIVE':>10} | {tp:>10} | {fn:>11} | {tp + fn:>6}")
    print(f"  {'NEGATIVE':>10} | {fp:>10} | {tn:>11} | {fp + tn:>6}")
    print()
    print(
        f"  precision = {precision:.3f}   recall = {recall:.3f}   F1 = {f1:.3f}"
    )

    print()
    print("  通过原因分布（PRED=PASS）:")
    for r, c in pass_reasons.most_common():
        print(f"    {r:<35} {c:>4}")

    print()
    print("  拒绝原因分布（PRED=REJECT）:")
    for r, c in reject_reasons.most_common():
        print(f"    {r:<35} {c:>4}")

    if fp_samples:
        print()
        print("  == 假阳性（NEGATIVE 但 filter.passed=True）— filter 漏掉的 bad turns ==")
        for t, reason, preview in fp_samples:
            print(
                f"  [{t.session} t{t.turn}] verdict={t.verdict} signals={t.signals}\n"
                f"    pass_reason={reason}\n"
                f"    agent={preview!r}"
            )

    if fn_samples:
        print()
        print("  == 假阴性（POSITIVE 但 filter.passed=False）— filter 误杀的 good turns ==")
        for t, reason, preview in fn_samples:
            print(
                f"  [{t.session} t{t.turn}] notes={t.notes!r}\n"
                f"    reject_reason={reason}\n"
                f"    agent={preview!r}"
            )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
