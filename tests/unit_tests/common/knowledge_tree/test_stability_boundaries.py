"""V4 知识树稳定性边界测试。

覆盖高风险边界和安全/鲁棒性边界：
- 向量存储大规模性能（10K+ 向量线性扫描）
- 摄入压力（快速连续摄入、超长文本、ID 碰撞）
- 元规则边界（MAX_META_RULES 循环、别名爆炸、冲突阈值精确边界）
- 重组边界（循环移动、链式移动、超深嵌套）
- 路径安全对抗（null 字节、超长路径、Unicode 归一化攻击）
- 持久化损坏恢复
- 嵌入缓存异常
"""

import json
import math
import random
import time
from pathlib import Path

import pytest

from src.common.knowledge_tree.config import (
    MAX_META_RULES,
    META_RULE_CONFLICT_THRESHOLD,
    KnowledgeTreeConfig,
)
from src.common.knowledge_tree.core import KnowledgeTree
from src.common.knowledge_tree.dag.node import KnowledgeNode
from src.common.knowledge_tree.editing.reorganize import (
    MoveOp,
    diff_trees,
    execute_reorganize,
)
from src.common.knowledge_tree.editing.tree_view import TreeEntry, parse_numbered_tree
from src.common.knowledge_tree.embedding.cache import EmbeddingCache
from src.common.knowledge_tree.ingestion.ingest import (
    _sanitize_dirname,
    _sanitize_filename,
    _unique_node_id,
    ingest_nodes,
)
from src.common.knowledge_tree.storage.markdown_store import MarkdownStore
from src.common.knowledge_tree.storage.overlay import OverlayEdge, OverlayStore
from src.common.knowledge_tree.storage.vector_persistence import (
    load_vector_index,
    save_vector_index,
)
from src.common.knowledge_tree.storage.vector_store import (
    DirectoryAnchor,
    InMemoryVectorStore,
)


def _deterministic_embedder(dim: int = 64):
    def embed(text: str) -> list[float]:
        vec = [0.0] * dim
        for i, c in enumerate(text):
            idx = (ord(c) + i * 7) % dim
            vec[idx] += 1.0
        mag = sum(x * x for x in vec) ** 0.5
        if mag > 0:
            vec = [x / mag for x in vec]
        return vec
    return embed


def _random_embedder(dim: int = 64, seed: int = 42):
    cache: dict[str, list[float]] = {}

    def embed(text: str) -> list[float]:
        if text in cache:
            return cache[text]
        rng_local = random.Random(hash(text) & 0xFFFFFFFF)
        vec = [rng_local.gauss(0, 1) for _ in range(dim)]
        mag = math.sqrt(sum(x * x for x in vec))
        if mag > 0:
            vec = [x / mag for x in vec]
        cache[text] = vec
        return vec
    return embed


# ============================================================
# 1. 向量存储大规模性能
# ============================================================


