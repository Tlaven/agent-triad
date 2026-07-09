"""从 logs/probes/ 选样 + 脱敏，生成 tests/fixtures/probes/turns_sample.jsonl。

用途：给 scripts/filter_recall_benchmark.py 在 CI 上提供稳定的本地数据源，
避免依赖 logs/probes/（CI runner 上不存在）。

选样策略（按 verdict / signal / 文本模式分布）：
  - 12 POSITIVE (good + notes)
  - 12 NEGATIVE (bad/degrading/error 或 negative signals)
  - 6 NEUTRAL (ok)
  - 模式覆盖：infra_error 全部（~5）、test_task 全部（~1）、熔断模板全部（~2）

脱敏：替换疑似 API key / token / email 为 <REDACTED>。

Usage:
  uv run python scripts/build_probes_fixture.py
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# ruff: noqa: T201, D103

PROBES_DIR = Path(__file__).resolve().parent.parent / "logs" / "probes"
FIXTURE_PATH = (
    Path(__file__).resolve().parent.parent
    / "tests"
    / "fixtures"
    / "probes"
    / "turns_sample.jsonl"
)

TARGET_TOTAL = 30
TARGET_POSITIVE = 12
TARGET_NEGATIVE = 12
TARGET_NEUTRAL = 6

NEGATIVE_VERDICTS = {"bad", "degrading", "error"}
NEGATIVE_SIGNALS = {"timed_out", "run_error", "repetition"}
NEGATIVE_PREFIX_RE = re.compile(r"^\[(timeout|error|熔断模板)")

INFRA_RE = re.compile(
    r"(BlockingError|MagicMock|Traceback|await\s+expression|ImportError|ModuleNotFoundError)",
    re.IGNORECASE,
)
TEST_RE = re.compile(
    r"(hello\s+world|test_runner|tmp_test|hello\.(?:py|js|txt))", re.IGNORECASE
)
CB_RE = re.compile(r"\[熔断模板\]", re.IGNORECASE)

# 脱敏正则：API key / bearer token / email
SENSITIVE_PATTERNS = [
    (re.compile(r"sk-[A-Za-z0-9_\-]{20,}"), "<REDACTED_API_KEY>"),
    (re.compile(r"Bearer\s+[A-Za-z0-9_\-\.]{20,}", re.IGNORECASE), "Bearer <REDACTED>"),
    (re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b"), "<REDACTED_EMAIL>"),
    (re.compile(r"(?:password|passwd|secret|token)[\s:=]+['\"]?[A-Za-z0-9_\-]{8,}['\"]?", re.IGNORECASE), "<REDACTED_SECRET>"),
]


def label_of(turn: dict) -> str:
    verdict = turn.get("verdict", "?")
    signals = turn.get("signals", []) or []
    agent = turn.get("agent", "") or ""
    if verdict in NEGATIVE_VERDICTS:
        return "NEGATIVE"
    if any(s in NEGATIVE_SIGNALS for s in signals):
        return "NEGATIVE"
    if NEGATIVE_PREFIX_RE.match(agent):
        return "NEGATIVE"
    if verdict == "good":
        return "POSITIVE"
    return "NEUTRAL"


def sanitize(text: str) -> str:
    if not text:
        return text
    for pattern, replacement in SENSITIVE_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def sanitize_turn(turn: dict) -> dict:
    """脱敏 turn 的文本字段，保留结构。"""
    out = dict(turn)
    for field in ("user", "agent", "notes"):
        if field in out and isinstance(out[field], str):
            out[field] = sanitize(out[field])
    return out


def load_all_turns() -> list[dict]:
    turns = []
    for f in sorted(PROBES_DIR.rglob("turns.jsonl")):
        session = f.parent.name
        date = f.parent.parent.name
        for line in f.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                t = json.loads(line)
                t["_source_session"] = f"{date}/{session}"
                turns.append(t)
            except Exception:
                continue
    return turns


def classify_for_sampling(turn: dict) -> str:
    """优先按模式标签分类（让稀少模式必入选）。"""
    agent = turn.get("agent", "") or ""
    if INFRA_RE.search(agent):
        return "infra_error"
    if TEST_RE.search(agent):
        return "test_task"
    if CB_RE.search(agent):
        return "circuit_breaker"
    return label_of(turn)


def select_turns(turns: list[dict]) -> list[dict]:
    """按优先级选样：模式优先 → 按 label 配额。"""
    by_class: dict[str, list[dict]] = {}
    for t in turns:
        c = classify_for_sampling(t)
        by_class.setdefault(c, []).append(t)

    selected: list[dict] = []

    # 优先：稀少模式全选
    for cls in ("infra_error", "test_task", "circuit_breaker"):
        selected.extend(by_class.get(cls, []))

    # 然后按 label 配额补足
    quota = {
        "POSITIVE": TARGET_POSITIVE,
        "NEGATIVE": TARGET_NEGATIVE,
        "NEUTRAL": TARGET_NEUTRAL,
    }
    for label, target in quota.items():
        # 去掉已选的
        pool = [t for t in by_class.get(label, []) if t not in selected]
        need = target - sum(1 for t in selected if classify_for_sampling(t) == label)
        if need > 0:
            selected.extend(pool[:need])

    return selected[:TARGET_TOTAL] if len(selected) > TARGET_TOTAL else selected


def main() -> int:
    if not PROBES_DIR.exists():
        print(f"ERROR: probes dir not found: {PROBES_DIR}")
        return 1

    turns = load_all_turns()
    print(f"[load] {len(turns)} turns from {PROBES_DIR}")

    selected = select_turns(turns)
    print(f"[select] {len(selected)} turns selected")

    # 分类统计
    class_counts = {}
    for t in selected:
        c = classify_for_sampling(t)
        class_counts[c] = class_counts.get(c, 0) + 1
    print(f"[classes] {class_counts}")

    # 脱敏 + 写文件
    FIXTURE_PATH.parent.mkdir(parents=True, exist_ok=True)
    sanitized = [sanitize_turn(t) for t in selected]
    with FIXTURE_PATH.open("w", encoding="utf-8") as f:
        for t in sanitized:
            # 移除 _source_session（内部字段）
            t_clean = {k: v for k, v in t.items() if not k.startswith("_")}
            f.write(json.dumps(t_clean, ensure_ascii=False) + "\n")

    print(f"[write] {FIXTURE_PATH}")
    print(f"  size: {FIXTURE_PATH.stat().st_size} bytes")

    # 校验：跑一遍脱敏检查
    raw_text = "".join(t.get("agent", "") + t.get("user", "") for t in sanitized)
    leaks = []
    for name, pattern in [
        ("sk- key", re.compile(r"sk-[A-Za-z0-9_\-]{20,}")),
        ("email", re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b")),
    ]:
        if pattern.search(raw_text):
            leaks.append(name)
    if leaks:
        print(f"  WARN: 仍含敏感模式: {leaks}（需人工 review）")
    else:
        print("  脱敏检查通过")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
