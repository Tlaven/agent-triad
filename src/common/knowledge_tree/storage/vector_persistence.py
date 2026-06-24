"""向量索引持久化：JSON 文件保存/加载 + 新鲜度检测。

保存时记录 {node_id: content_hash} manifest，加载时与磁盘 .md 文件比对。
如 manifest 匹配则直接使用加载的向量；否则回退到完整重建。
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

from src.common.knowledge_tree.storage.vector_store import BaseVectorStore

logger = logging.getLogger(__name__)

VERSION = 2
VECTOR_INDEX_FILENAME = ".vector_index.json"


@dataclass
class VectorIndexManifest:
    """向量索引 manifest，用于检测 .md 文件是否发生变更。"""

    version: int = VERSION
    saved_at: str = ""
    embedder_type: str = ""
    embedding_dimension: int = 0
    node_count: int = 0
    anchor_count: int = 0
    file_hashes: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "saved_at": self.saved_at,
            "embedder_type": self.embedder_type,
            "embedding_dimension": self.embedding_dimension,
            "node_count": self.node_count,
            "anchor_count": self.anchor_count,
            "file_hashes": self.file_hashes,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> VectorIndexManifest:
        return cls(
            version=data.get("version", 0),
            saved_at=data.get("saved_at", ""),
            embedder_type=data.get("embedder_type", ""),
            embedding_dimension=data.get("embedding_dimension", 0),
            node_count=data.get("node_count", 0),
            anchor_count=data.get("anchor_count", 0),
            file_hashes=data.get("file_hashes", {}),
        )


def _content_hash(text: str) -> str:
    """计算文本内容哈希。"""
    return sha256(text.encode("utf-8")).hexdigest()[:16]


def _compute_file_hashes(md_root: Path) -> dict[str, str]:
    """扫描 markdown_root 下所有 .md 文件，计算 {relative_path: content_hash}。"""
    hashes: dict[str, str] = {}
    if not md_root.is_dir():
        return hashes
    for p in sorted(md_root.rglob("*.md")):
        if p.is_file() and not p.name.startswith("."):
            try:
                rel = str(p.relative_to(md_root)).replace("\\", "/")
                hashes[rel] = _content_hash(p.read_text(encoding="utf-8"))
            except OSError:
                continue
    return hashes


def save_vector_index(
    vector_store: BaseVectorStore,
    md_root: Path,
    embedder_type: str,
    index_path: Path | None = None,
) -> bool:
    """将向量索引和 manifest 保存到 JSON 文件。

    使用原子写入（.tmp + os.replace）避免中断导致损坏。

    Args:
        vector_store: 向量存储实例。
        md_root: Markdown 文件根目录（用于计算 manifest）。
        embedder_type: embedder 类型（hash/api/local），用于加载时校验。
        index_path: 保存路径，默认为 md_root / VECTOR_INDEX_FILENAME。

    Returns:
        True 保存成功，False 保存失败。
    """
    if index_path is None:
        index_path = md_root / VECTOR_INDEX_FILENAME

    try:
        manifest = VectorIndexManifest(
            saved_at=datetime.now(UTC).isoformat(),
            embedder_type=embedder_type,
            embedding_dimension=getattr(vector_store, "_dimension", 0),
            node_count=getattr(vector_store, "node_count", 0),
            anchor_count=len(vector_store.get_all_anchors()),
            file_hashes=_compute_file_hashes(md_root),
        )

        vector_data = vector_store.to_dict()

        payload = {
            "manifest": manifest.to_dict(),
            "vectors": vector_data,
        }

        index_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = index_path.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(payload, ensure_ascii=False), "utf-8")
        os.replace(str(tmp_path), str(index_path))

        logger.info(
            "Vector index saved: %d nodes, %d anchors, %d file hashes",
            manifest.node_count,
            manifest.anchor_count,
            len(manifest.file_hashes),
        )
        return True

    except Exception as e:
        logger.warning("Failed to save vector index: %s", e)
        return False


def load_vector_index(
    vector_store: BaseVectorStore,
    md_root: Path,
    embedder_type: str,
    index_path: Path | None = None,
) -> bool:
    """从 JSON 文件加载向量索引。

    加载前验证：
    1. 版本号匹配
    2. embedder_type 匹配（避免 hash embedder 的向量用于 api 模式）
    3. dimension 匹配
    4. Manifest 中的 file_hashes 与当前 .md 文件一致

    任何验证失败均返回 False，调用方应回退到完整 bootstrap。

    Args:
        vector_store: 向量存储实例（将被填充）。
        md_root: Markdown 文件根目录。
        embedder_type: 当前 embedder 类型。
        index_path: 加载路径，默认为 md_root / VECTOR_INDEX_FILENAME。

    Returns:
        True 加载成功且数据新鲜，False 需要回退 bootstrap。
    """
    if index_path is None:
        index_path = md_root / VECTOR_INDEX_FILENAME

    if not index_path.is_file():
        logger.debug("Vector index file not found: %s", index_path)
        return False

    try:
        raw = index_path.read_text(encoding="utf-8")
        payload = json.loads(raw)
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Vector index file corrupted, will rebuild: %s", e)
        return False

    manifest_data = payload.get("manifest", {})
    vector_data = payload.get("vectors", {})

    manifest = VectorIndexManifest.from_dict(manifest_data)

    if manifest.version != VERSION:
        logger.info(
            "Vector index version mismatch: expected %d, got %d",
            VERSION,
            manifest.version,
        )
        return False

    if manifest.embedder_type and manifest.embedder_type != embedder_type:
        logger.info(
            "Embedder type mismatch: expected %s, got %s",
            embedder_type,
            manifest.embedder_type,
        )
        return False

    if manifest.embedding_dimension != getattr(vector_store, "_dimension", 0):
        logger.info(
            "Dimension mismatch: expected %d, got %d",
            getattr(vector_store, "_dimension", 0),
            manifest.embedding_dimension,
        )
        return False

    current_hashes = _compute_file_hashes(md_root)
    if manifest.file_hashes != current_hashes:
        stale_count = len(set(manifest.file_hashes) ^ set(current_hashes))
        logger.info(
            "Vector index stale: %d files changed, will rebuild",
            stale_count,
        )
        return False

    try:
        vector_store.load_from_dict(vector_data)
    except (ValueError, KeyError, TypeError) as e:
        logger.warning("Failed to load vector data: %s", e)
        return False

    loaded_nodes = getattr(vector_store, "node_count", 0)
    loaded_anchors = len(vector_store.get_all_anchors())
    logger.info(
        "Vector index loaded: %d nodes, %d anchors (from %s)",
        loaded_nodes,
        loaded_anchors,
        index_path,
    )
    return True