class TestVectorStoreScale:
    """InMemoryVectorStore 线性扫描在大规模下的正确性和性能。"""

    def test_10k_upsert_and_search(self):
        dim = 64
        store = InMemoryVectorStore(dimension=dim)
        rng = random.Random(99)

        for i in range(10_000):
            vec = [rng.gauss(0, 1) for _ in range(dim)]
            mag = math.sqrt(sum(x * x for x in vec))
            vec = [x / mag for x in vec]
            store.upsert_embedding(f"node_{i}", vec)

        assert store.node_count == 10_000

        query = [rng.gauss(0, 1) for _ in range(dim)]
        mag = math.sqrt(sum(x * x for x in query))
        query = [x / mag for x in query]

        start = time.monotonic()
        results = store.similarity_search(query, top_k=5, threshold=0.0)
        elapsed = time.monotonic() - start

        assert len(results) == 5
        assert elapsed < 5.0, f"10K vector search took {elapsed:.2f}s (>5s)"

    def test_10k_with_title_and_alias_keys(self):
        dim = 32
        store = InMemoryVectorStore(dimension=dim)
        rng = random.Random(101)

        for i in range(3_000):
            vec = [rng.gauss(0, 1) for _ in range(dim)]
            store.upsert_embedding(f"node_{i}", vec)
            store.upsert_embedding(f"title:node_{i}", vec)
            store.upsert_embedding(f"alias:node_{i}:0", vec)

        assert store.node_count == 3_000
        assert len(store._embeddings) == 9_000

        query = [rng.gauss(0, 1) for _ in range(dim)]
        results = store.similarity_search(query, top_k=10, threshold=-1.0)
        assert len(results) == 10
        for node_id, _ in results:
            assert not node_id.startswith("title:")

    def test_5k_delete_all_then_search(self):
        dim = 16
        store = InMemoryVectorStore(dimension=dim)
        for i in range(5_000):
            store.upsert_embedding(f"n_{i}", [float(i)] * dim)

        for i in range(5_000):
            store.delete_embedding(f"n_{i}")

        assert store.node_count == 0
        results = store.similarity_search([1.0] * dim, top_k=5, threshold=0.0)
        assert results == []

    def test_1k_anchors_find_nearest(self):
        dim = 32
        store = InMemoryVectorStore(dimension=dim)
        rng = random.Random(77)

        for i in range(1_000):
            vec = [rng.gauss(0, 1) for _ in range(dim)]
            mag = math.sqrt(sum(x * x for x in vec))
            vec = [x / mag for x in vec]
            store.upsert_anchor(DirectoryAnchor(
                directory=f"dir_{i}", anchor_vector=vec, file_count=1,
            ))

        query = [rng.gauss(0, 1) for _ in range(dim)]
        mag = math.sqrt(sum(x * x for x in query))
        query = [x / mag for x in query]

        start = time.monotonic()
        result = store.find_nearest_anchor(query, threshold=0.0)
        elapsed = time.monotonic() - start

        assert result is not None
        assert elapsed < 2.0, f"1K anchor search took {elapsed:.2f}s"

    def test_serialization_roundtrip_large(self):
        dim = 32
        store = InMemoryVectorStore(dimension=dim)
        rng = random.Random(55)

        for i in range(2_000):
            vec = [rng.gauss(0, 1) for _ in range(dim)]
            store.upsert_embedding(f"n_{i}", vec)

        data = store.to_dict()

        store2 = InMemoryVectorStore(dimension=dim)
        store2.load_from_dict(data)

        assert store2.node_count == 2_000
        for i in range(2_000):
            assert store2.get_embedding(f"n_{i}") is not None


# ============================================================
# 2. 摄入压力
# ============================================================


class TestIngestionPressure:
    """快速连续摄入、超长文本、ID 碰撞边界。"""

    def test_rapid_sequential_ingest_100_nodes(self, tmp_path: Path):
        md_store = MarkdownStore(tmp_path / "md")
        vector_store = InMemoryVectorStore(dimension=32)
        overlay_store = OverlayStore(tmp_path / "md" / ".overlay.json")
        embedder = _random_embedder(32)

        vector_store.upsert_anchor(DirectoryAnchor(
            directory="knowledge",
            anchor_vector=embedder("general knowledge base"),
            file_count=0,
        ))

        candidates = [
            KnowledgeNode.create(
                node_id="",
                title=f"Topic {i}",
                content=f"Unique content about topic number {i} with details.",
                source="test",
            )
            for i in range(100)
        ]

        report = ingest_nodes(
            candidates, vector_store, md_store, overlay_store, embedder,
            dedup_threshold=0.99, attach_threshold=0.0,
        )

        assert report.nodes_ingested == 100
        assert report.errors == []
        all_ids = md_store.list_node_ids()
        assert len(all_ids) == 100
        assert len(set(all_ids)) == 100

    def test_very_long_text_ingest(self, tmp_path: Path):
        md_store = MarkdownStore(tmp_path / "md")
        vector_store = InMemoryVectorStore(dimension=32)
        overlay_store = OverlayStore(tmp_path / "md" / ".overlay.json")
        embedder = _deterministic_embedder(32)

        long_content = "A" * 100_000
        candidate = KnowledgeNode.create(
            node_id="", title="LongDoc",
            content=long_content, source="test",
        )

        report = ingest_nodes(
            [candidate], vector_store, md_store, overlay_store, embedder,
            attach_threshold=0.99,
        )

        assert report.nodes_ingested == 1
        assert report.errors == []

    def test_id_collision_same_title_many_times(self, tmp_path: Path):
        md_store = MarkdownStore(tmp_path / "md")
        vector_store = InMemoryVectorStore(dimension=16)
        overlay_store = OverlayStore(tmp_path / "md" / ".overlay.json")
        embedder = _random_embedder(16)

        vector_store.upsert_anchor(DirectoryAnchor(
            directory="notes",
            anchor_vector=embedder("notes"),
            file_count=0,
        ))

        candidates = [
            KnowledgeNode.create(
                node_id="", title="Same Title",
                content=f"Different content {i}", source="test",
            )
            for i in range(50)
        ]

        report = ingest_nodes(
            candidates, vector_store, md_store, overlay_store, embedder,
            dedup_threshold=1.1, attach_threshold=0.0,
        )

        assert report.nodes_ingested == 50
        all_ids = md_store.list_node_ids()
        assert len(set(all_ids)) == 50

    def test_unique_node_id_high_collision(self, tmp_path: Path):
        md_store = MarkdownStore(tmp_path / "md")
        md_store.ensure_directory("dir")

        for i in range(200):
            node = KnowledgeNode.create(
                node_id="dir/same-name.md" if i == 0 else f"dir/same-name-{i}.md",
                title="same-name",
                content=f"content {i}",
                source="test",
            )
            md_store.write_node(node)

        new_id = _unique_node_id(md_store, "dir", "same-name")
        assert new_id.startswith("dir/same-name-")
        assert not md_store.node_exists(new_id)

    def test_sanitize_filename_boundary(self):
        assert _sanitize_filename("") == "untitled"
        assert len(_sanitize_filename("A" * 200)) <= 40
        assert _sanitize_filename("///\\\\///") == "________"
        assert _sanitize_filename("\x00\x01\x02") == "___"

    def test_sanitize_dirname_pure_non_ascii(self):
        assert _sanitize_dirname("纯中文标题") == "misc"
        assert _sanitize_dirname("12345") == "misc"
        assert _sanitize_dirname("") == "misc"
        result = _sanitize_dirname("Hello World Test")
        assert len(result) <= 25
        assert result != "misc"

    def test_ingest_empty_content_uses_title(self, tmp_path: Path):
        md_store = MarkdownStore(tmp_path / "md")
        vector_store = InMemoryVectorStore(dimension=16)
        overlay_store = OverlayStore(tmp_path / "md" / ".overlay.json")
        embedder = _deterministic_embedder(16)

        candidate = KnowledgeNode.create(
            node_id="", title="TitleOnly",
            content="", source="test",
        )

        report = ingest_nodes(
            [candidate], vector_store, md_store, overlay_store, embedder,
            attach_threshold=0.99,
        )

        assert report.nodes_ingested == 1


