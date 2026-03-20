import asyncio
import os
import sys

from langchain_mcp_adapters.tools import load_mcp_tools
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import Config
from create_logger import logger
from utils.model_factory import build_chat_model, build_order_agent, extract_text_from_agent_result

conf = Config()

# 初始化LLM
llm = build_chat_model(
    conf,
)


async def order_tickets(query):
    try:
        # 启动 MCP server，通过streamable建立连接
        async with streamablehttp_client("http://127.0.0.1:8003/mcp") as (read, write, _):
            # 使用读写通道创建 MCP 会话
            async with ClientSession(read, write) as session:
                try:
                    await session.initialize()

                    # 从 session 自动获取 MCP server 提供的工具列表。
                    tools = await load_mcp_tools(session)
                    agent = build_order_agent(llm, tools)
                    response = await agent.ainvoke(
                        {"messages": [{"role": "user", "content": query}]}
                    )
                    return extract_text_from_agent_result(response)
                except Exception as e:
                    logger.info(f"票务 MCP 测试出错：{str(e)}")
                    return f"票务 MCP 查询出错：{str(e)}"
    except Exception as e:
        logger.error(f"连接或会话初始化时发生错误: {e}")
        return "连接或会话初始化时发生错误"


if __name__ == "__main__":
    print("示例：当前用户：demo_user\\n帮我预订2026-03-21北京到上海的高铁票，二等座1张")
    while True:
        query = input("请输入查询：")
        if query == "exit":
            break
        print(asyncio.run(order_tickets(query)))
