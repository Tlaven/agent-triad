"""V4 涌现式知识树 — Supervisor 内嵌组件。

两层存储（文件系统 + 向量索引）+ Overlay JSON 跨目录关联。
文件系统目录层级 = 树结构，向量通过目录锚点聚簇。
通过 enable_knowledge_tree 配置项条件激活。
"""

from __future__ import annotations

import json
import logging
import time as _time
from collections.abc import Callable

# Path used transitively via storage modules
from typing import Any

from src.common.knowledge_tree.bootstrap import (
    BootstrapReport as BootstrapReport,
)
from src.common.knowledge_tree.bootstrap import (
    bootstrap_from_directory,
)
from src.common.knowledge_tree.config import KnowledgeTreeConfig
from src.common.knowledge_tree.dag.node import KnowledgeNode
from src.common.knowledge_tree.ingestion.chunker import chunk_text
from src.common.knowledge_tree.ingestion.filter import should_remember
from src.common.knowledge_tree.ingestion.ingest import IngestReport, ingest_nodes
from src.common.knowledge_tree.retrieval.log import RetrievalLog
from src.common.knowledge_tree.retrieval.rag_search import rag_search
from src.common.knowledge_tree.storage.markdown_store import MarkdownStore
from src.common.knowledge_tree.storage.overlay import OverlayStore
from src.common.knowledge_tree.storage.vector_store import (
    InMemoryVectorStore,
)

logger = logging.getLogger(__name__)

# 全局 KT 实例缓存：按 markdown_root 路径索引，避免每次 get_tools() 重新创建
_kt_cache: dict[str, KnowledgeTree] = {}


def get_or_create_kt(
    ctx_or_config: Any | KnowledgeTreeConfig,
) -> KnowledgeTree:
    """从全局缓存获取或创建 KnowledgeTree 实例。

    Graph 节点和工具共用同一缓存，避免重复创建。
    接受 Context 或 KnowledgeTreeConfig。

    Args:
        ctx_or_config: Context 实例或 KnowledgeTreeConfig 实例。

    Returns:
        缓存的或新创建的 KnowledgeTree 实例。
    """
    from src.common.context import Context

    if isinstance(ctx_or_config, KnowledgeTreeConfig):
        config = ctx_or_config
    elif isinstance(ctx_or_config, Context):
        config = KnowledgeTreeConfig.from_context(ctx_or_config)
    else:
        config = KnowledgeTreeConfig.from_context(ctx_or_config)

    cache_key = str(config.markdown_root)
    kt = _kt_cache.get(cache_key)
    if kt is not None:
        return kt

    t0 = _time.perf_counter()
    kt = KnowledgeTree(config)
    if config.markdown_root.is_dir():
        try:
            result = kt.bootstrap()
            elapsed = _time.perf_counter() - t0
            if result.get("ok") and not result.get("skipped"):
                logger.info(
                    "Auto-bootstrapped knowledge tree (%.2fs): %s", elapsed, result
                )
            else:
                logger.debug("KT init (%.2fs): bootstrap skipped", elapsed)
        except Exception as e:
            elapsed = _time.perf_counter() - t0
            logger.warning(
                "Auto-bootstrap failed (%.2fs, tree starts empty): %s", elapsed, e
            )
    else:
        logger.debug("KT init: no seed directory at %s", config.markdown_root)
    _kt_cache[cache_key] = kt
    return kt