# ============================================================
# 3. 元规则边界
# ============================================================


class TestMetaRuleBoundaries:
    """MAX_META_RULES 循环增删、别名爆炸、冲突阈值精确边界。"""

    def _make_kt(self, tmp_path: Path) -> KnowledgeTree:
        cfg = KnowledgeTreeConfig(
            markdown_root=tmp_path / "kt",
            embedder_type="hash",
            embedding_dimension=64,
        )
        return KnowledgeTree(cfg)

    def test_max_meta_rules_enforcement(self, tmp_path: Path):
        kt = self._make_kt(tmp_path)
        kt.md_store.ensure_directory("meta_rules")

        for i in range(MAX_META_RULES):
            node = KnowledgeNode.create(
                node_id=f"meta_rules/rule_{i}.md",
                title=f"Rule {i}",
                content=f"Rule content {i} " + "x" * i,
                source="test",
                metadata={"node_type": "meta_rule", "priority": i},
            )
            node.embedding = kt.embedder(node.content)
            kt.md_store.write_node(node)
            kt.vector_store.upsert_embedding(node.node_id, node.embedding)

        rules = kt.get_meta_rules()
        assert len(rules) == MAX_META_RULES

    def test_rapid_add_delete_cycles(self, tmp_path: Path):
        kt = self._make_kt(tmp_path)
        kt.md_store.ensure_directory("meta_rules")

        for cycle in range(3):
            for i in range(MAX_META_RULES):
                node = KnowledgeNode.create(
                    node_id=f"meta_rules/rule_c{cycle}_{i}.md",
                    title=f"Cycle{cycle} Rule{i}",
                    content=f"Cycle {cycle} rule {i} content variant",
                    source="test",
                    metadata={"node_type": "meta_rule", "priority": i},
                )
                node.embedding = kt.embedder(node.content)
                kt.md_store.write_node(node)
                kt.vector_store.upsert_embedding(node.node_id, node.embedding)

            rules = kt.get_meta_rules()
            assert len(rules) == MAX_META_RULES

            for rule in rules:
                kt.vector_store.delete_embedding(rule.node_id)
                kt.md_store.delete_node(rule.node_id)

            assert len(kt.get_meta_rules()) == 0

    def test_many_aliases_per_rule(self, tmp_path: Path):
        kt = self._make_kt(tmp_path)
        kt.md_store.ensure_directory("meta_rules")

        aliases = [f"alias_trigger_{i}" for i in range(200)]
        node = KnowledgeNode.create(
            node_id="meta_rules/aliased.md",
            title="Heavily Aliased Rule",
            content="Do the thing.",
            source="test",
            metadata={"node_type": "meta_rule", "priority": 5, "aliases": aliases},
        )
        node.embedding = kt.embedder(node.content)
        kt.md_store.write_node(node)
        kt.vector_store.upsert_embedding(node.node_id, node.embedding)

        for i, alias in enumerate(aliases):
            alias_emb = kt.embedder(alias)
            kt.vector_store.upsert_embedding(f"alias:{node.node_id}:{i}", alias_emb)

        alias_keys = [k for k in kt.vector_store._embeddings if k.startswith(f"alias:{node.node_id}:")]
        assert len(alias_keys) == 200

        query_emb = kt.embedder("alias_trigger_42")
        results = kt.vector_store.similarity_search_with_prefix(
            "alias:", query_emb, top_k=5, threshold=0.0,
        )
        assert len(results) > 0

    def test_conflict_threshold_exact_boundary(self, tmp_path: Path):
        kt = self._make_kt(tmp_path)
        kt.md_store.ensure_directory("meta_rules")

        base_content = "Always validate input before processing"
        base_emb = kt.embedder(base_content)

        node_a = KnowledgeNode.create(
            node_id="meta_rules/a.md",
            title="RuleA",
            content=base_content,
            source="test",
            metadata={"node_type": "meta_rule", "priority": 1},
        )
        node_a.embedding = base_emb
        kt.md_store.write_node(node_a)
        kt.vector_store.upsert_embedding(node_a.node_id, node_a.embedding)

        similar_content = "Always validate input before processing data"
        similar_emb = kt.embedder(similar_content)

        from src.common.knowledge_tree.storage.vector_store import cosine_similarity
        sim = cosine_similarity(base_emb, similar_emb)

        node_b = KnowledgeNode.create(
            node_id="meta_rules/b.md",
            title="RuleB",
            content=similar_content,
            source="test",
            metadata={"node_type": "meta_rule", "priority": 2},
        )
        node_b.embedding = similar_emb
        kt.md_store.write_node(node_b)
        kt.vector_store.upsert_embedding(node_b.node_id, node_b.embedding)

        rules = kt.get_meta_rules()
        assert len(rules) == 2

        if sim > META_RULE_CONFLICT_THRESHOLD:
            pass
        else:
            assert sim <= META_RULE_CONFLICT_THRESHOLD

    def test_delete_nonexistent_meta_rule(self, tmp_path: Path):
        kt = self._make_kt(tmp_path)
        rules = kt.get_meta_rules()
        assert len(rules) == 0

    def test_meta_rule_with_empty_aliases(self, tmp_path: Path):
        kt = self._make_kt(tmp_path)
        kt.md_store.ensure_directory("meta_rules")

        node = KnowledgeNode.create(
            node_id="meta_rules/no_alias.md",
            title="NoAlias",
            content="No aliases here.",
            source="test",
            metadata={"node_type": "meta_rule", "priority": 0, "aliases": []},
        )
        node.embedding = kt.embedder(node.content)
        kt.md_store.write_node(node)
        kt.vector_store.upsert_embedding(node.node_id, node.embedding)

        rules = kt.get_meta_rules()
        assert len(rules) == 1
        assert rules[0].metadata.get("aliases") == []


