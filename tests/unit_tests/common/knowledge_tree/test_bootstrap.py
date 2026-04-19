"""Bootstrap 测试。"""

import math
from pathlib import Path

import pytest

from src.common.knowledge_tree.bootstrap import (
    _GMM_AVAILABLE,
    _build_tree_gmm,
    _build_tree_simple,
    _find_optimal_gmm_k,
    _semantic_cluster,
    bootstrap_from_seed_files,
)
from src.common.knowledge_tree.config import KnowledgeTreeConfig
from src.common.knowledge_tree.dag.node import KnowledgeNode
from src.common.knowledge_tree.storage.graph_store import InMemoryGraphStore
from src.common.knowledge_tree.storage.markdown_store import MarkdownStore
from src.common.knowledge_tree.storage.vector_store import InMemoryVectorStore


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def seed_dir(tmp_path: Path) -> Path:
    """创建种子 Markdown 文件目录。"""
    d = tmp_path / "seeds"
    d.mkdir()

    seeds = [
        ("LangGraph 状态管理", "LangGraph 使用 TypedDict 定义状态模式。"),
        ("LangGraph 工具调用", "LangGraph 通过 ToolNode 自动执行工具。"),
        ("Agent ReAct 模式", "ReAct 模式结合推理和行动。"),
        ("向量嵌入原理", "文本嵌入将语义映射为高维向量。"),
    ]

    for title, content in seeds:
        node = KnowledgeNode.create(title=title, content=content, source="test_seed")
        (d / f"{node.node_id}.md").write_text(node.to_frontmatter_md(), encoding="utf-8")

    return d


@pytest.fixture
def stores(tmp_path: Path):
    config = KnowledgeTreeConfig(
        markdown_root=tmp_path / "md",
        db_path=tmp_path / "db",
    )
    md_store = MarkdownStore(config.markdown_root)
    graph_store = InMemoryGraphStore()
    graph_store.initialize()
    vector_store = InMemoryVectorStore(dimension=4)
    return md_store, graph_store, vector_store, config


def _mock_embedder(dim: int = 4):
    """简单 mock embedder（各节点产生不同向量）。"""
    def embed(text: str) -> list[float]:
        base = sum(ord(c) for c in text) % 100 / 100.0
        return [base + i * 0.01 for i in range(dim)]
    return embed


def _diverse_mock_embedder(dim: int = 16):
    """多样性 mock embedder——不同文本产生差异明显的向量，用于 GMM 测试。"""
    def embed(text: str) -> list[float]:
        # 基于字符 hash 生成差异化向量
        vec = [0.0] * dim
        for i, c in enumerate(text):
            idx = (ord(c) + i) % dim
            vec[idx] += (ord(c) % 17) * 0.1
        # L2 归一化
        mag = sum(x * x for x in vec) ** 0.5
        if mag > 0:
            vec = [x / mag for x in vec]
        return vec
    return embed


