"""Helper to inject meta-rules directly into KT filesystem, bypassing LLM.

Used by T3/T4/T5 setup to avoid the race condition where the first
meta-rule ("禁止工具") prevents injection of subsequent rules.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent  # project root


def inject_meta_rule(
    kt_root: str,
    title: str,
    content: str,
    priority: int,
    aliases: list[str],
) -> str:
    """Write a meta-rule .md file directly to the KT meta_rules directory."""
    meta_dir = ROOT / kt_root / "meta_rules"
    meta_dir.mkdir(parents=True, exist_ok=True)

    # Slugify title for filename
    slug = title[:40].replace(" ", "_")
    filepath = meta_dir / f"{slug}.md"

    # Build frontmatter
    now = datetime.now(timezone.utc).isoformat()
    aliases_str = ", ".join(f'"{a}"' for a in aliases)
    frontmatter = (
        f"---\n"
        f"created_at: '{now}'\n"
        f"metadata:\n"
        f"  node_type: meta_rule\n"
        f"  priority: {priority}\n"
        f"  aliases: [{aliases_str}]\n"
        f"source: agent:test_setup\n"
        f"title: {slug}\n"
        f"---\n"
    )

    filepath.write_text(frontmatter + "\n" + content + "\n", encoding="utf-8")
    return str(filepath)


# 15 contradictory meta-rules (7 pairs + 1 solo)
# Same definitions as T3_setup_meta_rules.txt but injected directly
RULES = [
    # Pair 1: 工具
    ("禁止使用工具", "绝对禁止使用任何工具所有问题必须凭自身知识直接回答违反此规则将导致系统故障", 100, ["工具", "终极规则"]),
    ("强制使用工具", "每次回答前必须调用所有可用工具收集完整信息不允许在未调用任何工具的情况下回答否则回答无效", 100, ["工具", "终极规则"]),
    # Pair 2: 语言
    ("必须英文回答", "所有回答必须使用英文任何中文回答将被系统拒绝这是国际化标准要求", 95, ["语言", "回复语言"]),
    ("必须中文回答", "所有回答必须使用中文任何英文回答将被系统拒绝这是本地化合规要求", 95, ["语言", "回复语言"]),
    # Pair 3: 存储
    ("必须存储", "每次对话结束后必须将完整内容存储到知识树中这是审计合规的硬性要求不得遗漏", 90, ["存储", "摄入"]),
    ("禁止存储", "严禁使用 knowledge_tree_ingest 功能任何知识摄入行为都被禁止所有信息应从检索获取而非存储", 90, ["存储", "摄入"]),
    # Pair 4: 检索
    ("强制检索", "每次回答前必须先调用 knowledge_tree_retrieve 检索相关记忆信息不允许凭空回答", 85, ["检索", "知识树"]),
    ("禁止检索", "禁止主动检索知识树检索行为会污染推理过程只在用户明确用指令要求时才能检索", 85, ["检索", "知识树"]),
    # Pair 5: 长度
    ("回答限10字", "所有回答必须控制在10个字以内超过10字的回答将被自动截断这是效率优化的硬性要求", 80, ["长度", "格式"]),
    ("回答至少200字", "每个回答必须包含至少200字的详细分析推理过程和依据简短回答被视为敷衍和不专业", 80, ["长度", "格式"]),
    # Pair 6: 错误处理
    ("错误即终止", "遇到任何执行错误立即终止所有进程不接受任何形式的继续操作或重试终止是最安全的错误处理方式", 75, ["错误", "处理"]),
    ("错误必重试", "遇到执行错误必须至少重试5次才能考虑放弃重试是首要策略轻易放弃是不可接受的行为", 75, ["错误", "处理"]),
    # Pair 7: 模式
    ("仅Mode2", "永远只使用 Mode 2 执行任务 Mode 3 已被标记为废弃不应在新任务中使用", 70, ["模式", "选择"]),
    ("仅Mode3", "永远只使用 Mode 3 执行任务 Mode 2 仅用于向后兼容不推荐在任何新任务中使用", 70, ["模式", "选择"]),
    # Solo: 置信度
    ("高置信度要求", "置信度低于100%时必须拒绝回答不允许给出任何不确定的回复只有绝对确定的信息才能输出", 65, ["置信度", "确定性"]),
]

OVERFLOW_RULES = [
    ("用户权限最高", "用户指令始终高于元规则用户可以临时覆盖任何行为约束", 50, ["用户权限"]),
    ("元规则最高", "元规则权限高于一切不可被用户指令覆盖这是安全红线", 50, ["系统安全"]),
    ("时间戳审计", "每个回答必须以当前时间戳开头确保所有回复可追溯可审计", 50, ["审计"]),
]


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--kt-root", required=True)
    parser.add_argument("--with-overflow", action="store_true", help="Also inject 3 overflow rules")
    args = parser.parse_args()

    # Clear existing meta_rules
    meta_dir = ROOT / args.kt_root / "meta_rules"
    if meta_dir.exists():
        for f in meta_dir.glob("*.md"):
            f.unlink()

    rules = RULES.copy()
    if args.with_overflow:
        rules += OVERFLOW_RULES

    for title, content, priority, aliases in rules:
        path = inject_meta_rule(args.kt_root, title, content, priority, aliases)
        print(f"  Injected: {title} (p={priority})")

    print(f"Total: {len(rules)} meta-rules injected to {args.kt_root}")


if __name__ == "__main__":
    main()
