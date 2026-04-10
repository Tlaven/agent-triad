"""基于 Tavily API 的 Web 搜索工具。"""

import os
from typing import Literal

import httpx
from langchain_core.tools import tool


TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "")
TAVILY_API_URL = "https://api.tavily.com/search"


@tool
def web_search_tavily(
    query: str,
    max_results: int = 5,
    search_depth: Literal["basic", "advanced"] = "basic",
    include_domains: list[str] | None = None,
    exclude_domains: list[str] | None = None,
) -> str:
    """使用 Tavily API 进行网络搜索。

    Args:
        query: 搜索查询字符串
        max_results: 返回结果数量 (1-10)
        search_depth: 搜索深度 ("basic" 或 "advanced")
        include_domains: 仅搜索这些域名
        exclude_domains: 排除这些域名

    Returns:
        格式化的搜索结果字符串
    """
    if not TAVILY_API_KEY:
        return "错误: 未配置 TAVILY_API_KEY 环境变量"

    # 限制参数范围
    max_results = max(1, min(int(max_results), 10))

    # 构建请求 payload
    payload = {
        "api_key": TAVILY_API_KEY,
        "query": query,
        "max_results": max_results,
        "search_depth": search_depth,
        "include_answer": True,  # 包含 AI 生成的答案摘要
        "include_raw_content": False,  # 不需要原始 HTML
        "include_images": False,  # 不需要图片
    }

    # 可选的域名过滤
    if include_domains:
        payload["include_domains"] = include_domains
    if exclude_domains:
        payload["exclude_domains"] = exclude_domains

    try:
        # 发送请求
        with httpx.Client(timeout=30.0) as client:
            response = client.post(TAVILY_API_URL, json=payload)
            response.raise_for_status()
            data = response.json()

        # 格式化结果
        formatted_parts = []

        # 添加 AI 答案（如果有）
        if "answer" in data and data["answer"]:
            formatted_parts.append(f"## AI 答案\n\n{data['answer']}\n")

        # 添加搜索结果
        formatted_parts.append("## 搜索结果\n\n")
        for idx, result in enumerate(data.get("results", []), 1):
            title = result.get("title", "无标题")
            url = result.get("url", "")
            content = result.get("content", "")
            score = result.get("score", 0.0)

            formatted_parts.append(
                f"{idx}. **{title}**\n"
                f"   - URL: {url}\n"
                f"   - 相关度: {score:.2f}\n"
                f"   - 内容摘要: {content[:300]}...\n"
            )

        return "\n".join(formatted_parts)

    except httpx.HTTPStatusError as e:
        return f"HTTP 错误: {e.response.status_code} - {e.response.text}"
    except httpx.TimeoutException:
        return "错误: 请求超时"
    except Exception as e:
        return f"错误: {str(e)}"


@tool
def web_search_tavily_quick(
    query: str,
    max_results: int = 3,
) -> str:
    """快速网络搜索（简化版，适合快速查询）。

    Args:
        query: 搜索查询字符串
        max_results: 返回结果数量 (1-5)

    Returns:
        简化的搜索结果
    """
    result = web_search_tavily.invoke(
        {
            "query": query,
            "max_results": max(1, min(int(max_results), 5)),
            "search_depth": "basic",
        }
    )

    # 提取关键信息（去掉 "AI 答案" 部分）
    lines = result.split("\n")
    filtered_lines = []
    skip_ai_answer = False

    for line in lines:
        if "## AI 答案" in line:
            skip_ai_answer = True
            continue
        if skip_ai_answer and line.startswith("## 搜索结果"):
            skip_ai_answer = False
            continue
        if not skip_ai_answer:
            filtered_lines.append(line)

    return "\n".join(filtered_lines).strip()
