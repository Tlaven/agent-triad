"""知识树 LangChain 工具构建。"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from langchain_core.tools import tool as lc_tool

from src.common.knowledge_tree.config import KnowledgeTreeConfig
from src.common.knowledge_tree.dag.node import KnowledgeNode
from src.common.knowledge_tree.factory import get_or_create_kt


def build_knowledge_tree_tools(runtime_context: Any) -> list:
    """构建知识树 Supervisor 工具列表。

    P1 工具：retrieve, ingest。
    P2 工具：status, list（Agent 可见性）。
    惰性初始化：KnowledgeTree 实例在首次工具调用时才创建。
    实例按 markdown_root 路径缓存，Graph 节点和工具共用同一缓存。
    """
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

    def _sync_add_meta_rule(
        title: str, content: str, priority: int = 0, aliases: list[str] | None = None
    ) -> str:
        aliases = aliases or []
        kt = get_or_create_kt(config)
        existing = kt.get_meta_rules()
        for n in existing:
            if n.title == title:
                n.content = content
                n.metadata["priority"] = priority
                n.metadata["node_type"] = "meta_rule"
                if aliases:
                    n.metadata["aliases"] = aliases
                kt.md_store.write_node(n)
                _reindex_aliases(kt, n.node_id, aliases)
                return json.dumps(
                    {"ok": True, "action": "updated", "node_id": n.node_id},
                    ensure_ascii=False,
                )

        metadata: dict[str, Any] = {"node_type": "meta_rule", "priority": priority}
        if aliases:
            metadata["aliases"] = aliases
        node = KnowledgeNode.create(
            node_id="",
            title=title,
            content=content,
            source="agent:supervisor",
            metadata=metadata,
        )
        node.embedding = kt.embedder(content)
        meta_dir = "meta_rules"
        kt.md_store.ensure_directory(meta_dir)
        from src.common.knowledge_tree.ingestion.ingest import (
            _unique_node_id,
        )

        node.node_id = _unique_node_id(kt.md_store, meta_dir, title)
        node.directory = meta_dir
        kt.md_store.write_node(node)
        kt.vector_store.upsert_embedding(node.node_id, node.embedding)
        _reindex_aliases(kt, node.node_id, aliases)
        return json.dumps(
            {"ok": True, "action": "created", "node_id": node.node_id},
            ensure_ascii=False,
        )

    def _reindex_aliases(kt: Any, node_id: str, aliases: list[str]) -> None:
        """重建节点的 alias embedding 索引。"""
        alias_prefix = f"alias:{node_id}:"
        for key in [k for k in kt.vector_store._embeddings if k.startswith(alias_prefix)]:
            del kt.vector_store._embeddings[key]
        for i, alias in enumerate(aliases):
            alias_emb = kt.embedder(alias)
            kt.vector_store.upsert_embedding(f"alias:{node_id}:{i}", alias_emb)

    def _sync_list_meta_rules() -> str:
        kt = get_or_create_kt(config)
        rules = kt.get_meta_rules()
        items = [
            {
                "node_id": r.node_id,
                "title": r.title,
                "priority": r.metadata.get("priority", 0),
                "aliases": r.metadata.get("aliases", []),
                "content_preview": r.content[:120],
            }
            for r in sorted(
                rules, key=lambda r: r.metadata.get("priority", 0), reverse=True
            )
        ]
        return json.dumps(
            {"ok": True, "total": len(items), "rules": items}, ensure_ascii=False
        )

    @lc_tool
    async def knowledge_tree_add_meta_rule(
        title: str, content: str, priority: int = 0, aliases: list[str] | None = None
    ) -> str:
        """Add or update a persistent meta-rule in the knowledge tree.

        Meta-rules are behavioral directives injected into EVERY request as
        system-level instructions (not as reference information). Use them for
        rules the agent MUST follow regardless of context.

        Args:
            title: Short descriptive title for the rule.
            content: The rule text. Be specific and imperative.
            priority: Higher priority rules appear first. Default 0.
            aliases: Optional trigger phrases expanding the rule's RAG retrievability.
                     E.g. ["vague request", "unclear task"]. Default None.
        """
        return await asyncio.to_thread(_sync_add_meta_rule, title, content, priority, aliases or [])

    @lc_tool
    async def knowledge_tree_list_meta_rules() -> str:
        """List all persistent meta-rules currently active in the knowledge tree.

        Returns each rule's title, priority, and content preview.
        """
        return await asyncio.to_thread(_sync_list_meta_rules)

    def _sync_record_feedback(query_id: str, satisfaction: bool, feedback: str) -> str:
        kt = get_or_create_kt(config)
        kt.record_feedback(query_id, satisfaction, feedback)
        return json.dumps({"ok": True, "query_id": query_id, "satisfaction": satisfaction})

    @lc_tool
    async def knowledge_tree_record_feedback(
        query_id: str, satisfaction: bool, feedback: str = ""
    ) -> str:
        """Record whether the retrieval results were useful.

        Call this after evaluating retrieval results quality.
        This feedback improves the knowledge tree's self-optimization signals.

        Args:
            query_id: The query_id from a previous knowledge_tree_retrieve call.
            satisfaction: True if results were relevant, False otherwise.
            feedback: Optional explanation of what was wrong or missing.
        """
        return await asyncio.to_thread(_sync_record_feedback, query_id, satisfaction, feedback)

    return [
        knowledge_tree_retrieve,
        knowledge_tree_ingest,
        knowledge_tree_status,
        knowledge_tree_list,
        knowledge_tree_overlay,
        knowledge_tree_tree,
        knowledge_tree_reorganize,
        knowledge_tree_add_meta_rule,
        knowledge_tree_list_meta_rules,
        knowledge_tree_record_feedback,
    ]
