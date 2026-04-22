"""Knowledge Tree test fixtures."""

from pathlib import Path

import pytest

from src.common.knowledge_tree.config import KnowledgeTreeConfig
from src.common.knowledge_tree.dag.node import KnowledgeNode
from src.common.knowledge_tree.storage.markdown_store import MarkdownStore
from src.common.knowledge_tree.storage.overlay import OverlayStore
from src.common.knowledge_tree.storage.vector_store import InMemoryVectorStore


@pytest.fixture
def kt_config(tmp_path: Path) -> KnowledgeTreeConfig:
    """配置指向临时目录。"""
    return KnowledgeTreeConfig(markdown_root=tmp_path / "kt_md")


@pytest.fixture
def mock_embedder():
    """确定性 mock embedder（不下载模型）。"""

    def embed(text: str, dim: int = 512) -> list[float]:
        base = sum(ord(c) for c in text) / 1000.0
        return [base + i * 0.001 for i in range(dim)]

    return embed


@pytest.fixture
def md_store(tmp_path: Path) -> MarkdownStore:
    """MarkdownStore 指向临时目录。"""
    return MarkdownStore(tmp_path / "kt_md")


@pytest.fixture
def vector_store() -> InMemoryVectorStore:
    """InMemoryVectorStore 实例。"""
    return InMemoryVectorStore(dimension=512)


@pytest.fixture
def overlay_store(tmp_path: Path) -> OverlayStore:
    """OverlayStore 指向临时文件。"""
    return OverlayStore(tmp_path / "kt_md" / ".overlay.json")


@pytest.fixture
def sample_node() -> KnowledgeNode:
    """单个示例节点。"""
    return KnowledgeNode.create(
        node_id="development/langgraph.md",
        title="LangGraph 状态管理",
        content="LangGraph 使用 TypedDict 定义状态模式，通过 StateGraph 构建执行图。",
        source="官方文档",
        summary="LangGraph StateGraph 的状态传递模式",
    )


@pytest.fixture
def sample_nodes() -> list[KnowledgeNode]:
    """一组示例节点。"""
    return [
        KnowledgeNode.create(
            node_id="development/langgraph.md",
            title="LangGraph 状态管理",
            content="LangGraph 使用 TypedDict 定义状态模式。",
            source="官方文档",
            summary="状态传递模式",
        ),
        KnowledgeNode.create(
            node_id="development/tools.md",
            title="LangGraph 工具调用",
            content="LangGraph 通过 ToolNode 自动执行工具调用。",
            source="官方文档",
            summary="工具调用机制",
        ),
        KnowledgeNode.create(
            node_id="patterns/react.md",
            title="Agent ReAct 模式",
            content="ReAct 模式结合推理和行动，逐步解决复杂问题。",
            source="论文",
            summary="推理-行动循环",
        ),
        KnowledgeNode.create(
            node_id="fundamentals/embedding.md",
            title="向量嵌入基础",
            content="文本嵌入将语义信息映射为高维向量，支持相似度检索。",
            source="教材",
            summary="嵌入向量原理",
        ),
        KnowledgeNode.create(
            node_id="fundamentals/dag.md",
            title="DAG 有向无环图",
            content="DAG 允许节点有多个父节点但不允许环，适合层级知识组织。",
            source="图论教材",
            summary="DAG 结构特性",
        ),
    ]
