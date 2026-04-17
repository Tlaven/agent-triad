"""V4 涌现式知识树 — Supervisor 内嵌组件。

三层存储（Markdown + 图数据库 + 向量索引）+ 双路径检索 + 编辑闭环 + 异步优化。
通过 enable_knowledge_tree 配置项条件激活。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Callable

from src.common.knowledge_tree.config import KnowledgeTreeConfig
from src.common.knowledge_tree.dag.edge import KnowledgeEdge
from src.common.knowledge_tree.dag.node import KnowledgeNode
from src.common.knowledge_tree.editing.change_map import (
    ChangeDelta,
    apply_json_patch,
    compute_delta,
    validate_delta,
)
from src.common.knowledge_tree.editing.merge_split import merge_nodes, split_node
from src.common.knowledge_tree.editing.re_embed import re_embed_nodes
from src.common.knowledge_tree.optimization.anti_oscillation import OptimizationHistory
from src.common.knowledge_tree.optimization.optimizer import (
    OptimizationReport,
    run_optimization_cycle,
)
from src.common.knowledge_tree.retrieval.fusion import RetrievalResult, fuse_results
from src.common.knowledge_tree.retrieval.log import RetrievalLog
from src.common.knowledge_tree.retrieval.rag_fallback import rag_search
from src.common.knowledge_tree.retrieval.router import NavigationResult, navigate_tree
from src.common.knowledge_tree.storage.graph_store import (
    BaseGraphStore,
    InMemoryGraphStore,
)
from src.common.knowledge_tree.storage.markdown_store import MarkdownStore
from src.common.knowledge_tree.storage.sync import full_rebuild, sync_node_to_stores
from src.common.knowledge_tree.storage.vector_store import (
    BaseVectorStore,
    InMemoryVectorStore,
)

logger = logging.getLogger(__name__)


class KnowledgeTree:
    """知识树门面类——组合所有子模块提供统一 API。"""

    def __init__(
        self,
        config: KnowledgeTreeConfig,
        embedder: Callable[[str], list[float]] | None = None,
        llm: Any = None,
    ) -> None:
        self.config = config
        self.embedder = embedder or _default_embedder(config.embedding_dimension)
        self.llm = llm

        # 初始化三层存储
        self.md_store = MarkdownStore(config.markdown_root)
        self.graph_store = InMemoryGraphStore()
        self.graph_store.initialize()
        self.vector_store = InMemoryVectorStore(dimension=config.embedding_dimension)

        # 优化历史
        self.optimization_history = OptimizationHistory(
            window=config.optimization_window,
            max_per_window=config.max_optimizations_per_window,
        )

        # 检索日志缓冲
        self._retrieval_logs: list[RetrievalLog] = []

    def retrieve(self, query: str) -> tuple[RetrievalResult, RetrievalLog]:
        """主检索入口（决策 21 完整流程）。"""
        log = RetrievalLog.create(query)

        # ① 向量化
        log.query_vector = self.embedder(query)

        # ② 树导航
        nav_result: NavigationResult | None = None
        if self.llm is not None:
            nav_result = navigate_tree(
                query, self.graph_store, self.llm,
                confidence_threshold=self.config.tree_nav_confidence,
                max_depth=self.config.max_tree_depth,
            )
            log.tree_path = nav_result.path
            log.tree_confidence = nav_result.confidence
            log.tree_success = nav_result.success

        # ③ RAG 兜底
        rag_results: list[tuple[KnowledgeNode, float]] = []
        if nav_result is None or not nav_result.success:
            rag_results = rag_search(
                log.query_vector,
                self.graph_store,
                self.vector_store,
                threshold=self.config.rag_similarity_threshold,
            )
            log.rag_triggered = len(rag_results) > 0
            log.rag_results = [(n.node_id, s) for n, s in rag_results]

        # ④ 融合
        result = fuse_results(nav_result, rag_results)
        log.fusion_mode = result.fusion_mode
        log.final_node_ids = [n.node_id for n in result.nodes]

        self._retrieval_logs.append(log)
        return result, log

    def edit(self, operation: str, params: dict[str, Any]) -> ChangeDelta | None:
        """执行编辑操作（决策 22 P1）。"""
        if operation == "merge":
            merged = merge_nodes(
                params["node_ids"],
                self.graph_store,
                title=params.get("title"),
                content=params.get("content"),
                summary=params.get("summary"),
            )
            # 同步到存储
            sync_node_to_stores(merged, self.md_store, self.graph_store, self.vector_store)
            # 重嵌入
            if merged.embedding is None:
                re_embed_nodes([merged.node_id], self.graph_store, self.vector_store, self.embedder)
            return ChangeDelta(
                delta_id=merged.node_id,
                operation="merge",
                patches=[],
                affected_node_ids=params["node_ids"] + [merged.node_id],
                before_snapshot={},
                after_snapshot=merged.to_dict(),
            )

        elif operation == "split":
            children = split_node(
                params["node_id"],
                params["splits"],
                self.graph_store,
            )
            # 同步到存储
            for child in children:
                sync_node_to_stores(child, self.md_store, self.graph_store, self.vector_store)
            # 重嵌入
            child_ids = [c.node_id for c in children]
            re_embed_nodes(child_ids, self.graph_store, self.vector_store, self.embedder)
            return ChangeDelta(
                delta_id=params["node_id"],
                operation="split",
                patches=[],
                affected_node_ids=[params["node_id"]] + child_ids,
                before_snapshot={},
                after_snapshot={c.node_id: c.to_dict() for c in children},
            )

        elif operation == "update_content":
            node = self.graph_store.get_node(params["node_id"])
            if node is None:
                return None
            before = KnowledgeNode.from_dict(node.to_dict())
            updated = apply_json_patch(node, params.get("patches", []))
            sync_node_to_stores(updated, self.md_store, self.graph_store, self.vector_store)
            re_embed_nodes([updated.node_id], self.graph_store, self.vector_store, self.embedder)
            return compute_delta("update_content", before, updated)

        return None

    def status(self) -> dict[str, Any]:
        """返回树结构概览。"""
        root_id = self.graph_store.get_root_id()
        all_edges = self.graph_store.get_all_edges()
        return {
            "ok": True,
            "root_id": root_id,
            "total_nodes": len(self.md_store.list_node_ids()),
            "total_edges": len(all_edges),
            "retrieval_logs_count": len(self._retrieval_logs),
        }

    def optimize(self) -> OptimizationReport:
        """执行一轮优化。"""
        return run_optimization_cycle(
            logs=self._retrieval_logs,
            history=self.optimization_history,
            nav_failure_threshold=self.config.nav_failure_threshold,
            rag_false_positive_threshold=self.config.rag_false_positive_threshold,
            total_failure_threshold=self.config.total_failure_threshold,
            content_insufficient_threshold=self.config.content_insufficient_threshold,
        )

    def record_feedback(self, query_id: str, satisfaction: bool, feedback: str = "") -> None:
        """记录 Agent 对检索结果的反馈。"""
        for log in self._retrieval_logs:
            if log.query_id == query_id:
                log.agent_satisfaction = satisfaction
                log.agent_feedback = feedback
                break


def _default_embedder(dimension: int) -> Callable[[str], list[float]]:
    """默认确定性 embedder（不依赖外部模型）。"""
    def embed(text: str) -> list[float]:
        base = sum(ord(c) for c in text) % 100 / 100.0
        return [base + i * 0.001 for i in range(dimension)]
    return embed


def build_knowledge_tree_tools(runtime_context: Any) -> list:
    """构建知识树 Supervisor 工具列表。"""
    from langchain_core.tools import tool as lc_tool

    config = KnowledgeTreeConfig.from_context(runtime_context)
    kt = KnowledgeTree(config)

    @lc_tool
    async def knowledge_tree_retrieve(query: str) -> str:
        """Search the knowledge tree for relevant information.

        Uses LLM-guided tree navigation first, falls back to vector similarity search.

        Args:
            query: The search query text.
        """
        result, log = kt.retrieve(query)
        if not result.nodes:
            return json.dumps({"ok": False, "message": "No results found"})

        # 返回最相关的节点
        top_node = result.nodes[0]
        return json.dumps({
            "ok": True,
            "source": result.fusion_mode,
            "query_id": log.query_id,
            "node_id": top_node.node_id,
            "title": top_node.title,
            "content": top_node.content[:500],
            "confidence": result.confidence,
            "additional_results": len(result.nodes) - 1,
        }, ensure_ascii=False)

    @lc_tool
    async def knowledge_tree_edit(operation: str, params_json: str) -> str:
        """Edit the knowledge tree (merge, split, or update content).

        Args:
            operation: One of "merge", "split", "update_content".
            params_json: JSON string with operation parameters.
        """
        try:
            params = json.loads(params_json)
        except json.JSONDecodeError as e:
            return json.dumps({"ok": False, "error": f"Invalid JSON: {e}"})

        delta = kt.edit(operation, params)
        if delta is None:
            return json.dumps({"ok": False, "error": "Edit failed"})

        return json.dumps({
            "ok": True,
            "operation": delta.operation,
            "affected_nodes": delta.affected_node_ids,
        }, ensure_ascii=False)

    @lc_tool
    async def knowledge_tree_status() -> str:
        """Get the current status and health of the knowledge tree."""
        return json.dumps(kt.status(), ensure_ascii=False)

    return [knowledge_tree_retrieve, knowledge_tree_edit, knowledge_tree_status]