def _make_large_seed_dir(tmp_path: Path, count: int = 30) -> Path:
    """创建包含大量种子文件的目录，用于 GMM 聚类测试。"""
    d = tmp_path / "seeds_large"
    d.mkdir()

    topics = [
        ("Python 类型系统", "Python 3.11 引入了更强大的类型提示系统。"),
        ("Python 异步编程", "asyncio 是 Python 的异步编程框架。"),
        ("Python 装饰器", "装饰器是 Python 的高级函数特性。"),
        ("Python 生成器", "生成器使用 yield 实现惰性计算。"),
        ("Python 上下文管理", "with 语句管理资源的获取和释放。"),
        ("Rust 所有权", "Rust 的所有权系统保证内存安全。"),
        ("Rust 生命周期", "生命周期标注帮助编译器检查引用有效性。"),
        ("Rust trait 系统", "trait 定义共享行为接口。"),
        ("Rust 模式匹配", "match 表达式提供强大的模式匹配。"),
        ("Rust 错误处理", "Result 和 Option 类型处理错误和空值。"),
        ("React 组件", "React 使用组件化构建用户界面。"),
        ("React Hooks", "useState 和 useEffect 管理组件状态。"),
        ("React 路由", "React Router 管理单页应用导航。"),
        ("React 状态管理", "Redux 和 Zustand 管理全局应用状态。"),
        ("React 服务端渲染", "Next.js 提供服务端渲染能力。"),
        ("数据库索引", "B+树索引加速数据库查询。"),
        ("数据库事务", "ACID 特性保证数据一致性。"),
        ("SQL 优化", "查询优化器选择最优执行计划。"),
        ("NoSQL 概述", "文档数据库和键值数据库的使用场景。"),
        ("数据库复制", "主从复制提高数据可用性。"),
        ("TCP 协议", "TCP 提供可靠的字节流传输。"),
        ("HTTP 协议", "HTTP/2 和 HTTP/3 改进 Web 性能。"),
        ("DNS 解析", "DNS 将域名解析为 IP 地址。"),
        ("TLS 加密", "TLS 保护网络通信安全。"),
        ("WebSocket", "WebSocket 实现全双工实时通信。"),
        ("Docker 容器", "Docker 容器化应用部署。"),
        ("Kubernetes 编排", "K8s 管理容器化应用集群。"),
        ("CI/CD 流水线", "自动化构建、测试和部署流程。"),
        ("Git 工作流", "分支策略和代码审查最佳实践。"),
        ("监控告警", "Prometheus 和 Grafana 监控系统健康。"),
    ]

    for i in range(min(count, len(topics))):
        title, content = topics[i]
        node = KnowledgeNode.create(title=title, content=content, source="test_seed")
        (d / f"{node.node_id}.md").write_text(node.to_frontmatter_md(), encoding="utf-8")

    return d


# ---------------------------------------------------------------------------
# 现有测试（simple fallback 路径）
# ---------------------------------------------------------------------------


class TestBootstrapSimple:
    """测试简单余弦 BFS 聚类路径（小数据集自动回退）。"""

    def test_bootstrap_creates_tree(self, seed_dir: Path, stores):
        md_store, graph_store, vector_store, config = stores
        embedder = _mock_embedder()

        report = bootstrap_from_seed_files(
            seed_dir, md_store, graph_store, vector_store, embedder, config
        )

        assert report.errors == []
        assert report.nodes_created > 0
        assert report.edges_created > 0
        assert report.embeddings_generated > 0
        assert report.max_depth == 3  # root → group → leaf

    def test_bootstrap_root_exists(self, seed_dir: Path, stores):
        md_store, graph_store, vector_store, config = stores
        embedder = _mock_embedder()

        bootstrap_from_seed_files(seed_dir, md_store, graph_store, vector_store, embedder, config)

        root_id = graph_store.get_root_id()
        assert root_id is not None

    def test_bootstrap_leaf_nodes_have_embeddings(self, seed_dir: Path, stores):
        md_store, graph_store, vector_store, config = stores
        embedder = _mock_embedder()

        bootstrap_from_seed_files(seed_dir, md_store, graph_store, vector_store, embedder, config)

        root_id = graph_store.get_root_id()
        assert root_id is not None
        groups = graph_store.get_children(root_id)
        assert len(groups) > 0

        for group in groups:
            children = graph_store.get_children(group.node_id)
            for child in children:
                assert vector_store.get_embedding(child.node_id) is not None

    def test_bootstrap_empty_dir(self, tmp_path: Path, stores):
        md_store, graph_store, vector_store, config = stores
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()
        embedder = _mock_embedder()

        report = bootstrap_from_seed_files(
            empty_dir, md_store, graph_store, vector_store, embedder, config
        )
        assert len(report.errors) > 0
        assert report.nodes_created == 0

    def test_bootstrap_nonexistent_dir(self, stores):
        md_store, graph_store, vector_store, config = stores
        embedder = _mock_embedder()

        report = bootstrap_from_seed_files(
            "/nonexistent/path", md_store, graph_store, vector_store, embedder, config
        )
        assert len(report.errors) > 0

    def test_explicit_simple_method(self, seed_dir: Path, stores):
        """显式指定 simple 方法，即使 sklearn 可用也用简单聚类。"""
        md_store, graph_store, vector_store, _config = stores
        config = KnowledgeTreeConfig(
            markdown_root=_config.markdown_root,
            db_path=_config.db_path,
            cluster_method="simple",
        )
        embedder = _mock_embedder()

        report = bootstrap_from_seed_files(
            seed_dir, md_store, graph_store, vector_store, embedder, config
        )

        assert report.errors == []
        assert report.cluster_method_used == "simple"
        assert report.max_depth == 3


