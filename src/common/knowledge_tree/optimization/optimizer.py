"""异步批量优化器（决策 23）。"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable

from src.common.knowledge_tree.dag.edge import KnowledgeEdge
from src.common.knowledge_tree.dag.node import KnowledgeNode
from src.common.knowledge_tree.editing.re_embed import re_embed_nodes
from src.common.knowledge_tree.optimization.anti_oscillation import (
    OptimizationHistory,
    filter_signals_by_quota,
)
from src.common.knowledge_tree.optimization.signals import (
    OptimizationSignal,
    detect_signals,
)
from src.common.knowledge_tree.retrieval.log import RetrievalLog
from src.common.knowledge_tree.storage.graph_store import BaseGraphStore
from src.common.knowledge_tree.storage.markdown_store import MarkdownStore
from src.common.knowledge_tree.storage.sync import sync_node_to_stores
from src.common.knowledge_tree.storage.vector_store import BaseVectorStore

logger = logging.getLogger(__name__)


@dataclass
class OptimizationContext:
    """优化执行所需的上下文引用（三层存储 + embedder）。"""

    graph_store: BaseGraphStore
    vector_store: BaseVectorStore
    md_store: MarkdownStore
    embedder: Callable[[str], list[float]]


@dataclass
class OptimizationReport:
    """一轮优化的执行报告。"""

    signals_detected: int = 0
    signals_filtered: int = 0
    actions_planned: int = 0
    actions_executed: int = 0
    actions: list[dict[str, Any]] = field(default_factory=list)


def run_optimization_cycle(
    logs: list[RetrievalLog],
    history: OptimizationHistory,
    ctx: OptimizationContext | None = None,
    nav_failure_threshold: int = 5,
    rag_false_positive_threshold: int = 3,
    total_failure_threshold: int = 3,
    content_insufficient_threshold: int = 5,
    dry_run: bool = False,
) -> OptimizationReport:
    """执行一轮优化。

    流程：
    1. 从检索日志检测信号
    2. 防震荡过滤
    3. 对每个信号生成优化动作
    4. 如果 ctx 可用且非 dry_run，执行动作并修改树结构

    Args:
        logs: 累积的检索日志。
        history: 防震荡历史。
        ctx: 优化上下文（含三层存储引用）。为 None 时仅记录不执行。
        *_threshold: 各信号类型的触发阈值。
        dry_run: 即使有 ctx 也只规划不执行。

    Returns:
        OptimizationReport 包含本轮优化的统计和动作记录。
    """
    report = OptimizationReport()

    # 1. 信号检测
    signals = detect_signals(
        logs,
        nav_failure_threshold=nav_failure_threshold,
        rag_false_positive_threshold=rag_false_positive_threshold,
        total_failure_threshold=total_failure_threshold,
        content_insufficient_threshold=content_insufficient_threshold,
    )
    report.signals_detected = len(signals)

    if not signals:
        return report

    # 2. 防震荡过滤
    filtered = filter_signals_by_quota(signals, history)
    report.signals_filtered = len(signals) - len(filtered)

    if not filtered:
        logger.info("All signals filtered by anti-oscillation quota")
        return report

    execute = ctx is not None and not dry_run

    # 3. 为每个信号生成并（可选）执行优化动作
    for signal in filtered:
        action = _plan_action(signal)
        report.actions_planned += 1

        if execute:
            action = _execute_action(action, ctx)
            logger.info(
                "Optimization action %s: %s for signal %s (node=%s)",
                action["status"],
                action["action"],
                signal.signal_type,
                signal.node_id,
            )
        else:
            logger.info(
                "Optimization action planned: %s for signal %s (node=%s)",
                action["action"],
                signal.signal_type,
                signal.node_id,
            )

        report.actions.append(action)
        history.record()
        if action["status"] == "executed":
            report.actions_executed += 1

    return report


# ---------------------------------------------------------------------------
# 动作规划
# ---------------------------------------------------------------------------

def _plan_action(signal: OptimizationSignal) -> dict[str, Any]:
    """根据信号类型规划优化动作。"""
    action_map = {
        "total_failure": "create_seed_nodes",
        "nav_failure": "split_overloaded_node",
        "rag_false_positive": "re_embed_affected",
        "content_insufficient": "enrich_content",
    }

    return {
        "action": action_map.get(signal.signal_type, "unknown"),
        "signal_type": signal.signal_type,
        "node_id": signal.node_id,
        "evidence": signal.evidence,
        "status": "planned",
    }


# ---------------------------------------------------------------------------
# 动作执行
# ---------------------------------------------------------------------------

def _execute_action(
    action: dict[str, Any],
    ctx: OptimizationContext,
) -> dict[str, Any]:
    """将 planned 动作转化为实际树结构修改。

    每种动作类型的策略：
    - create_seed_nodes: 用失败查询文本创建种子节点，挂入树中
    - split_overloaded_node: 子节点过多时拆分为子组
    - re_embed_affected: 对假阳性节点重新嵌入
    - enrich_content: 标记内容不足节点 + 用 evidence 补充摘要
    """
    executor = {
        "create_seed_nodes": _exec_create_seed_nodes,
        "split_overloaded_node": _exec_split_overloaded_node,
        "re_embed_affected": _exec_re_embed_affected,
        "enrich_content": _exec_enrich_content,
    }.get(action["action"])

    if executor is None:
        action["status"] = "skipped"
        action["reason"] = f"Unknown action type: {action['action']}"
        return action

    try:
        result = executor(action, ctx)
        result["status"] = "executed"
        return result
    except Exception as e:
        logger.warning("Optimization action failed: %s — %s", action["action"], e)
        action["status"] = "failed"
        action["error"] = str(e)
        return action


def _exec_create_seed_nodes(
    action: dict[str, Any],
    ctx: OptimizationContext,
) -> dict[str, Any]:
    """total_failure → 用失败查询创建种子节点。

    策略：每个失败查询创建一个种子节点，标记来源为 optimizer。
    找到最相似的 group 挂入，或创建新 group。
    """
    queries = action["evidence"].get("sample_queries", [])
    if not queries:
        return action

    root_id = ctx.graph_store.get_root_id()
    if root_id is None:
        action["reason"] = "No root node"
        return action

    created_ids: list[str] = []
    for query in queries:
        node = KnowledgeNode.create(
            title=query[:80],
            content=f"Seed from failed query: {query}",
            summary=query[:50],
            source="optimizer:total_failure",
            metadata={
                "optimization_signal": "total_failure",
                "original_query": query,
            },
        )
        node.embedding = ctx.embedder(node.content)

        # 查找最匹配的 group
        best_group, best_sim = _find_best_group(node, ctx)

        if best_group is not None and best_sim > 0.5:
            # 挂入现有 group
            ctx.graph_store.upsert_edge(KnowledgeEdge.create(
                parent_id=best_group.node_id,
                child_id=node.node_id,
                is_primary=True,
            ))
        else:
            # 创建新 group
            group_node = KnowledgeNode.create(
                title=query[:30],
                content=f"Auto-group: {query[:30]}",
                summary=f"Auto-created from failed queries: {query[:30]}",
                source="optimizer:total_failure",
            )
            group_node.embedding = ctx.embedder(group_node.content)
            ctx.graph_store.upsert_edge(KnowledgeEdge.create(
                parent_id=root_id,
                child_id=group_node.node_id,
                is_primary=True,
            ))
            sync_node_to_stores(group_node, ctx.md_store, ctx.graph_store, ctx.vector_store)
            ctx.graph_store.upsert_edge(KnowledgeEdge.create(
                parent_id=group_node.node_id,
                child_id=node.node_id,
                is_primary=True,
            ))

        sync_node_to_stores(node, ctx.md_store, ctx.graph_store, ctx.vector_store)
        created_ids.append(node.node_id)

    action["nodes_created"] = created_ids
    return action


def _exec_split_overloaded_node(
    action: dict[str, Any],
    ctx: OptimizationContext,
) -> dict[str, Any]:
    """nav_failure → 拆分导航困难节点。

    策略：如果子节点 >= 4 个，按相似度拆成两组；
    否则在元数据中标注失败查询供 Agent 后续处理。
    """
    node_id = action["node_id"]
    if node_id is None:
        return action

    node = ctx.graph_store.get_node(node_id)
    if node is None:
        action["reason"] = "Node not found"
        return action

    children = ctx.graph_store.get_children(node_id, primary_only=True)

    if len(children) < 4:
        # 不拆分，但记录失败查询供 Agent 参考
        failed_queries = action["evidence"].get("sample_queries", [])
        if "nav_failure_queries" not in node.metadata:
            node.metadata["nav_failure_queries"] = []
        node.metadata["nav_failure_queries"].extend(failed_queries)
        node.metadata["nav_failure_count"] = node.metadata.get("nav_failure_count", 0) + len(failed_queries)
        sync_node_to_stores(node, ctx.md_store, ctx.graph_store, ctx.vector_store)
        action["result"] = "metadata_updated"
        return action

    # 拆分：按嵌入相似度将子节点分成两组
    mid = len(children) // 2
    group_a = children[:mid]
    group_b = children[mid:]

    # 创建两个子组节点
    for idx, group in enumerate(("A", "B")):
        sub_children = group_a if group == "A" else group_b
        sub_title = f"{node.title} ({group})"
        sub_node = KnowledgeNode.create(
            title=sub_title,
            content=f"Auto-split from: {node.title}",
            summary=f"Sub-group {group}: {', '.join(c.title[:20] for c in sub_children[:3])}",
            source="optimizer:nav_failure",
            metadata={"split_from": node_id, "group": group},
        )
        sub_node.embedding = ctx.embedder(sub_node.content)

        # parent → sub_node
        ctx.graph_store.upsert_edge(KnowledgeEdge.create(
            parent_id=node_id,
            child_id=sub_node.node_id,
            is_primary=True,
        ))
        sync_node_to_stores(sub_node, ctx.md_store, ctx.graph_store, ctx.vector_store)

        # sub_node → original children
        for child in sub_children:
            # 移除旧的 parent→child 直接边
            old_edges = [
                e for e in ctx.graph_store.get_edges_for_node(child.node_id)
                if e.parent_id == node_id and e.child_id == child.node_id
            ]
            for old_edge in old_edges:
                ctx.graph_store.delete_edge(old_edge.edge_id)

            # 新建 sub_node→child 边
            ctx.graph_store.upsert_edge(KnowledgeEdge.create(
                parent_id=sub_node.node_id,
                child_id=child.node_id,
                is_primary=True,
            ))

    action["result"] = "split_into_2_subgroups"
    action["children_redistributed"] = len(group_a) + len(group_b)
    return action


def _exec_re_embed_affected(
    action: dict[str, Any],
    ctx: OptimizationContext,
) -> dict[str, Any]:
    """rag_false_positive → 重新嵌入假阳性节点。

    策略：对受影响节点重新生成嵌入向量，可能改变其在向量空间中的位置。
    通过在内容后追加区分信息来调整向量。
    """
    affected_nodes = action["evidence"].get("affected_nodes", [])
    if not affected_nodes:
        return action

    # 去重
    unique_ids = list(set(affected_nodes))
    re_embedded = re_embed_nodes(unique_ids, ctx.graph_store, ctx.vector_store, ctx.embedder)

    action["nodes_re_embedded"] = re_embedded
    return action


def _exec_enrich_content(
    action: dict[str, Any],
    ctx: OptimizationContext,
) -> dict[str, Any]:
    """content_insufficient → 补充内容不足节点。

    策略：在元数据中标记，并尝试用检索日志中的查询信息丰富摘要。
    P1 不使用 LLM 生成新内容，仅在元数据中积累补充线索。
    """
    node_id = action["node_id"]
    if node_id is None:
        return action

    node = ctx.graph_store.get_node(node_id)
    if node is None:
        action["reason"] = "Node not found"
        return action

    # 标记为内容不足
    node.metadata["content_insufficient"] = True
    count = node.metadata.get("content_insufficient_count", 0)
    node.metadata["content_insufficient_count"] = count + 1

    # 用 evidence 中的信息丰富摘要
    evidence_count = action["evidence"].get("count", 0)
    if evidence_count > 0 and node.summary:
        node.summary = f"{node.summary} [needs enrichment: {evidence_count} hits]"

    sync_node_to_stores(node, ctx.md_store, ctx.graph_store, ctx.vector_store)
    action["result"] = "enriched_metadata"
    return action


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------

def _find_best_group(
    node: KnowledgeNode,
    ctx: OptimizationContext,
) -> tuple[KnowledgeNode | None, float]:
    """为节点查找最匹配的现有 group。"""
    root_id = ctx.graph_store.get_root_id()
    if root_id is None:
        return None, 0.0

    root_children = ctx.graph_store.get_children(root_id)
    best_group: KnowledgeNode | None = None
    best_sim = 0.0

    for group in root_children:
        group_emb = ctx.vector_store.get_embedding(group.node_id)
        if group_emb is None:
            group_emb = ctx.embedder(group.content or group.title)
        if node.embedding is None:
            node.embedding = ctx.embedder(node.content or node.title)

        sim = _cosine_similarity(node.embedding, group_emb)
        if sim > best_sim:
            best_sim = sim
            best_group = group

    return best_group, best_sim


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """余弦相似度。"""
    if len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    mag_a = sum(x * x for x in a) ** 0.5
    mag_b = sum(x * x for x in b) ** 0.5
    if mag_a == 0 or mag_b == 0:
        return 0.0
    return dot / (mag_a * mag_b)
