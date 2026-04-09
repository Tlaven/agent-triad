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


def extract_reasoning_text(msg: AIMessage) -> str:
    """Extract reasoning text from provider-specific response payloads.

    This is best-effort across common OpenAI-compatible key names.
    """
    candidates: list[Any] = []
    if hasattr(msg, "additional_kwargs") and isinstance(msg.additional_kwargs, dict):
        candidates.extend(
            [
                msg.additional_kwargs.get("reasoning_content"),
                msg.additional_kwargs.get("reasoning"),
                msg.additional_kwargs.get("thinking"),
            ]
        )
    if hasattr(msg, "response_metadata") and isinstance(msg.response_metadata, dict):
        candidates.extend(
            [
                msg.response_metadata.get("reasoning_content"),
                msg.response_metadata.get("reasoning"),
                msg.response_metadata.get("thinking"),
            ]
        )
    for item in candidates:
        if isinstance(item, str) and item.strip():
            return item.strip()
    return ""


def load_chat_model(
    fully_specified_name: str,
    **kwargs: Any,
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

        return create_qwen_model(model, **kwargs)

    # Handle SiliconFlow models
    if provider_lower == "siliconflow":
        from .models import create_siliconflow_model

        return create_siliconflow_model(model, **kwargs)

    # Use standard langchain initialization for other providers
    return init_chat_model(model, model_provider=provider, **kwargs)


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
    def _is_chunk_message(obj: Any) -> bool:
        return obj.__class__.__name__.endswith("Chunk")

    if not enable_streaming:
        result = await model.ainvoke(messages)
        if _is_chunk_message(result):
            logger.warning("ainvoke 返回了消息 Chunk，尝试转换为标准 AIMessage")
            if hasattr(result, "to_message"):
                converted = result.to_message()
                if isinstance(converted, AIMessage):
                    return converted
            raise TypeError(f"ainvoke returned unsupported chunk message type: {type(result).__name__}")
        return result

    chunks: list[Any] = []
    stream_error: Exception | None = None
    try:
        async for chunk in model.astream(messages):
            if chunk is not None:
                chunks.append(chunk)
    except Exception as exc:
        stream_error = exc
        logger.warning("LLM astream 过程中发生异常，尝试使用已接收 chunk 聚合", exc_info=True)

    if not chunks:
        if stream_error is not None:
            raise RuntimeError("LLM astream 在收到任何 chunk 前失败") from stream_error
        raise RuntimeError("LLM astream 未返回任何内容")

    merged = chunks[0]
    for chunk in chunks[1:]:
        merged = merged + chunk

    if isinstance(merged, AIMessage) and not _is_chunk_message(merged):
        return merged

    if hasattr(merged, "to_message"):
        msg = merged.to_message()
        if isinstance(msg, AIMessage) and not _is_chunk_message(msg):
            return msg

    text_parts: list[str] = []
    for chunk in chunks:
        content = getattr(chunk, "content", "")
        if isinstance(content, str):
            text_parts.append(content)
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, str):
                    text_parts.append(block)
                elif isinstance(block, dict) and "text" in block:
                    text_parts.append(str(block["text"]))
    fallback_text = "".join(text_parts).strip()
    if fallback_text:
        return AIMessage(content=fallback_text)

    raise RuntimeError("LLM astream 聚合后无法转换为 AIMessage")
