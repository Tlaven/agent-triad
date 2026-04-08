"""Utility & helper functions."""

import logging
from typing import Any, Optional, Union

from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_qwq import ChatQwen, ChatQwQ

logger = logging.getLogger(__name__)


def normalize_region(region: str) -> Optional[str]:
    """Normalize region aliases to standard values.

    Args:
        region: Region string to normalize

    Returns:
        Normalized region ('prc' or 'international') or None if invalid
    """
    if not region:
        return None

    region_lower = region.lower()
    if region_lower in ("prc", "cn"):
        return "prc"
    elif region_lower in ("international", "en"):
        return "international"
    return None


def get_message_text(msg: BaseMessage) -> str:
    """Get the text content of a message."""
    content = msg.content
    if isinstance(content, str):
        return content
    elif isinstance(content, dict):
        return content.get("text", "")
    else:
        txts = [c if isinstance(c, str) else (c.get("text") or "") for c in content]
        return "".join(txts).strip()


def load_chat_model(
    fully_specified_name: str,
) -> Union[BaseChatModel, ChatQwQ, ChatQwen]:
    """Load a chat model from a fully specified name.

    Args:
        fully_specified_name (str): String in the format 'provider:model'.
    """
    provider, model = fully_specified_name.split(":", maxsplit=1)
    provider_lower = provider.lower()

    # Handle Qwen models specially with dashscope integration
    if provider_lower == "qwen":
        from .models import create_qwen_model

        return create_qwen_model(model)

    # Handle SiliconFlow models
    if provider_lower == "siliconflow":
        from .models import create_siliconflow_model

        return create_siliconflow_model(model)

    # Use standard langchain initialization for other providers
    return init_chat_model(model, model_provider=provider)


async def invoke_chat_model(
    model: Any,
    messages: list[dict[str, Any]] | list[BaseMessage],
    *,
    enable_streaming: bool = False,
) -> AIMessage:
    """Invoke chat model with optional streaming aggregation.

    When streaming is enabled, aggregate `astream` chunks into one final AIMessage.
    Falls back to `ainvoke` when streaming aggregation fails.
    """
    if not enable_streaming:
        return await model.ainvoke(messages)

    try:
        chunks: list[Any] = []
        async for chunk in model.astream(messages):
            if chunk is not None:
                chunks.append(chunk)

        if not chunks:
            return await model.ainvoke(messages)

        merged = chunks[0]
        for chunk in chunks[1:]:
            merged = merged + chunk

        if isinstance(merged, AIMessage):
            return merged

        if hasattr(merged, "to_message"):
            msg = merged.to_message()
            if isinstance(msg, AIMessage):
                return msg

        return await model.ainvoke(messages)
    except Exception:
        logger.warning(
            "LLM astream 聚合失败，回退到 ainvoke", exc_info=True
        )
        return await model.ainvoke(messages)