# ---------------------------------------------------------------------------
# GMM 路径测试
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _GMM_AVAILABLE, reason="scikit-learn not installed")
class TestBootstrapGMM:
    """测试 GMM+UMAP 层次聚类路径。"""

    def test_gmm_large_dataset(self, tmp_path: Path):
        """大数据集应使用 GMM 聚类，生成多层结构。"""
        seed_dir = _make_large_seed_dir(tmp_path, count=30)
        config = KnowledgeTreeConfig(
            markdown_root=tmp_path / "md",
            db_path=tmp_path / "db",
            cluster_size=5,  # 小 cluster_size 使 30 节点触发 GMM
            embedding_dimension=16,
        )
        md_store = MarkdownStore(config.markdown_root)
        graph_store = InMemoryGraphStore()
        graph_store.initialize()
        vector_store = InMemoryVectorStore(dimension=16)
        embedder = _diverse_mock_embedder(dim=16)

        report = bootstrap_from_seed_files(
            seed_dir, md_store, graph_store, vector_store, embedder, config
        )

        assert report.errors == []
        assert report.cluster_method_used == "gmm"
        assert report.nodes_created > 30  # 叶子 + 中间节点 + 根
        assert report.edges_created > 30
        assert report.max_depth >= 2  # 至少 2 层（root + leaf 或更深）

    def test_gmm_creates_root(self, tmp_path: Path):
        """GMM bootstrap 应创建根节点。"""
        seed_dir = _make_large_seed_dir(tmp_path, count=25)
        config = KnowledgeTreeConfig(
            markdown_root=tmp_path / "md",
            db_path=tmp_path / "db",
            cluster_size=5,
            embedding_dimension=16,
        )
        md_store = MarkdownStore(config.markdown_root)
        graph_store = InMemoryGraphStore()
        graph_store.initialize()
        vector_store = InMemoryVectorStore(dimension=16)
        embedder = _diverse_mock_embedder(dim=16)

        bootstrap_from_seed_files(
            seed_dir, md_store, graph_store, vector_store, embedder, config
        )

        root_id = graph_store.get_root_id()
        assert root_id is not None

    def test_gmm_all_leaves_connected(self, tmp_path: Path):
        """所有叶子节点都应有从根可达的主路径。"""
        seed_dir = _make_large_seed_dir(tmp_path, count=20)
        config = KnowledgeTreeConfig(
            markdown_root=tmp_path / "md",
            db_path=tmp_path / "db",
            cluster_size=5,
            embedding_dimension=16,
        )
        md_store = MarkdownStore(config.markdown_root)
        graph_store = InMemoryGraphStore()
        graph_store.initialize()
        vector_store = InMemoryVectorStore(dimension=16)
        embedder = _diverse_mock_embedder(dim=16)

        report = bootstrap_from_seed_files(
            seed_dir, md_store, graph_store, vector_store, embedder, config
        )
        assert report.errors == []

        root_id = graph_store.get_root_id()
        assert root_id is not None

        # 验证根节点有子节点
        top_children = graph_store.get_children(root_id)
        assert len(top_children) > 0

        # 所有边构成连通树
        all_edges = graph_store.get_all_edges()
        assert len(all_edges) == report.edges_created

    def test_explicit_gmm_small_dataset(self, tmp_path: Path):
        """显式指定 gmm 方法，即使数据集较小也应尝试。"""
        seed_dir = _make_large_seed_dir(tmp_path, count=10)
        config = KnowledgeTreeConfig(
            markdown_root=tmp_path / "md",
            db_path=tmp_path / "db",
            cluster_method="gmm",
            cluster_size=3,  # 小 cluster_size
            embedding_dimension=16,
        )
        md_store = MarkdownStore(config.markdown_root)
        graph_store = InMemoryGraphStore()
        graph_store.initialize()
        vector_store = InMemoryVectorStore(dimension=16)
        embedder = _diverse_mock_embedder(dim=16)

        report = bootstrap_from_seed_files(
            seed_dir, md_store, graph_store, vector_store, embedder, config
        )

        assert report.errors == []
        assert report.nodes_created > 0
        assert report.edges_created > 0

    def test_gmm_leaf_embeddings_stored(self, tmp_path: Path):
        """GMM bootstrap 后叶子节点嵌入应存储在向量索引中。"""
        seed_dir = _make_large_seed_dir(tmp_path, count=15)
        config = KnowledgeTreeConfig(
            markdown_root=tmp_path / "md",
            db_path=tmp_path / "db",
            cluster_size=5,
            embedding_dimension=16,
        )
        md_store = MarkdownStore(config.markdown_root)
        graph_store = InMemoryGraphStore()
        graph_store.initialize()
        vector_store = InMemoryVectorStore(dimension=16)
        embedder = _diverse_mock_embedder(dim=16)

        bootstrap_from_seed_files(
            seed_dir, md_store, graph_store, vector_store, embedder, config
        )

        # 检查所有非根节点有嵌入
        root_id = graph_store.get_root_id()
        all_edges = graph_store.get_all_edges()
        child_ids = {e.child_id for e in all_edges}
        for cid in child_ids:
            emb = vector_store.get_embedding(cid)
            assert emb is not None, f"Node {cid} missing embedding"


