"""查询扩展：用 LLM 生成同义/变体查询，多路 RRF 融合。

P2 组件：提升检索召回率，解决语义 embedder 仍可能遗漏的同义场景。
仅在 enable_query_expansion=True 时激活，成本 +1 次 LLM 调用（~500 token）。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from langchain_core.language_models import BaseChatModel

logger = logging.getLogger(__name__)

_EXPANSION_PROMPT = """你是查询扩展助手。为以下查询生成 3 个同义/变体表述，用于知识库检索。

规则：
- 保持原意不变
- 使用不同的措辞、关键词、语言风格
- 可以包含中英混合
- 每行一个，不要编号

查询：{query}"""


def expand_query(
    query: str,
    llm: BaseChatModel,
    n: int = 3,
) -> list[str]:
    """用 LLM 生成 n 个同义查询。

    Args:
        query: 原始查询。
        llm: LangChain chat model 实例。
        n: 生成变体数量。

    Returns:
        包含原查询 + 变体的列表（最多 n+1 条）。
    """
    from langchain_core.messages import HumanMessage

    prompt = _EXPANSION_PROMPT.format(query=query)
    try:
        response = llm.invoke([HumanMessage(content=prompt)])
        text = response.content if isinstance(response.content, str) else str(response.content)
    except Exception as e:
        logger.warning("Query expansion failed: %s", e)
        return [query]

    # 解析：每行一个变体
    variants = [line.strip() for line in text.strip().splitlines() if line.strip()]
    # 去重 + 限制数量
    seen = {query}
    results = [query]
    for v in variants:
        if v not in seen and len(v) > 2:
            seen.add(v)
            results.append(v)
            if len(results) >= n + 1:
                break

    logger.debug("Query expansion: '%s' → %d variants", query, len(results) - 1)
    return results