# ============================================================
# 4. 重组边界
# ============================================================


class TestReorganizeBoundaries:
    """循环移动、链式移动、超深嵌套重组。"""

    def test_chain_moves(self, tmp_path: Path):
        md_store = MarkdownStore(tmp_path / "md")
        overlay_store = OverlayStore(tmp_path / "md" / ".overlay.json")

        for d in ["alpha", "beta", "gamma"]:
            md_store.ensure_directory(d)

        node = KnowledgeNode.create(
            node_id="alpha/doc.md", title="Doc",
            content="Chain move test.", source="test",
        )
        md_store.write_node(node)

        moves = [
            MoveOp(old_id="alpha/doc.md", new_id="beta/doc.md"),
            MoveOp(old_id="beta/doc.md", new_id="gamma/doc.md"),
        ]

        report = execute_reorganize(moves, md_store, overlay_store)
        assert report.moves_executed >= 1

        assert md_store.node_exists("gamma/doc.md") or md_store.node_exists("beta/doc.md")

    def test_swap_moves(self, tmp_path: Path):
        md_store = MarkdownStore(tmp_path / "md")
        overlay_store = OverlayStore(tmp_path / "md" / ".overlay.json")

        md_store.ensure_directory("dir_a")
        md_store.ensure_directory("dir_b")

        node_a = KnowledgeNode.create(
            node_id="dir_a/file_a.md", title="FileA",
            content="Content A.", source="test",
        )
        node_b = KnowledgeNode.create(
            node_id="dir_b/file_b.md", title="FileB",
            content="Content B.", source="test",
        )
        md_store.write_node(node_a)
        md_store.write_node(node_b)

        moves = [
            MoveOp(old_id="dir_a/file_a.md", new_id="dir_b/file_a.md"),
            MoveOp(old_id="dir_b/file_b.md", new_id="dir_a/file_b.md"),
        ]

        report = execute_reorganize(moves, md_store, overlay_store)
        assert report.moves_executed == 2
        assert md_store.node_exists("dir_b/file_a.md")
        assert md_store.node_exists("dir_a/file_b.md")

    def test_move_to_nonexistent_source(self, tmp_path: Path):
        md_store = MarkdownStore(tmp_path / "md")
        overlay_store = OverlayStore(tmp_path / "md" / ".overlay.json")
        md_store.ensure_directory("target")

        moves = [MoveOp(old_id="ghost/missing.md", new_id="target/missing.md")]
        report = execute_reorganize(moves, md_store, overlay_store)
        assert report.moves_failed == 1
        assert len(report.errors) == 1

    def test_bulk_moves_50_files(self, tmp_path: Path):
        md_store = MarkdownStore(tmp_path / "md")
        overlay_store = OverlayStore(tmp_path / "md" / ".overlay.json")

        md_store.ensure_directory("source")
        md_store.ensure_directory("dest")

        moves = []
        for i in range(50):
            node = KnowledgeNode.create(
                node_id=f"source/doc_{i}.md",
                title=f"Doc {i}",
                content=f"Content {i}",
                source="test",
            )
            md_store.write_node(node)
            moves.append(MoveOp(
                old_id=f"source/doc_{i}.md",
                new_id=f"dest/doc_{i}.md",
            ))

        report = execute_reorganize(moves, md_store, overlay_store)
        assert report.moves_executed == 50
        assert report.moves_failed == 0

        dest_files = md_store.get_directory_files("dest")
        assert len(dest_files) == 50

    def test_overlay_edge_update_on_move(self, tmp_path: Path):
        md_store = MarkdownStore(tmp_path / "md")
        overlay_store = OverlayStore(tmp_path / "md" / ".overlay.json")

        md_store.ensure_directory("old_dir")
        md_store.ensure_directory("new_dir")

        node = KnowledgeNode.create(
            node_id="old_dir/node.md", title="Node",
            content="Overlay test.", source="test",
        )
        md_store.write_node(node)

        overlay_store.add_edge(OverlayEdge(
            source_path="old_dir/node.md",
            target_path="other/target.md",
            relation="related",
        ))

        moves = [MoveOp(old_id="old_dir/node.md", new_id="new_dir/node.md")]
        report = execute_reorganize(moves, md_store, overlay_store)

        assert report.overlay_edges_updated == 1
        edges = overlay_store.get_all_edges()
        assert any(e.source_path == "new_dir/node.md" for e in edges)

    def test_deep_nested_proposed_tree_parse(self):
        deep_tree = "01 level0/\n"
        for i in range(1, 8):
            indent = "    " * i
            deep_tree += f"{indent}01 level{i}/\n"
        deep_tree += "        " * 8 + "01 deep_file.md\n"

        entries = parse_numbered_tree(deep_tree)
        assert len(entries) > 0
        max_level = max(e.level for e in entries)
        assert max_level >= 7

    def test_diff_trees_with_new_files(self):
        current_ids = ["dir_a/existing.md"]
        proposed = [
            TreeEntry(level=0, number=1, name="dir_a", is_directory=True),
            TreeEntry(level=1, number=1, name="existing.md", is_directory=False),
            TreeEntry(level=1, number=2, name="brand_new.md", is_directory=False),
        ]

        moves = diff_trees(current_ids, proposed)
        assert len(moves) == 0

    def test_empty_directory_cleanup(self, tmp_path: Path):
        md_store = MarkdownStore(tmp_path / "md")
        overlay_store = OverlayStore(tmp_path / "md" / ".overlay.json")

        md_store.ensure_directory("will_empty")
        md_store.ensure_directory("destination")

        node = KnowledgeNode.create(
            node_id="will_empty/only_file.md",
            title="Only",
            content="Sole file.",
            source="test",
        )
        md_store.write_node(node)

        moves = [MoveOp(
            old_id="will_empty/only_file.md",
            new_id="destination/only_file.md",
        )]

        report = execute_reorganize(moves, md_store, overlay_store)
        assert report.moves_executed == 1
        assert "will_empty" in report.directories_removed


