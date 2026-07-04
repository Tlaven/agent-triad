"""KnowledgeTree 门面类 — 两层存储 + Overlay 架构。"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from src.common.knowledge_tree.config import KnowledgeTreeConfig
from src.common.knowledge_tree.dag.node import KnowledgeNode
from src.common.knowledge_tree.ingestion.chunker import chunk_text
from src.common.knowledge_tree.ingestion.filter import should_remember
from src.common.knowledge_tree.ingestion.ingest import IngestReport, ingest_nodes
from src.common.knowledge_tree.retrieval.log import RetrievalLog
from src.common.knowledge_tree.retrieval.rag_search import rag_search
from src.common.knowledge_tree.storage.markdown_store import MarkdownStore
from src.common.knowledge_tree.storage.overlay import OverlayStore
from src.common.knowledge_tree.storage.vector_store import InMemoryVectorStore

logger = logging.getLogger(__name__)


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
        self.llm = llm
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

        self.md_store = MarkdownStore(
            config.markdown_root, on_change=self._on_fs_change
        )
        self.vector_store = InMemoryVectorStore(dimension=config.embedding_dimension)

        overlay_path = config.markdown_root / ".overlay.json"
        self.overlay_store = OverlayStore(overlay_path)

        self._vector_index_path = config.markdown_root / ".vector_index.json"

        self._retrieval_logs: list[RetrievalLog] = []
        self._max_retrieval_logs = 1000

        self._signal_check_counter = 0
        self._last_signals: list = []
        self._dirty = False
        from src.common.knowledge_tree.optimization.anti_oscillation import (
            OptimizationHistory,
        )
        self._opt_history = OptimizationHistory(
            window=config.optimization_window,
            max_per_window=config.max_optimizations_per_window,
        )

    def _on_fs_change(self, change_type: str, directory: str) -> None:
        """文件系统变更回调：刷新受影响目录的锚点 + 标记脏数据。"""
        if directory:
            from src.common.knowledge_tree.storage.sync import _refresh_anchor

            _refresh_anchor(directory, self.md_store, self.vector_store)

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

        self.mark_dirty()

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

            cache_path = config.markdown_root / f".embedding_cache_{config.embedding_model.replace('/', '_')}.json"
            api_emb = create_api_embedder(
                model=config.embedding_model,
                dimension=config.embedding_dimension,
                cache_path=cache_path,
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

            cache_path = config.markdown_root / f".embedding_cache_{config.embedding_model.replace('/', '_')}.json"
            local_emb = create_semantic_embedder(
                config.embedding_model, config.embedding_dimension,
                cache_path=cache_path,
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

        return _default_embedder(config.embedding_dimension), "hash"

    def retrieve(
        self, query: str
    ) -> tuple[list[tuple[KnowledgeNode, float]], RetrievalLog]:
        """RAG 向量检索（主检索路径）。"""
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
        self._check_signals()
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

    def _check_signals(self) -> list:
        """懒检测：每 signal_check_interval 次 retrieve 检查一次优化信号。"""
        self._signal_check_counter += 1
        if self._signal_check_counter < 50:
            return []
        self._signal_check_counter = 0

        from src.common.knowledge_tree.optimization.anti_oscillation import (
            filter_signals_by_quota,
        )
        from src.common.knowledge_tree.optimization.signals import detect_signals

        signals = detect_signals(
            self._retrieval_logs,
            total_failure_threshold=self.config.total_failure_threshold,
            rag_false_positive_threshold=self.config.rag_false_positive_threshold,
            content_insufficient_threshold=self.config.content_insufficient_threshold,
        )
        if not signals:
            self._last_signals = []
            return []

        filtered = filter_signals_by_quota(signals, self._opt_history)
        self._last_signals = filtered
        if filtered:
            logger.info("KT optimization signals detected: %s", [
                {"type": s.signal_type, "node": s.node_id} for s in filtered
            ])
        return filtered

    def bootstrap(self) -> dict[str, Any]:
        """从种子目录构建初始知识树。"""
        if self.vector_store.get_all_anchors():
            return {"ok": True, "message": "Tree already initialized", "skipped": True}

        if self._load_vector_index() and self.vector_store.get_all_anchors():
            logger.info(
                "KT: loaded vector index from disk "
                "(%d nodes, %d anchors), skipping bootstrap",
                self.vector_store.node_count,
                len(self.vector_store.get_all_anchors()),
            )
            return {
                "ok": True,
                "message": "Loaded from persisted vector index",
                "skipped": True,
                "nodes_loaded": self.vector_store.node_count,
                "anchors_loaded": len(self.vector_store.get_all_anchors()),
            }

        seed_dir = self.config.markdown_root
        if not seed_dir.is_dir():
            return {"ok": False, "errors": [f"Seed directory not found: {seed_dir}"]}

        from src.common.knowledge_tree.bootstrap import bootstrap_from_directory

        report = bootstrap_from_directory(
            seed_dir=seed_dir,
            md_store=self.md_store,
            vector_store=self.vector_store,
            overlay_store=self.overlay_store,
            embedder=self.embedder,
        )

        from src.common.knowledge_tree.bootstrap import seed_meta_rules
        try:
            seed_meta_rules(self)
        except Exception as e:
            logger.warning("Meta rule seeding failed (non-critical): %s", e)

        self.save(force=True)

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
        """
        if not self.config.ingest_enabled:
            return IngestReport()

        report = IngestReport()

        chunks = chunk_text(text, max_tokens=self.config.ingest_chunk_max_tokens)
        if not chunks:
            return report

        candidates: list[KnowledgeNode] = []
        meta_in = metadata or {}
        goal_title = (meta_in.get("goal") or "")[:60]
        intent_title = (meta_in.get("primary_intent") or "")[:60]
        for chunk in chunks:
            result = should_remember(chunk, trigger=trigger)
            if result.passed:
                meta = dict(metadata) if metadata else {}
                meta["trigger"] = trigger
                meta["filter_confidence"] = result.confidence
                title = goal_title or intent_title or chunk[:50]
                node = KnowledgeNode.create(
                    node_id="",
                    title=title,
                    content=chunk,
                    source=source,
                    metadata=meta,
                )
                candidates.append(node)
            else:
                report.nodes_filtered += 1

        ingest_report = ingest_nodes(
            candidates,
            self.vector_store,
            self.md_store,
            self.overlay_store,
            self.embedder,
            dedup_threshold=self.config.dedup_threshold,
            attach_threshold=self.config.ingest_attach_threshold,
        )

        report.nodes_ingested = ingest_report.nodes_ingested
        report.nodes_deduplicated = ingest_report.nodes_deduplicated
        report.errors = ingest_report.errors

        if report.nodes_ingested > 0:
            self.mark_dirty()
            self._flush_embedding_cache()

        return report

    def overlay_add(
        self,
        source: str,
        target: str,
        relation: str = "related",
        note: str = "",
    ) -> dict[str, Any]:
        """添加跨目录关联边。"""
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
        """移除跨目录关联边。"""
        removed = self.overlay_store.remove_edge(source, target, relation)
        return {"ok": removed}

    def overlay_list(self, path: str = "") -> dict[str, Any]:
        """列出关联边。"""
        if path:
            edges = self.overlay_store.get_edges_for(path)
        else:
            edges = self.overlay_store.get_all_edges()
        return {
            "ok": True,
            "total": len(edges),
            "edges": [e.to_dict() for e in edges],
        }

    def tree(self) -> dict[str, Any]:
        """返回编号树视图。"""
        from src.common.knowledge_tree.editing.tree_view import render_numbered_tree

        tree_text = render_numbered_tree(self.md_store)
        return {"ok": True, "tree": tree_text}

    def reorganize(self, proposed_tree: str) -> dict[str, Any]:
        """解析提议树并执行重组。"""
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

        # 清理重组后的旧向量条目，并重建新路径的嵌入
        for old_id, actual_new_id in report.executed_moves.items():
            self.vector_store.delete_embedding(old_id)
            node = self.md_store.read_node(actual_new_id)
            if node is not None:
                node_embedding = self.embedder(node.content or node.title)
                self.vector_store.upsert_embedding(actual_new_id, node_embedding)
                if node.title:
                    title_embedding = self.embedder(node.title)
                    self.vector_store.upsert_embedding(
                        f"title:{actual_new_id}", title_embedding
                    )
                from src.common.knowledge_tree.editing.stored_vector import (
                    compute_and_store_stored_vector,
                )
                compute_and_store_stored_vector(
                    actual_new_id,
                    self.vector_store,
                    self.config.content_weight,
                    self.config.structural_weight,
                )

        if report.moves_executed > 0:
            self._opt_history.record()
            self.mark_dirty()
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

    def get_node_count(self) -> int:
        """返回节点总数。"""
        return len(self.md_store.list_nodes())

    def get_directory_count(self) -> int:
        """返回目录数。"""
        return len(self.vector_store.get_all_anchors())

    def get_meta_rules(self) -> list[KnowledgeNode]:
        """返回所有持久元规则节点（绕过相似度阈值，每次请求都注入）。"""
        all_nodes = self.md_store.list_nodes()
        return [
            n for n in all_nodes
            if n.metadata.get("node_type") == "meta_rule"
        ]

    def save(self, force: bool = False) -> bool:
        """保存向量索引到磁盘。仅在有脏数据或 force=True 时写入。"""
        if not self.config.vector_persistence_enabled:
            return False
        if not force and not self._dirty:
            return True
        self._flush_embedding_cache()
        from src.common.knowledge_tree.storage.vector_persistence import (
            save_vector_index,
        )
        ok = save_vector_index(
            self.vector_store,
            self.config.markdown_root,
            self.embedder_type,
            self._vector_index_path,
        )
        if ok:
            self._dirty = False
        return ok

    def mark_dirty(self) -> None:
        """标记向量索引为脏，下次 save() 时写入磁盘。"""
        self._dirty = True

    def _load_vector_index(self) -> bool:
        """尝试从磁盘加载向量索引。返回 True 表示加载成功。"""
        if not self.config.vector_persistence_enabled:
            return False
        from src.common.knowledge_tree.storage.vector_persistence import (
            load_vector_index,
        )
        return load_vector_index(
            self.vector_store,
            self.config.markdown_root,
            self.embedder_type,
            self._vector_index_path,
        )

    def _flush_embedding_cache(self) -> None:
        """Flush embedding cache to disk if available."""
        cache = getattr(self.embedder, "_cache", None)
        if cache is not None and hasattr(cache, "flush"):
            cache.flush()
