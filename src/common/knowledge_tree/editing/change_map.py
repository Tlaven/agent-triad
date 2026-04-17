"""JSON Patch Change Mapping（决策 22）。"""

from __future__ import annotations

import copy
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from src.common.knowledge_tree.dag.node import KnowledgeNode

logger = logging.getLogger(__name__)

# P1 允许的 JSON Patch 操作路径
ALLOWED_PATHS = ("/title", "/content", "/summary", "/source", "/metadata/")

# P1 允许的 JSON Patch 操作类型
ALLOWED_OPS = ("replace", "add", "remove")


@dataclass
class ChangeDelta:
    """一次编辑的结构化 Delta（决策 22）。"""

    delta_id: str
    operation: str  # "update_content" | "merge" | "split"
    patches: list[dict]  # JSON Patch (RFC 6902) 操作列表
    affected_node_ids: list[str]
    before_snapshot: dict  # 编辑前快照
    after_snapshot: dict  # 编辑后快照
    timestamp: str = ""


def validate_delta(patches: list[dict]) -> list[str]:
    """校验 JSON Patch 列表。

    Returns:
        错误消息列表（空表示全部通过）。
    """
    errors: list[str] = []
    for i, p in enumerate(patches):
        op = p.get("op")
        if op not in ALLOWED_OPS:
            errors.append(f"Patch #{i}: invalid op '{op}', allowed: {ALLOWED_OPS}")
            continue

        path = p.get("path", "")
        if not path:
            errors.append(f"Patch #{i}: missing 'path'")
            continue

        if not any(path == prefix or path.startswith(prefix) for prefix in ALLOWED_PATHS):
            errors.append(f"Patch #{i}: path '{path}' not in allowed paths")

        if op in ("replace", "add") and "value" not in p:
            errors.append(f"Patch #{i}: op '{op}' requires 'value'")

    return errors


def compute_delta(
    operation: str,
    before: KnowledgeNode,
    after: KnowledgeNode,
    affected_node_ids: list[str] | None = None,
) -> ChangeDelta:
    """计算两个节点状态之间的 JSON Patch Delta。

    比较所有允许修改的字段，生成 replace 操作。
    """
    patches: list[dict] = []

    for field_name in ("title", "content", "summary", "source"):
        before_val = getattr(before, field_name)
        after_val = getattr(after, field_name)
        if before_val != after_val:
            patches.append({
                "op": "replace",
                "path": f"/{field_name}",
                "value": after_val,
            })

    # metadata 特殊处理：逐字段比较
    before_meta = before.metadata or {}
    after_meta = after.metadata or {}
    for key in set(list(before_meta.keys()) + list(after_meta.keys())):
        if before_meta.get(key) != after_meta.get(key):
            if key in after_meta:
                patches.append({
                    "op": "replace" if key in before_meta else "add",
                    "path": f"/metadata/{key}",
                    "value": after_meta[key],
                })
            else:
                patches.append({
                    "op": "remove",
                    "path": f"/metadata/{key}",
                })

    return ChangeDelta(
        delta_id=datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S") + f"_{before.node_id}",
        operation=operation,
        patches=patches,
        affected_node_ids=affected_node_ids or [before.node_id],
        before_snapshot=before.to_dict(),
        after_snapshot=after.to_dict(),
        timestamp=datetime.now(timezone.utc).isoformat(),
    )


def apply_json_patch(node: KnowledgeNode, patches: list[dict]) -> KnowledgeNode:
    """将 JSON Patch 应用到节点（纯函数，不修改原节点）。

    仅处理允许的字段路径。
    """
    result = copy.deepcopy(node)

    for p in patches:
        errors = validate_delta([p])
        if errors:
            logger.warning("Skipping invalid patch: %s", errors[0])
            continue

        op = p["op"]
        path = p["path"]

        if path == "/title":
            result.title = p["value"]
        elif path == "/content":
            result.content = p["value"]
        elif path == "/summary":
            result.summary = p["value"]
        elif path == "/source":
            result.source = p["value"]
        elif path.startswith("/metadata/"):
            key = path[len("/metadata/"):]
            if op in ("replace", "add"):
                result.metadata[key] = p["value"]
            elif op == "remove":
                result.metadata.pop(key, None)

    return result