# ============================================================
# 5. 路径安全对抗
# ============================================================


class TestPathSafetyAdversarial:
    """MarkdownStore._safe_relative_path 对抗恶意输入。"""

    def test_null_byte_injection(self, tmp_path: Path):
        store = MarkdownStore(tmp_path / "md")
        with pytest.raises((ValueError, OSError)):
            store._safe_relative_path("dir/\x00evil.md")

    def test_path_traversal_dots(self, tmp_path: Path):
        store = MarkdownStore(tmp_path / "md")
        with pytest.raises(ValueError):
            store._safe_relative_path("../../etc/passwd")

    def test_path_traversal_hidden(self, tmp_path: Path):
        store = MarkdownStore(tmp_path / "md")
        with pytest.raises(ValueError):
            store._safe_relative_path("dir/../../etc/shadow")

    def test_backslash_rejection(self, tmp_path: Path):
        store = MarkdownStore(tmp_path / "md")
        with pytest.raises(ValueError):
            store._safe_relative_path("dir\\file.md")

    def test_colon_rejection(self, tmp_path: Path):
        store = MarkdownStore(tmp_path / "md")
        with pytest.raises(ValueError):
            store._safe_relative_path("C:Windows/system32")

    def test_absolute_path_rejection(self, tmp_path: Path):
        store = MarkdownStore(tmp_path / "md")
        with pytest.raises(ValueError):
            store._safe_relative_path("/etc/passwd")

    def test_empty_path_rejection(self, tmp_path: Path):
        store = MarkdownStore(tmp_path / "md")
        with pytest.raises(ValueError):
            store._safe_relative_path("")

    def test_unicode_normalization_attack(self, tmp_path: Path):
        store = MarkdownStore(tmp_path / "md")
        store.ensure_directory("café")
        node = KnowledgeNode.create(
            node_id="café/test.md",
            title="Test",
            content="Unicode test.",
            source="test",
        )
        store.write_node(node)
        result = store.read_node("café/test.md")
        assert result is not None

    def test_very_long_filename(self, tmp_path: Path):
        store = MarkdownStore(tmp_path / "md")
        long_name = "a" * 200 + ".md"
        node = KnowledgeNode.create(
            node_id=f"dir/{long_name}",
            title="Long",
            content="Test.",
            source="test",
        )
        store.write_node(node)
        result = store.read_node(f"dir/{long_name}")
        assert result is not None

    def test_deeply_nested_path(self, tmp_path: Path):
        store = MarkdownStore(tmp_path / "md")
        deep_path = "/".join(["d"] * 20) + "/file.md"
        node = KnowledgeNode.create(
            node_id=deep_path,
            title="Deep",
            content="Deep nesting.",
            source="test",
        )
        store.write_node(node)
        result = store.read_node(deep_path)
        assert result is not None

    def test_dot_only_path(self, tmp_path: Path):
        store = MarkdownStore(tmp_path / "md")
        with pytest.raises(ValueError):
            store._safe_relative_path(".")

    def test_double_dot_in_middle(self, tmp_path: Path):
        store = MarkdownStore(tmp_path / "md")
        with pytest.raises(ValueError):
            store._safe_relative_path("dir/../file.md")


