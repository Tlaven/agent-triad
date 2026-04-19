"""Bootstrap：从种子数据构建初始知识树。

聚类策略：
- GMM+UMAP（需要 scikit-learn + umap-learn）：多层递归聚类，自动决定深度
- 简单余弦 BFS 连通分量（零依赖回退）：单层 flat 聚类
- ``cluster_method`` 配置项控制："auto"（推荐）| "gmm" | "simple"
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from src.common.knowledge_tree.config import KnowledgeTreeConfig
from src.common.knowledge_tree.dag.edge import KnowledgeEdge
from src.common.knowledge_tree.dag.node import KnowledgeNode
from src.common.knowledge_tree.storage.graph_store import BaseGraphStore, InMemoryGraphStore
from src.common.knowledge_tree.storage.markdown_store import MarkdownStore
from src.common.knowledge_tree.storage.sync import sync_node_to_stores
from src.common.knowledge_tree.storage.vector_store import BaseVectorStore, InMemoryVectorStore

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 可选依赖检测
# ---------------------------------------------------------------------------

_GMM_AVAILABLE = False
_UMAP_AVAILABLE = False

try:
    from sklearn.mixture import GaussianMixture  # noqa: F401

    _GMM_AVAILABLE = True
except ImportError:
    pass

try:
    import umap as _umap_module  # noqa: F401

    _UMAP_AVAILABLE = True
except ImportError:
    pass


# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------


@dataclass
class BootstrapReport:
    """Bootstrap 结果报告。"""

    nodes_created: int = 0
    edges_created: int = 0
    embeddings_generated: int = 0
    max_depth: int = 0
    cluster_method_used: str = ""
    errors: list[str] = field(default_factory=list)


@dataclass
class _TreeBuild:
    """内部用：聚类产出的树结构。"""

    root: KnowledgeNode
    intermediate_nodes: list[KnowledgeNode]  # 所有中间层 group 节点
    edges: list[tuple[str, str]]  # (parent_id, child_id)
    max_depth: int


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------


def bootstrap_from_seed_files(
    seed_dir: str | Path,
    md_store: MarkdownStore,
    graph_store: BaseGraphStore,
    vector_store: BaseVectorStore,
    embedder: Callable[[str], list[float]],
    config: KnowledgeTreeConfig,
) -> BootstrapReport:
    """从种子 Markdown 文件构建初始知识树。

    流程：
    1. 读取种子目录下的 .md 文件
    2. 解析为 KnowledgeNode
    3. 为每个节点生成嵌入
    4. 按 ``cluster_method`` 选择聚类策略，构建多层树
    5. 同步到三层存储

    Args:
        seed_dir: 种子 Markdown 文件目录。
        md_store: Markdown 存储层。
        graph_store: 图数据库。
        vector_store: 向量存储。
        embedder: 嵌入函数。
        config: 知识树配置。

    Returns:
        BootstrapReport 统计信息。
    """
    report = BootstrapReport()
    seed_path = Path(seed_dir)

    if not seed_path.exists():
        report.errors.append(f"Seed directory not found: {seed_dir}")
        return report

    # 1. 读取种子文件
    nodes: list[KnowledgeNode] = []
    for md_file in sorted(seed_path.glob("*.md")):
        try:
            text = md_file.read_text(encoding="utf-8")
            node = KnowledgeNode.from_frontmatter_md(text)
            nodes.append(node)
        except Exception as e:
            report.errors.append(f"Failed to parse {md_file.name}: {e}")
            logger.warning("Skipping seed file %s: %s", md_file.name, e)

    if not nodes:
        report.errors.append("No valid seed files found")
        return report

    # 2. 生成嵌入
    for node in nodes:
        try:
            node.embedding = embedder(node.content)
            report.embeddings_generated += 1
        except Exception as e:
            report.errors.append(f"Embedding failed for {node.node_id}: {e}")

    # 3. 构建树结构
    tree = _build_tree(nodes, embedder, config)
    report.cluster_method_used = tree.root.metadata.get("cluster_method", "unknown")

    # 4. 写入存储
    graph_store.initialize()

    # 根节点
    sync_node_to_stores(tree.root, md_store, graph_store, vector_store)
    report.nodes_created += 1

    # 中间节点
    for node in tree.intermediate_nodes:
        sync_node_to_stores(node, md_store, graph_store, vector_store)
        report.nodes_created += 1

    # 叶子节点
    for node in nodes:
        sync_node_to_stores(node, md_store, graph_store, vector_store)
        report.nodes_created += 1

    # 边
    for parent_id, child_id in tree.edges:
        graph_store.upsert_edge(KnowledgeEdge.create(
            parent_id=parent_id,
            child_id=child_id,
            is_primary=True,
        ))
        report.edges_created += 1

    report.max_depth = tree.max_depth
    return report


# ---------------------------------------------------------------------------
# 树构建调度
# ---------------------------------------------------------------------------


def _build_tree(
    leaf_nodes: list[KnowledgeNode],
    embedder: Callable[[str], list[float]],
    config: KnowledgeTreeConfig,
) -> _TreeBuild:
    """根据配置选择聚类策略并构建树。"""
    use_gmm = (
        config.cluster_method == "gmm"
        or (config.cluster_method == "auto" and _GMM_AVAILABLE and len(leaf_nodes) >= config.cluster_size)
    )

    if use_gmm and _GMM_AVAILABLE:
        return _build_tree_gmm(leaf_nodes, embedder, config.cluster_size)

    return _build_tree_simple(leaf_nodes, embedder)


# ---------------------------------------------------------------------------
# GMM+UMAP 层次聚类（借鉴 LeanRAG 核心算法）
# ---------------------------------------------------------------------------


def _build_tree_gmm(
    leaf_nodes: list[KnowledgeNode],
    embedder: Callable[[str], list[float]],
    cluster_size: int = 20,
) -> _TreeBuild:
    """用 GMM+UMAP 递归聚类构建多层树。

    算法流程（借鉴 LeanRAG）：
    1. 叶子节点嵌入 → UMAP 降维 → GMM 聚类
    2. 每个簇创建摘要中间节点
    3. 用中间节点嵌入重复步骤 1-2，直到簇数 ≤ 1 或节点数过少
    4. 若顶层仍有多个节点，创建根连接它们
    """
    from sklearn.mixture import GaussianMixture

    import numpy as np

    all_intermediate: list[KnowledgeNode] = []
    all_edges: list[tuple[str, str]] = []

    current_nodes = list(leaf_nodes)
    depth = 0

    # 计算预期最大深度
    max_depth_estimate = max(1, round(math.log(max(len(current_nodes), 2), max(cluster_size, 2))) + 1)

    for _layer_idx in range(max_depth_estimate):
        n = len(current_nodes)
        if n <= 1:
            break

        # 收集嵌入矩阵
        embeddings_list = [nd.embedding for nd in current_nodes if nd.embedding is not None]
        if len(embeddings_list) < 2:
            break
        embeddings = np.array(embeddings_list)

        # 节点数不足以继续聚类
        max_k = max(n // cluster_size, 1)
        if max_k <= 1:
            break

        # UMAP 降维（如果可用且节点数足够）
        reduced = _umap_reduce(embeddings)

        # BIC 选最优 k
        optimal_k = _find_optimal_gmm_k(reduced, max_k, min_clusters=2)
        if optimal_k <= 1:
            break

        # GMM 聚类
        gm = GaussianMixture(n_components=optimal_k, random_state=42, n_init=5)
        labels = gm.fit_predict(reduced)

        # 按标签分组
        groups: dict[int, list[KnowledgeNode]] = {}
        for idx, label in enumerate(labels):
            groups.setdefault(int(label), []).append(current_nodes[idx])

        # 过滤空簇
        groups = {k: v for k, v in groups.items() if len(v) > 0}
        if len(groups) <= 1:
            break

        # 创建中间节点
        next_level_nodes: list[KnowledgeNode] = []
        for _label, members in groups.items():
            group_node = _create_group_node(members, embedder, depth=depth + 1)
            all_intermediate.append(group_node)

            for member in members:
                all_edges.append((group_node.node_id, member.node_id))

            next_level_nodes.append(group_node)

        current_nodes = next_level_nodes
        depth += 1

    # 创建根节点
    if len(current_nodes) == 1 and current_nodes[0] in all_intermediate:
        # 唯一的顶层节点就是根
        root = current_nodes[0]
        root.metadata["cluster_method"] = "gmm"
    else:
        # 创建新根连接所有顶层节点
        root = KnowledgeNode.create(
            title="Knowledge Root",
            content="Root of the knowledge tree",
            summary="Root node",
            source="system",
            metadata={"cluster_method": "gmm"},
        )
        root.embedding = embedder(root.content)

        for node in current_nodes:
            all_edges.append((root.node_id, node.node_id))

        depth += 1

    return _TreeBuild(
        root=root,
        intermediate_nodes=[n for n in all_intermediate if n.node_id != root.node_id],
        edges=all_edges,
        max_depth=depth + 1,  # +1 for leaf level
    )


def _umap_reduce(embeddings: Any) -> Any:
    """UMAP 降维，不可用时返回原始嵌入。"""
    if not _UMAP_AVAILABLE:
        return embeddings

    n = len(embeddings)
    if n < 6:  # UMAP 需要足够数据点
        return embeddings

    import numpy as np

    n_neighbors = min(15, n - 1)
    n_components = min(2, n - 2)
    if n_components < 1:
        return embeddings

    reducer = _umap_module.UMAP(
        n_neighbors=n_neighbors,
        n_components=n_components,
        metric="cosine",
        random_state=42,
    )
    return reducer.fit_transform(embeddings)


def _find_optimal_gmm_k(
    embeddings: Any,
    max_k: int,
    min_clusters: int = 2,
) -> int:
    """用 BIC 准则找最优 GMM 簇数。"""
    from sklearn.mixture import GaussianMixture

    if max_k < min_clusters:
        return max_k

    best_k = min_clusters
    best_bic = float("inf")

    for k in range(min_clusters, max_k + 1):
        gm = GaussianMixture(n_components=k, random_state=42, n_init=3)
        gm.fit(embeddings)
        bic = gm.bic(embeddings)
        if bic < best_bic:
            best_bic = bic
            best_k = k

    return best_k


# ---------------------------------------------------------------------------
# 简单余弦 BFS 聚类（零依赖回退）
# ---------------------------------------------------------------------------


def _build_tree_simple(
    leaf_nodes: list[KnowledgeNode],
    embedder: Callable[[str], list[float]],
) -> _TreeBuild:
    """单层 flat 聚类（root → group → leaf），零外部依赖。"""
    root = KnowledgeNode.create(
        title="Knowledge Root",
        content="Root of the knowledge tree",
        summary="Root node",
        source="system",
        metadata={"cluster_method": "simple"},
    )
    root.embedding = embedder(root.content)

    groups: dict[int, list[KnowledgeNode]] = _semantic_cluster(leaf_nodes, threshold=0.6)

    intermediate: list[KnowledgeNode] = []
    edges: list[tuple[str, str]] = []

    for _group_id, members in groups.items():
        group_title = _derive_group_title(members)
        group_node = KnowledgeNode.create(
            title=group_title,
            content=f"Category: {group_title}",
            summary=f"Semantic cluster: {group_title} ({len(members)} nodes)",
            source="bootstrap",
            metadata={"cluster_method": "simple", "cluster_size": len(members)},
        )
        group_node.embedding = embedder(group_node.content)

        edges.append((root.node_id, group_node.node_id))
        intermediate.append(group_node)

        for node in members:
            edges.append((group_node.node_id, node.node_id))

    return _TreeBuild(
        root=root,
        intermediate_nodes=intermediate,
        edges=edges,
        max_depth=3,  # root → group → leaf
    )


def _semantic_cluster(
    nodes: list[KnowledgeNode],
    threshold: float = 0.6,
) -> dict[int, list[KnowledgeNode]]:
    """基于嵌入相似度的连通分量聚类。

    用余弦相似度构建邻接矩阵，连通分量即为聚类。
    无需外部依赖（networkx/leidenalg），零成本回退。
    """
    n = len(nodes)
    if n == 0:
        return {}
    if n == 1:
        return {0: nodes}

    adj: dict[int, list[int]] = {i: [] for i in range(n)}
    for i in range(n):
        for j in range(i + 1, n):
            ei = nodes[i].embedding
            ej = nodes[j].embedding
            if ei is not None and ej is not None:
                sim = _cosine_similarity(ei, ej)
                if sim >= threshold:
                    adj[i].append(j)
                    adj[j].append(i)

    visited: set[int] = set()
    clusters: dict[int, list[KnowledgeNode]] = {}
    cluster_id = 0

    for start in range(n):
        if start in visited:
            continue
        queue = [start]
        visited.add(start)
        members: list[KnowledgeNode] = []
        while queue:
            cur = queue.pop(0)
            members.append(nodes[cur])
            for neighbor in adj[cur]:
                if neighbor not in visited:
                    visited.add(neighbor)
                    queue.append(neighbor)
        clusters[cluster_id] = members
        cluster_id += 1

    return clusters


# ---------------------------------------------------------------------------
# 共享工具
# ---------------------------------------------------------------------------


def _create_group_node(
    members: list[KnowledgeNode],
    embedder: Callable[[str], list[float]],
    depth: int = 1,
) -> KnowledgeNode:
    """从簇成员创建摘要中间节点。

    P1 策略：启发式标题 + 内容列表（不调用 LLM）。
    P2 将引入 LLM 生成摘要。
    """
    group_title = _derive_group_title(members)
    content_lines = [f"- {m.title}: {(m.content or '')[:80]}" for m in members[:10]]
    content = f"Cluster: {group_title}\n\nMembers:\n" + "\n".join(content_lines)

    node = KnowledgeNode.create(
        title=group_title,
        content=content,
        summary=f"Level {depth} cluster: {len(members)} items",
        source="bootstrap:gmm",
        metadata={
            "cluster_method": "gmm",
            "cluster_size": len(members),
            "depth": depth,
        },
    )
    node.embedding = embedder(content)
    return node


def _derive_group_title(nodes: list[KnowledgeNode]) -> str:
    """从组内节点推导分组标题。

    策略：取组内节点标题的最长公共前缀，或取首个节点标题的前 20 字。
    """
    if not nodes:
        return "Uncategorized"

    titles = [n.title for n in nodes if n.title]
    if not titles:
        return "Untitled Group"

    prefix = titles[0]
    for t in titles[1:]:
        common = []
        for a, b in zip(prefix, t):
            if a == b:
                common.append(a)
            else:
                break
        prefix = "".join(common)

    if len(prefix) >= 2:
        return prefix

    return titles[0][:20]


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
