"""LLM 路由树导航（决策 21 第二节）。

从根节点出发，每层将子节点摘要列表与查询一起提交给 LLM，
由 LLM 选择最相关的子节点并返回置信度。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from src.common.knowledge_tree.dag.node import KnowledgeNode
from src.common.knowledge_tree.storage.graph_store import BaseGraphStore

logger = logging.getLogger(__name__)


@dataclass
class NavigationResult:
    """树导航结果。"""

    path: list[str]           # 经过的 node_id 列表
    confidence: float         # 最终导航置信度
    final_node: KnowledgeNode | None  # 最终到达的节点
    success: bool             # 是否成功找到节点


def navigate_tree(
    query: str,
    graph_store: BaseGraphStore,
    llm,
    confidence_threshold: float = 0.7,
    max_depth: int = 5,
) -> NavigationResult:
    """LLM 路由树导航。

    Args:
        query: 用户查询文本。
        graph_store: 图数据库。
        llm: LangChain LLM 实例（或 mock）。
        confidence_threshold: 继续下钻的置信度阈值。
        max_depth: 最大导航深度。

    Returns:
        NavigationResult 包含路径、置信度和最终节点。
    """
    path: list[str] = []
    current_id = graph_store.get_root_id()

    if current_id is None:
        logger.warning("No root node found in knowledge tree")
        return NavigationResult(path=[], confidence=0.0, final_node=None, success=False)

    path.append(current_id)

    for _ in range(max_depth):
        children = graph_store.get_children(current_id, primary_only=True)
        if not children:
            # 叶子节点——导航成功
            node = graph_store.get_node(current_id)
            return NavigationResult(
                path=path,
                confidence=1.0,
                final_node=node,
                success=node is not None,
            )

        # 调用 LLM 做路由决策
        selected_id, confidence = _llm_route(query, children, llm)

        if selected_id is None or confidence < confidence_threshold:
            # 置信度不足或无合适子节点
            node = graph_store.get_node(current_id)
            if node is not None:
                return NavigationResult(
                    path=path,
                    confidence=confidence,
                    final_node=node,
                    success=confidence >= confidence_threshold,
                )
            return NavigationResult(
                path=path,
                confidence=confidence,
                final_node=None,
                success=False,
            )

        path.append(selected_id)
        current_id = selected_id

    # 达到最大深度
    node = graph_store.get_node(current_id)
    return NavigationResult(
        path=path,
        confidence=0.0,
        final_node=node,
        success=node is not None,
    )


def _llm_route(
    query: str,
    children: list[KnowledgeNode],
    llm,
) -> tuple[str | None, float]:
    """调用 LLM 做路由决策。

    Returns:
        (selected_node_id, confidence) 或 (None, 0.0)。
    """
    # 构建子节点摘要列表
    child_descriptions = "\n".join(
        f"[{i}] ID={c.node_id} | {c.title} | {c.summary or c.content[:100]}"
        for i, c in enumerate(children)
    )

    prompt = (
        f"Given the query: \"{query}\"\n\n"
        f"Which of the following categories is most relevant?\n\n"
        f"{child_descriptions}\n\n"
        f"Respond in JSON format: {{\"selected_index\": <int>, \"confidence\": <float 0-1>}}\n"
        f"If none is relevant, set selected_index to -1."
    )

    try:
        response = llm.invoke(prompt)
        # 解析响应
        text = response if isinstance(response, str) else str(response)
        # 尝试提取 JSON
        parsed = _extract_json(text)
        if parsed is None:
            return None, 0.0

        idx = parsed.get("selected_index", -1)
        confidence = float(parsed.get("confidence", 0.0))

        if idx < 0 or idx >= len(children):
            return None, confidence

        return children[idx].node_id, confidence

    except Exception as e:
        logger.warning("LLM routing failed: %s", e)
        return None, 0.0


def _extract_json(text: str) -> dict | None:
    """从文本中提取 JSON 对象。"""
    # 尝试直接解析
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # 尝试找到 JSON 块
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            pass

    return None