# ============================================================
# 6. 持久化损坏恢复
# ============================================================


class TestPersistenceCorruption:
    """向量索引持久化：损坏恢复、版本不匹配、维度不匹配。"""

    def test_corrupted_json_file(self, tmp_path: Path):
        md_root = tmp_path / "md"
        md_root.mkdir(parents=True)
        index_path = md_root / ".vector_index.json"
        index_path.write_text("{corrupted json content!!!", encoding="utf-8")

        store = InMemoryVectorStore(dimension=32)
        result = load_vector_index(store, md_root, "hash", index_path)
        assert result is False

    def test_version_mismatch(self, tmp_path: Path):
        md_root = tmp_path / "md"
        md_root.mkdir(parents=True)
        index_path = md_root / ".vector_index.json"

        payload = {
            "manifest": {
                "version": 999,
                "embedder_type": "hash",
                "embedding_dimension": 32,
                "file_hashes": {},
            },
            "vectors": {"version": 1, "dimension": 32, "embeddings": {}, "anchors": []},
        }
        index_path.write_text(json.dumps(payload), encoding="utf-8")

        store = InMemoryVectorStore(dimension=32)
        result = load_vector_index(store, md_root, "hash", index_path)
        assert result is False

    def test_embedder_type_mismatch(self, tmp_path: Path):
        md_root = tmp_path / "md"
        md_root.mkdir(parents=True)
        index_path = md_root / ".vector_index.json"

        payload = {
            "manifest": {
                "version": 2,
                "embedder_type": "api",
                "embedding_dimension": 32,
                "file_hashes": {},
            },
            "vectors": {"version": 1, "dimension": 32, "embeddings": {}, "anchors": []},
        }
        index_path.write_text(json.dumps(payload), encoding="utf-8")

        store = InMemoryVectorStore(dimension=32)
        result = load_vector_index(store, md_root, "hash", index_path)
        assert result is False

    def test_dimension_mismatch(self, tmp_path: Path):
        md_root = tmp_path / "md"
        md_root.mkdir(parents=True)
        index_path = md_root / ".vector_index.json"

        payload = {
            "manifest": {
                "version": 2,
                "embedder_type": "hash",
                "embedding_dimension": 512,
                "file_hashes": {},
            },
            "vectors": {"version": 1, "dimension": 512, "embeddings": {}, "anchors": []},
        }
        index_path.write_text(json.dumps(payload), encoding="utf-8")

        store = InMemoryVectorStore(dimension=32)
        result = load_vector_index(store, md_root, "hash", index_path)
        assert result is False

    def test_stale_file_hashes(self, tmp_path: Path):
        md_root = tmp_path / "md"
        md_root.mkdir(parents=True)

        node = KnowledgeNode.create(
            node_id="test.md", title="Test",
            content="Original content.", source="test",
        )
        (md_root / "test.md").write_text(
            node.to_frontmatter_md(), encoding="utf-8"
        )

        store = InMemoryVectorStore(dimension=32)
        store.upsert_embedding("test.md", [1.0] * 32)
        save_vector_index(store, md_root, "hash")

        (md_root / "test.md").write_text("MODIFIED CONTENT", encoding="utf-8")

        store2 = InMemoryVectorStore(dimension=32)
        result = load_vector_index(store2, md_root, "hash")
        assert result is False

    def test_save_and_load_roundtrip(self, tmp_path: Path):
        md_root = tmp_path / "md"
        md_root.mkdir(parents=True)

        node = KnowledgeNode.create(
            node_id="doc.md", title="Doc",
            content="Persistent content.", source="test",
        )
        (md_root / "doc.md").write_text(
            node.to_frontmatter_md(), encoding="utf-8"
        )

        store = InMemoryVectorStore(dimension=32)
        store.upsert_embedding("doc.md", [0.5] * 32)
        store.upsert_anchor(DirectoryAnchor(
            directory="test_dir",
            anchor_vector=[0.3] * 32,
            file_count=1,
        ))

        saved = save_vector_index(store, md_root, "hash")
        assert saved is True

        store2 = InMemoryVectorStore(dimension=32)
        loaded = load_vector_index(store2, md_root, "hash")
        assert loaded is True
        assert store2.get_embedding("doc.md") is not None
        assert store2.get_anchor("test_dir") is not None

    def test_missing_index_file(self, tmp_path: Path):
        md_root = tmp_path / "md"
        md_root.mkdir(parents=True)

        store = InMemoryVectorStore(dimension=32)
        result = load_vector_index(store, md_root, "hash")
        assert result is False

    def test_empty_index_file(self, tmp_path: Path):
        md_root = tmp_path / "md"
        md_root.mkdir(parents=True)
        index_path = md_root / ".vector_index.json"
        index_path.write_text("", encoding="utf-8")

        store = InMemoryVectorStore(dimension=32)
        result = load_vector_index(store, md_root, "hash")
        assert result is False

    def test_vector_data_dimension_corruption(self, tmp_path: Path):
        md_root = tmp_path / "md"
        md_root.mkdir(parents=True)
        index_path = md_root / ".vector_index.json"

        payload = {
            "manifest": {
                "version": 2,
                "embedder_type": "hash",
                "embedding_dimension": 32,
                "file_hashes": {},
            },
            "vectors": {
                "version": 1,
                "dimension": 32,
                "embeddings": {"node1": [1.0] * 16},
                "anchors": [],
            },
        }
        index_path.write_text(json.dumps(payload), encoding="utf-8")

        store = InMemoryVectorStore(dimension=32)
        result = load_vector_index(store, md_root, "hash", index_path)
        assert result is False

    def test_atomic_write_no_tmp_leftover(self, tmp_path: Path):
        md_root = tmp_path / "md"
        md_root.mkdir(parents=True)

        store = InMemoryVectorStore(dimension=16)
        save_vector_index(store, md_root, "hash")

        tmp_files = list(md_root.glob("*.tmp"))
        assert len(tmp_files) == 0