# ---------------------------------------------------------------------------
# 内部函数单元测试
# ---------------------------------------------------------------------------


class TestSemanticCluster:
    """测试简单余弦 BFS 聚类。"""

    def test_single_node(self):
        node = KnowledgeNode.create(title="A", content="test", source="test")
        node.embedding = [1.0, 0.0]
        clusters = _semantic_cluster([node])
        assert len(clusters) == 1
        assert len(clusters[0]) == 1

    def test_two_similar_nodes(self):
        n1 = KnowledgeNode.create(title="A", content="hello world", source="test")
        n1.embedding = [1.0, 0.0, 0.0]
        n2 = KnowledgeNode.create(title="B", content="hello earth", source="test")
        n2.embedding = [0.95, 0.05, 0.0]
        clusters = _semantic_cluster([n1, n2], threshold=0.9)
        assert len(clusters) == 1

    def test_two_dissimilar_nodes(self):
        n1 = KnowledgeNode.create(title="A", content="hello", source="test")
        n1.embedding = [1.0, 0.0, 0.0]
        n2 = KnowledgeNode.create(title="B", content="world", source="test")
        n2.embedding = [0.0, 1.0, 0.0]
        clusters = _semantic_cluster([n1, n2], threshold=0.9)
        assert len(clusters) == 2


class TestBuildTreeSimple:
    """测试简单树构建。"""

    def test_simple_tree_structure(self):
        nodes = []
        for i in range(4):
            n = KnowledgeNode.create(title=f"Node {i}", content=f"Content {i}", source="test")
            n.embedding = [float(i), 0.0, 0.0]
            nodes.append(n)

        tree = _build_tree_simple(nodes, _mock_embedder(3))

        assert tree.root is not None
        assert tree.root.title == "Knowledge Root"
        assert len(tree.intermediate_nodes) > 0
        assert len(tree.edges) > 0
        assert tree.max_depth == 3


@pytest.mark.skipif(not _GMM_AVAILABLE, reason="scikit-learn not installed")
class TestFindOptimalGmmK:
    """测试 BIC 最优 k 选择。"""

    def test_optimal_k_returns_value(self):
        import numpy as np

        # 生成 3 个明显分离的高斯簇
        rng = np.random.RandomState(42)
        data = np.vstack([
            rng.normal(0, 0.1, (20, 2)),
            rng.normal(5, 0.1, (20, 2)),
            rng.normal(10, 0.1, (20, 2)),
        ])
        k = _find_optimal_gmm_k(data, max_k=6, min_clusters=2)
        assert 2 <= k <= 6

    def test_optimal_k_small_max(self):
        import numpy as np

        data = np.random.RandomState(42).normal(0, 1, (10, 2))
        k = _find_optimal_gmm_k(data, max_k=1, min_clusters=2)
        assert k == 1  # max_k < min_clusters → return max_k
