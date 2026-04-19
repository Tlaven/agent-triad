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
from src.common.knowledge_tree.ingestion.chunker import chunk_conversation, chunk_text
from src.common.knowledge_tree.ingestion.filter import FilterResult, should_remember
from src.common.knowledge_tree.ingestion.ingest import IngestReport, ingest_nodes
from src.common.knowledge_tree.optimization.anti_oscillation import OptimizationHistory
from src.common.knowledge_tree.optimization.optimizer import (
    OptimizationContext,
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

    def optimize(self, dry_run: bool = False) -> OptimizationReport:
        """执行一轮优化（含实际树结构修改）。

        Args:
            dry_run: 仅规划不执行（用于调试）。
        """
        ctx = OptimizationContext(
            graph_store=self.graph_store,
            vector_store=self.vector_store,
            md_store=self.md_store,
            embedder=self.embedder,
        )
        return run_optimization_cycle(
            logs=self._retrieval_logs,
            history=self.optimization_history,
            ctx=ctx,
            nav_failure_threshold=self.config.nav_failure_threshold,
            rag_false_positive_threshold=self.config.rag_false_positive_threshold,
            total_failure_threshold=self.config.total_failure_threshold,
            content_insufficient_threshold=self.config.content_insufficient_threshold,
            dry_run=dry_run,
        )

    def record_feedback(self, query_id: str, satisfaction: bool, feedback: str = "") -> None:
        """记录 Agent 对检索结果的反馈。"""
        for log in self._retrieval_logs:
            if log.query_id == query_id:
                log.agent_satisfaction = satisfaction
                log.agent_feedback = feedback
                break

    def bootstrap_from_wiki(self) -> dict[str, Any]:
        """从 workspace/knowledge_tree/ wiki 种子目录建树。

        使用 WikiFolderAdapter 解析 wiki 格式 Markdown（YAML frontmatter +
        [[wiki-links]]），生成嵌入，然后通过聚类算法构建 DAG 树结构。

        仅在树为空时执行；已有数据则跳过。

        Returns:
            报告字典，含 nodes_created / edges_created / errors 等。
        """
        from src.common.knowledge_tree.ingestion.wiki_adapter import parse_wiki_folder

        # 如果树已有根节点，跳过
        if self.graph_store.get_root_id() is not None:
            return {"ok": True, "message": "Tree already initialized", "skipped": True}

        wiki_root = self.config.markdown_root
        if not wiki_root.is_dir():
            return {"ok": False, "errors": [f"Wiki directory not found: {wiki_root}"]}

        # 1. 解析 wiki 目录
        nodes, hints, report = parse_wiki_folder(wiki_root)
        if report.errors:
            return {"ok": False, "errors": report.errors}
        if not nodes:
            return {"ok": False, "errors": ["No parseable nodes found in wiki directory"]}

        # 2. 生成嵌入
        for node in nodes:
            if node.embedding is None:
                node.embedding = self.embedder(node.content or node.title)

        # 3. 聚类建树
        from src.common.knowledge_tree.bootstrap import _build_tree
        tree = _build_tree(nodes, self.embedder, self.config)

        # 4. 写入三层存储
        self.graph_store.initialize()

        # 根节点
        sync_node_to_stores(tree.root, self.md_store, self.graph_store, self.vector_store)

        # 中间节点
        for node in tree.intermediate_nodes:
            sync_node_to_stores(node, self.md_store, self.graph_store, self.vector_store)

        # 叶子节点
        for node in nodes:
            sync_node_to_stores(node, self.md_store, self.graph_store, self.vector_store)

        # 边
        for parent_id, child_id in tree.edges:
            from src.common.knowledge_tree.dag.edge import KnowledgeEdge
            self.graph_store.upsert_edge(KnowledgeEdge.create(
                parent_id=parent_id,
                child_id=child_id,
                is_primary=True,
            ))

        # P2: 利用 RelationHint 辅助边构建（当前作为 metadata 记录）
        for hint in hints:
            logger.debug("Relation hint: %s -> %s (deferred to P2)", hint.source_title, hint.target_title)

        return {
            "ok": True,
            "nodes_created": 1 + len(tree.intermediate_nodes) + len(nodes),
            "edges_created": len(tree.edges),
            "wiki_nodes_parsed": report.nodes_created,
            "wiki_meta_skipped": report.meta_skipped,
            "relation_hints": len(hints),
            "max_depth": tree.max_depth,
            "cluster_method": tree.root.metadata.get("cluster_method", "unknown"),
        }

    def ingest(
        self,
        text: str,
        trigger: str = "",
        source: str = "agent:supervisor",
        metadata: dict[str, Any] | None = None,
    ) -> IngestReport:
        """知识摄入管道入口（决策 26）。

        完整流程：切分 → 过滤 → 去重 → ingest_nodes。

        Args:
            text: 待摄入的原始文本。
            trigger: 触发类型（"task_complete"、"user_explicit" 等）。
            source: 来源标识。
            metadata: 来源元数据（plan_id 等）。

        Returns:
            IngestReport 统计信息。
        """
        if not self.config.ingest_enabled:
            return IngestReport()

        report = IngestReport()

        # 1. 切分
        chunks = chunk_text(text, max_tokens=self.config.ingest_chunk_max_tokens)
        if not chunks:
            return report

        # 2. 过滤
        candidates: list[KnowledgeNode] = []
        for chunk in chunks:
            result = should_remember(chunk, trigger=trigger)
            if result.passed:
                meta = dict(metadata) if metadata else {}
                meta["trigger"] = trigger
                meta["filter_confidence"] = result.confidence
                node = KnowledgeNode.create(
                    title=chunk[:50],
                    content=chunk,
                    source=source,
                    metadata=meta,
                )
                candidates.append(node)
            else:
                report.nodes_filtered += 1

        # 3. 增量摄入
        ingest_report = ingest_nodes(
            candidates,
            self.graph_store,
            self.vector_store,
            self.md_store,
            self.embedder,
            dedup_threshold=self.config.dedup_threshold,
            cluster_attach_threshold=self.config.cluster_attach_threshold,
        )

        # 合并报告
        report.nodes_ingested = ingest_report.nodes_ingested
        report.nodes_deduplicated = ingest_report.nodes_deduplicated
        report.errors = ingest_report.errors
        return report


def _default_embedder(dimension: int) -> Callable[[str], list[float]]:
    """默认 n-gram 哈希 embedder（零外部依赖）。

    用 2-gram 和 3-gram 分片哈希映射到向量维度，使：
    - 相似文本产生相似向量
    - 不同文本产生不同向量
    - 确定性输出（同文本同向量）
    """
    def embed(text: str) -> list[float]:
        vec = [0.0] * dimension
        if not text:
            return vec

        # 2-gram 和 3-gram 分片
        for n in (2, 3):
            for i in range(len(text) - n + 1):
                gram = text[i:i + n]
                idx = hash(gram) % dimension
                vec[idx] += 1.0

        # 单字符（1-gram）补充
        for c in text:
            idx = ord(c) % dimension
            vec[idx] += 0.5

        # L2 归一化
        mag = sum(x * x for x in vec) ** 0.5
        if mag > 0:
            vec = [x / mag for x in vec]
        return vec
    return embed


def build_knowledge_tree_tools(runtime_context: Any) -> list:
    """构建知识树 Supervisor 工具列表。

    采用惰性初始化：KnowledgeTree 实例在首次工具调用时才创建，
    避免 get_tools() 阶段触发文件系统 blocking 调用。
    所有工具共享同一个实例（闭包捕获 _kt_holder）。

    每个工具的 sync 逻辑通过 asyncio.to_thread() 卸载到线程池，
    避免在 ASGI 事件循环中触发 BlockingError。
    """
    import asyncio

    from langchain_core.tools import tool as lc_tool

    config = KnowledgeTreeConfig.from_context(runtime_context)
    _kt_holder: list[KnowledgeTree | None] = [None]

    def _kt() -> KnowledgeTree:
        """惰性获取 KnowledgeTree 单例，首次调用时自动从 wiki 种子 bootstrap。"""
        if _kt_holder[0] is None:
            kt = KnowledgeTree(config)
            _kt_holder[0] = kt
            # 自动从 wiki 种子 bootstrap（如果目录存在且树为空）
            if config.markdown_root.is_dir():
                try:
                    result = kt.bootstrap_from_wiki()
                    if result.get("ok") and not result.get("skipped"):
                        logger.info("Auto-bootstrapped knowledge tree from wiki: %s", result)
                except Exception as e:
                    logger.warning("Auto-bootstrap failed (tree starts empty): %s", e)
        return _kt_holder[0]
        """惰性获取 KnowledgeTree 单例。"""
        if _kt_holder[0] is None:
            _kt_holder[0] = KnowledgeTree(config)
        return _kt_holder[0]

    # -- sync 业务逻辑（纯同步，方便测试直接调用） --

    def _sync_retrieve(query: str) -> str:
        result, log = _kt().retrieve(query)
        if not result.nodes:
            return json.dumps({"ok": False, "message": "No results found"})
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

    def _sync_edit(operation: str, params_json: str | dict) -> str:
        try:
            if isinstance(params_json, dict):
                params = params_json
            else:
                params = json.loads(params_json)
        except json.JSONDecodeError as e:
            return json.dumps({"ok": False, "error": f"Invalid JSON: {e}"})
        delta = _kt().edit(operation, params)
        if delta is None:
            return json.dumps({"ok": False, "error": "Edit failed"})
        return json.dumps({
            "ok": True,
            "operation": delta.operation,
            "affected_nodes": delta.affected_node_ids,
        }, ensure_ascii=False)

    def _sync_status() -> str:
        return json.dumps(_kt().status(), ensure_ascii=False)

    def _sync_ingest(text: str, trigger: str, source: str) -> str:
        report = _kt().ingest(text, trigger=trigger, source=source)
        return json.dumps({
            "ok": True,
            "nodes_ingested": report.nodes_ingested,
            "nodes_deduplicated": report.nodes_deduplicated,
            "nodes_filtered": report.nodes_filtered,
            "errors": report.errors,
        }, ensure_ascii=False)

    def _sync_optimize(dry_run: bool) -> str:
        report = _kt().optimize(dry_run=dry_run)
        return json.dumps({
            "ok": True,
            "signals_detected": report.signals_detected,
            "signals_filtered": report.signals_filtered,
            "actions_planned": report.actions_planned,
            "actions_executed": report.actions_executed,
            "actions": report.actions,
        }, ensure_ascii=False)

    def _sync_bootstrap() -> str:
        result = _kt().bootstrap_from_wiki()
        return json.dumps(result, ensure_ascii=False)

    # -- async 工具（通过 to_thread 卸载 sync 逻辑） --

    @lc_tool
    async def knowledge_tree_retrieve(query: str) -> str:
        """Search the knowledge tree for relevant information.

        Uses LLM-guided tree navigation first, falls back to vector similarity search.
        Returns matching nodes with their content and a query_id for feedback.

        Args:
            query: The search query text.
        """
        return await asyncio.to_thread(_sync_retrieve, query)

    @lc_tool
    async def knowledge_tree_edit(operation: str, params_json: str) -> str:
        """Edit the knowledge tree structure (merge, split, or update content).

        Args:
            operation: One of "merge", "split", "update_content".
                - merge: combine multiple nodes. params_json: {"node_ids": [...], "title"?: "..."}
                - split: divide one node into sub-nodes. params_json: {"node_id": "...", "splits": [{"title": "...", "content": "..."}]}
                - update_content: apply JSON patches. params_json: {"node_id": "...", "patches": [...]}
            params_json: JSON string (or dict) with operation parameters.
        """
        return await asyncio.to_thread(_sync_edit, operation, params_json)

    @lc_tool
    async def knowledge_tree_status() -> str:
        """Get the current status and health of the knowledge tree.

        Returns node count, edge count, and retrieval log statistics.
        """
        return await asyncio.to_thread(_sync_status)

    @lc_tool
    async def knowledge_tree_ingest(
        text: str,
        trigger: str = "agent:supervisor",
        source: str = "agent:supervisor",
    ) -> str:
        """Ingest new knowledge into the tree from text.

        The text is automatically chunked, filtered for relevance, deduplicated,
        and attached to the most matching group in the tree.

        Args:
            text: The text content to ingest.
            trigger: Trigger type, e.g. "task_complete", "user_explicit", "agent:supervisor".
            source: Source identifier for provenance tracking.
        """
        return await asyncio.to_thread(_sync_ingest, text, trigger, source)

    @lc_tool
    async def knowledge_tree_optimize(dry_run: bool = False) -> str:
        """Run one optimization cycle on the knowledge tree.

        Analyzes retrieval logs for failure patterns and automatically
        restructures the tree (merge, split, create seed nodes, re-embed).

        Args:
            dry_run: If true, only plan actions without executing them.
        """
        return await asyncio.to_thread(_sync_optimize, dry_run)

    @lc_tool
    async def knowledge_tree_bootstrap() -> str:
        """Bootstrap the knowledge tree from the wiki seed directory.

        Parses workspace/knowledge_tree/ Markdown files (with YAML frontmatter
        and [[wiki-links]]), generates embeddings, clusters them with GMM+UMAP
        or simple cosine BFS, and builds the initial DAG tree structure.

        Safe to call multiple times — skips if tree already has data.
        """
        return await asyncio.to_thread(_sync_bootstrap)

    return [
        knowledge_tree_retrieve,
        knowledge_tree_edit,
        knowledge_tree_status,
        knowledge_tree_ingest,
        knowledge_tree_optimize,
        knowledge_tree_bootstrap,
    ]