class KnowledgeTree:
    """知识树门面类——两层存储 + Overlay 架构。

    文件系统目录层级 = 树结构。
    向量索引跟随树结构调整。
    Overlay JSON 管理跨目录关联边。
    """

    def __init__(
        self,
        config: KnowledgeTreeConfig,
        embedder: Callable[[str], list[float]] | None = None,
        llm: Any | None = None,
    ) -> None:
        self.config = config
        self.llm = llm  # 可选 LLM 实例（用于查询扩展）
        if embedder is not None:
            self.embedder = embedder
            self.embedder_type = "external"
        else:
            self.embedder, self.embedder_type = self._create_embedder(config)

        logger.info(
            "KT embedder: type=%s model=%s dim=%d threshold=%.2f",
            self.embedder_type,
            config.embedding_model,
            config.embedding_dimension,
            config.rag_similarity_threshold,
        )

        # 两层存储
        self.md_store = MarkdownStore(
            config.markdown_root, on_change=self._on_fs_change
        )
        self.vector_store = InMemoryVectorStore(dimension=config.embedding_dimension)

        # Overlay JSON
        overlay_path = config.markdown_root / ".overlay.json"
        self.overlay_store = OverlayStore(overlay_path)

        # 检索日志缓冲（上限防止内存泄漏）
        self._retrieval_logs: list[RetrievalLog] = []
        self._max_retrieval_logs = 1000

    def _on_fs_change(self, change_type: str, directory: str) -> None:
        """文件系统变更回调：刷新受影响目录的锚点。

        Change Mapping 的心跳——任何 write/delete/move 操作自动触发。
        """
        if directory:
            from src.common.knowledge_tree.storage.sync import _refresh_anchor

            _refresh_anchor(directory, self.md_store, self.vector_store)

            # P2: 锚点刷新后重算 stored_vector
            from src.common.knowledge_tree.editing.stored_vector import (
                compute_stored_vectors_for_directory,
            )

            compute_stored_vectors_for_directory(
                directory,
                self.md_store,
                self.vector_store,
                self.config.content_weight,
                self.config.structural_weight,
            )

    @staticmethod
    def _create_embedder(
        config: KnowledgeTreeConfig,
    ) -> tuple[Callable[[str], list[float]], str]:
        """根据 config 选择 embedder。返回 (embedder, type_name)。"""
        etype = config.embedder_type

        if etype == "hash":
            return _default_embedder(config.embedding_dimension), "hash"

        if etype == "api":
            from src.common.knowledge_tree.embedding.api import create_api_embedder

            api_emb = create_api_embedder(
                model=config.embedding_model,
                dimension=config.embedding_dimension,
            )
            if api_emb is not None:
                if config.rag_similarity_threshold < 0.3:
                    logger.info(
                        "API embedder: raising threshold %.2f → 0.50",
                        config.rag_similarity_threshold,
                    )
                    config.rag_similarity_threshold = 0.5
                return api_emb, "api"
            logger.warning("API embedder failed, falling back to hash")

        if etype == "local":
            from src.common.knowledge_tree.embedding.semantic import (
                create_semantic_embedder,
            )

            local_emb = create_semantic_embedder(
                config.embedding_model, config.embedding_dimension
            )
            if local_emb is not None:
                if config.rag_similarity_threshold < 0.3:
                    logger.info(
                        "Local embedder: raising threshold %.2f → 0.50",
                        config.rag_similarity_threshold,
                    )
                    config.rag_similarity_threshold = 0.5
                return local_emb, "local"
            logger.warning("Local embedder failed, falling back to hash")

        # Fallback to hash
        return _default_embedder(config.embedding_dimension), "hash"

    def retrieve(
        self, query: str
    ) -> tuple[list[tuple[KnowledgeNode, float]], RetrievalLog]:
        """RAG 向量检索（主检索路径）。

        Args:
            query: 查询文本。

        Returns:
            (results, log) — results 为 (node, similarity) 列表。
        """
        log = RetrievalLog.create(query)
        log.query_vector = self.embedder(query)

        results = rag_search(
            log.query_vector,
            self.vector_store,
            self.md_store,
            embedder=self.embedder,
            threshold=self.config.rag_similarity_threshold,
            anchor_boost_threshold=self.config.ingest_attach_threshold,
        )

        log.rag_results = [(n.node_id, s) for n, s in results]

        logger.info(
            "RAG retrieval: query=%r results=%d scores=%s",
            query[:40],
            len(results),
            [round(s, 3) for _, s in results[:5]],
        )

        self._retrieval_logs.append(log)
        if len(self._retrieval_logs) > self._max_retrieval_logs:
            self._retrieval_logs = self._retrieval_logs[-self._max_retrieval_logs :]
        return results, log

    def status(self) -> dict[str, Any]:
        """返回知识树概览。"""
        node_ids = self.md_store.list_node_ids()
        directories = self.md_store.list_directories()
        anchors = self.vector_store.get_all_anchors()

        return {
            "ok": True,
            "total_nodes": len(node_ids),
            "total_directories": len(directories),
            "total_anchors": len(anchors),
            "overlay_edges": len(self.overlay_store.get_all_edges()),
            "retrieval_logs_count": len(self._retrieval_logs),
            "directories": directories,
            "anchor_directories": [a.directory for a in anchors if a.directory],
        }

    def record_feedback(
        self, query_id: str, satisfaction: bool, feedback: str = ""
    ) -> None:
        """记录 Agent 对检索结果的反馈。"""
        for log in self._retrieval_logs:
            if log.query_id == query_id:
                log.agent_satisfaction = satisfaction
                log.agent_feedback = feedback
                break

    def bootstrap(self) -> dict[str, Any]:
        """从种子目录构建初始知识树。

        种子目录下的目录结构直接成为树结构。
        仅在向量索引为空时执行。

        Returns:
            报告字典。
        """
        # 已有数据则跳过
        if self.vector_store.get_all_anchors():
            return {"ok": True, "message": "Tree already initialized", "skipped": True}

        seed_dir = self.config.markdown_root
        if not seed_dir.is_dir():
            return {"ok": False, "errors": [f"Seed directory not found: {seed_dir}"]}

        report = bootstrap_from_directory(
            seed_dir=seed_dir,
            md_store=self.md_store,
            vector_store=self.vector_store,
            overlay_store=self.overlay_store,
            embedder=self.embedder,
        )

        return {
            "ok": True,
            "nodes_created": report.nodes_created,
            "directories_created": report.directories_created,
            "anchors_computed": report.anchors_computed,
            "embeddings_generated": report.embeddings_generated,
            "max_depth": report.max_depth,
            "errors": report.errors,
        }

    def ingest(
        self,
        text: str,
        trigger: str = "",
        source: str = "agent:supervisor",
        metadata: dict[str, Any] | None = None,
    ) -> IngestReport:
        """知识摄入管道入口。

        完整流程：切分 → 过滤 → 去重 → 增量嫁接。

        Args:
            text: 待摄入的原始文本。
            trigger: 触发类型（"task_complete"、"user_explicit" 等）。
            source: 来源标识。
            metadata: 来源元数据。

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
                    node_id="",  # 将在 ingest_nodes 中分配
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
            self.vector_store,
            self.md_store,
            self.overlay_store,
            self.embedder,
            dedup_threshold=self.config.dedup_threshold,
            attach_threshold=self.config.ingest_attach_threshold,
        )

        # 合并报告
        report.nodes_ingested = ingest_report.nodes_ingested
        report.nodes_deduplicated = ingest_report.nodes_deduplicated
        report.errors = ingest_report.errors
        return report

    # -- Overlay 管理 (P2) --

    def overlay_add(
        self,
        source: str,
        target: str,
        relation: str = "related",
        note: str = "",
    ) -> dict[str, Any]:
        """添加跨目录关联边。

        Args:
            source: 源节点路径。
            target: 目标节点路径。
            relation: 关系类型（默认 "related"）。
            note: 关系说明。

        Returns:
            结果字典。
        """
        if source == target:
            return {"ok": False, "error": "source and target must be different"}
        if not self.md_store.node_exists(source):
            return {"ok": False, "error": f"source not found: {source}"}
        if not self.md_store.node_exists(target):
            return {"ok": False, "error": f"target not found: {target}"}

        from src.common.knowledge_tree.storage.overlay import OverlayEdge

        edge = OverlayEdge(
            source_path=source,
            target_path=target,
            relation=relation,
            strength=1.0,
            created_by="agent",
            note=note,
        )
        self.overlay_store.add_edge(edge)
        return {
            "ok": True,
            "edge": {"source": source, "target": target, "relation": relation},
        }

    def overlay_remove(
        self,
        source: str,
        target: str,
        relation: str = "related",
    ) -> dict[str, Any]:
        """移除跨目录关联边。

        Returns:
            结果字典。
        """
        removed = self.overlay_store.remove_edge(source, target, relation)
        return {"ok": removed}

    def overlay_list(self, path: str = "") -> dict[str, Any]:
        """列出关联边。

        Args:
            path: 可选过滤路径。

        Returns:
            边列表。
        """
        if path:
            edges = self.overlay_store.get_edges_for(path)
        else:
            edges = self.overlay_store.get_all_edges()
        return {
            "ok": True,
            "total": len(edges),
            "edges": [e.to_dict() for e in edges],
        }

    # -- 编号树 + 重组 (P2) --

    def tree(self) -> dict[str, Any]:
        """返回编号树视图。

        Returns:
            {"ok": True, "tree": "...numbered text..."}
        """
        from src.common.knowledge_tree.editing.tree_view import render_numbered_tree

        tree_text = render_numbered_tree(self.md_store)
        return {"ok": True, "tree": tree_text}

    def reorganize(self, proposed_tree: str) -> dict[str, Any]:
        """解析提议树并执行重组。

        Args:
            proposed_tree: 编号树格式的提议结构。

        Returns:
            重组报告。
        """
        from src.common.knowledge_tree.editing.reorganize import (
            diff_trees,
            execute_reorganize,
        )
        from src.common.knowledge_tree.editing.tree_view import parse_numbered_tree

        try:
            entries = parse_numbered_tree(proposed_tree)
        except ValueError as e:
            return {"ok": False, "error": str(e)}

        if not entries:
            return {"ok": True, "message": "No changes proposed", "report": None}

        current_ids = self.md_store.list_node_ids()
        moves = diff_trees(current_ids, entries)

        if not moves:
            return {"ok": True, "message": "No changes needed", "report": None}

        report = execute_reorganize(moves, self.md_store, self.overlay_store)
        return {
            "ok": True,
            "report": {
                "moves_executed": report.moves_executed,
                "moves_failed": report.moves_failed,
                "directories_created": report.directories_created,
                "directories_removed": report.directories_removed,
                "overlay_edges_updated": report.overlay_edges_updated,
                "errors": report.errors,
            },
        }


def _default_embedder(dimension: int) -> Callable[[str], list[float]]:
    """默认 n-gram 哈希 embedder（零外部依赖）.

    改进版：使用多粒度 n-gram（1~4）+ 稳定哈希分桶，
    对中英文混合文本有更好的区分度。
    注意：此 embedder 的余弦相似度低于语义嵌入，
    检索阈值应适当降低（建议 0.15-0.30）。
    """

    def _stable_hash(s: str) -> int:
        """稳定的字符串哈希（跨 Python 版本一致）。"""
        h = 5381
        for c in s:
            h = ((h << 5) + h + ord(c)) & 0xFFFFFFFF
        return h

    def embed(text: str) -> list[float]:
        vec = [0.0] * dimension
        if not text:
            return vec

        # 多粒度 n-gram：1-gram 权重低，2~4 gram 权重高
        weights = {1: 0.3, 2: 1.0, 3: 1.0, 4: 0.8}
        for n, weight in weights.items():
            for i in range(max(0, len(text) - n + 1)):
                gram = text[i : i + n]
                idx = _stable_hash(gram) % dimension
                vec[idx] += weight

        mag = sum(x * x for x in vec) ** 0.5
        if mag > 0:
            vec = [x / mag for x in vec]
        return vec

    return embed


def build_knowledge_tree_tools(runtime_context: Any) -> list:
    """构建知识树 Supervisor 工具列表。

    P1 工具：retrieve, ingest。
    P2 工具：status, list（Agent 可见性）。
    惰性初始化：KnowledgeTree 实例在首次工具调用时才创建。
    实例按 markdown_root 路径缓存，Graph 节点和工具共用同一缓存。
    """
    import asyncio

    from langchain_core.tools import tool as lc_tool

    config = KnowledgeTreeConfig.from_context(runtime_context)

    # -- sync 业务逻辑 --

    def _sync_retrieve(query: str) -> str:
        results, log = get_or_create_kt(config).retrieve(query)
        if not results:
            return json.dumps(
                {
                    "ok": False,
                    "message": "No results found",
                    "query_id": log.query_id,
                }
            )
        top_node, top_score = results[0]
        # 质量标记：帮助 Supervisor 判断检索结果的可信度
        quality = (
            "high" if top_score >= 0.5 else ("medium" if top_score >= 0.25 else "low")
        )
        response = {
            "ok": True,
            "source": "rag",
            "query_id": log.query_id,
            "node_id": top_node.node_id,
            "title": top_node.title,
            "content": top_node.content[:500],
            "similarity": round(top_score, 3),
            "quality": quality,
            "additional_results": len(results) - 1,
        }
        if quality == "low":
            response["warning"] = (
                "Low similarity score — result may not be relevant. "
                "Consider rephrasing the query or using workspace tools to search files directly."
            )
        return json.dumps(response, ensure_ascii=False)

    def _sync_ingest(text: str, trigger: str, source: str) -> str:
        report = get_or_create_kt(config).ingest(text, trigger=trigger, source=source)
        return json.dumps(
            {
                "ok": True,
                "nodes_ingested": report.nodes_ingested,
                "nodes_deduplicated": report.nodes_deduplicated,
                "nodes_filtered": report.nodes_filtered,
                "errors": report.errors,
            },
            ensure_ascii=False,
        )

    def _sync_status() -> str:
        kt = get_or_create_kt(config)
        s = kt.status()
        return json.dumps(s, ensure_ascii=False)

    def _sync_list(directory: str) -> str:
        kt = get_or_create_kt(config)
        nodes = kt.md_store.list_nodes()
        # Filter by directory if specified
        if directory:
            nodes = [n for n in nodes if n.directory == directory]
        items = []
        for n in nodes:
            items.append(
                {
                    "node_id": n.node_id,
                    "title": n.title,
                    "directory": n.directory,
                    "created_at": n.created_at,
                    "content_preview": n.content[:80] if n.content else "",
                }
            )
        return json.dumps(
            {
                "ok": True,
                "total": len(items),
                "items": items,
            },
            ensure_ascii=False,
        )

    # -- async 工具 --

    @lc_tool
    async def knowledge_tree_retrieve(query: str) -> str:
        """Search the knowledge tree for relevant information using RAG vector similarity.

        Returns matching nodes with their content and a query_id for feedback.

        Args:
            query: The search query text.
        """
        return await asyncio.to_thread(_sync_retrieve, query)

    @lc_tool
    async def knowledge_tree_ingest(
        text: str,
        trigger: str = "task_complete",
        source: str = "agent:supervisor",
    ) -> str:
        """Ingest new knowledge into the tree from text.

        The text is automatically chunked, filtered for relevance, deduplicated,
        and placed in the most matching directory based on anchor similarity.

        Args:
            text: The text content to ingest.
            trigger: Trigger type, e.g. "task_complete", "user_explicit".
            source: Source identifier for provenance tracking.
        """
        return await asyncio.to_thread(_sync_ingest, text, trigger, source)

    @lc_tool
    async def knowledge_tree_status() -> str:
        """Get knowledge tree overview: total nodes, directories, anchors.

        Use this to understand what knowledge is available before deciding
        whether to search, ingest, or rely on auto-injection.
        """
        return await asyncio.to_thread(_sync_status)

    @lc_tool
    async def knowledge_tree_list(directory: str = "") -> str:
        """List knowledge tree nodes, optionally filtered by directory.

        Returns each node's title, directory, and content preview.
        Use this to browse the tree structure and understand what knowledge exists.

        Args:
            directory: Optional directory filter (e.g. "architecture", "patterns").
                       Empty string lists all nodes.
        """
        return await asyncio.to_thread(_sync_list, directory)

    # -- P2: Overlay 管理 --

    def _sync_overlay(
        action: str,
        source: str = "",
        target: str = "",
        relation: str = "related",
        note: str = "",
        path: str = "",
    ) -> str:
        kt = get_or_create_kt(config)
        if action == "add":
            result = kt.overlay_add(source, target, relation, note)
        elif action == "remove":
            result = kt.overlay_remove(source, target, relation)
        elif action == "list":
            result = kt.overlay_list(path)
        else:
            result = {"ok": False, "error": f"Unknown action: {action}"}
        return json.dumps(result, ensure_ascii=False)

    @lc_tool
    async def knowledge_tree_overlay(
        action: str,
        source: str = "",
        target: str = "",
        relation: str = "related",
        note: str = "",
        path: str = "",
    ) -> str:
        """Manage cross-directory knowledge associations (overlay edges).

        Actions:
        - "add": Create a link between two knowledge nodes. Both must exist.
        - "remove": Delete a link between two knowledge nodes.
        - "list": Show all links, or links involving a specific node.

        Args:
            action: One of "add", "remove", "list".
            source: Source node path (for add/remove).
            target: Target node path (for add/remove).
            relation: Relationship type, default "related".
            note: Optional note describing the relationship.
            path: Filter path (for list action).
        """
        return await asyncio.to_thread(
            _sync_overlay, action, source, target, relation, note, path
        )

    # -- P2: 编号树 + 重组 --

    def _sync_tree() -> str:
        kt = get_or_create_kt(config)
        result = kt.tree()
        return json.dumps(result, ensure_ascii=False)

    def _sync_reorganize(proposed_tree: str) -> str:
        kt = get_or_create_kt(config)
        result = kt.reorganize(proposed_tree)
        return json.dumps(result, ensure_ascii=False)

    @lc_tool
    async def knowledge_tree_tree() -> str:
        """Display the knowledge tree as a numbered directory listing.

        Shows the full tree structure with numbered directories and files.
        Use this to understand the current organization before proposing changes.
        """
        return await asyncio.to_thread(_sync_tree)

    @lc_tool
    async def knowledge_tree_reorganize(proposed_tree: str) -> str:
        """Reorganize the knowledge tree by proposing a new numbered structure.

        The proposed_tree must follow the same numbered format as shown by
        knowledge_tree_tree(). Files are matched by name -- if a file appears
        in a different directory, it will be moved there. Files not included
        in the proposal are left unchanged (not deleted).

        Args:
            proposed_tree: The new tree structure in numbered format.
        """
        return await asyncio.to_thread(_sync_reorganize, proposed_tree)

    return [
        knowledge_tree_retrieve,
        knowledge_tree_ingest,
        knowledge_tree_status,
        knowledge_tree_list,
        knowledge_tree_overlay,
        knowledge_tree_tree,
        knowledge_tree_reorganize,
    ]