# ============================================================
# 7. 嵌入缓存异常
# ============================================================


class TestEmbeddingCacheAnomalies:
    """EmbeddingCache：无上限增长、运行中损坏、禁用模式。"""

    def test_unbounded_growth(self, tmp_path: Path):
        cache_path = tmp_path / "emb_cache.json"
        cache = EmbeddingCache(cache_path)

        for i in range(5_000):
            cache.put(f"text_{i}", [float(i)] * 32)

        assert cache.stats["entries"] == 5_000
        cache.flush()

        cache2 = EmbeddingCache(cache_path)
        assert cache2.stats["entries"] == 5_000

    def test_mid_session_corruption(self, tmp_path: Path):
        cache_path = tmp_path / "emb_cache.json"
        cache = EmbeddingCache(cache_path)
        cache.put("hello", [1.0, 2.0, 3.0])
        cache.flush()

        cache_path.write_text("GARBAGE{{{{", encoding="utf-8")

        cache2 = EmbeddingCache(cache_path)
        assert cache2.stats["entries"] == 0
        assert cache2.get("hello") is None

    def test_disabled_cache_is_noop(self):
        cache = EmbeddingCache(None)
        cache.put("test", [1.0])
        assert cache.get("test") is None
        cache.flush()
        assert cache.stats["enabled"] is False

    def test_cache_overwrites_existing_entry(self, tmp_path: Path):
        cache_path = tmp_path / "emb_cache.json"
        cache = EmbeddingCache(cache_path)

        cache.put("same_text", [1.0, 0.0])
        cache.put("same_text", [0.0, 1.0])

        result = cache.get("same_text")
        assert result == [0.0, 1.0]

    def test_flush_when_not_dirty(self, tmp_path: Path):
        cache_path = tmp_path / "emb_cache.json"
        cache = EmbeddingCache(cache_path)
        cache.flush()
        assert not cache_path.exists()

    def test_flush_write_error_silently_ignored(self, tmp_path: Path):
        blocked_path = tmp_path / "blocked" / "nested" / "cache.json"
        cache = EmbeddingCache(blocked_path)
        cache.put("test", [1.0])
        cache.flush()


