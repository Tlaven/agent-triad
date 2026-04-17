"""Knowledge Tree test fixtures."""

from pathlib import Path
from typing import Any

import pytest

from src.common.knowledge_tree.config import KnowledgeTreeConfig
from src.common.knowledge_tree.dag.edge import KnowledgeEdge
from src.common.knowledge_tree.dag.node import KnowledgeNode


@pytest.fixture
def kt_config(tmp_path: Path) -> KnowledgeTreeConfig:
    """配置指向临时目录。"""
    return KnowledgeTreeConfig(
        markdown_root=tmp_path / "kt_md",
        db_path=tmp_path / "kt_db" / "kuzu",
    )


@pytest.fixture
def mock_embedder():
    """确定性 mock embedder（不下载模型）。

    基于文本内容的确定性哈希生成固定维度向量。
    """

    def embed(text: str, dim: int = 512) -> list[float]:
        # 简单确定性映射：每个字符贡献一个浮点值
        base = sum(ord(c) for c in text) / 1000.0
        return [base + i * 0.001 for i in range(dim)]

    return embed


@pytest.fixture
def sample_node() -> KnowledgeNode:
    """单个示例节点。"""
    return KnowledgeNode.create(
        title="LangGraph 状态管理",
        content="LangGraph 使用 TypedDict 定义状态模式，通过 StateGraph 构建执行图。",
        source="官方文档",
        summary="LangGraph StateGraph 的状态传递模式",
    )


@pytest.fixture
def sample_nodes() -> list[KnowledgeNode]:
    """一组示例节点（5个，覆盖不同主题）。"""
    return [
        KnowledgeNode.create(
            title="LangGraph 状态管理",
            content="LangGraph 使用 TypedDict 定义状态模式。",
            source="官方文档",
            summary="状态传递模式",
        ),
        KnowledgeNode.create(
            title="LangGraph 工具调用",
            content="LangGraph 通过 ToolNode 自动执行工具调用。",
            source="官方文档",
            summary="工具调用机制",
        ),
        KnowledgeNode.create(
            title="Agent ReAct 模式",
            content="ReAct 模式结合推理和行动，逐步解决复杂问题。",
            source="论文",
            summary="推理-行动循环",
        ),
        KnowledgeNode.create(
            title="向量嵌入基础",
            content="文本嵌入将语义信息映射为高维向量，支持相似度检索。",
            source="教材",
            summary="嵌入向量原理",
        ),
        KnowledgeNode.create(
            title="DAG 有向无环图",
            content="DAG 允许节点有多个父节点但不允许环，适合层级知识组织。",
            source="图论教材",
            summary="DAG 结构特性",
        ),
    ]


@pytest.fixture
def sample_edges(sample_nodes: list[KnowledgeNode]) -> list[KnowledgeEdge]:
    """示例边：构建两层树结构。

    root → [node0, node1, node2]
    node0 → [node3, node4]
    """
    root_id = "root"
    edges = [
        KnowledgeEdge.create(parent_id=root_id, child_id=sample_nodes[0].node_id, is_primary=True),
        KnowledgeEdge.create(parent_id=root_id, child_id=sample_nodes[1].node_id, is_primary=True),
        KnowledgeEdge.create(parent_id=root_id, child_id=sample_nodes[2].node_id, is_primary=True),
        KnowledgeEdge.create(parent_id=sample_nodes[0].node_id, child_id=sample_nodes[3].node_id, is_primary=True),
        KnowledgeEdge.create(parent_id=sample_nodes[0].node_id, child_id=sample_nodes[4].node_id, is_primary=True),
    ]
    return edges
