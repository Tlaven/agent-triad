"""V4 涌现式知识树 — Supervisor 内嵌组件。

两层存储（文件系统 + 向量索引）+ Overlay JSON 跨目录关联。
文件系统目录层级 = 树结构，向量通过目录锚点聚簇。
通过 enable_knowledge_tree 配置项条件激活。
"""

from src.common.knowledge_tree.bootstrap import BootstrapReport as BootstrapReport
from src.common.knowledge_tree.bootstrap import bootstrap_from_directory
from src.common.knowledge_tree.config import KnowledgeTreeConfig
from src.common.knowledge_tree.core import KnowledgeTree
from src.common.knowledge_tree.dag.node import KnowledgeNode
from src.common.knowledge_tree.factory import get_or_create_kt
from src.common.knowledge_tree.ingestion.chunker import chunk_text
from src.common.knowledge_tree.ingestion.filter import should_remember
from src.common.knowledge_tree.ingestion.ingest import IngestReport, ingest_nodes
from src.common.knowledge_tree.retrieval.log import RetrievalLog
from src.common.knowledge_tree.retrieval.rag_search import rag_search
from src.common.knowledge_tree.storage.markdown_store import MarkdownStore
from src.common.knowledge_tree.storage.overlay import OverlayStore
from src.common.knowledge_tree.storage.vector_store import InMemoryVectorStore
from src.common.knowledge_tree.tools import build_knowledge_tree_tools

__all__ = [
    "BootstrapReport",
    "KnowledgeTree",
    "KnowledgeTreeConfig",
    "KnowledgeNode",
    "IngestReport",
    "InMemoryVectorStore",
    "MarkdownStore",
    "OverlayStore",
    "RetrievalLog",
    "bootstrap_from_directory",
    "build_knowledge_tree_tools",
    "chunk_text",
    "get_or_create_kt",
    "ingest_nodes",
    "rag_search",
    "should_remember",
]
