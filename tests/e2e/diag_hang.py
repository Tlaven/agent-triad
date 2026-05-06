"""诊断 graph.ainvoke() 挂死问题"""
import asyncio
import logging
import os
import sys

# 确保 src 在 path 中
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

# 加载 .env
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.DEBUG, format="%(name)s %(levelname)s %(message)s")
logger = logging.getLogger("diag")

async def main():
    from src.common.context import Context
    from src.supervisor_agent.graph import graph

    # 构造 context（禁用 KT 隔离变量）
    ctx = Context()
    ctx.enable_knowledge_tree = False

    query = "这个项目的包管理器叫什么名字？"
    logger.info("=== 发送查询: %s ===", query)
    logger.info("=== KT 禁用: %s ===", ctx.enable_knowledge_tree)

    from langchain_core.messages import HumanMessage

    try:
        result = await asyncio.wait_for(
            graph.ainvoke(
                {"messages": [HumanMessage(content=query)]},
                context=ctx,
            ),
            timeout=30,
        )
        logger.info("=== 结果 ===")
        for msg in result.get("messages", []):
            logger.info("  %s: %s", type(msg).__name__, str(msg.content)[:200])
    except asyncio.TimeoutError:
        logger.error("=== 30s 超时！===")
    except Exception as e:
        logger.error("=== 错误: %s ===", e, exc_info=True)

if __name__ == "__main__":
    asyncio.run(main())
