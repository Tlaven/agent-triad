"""KnowledgeTree 实例工厂与全局缓存。"""

from __future__ import annotations

import atexit
import logging
import time as _time
from typing import Any

from src.common.knowledge_tree.config import KnowledgeTreeConfig
from src.common.knowledge_tree.core import KnowledgeTree

logger = logging.getLogger(__name__)

_kt_cache: dict[str, KnowledgeTree] = {}
_atexit_registered: set[str] = set()


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

    if cache_key not in _atexit_registered and kt.config.vector_persistence_enabled:
        _atexit_registered.add(cache_key)

        def _save_on_exit(kt_ref=kt):
            try:
                kt_ref.save(force=True)
            except Exception:
                pass

        atexit.register(_save_on_exit)

    return kt