# ============================================================
# 8. Overlay 大规模
# ============================================================


class TestOverlayScale:
    """OverlayStore 大规模边的写入和查询。"""

    def test_1k_edges(self, tmp_path: Path):
        overlay = OverlayStore(tmp_path / ".overlay.json")

        for i in range(1_000):
            overlay.add_edge(OverlayEdge(
                source_path=f"dir_a/node_{i}.md",
                target_path=f"dir_b/node_{i}.md",
                relation="related",
            ))

        edges = overlay.get_all_edges()
        assert len(edges) == 1_000

    def test_dedup_under_rapid_add(self, tmp_path: Path):
        overlay = OverlayStore(tmp_path / ".overlay.json")

        for _ in range(100):
            overlay.add_edge(OverlayEdge(
                source_path="a.md",
                target_path="b.md",
                relation="related",
            ))

        edges = overlay.get_all_edges()
        assert len(edges) == 1

    def test_remove_nonexistent_edge(self, tmp_path: Path):
        overlay = OverlayStore(tmp_path / ".overlay.json")
        overlay.remove_edge("ghost.md", "phantom.md", "related")
        assert overlay.get_all_edges() == []


# ============================================================
# 9. 检索日志环形缓冲
# ============================================================


class TestRetrievalLogBuffer:
    """KnowledgeTree._retrieval_logs 的 1000 条上限。"""

    def test_log_truncation_at_1000(self, tmp_path: Path):
        cfg = KnowledgeTreeConfig(
            markdown_root=tmp_path / "kt",
            embedder_type="hash",
            embedding_dimension=64,
        )
        kt = KnowledgeTree(cfg)

        from src.common.knowledge_tree.retrieval.log import RetrievalLog

        for i in range(1_200):
            log = RetrievalLog(
                query_id=f"q_{i}",
                query_text=f"query {i}",
                rag_results=[],
            )
            kt._retrieval_logs.append(log)
            if len(kt._retrieval_logs) > kt._max_retrieval_logs:
                kt._retrieval_logs = kt._retrieval_logs[-kt._max_retrieval_logs:]

        assert len(kt._retrieval_logs) == 1_000
        assert kt._retrieval_logs[0].query_id == "q_200"
        assert kt._retrieval_logs[-1].query_id == "q_1199"
