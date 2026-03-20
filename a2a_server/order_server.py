"""
order_server.py：
    订单代理服务器，负责交通票务下单与“查询我的订单”。
    下单时先调用票务查询 Agent 获取余票，再调用订单 MCP 完成订票和落库。
"""
import asyncio
import os
import re
import sys
import uuid

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from langchain_mcp_adapters.tools import load_mcp_tools
from python_a2a import (
    AgentCard,
    AgentSkill,
    run_server,
    TaskStatus,
    TaskState,
    A2AServer,
    A2AClient,
    Message,
    TextContent,
    MessageRole,
    Task,
)

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from create_logger import logger
from config import Config
from utils.model_factory import build_order_agent, extract_text_from_agent_result
from utils.resilient_llm import ResilientModelInvoker

conf = Config()
model_invoker = ResilientModelInvoker(conf)


def extract_username(conversation: str) -> str:
    matches = re.findall(r"当前用户[:：]\s*([^\s，。,\.]+)", conversation)
    if matches:
        return matches[-1].strip()
    return conf.default_username


def extract_departure_date(conversation: str) -> str:
    match = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", conversation)
    return match.group(1) if match else ""


def latest_user_request(conversation: str) -> str:
    marker = "\nUser:"
    if marker in conversation:
        return conversation.rsplit(marker, 1)[-1].strip()
    if conversation.strip().startswith("User:"):
        return conversation.split("User:", 1)[-1].strip()
    return conversation.strip()


def is_order_query(conversation: str) -> bool:
    keywords = (
        "我的订单",
        "查询订单",
        "查询我的订单",
        "查看订单",
        "看看我订",
        "我订了哪些",
        "已订",
        "已预订",
    )
    latest_query = latest_user_request(conversation)
    return any(keyword in latest_query for keyword in keywords)


async def order_tickets(query: str):
    try:
        async with streamablehttp_client("http://127.0.0.1:8003/mcp") as (read, write, _):
            async with ClientSession(read, write) as session:
                try:
                    await session.initialize()
                    tools = await load_mcp_tools(session)
                    response = await model_invoker.ainvoke_agent(
                        lambda model: build_order_agent(model, tools),
                        {"messages": [{"role": "user", "content": query}]},
                        description="订票 Agent 执行",
                    )
                    return {
                        "status": "success",
                        "message": extract_text_from_agent_result(response),
                    }
                except Exception as e:
                    logger.error(f"票务 MCP 调用出错：{str(e)}")
                    return {"status": "error", "message": f"票务 MCP 调用出错：{str(e)}"}
    except Exception as e:
        logger.error(f"连接或会话初始化时发生错误: {e}")
        return {"status": "error", "message": "连接或会话初始化时发生错误"}


async def query_my_orders(username: str, departure_date: str):
    try:
        async with streamablehttp_client("http://127.0.0.1:8003/mcp") as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                params = {"username": username}
                if departure_date:
                    params["departure_date"] = departure_date
                result = await session.call_tool("query_user_orders", params)
                if isinstance(result, str):
                    return result
                return result.content[0].text
    except Exception as exc:
        logger.error(f"查询订单失败: {exc}")
        return f"查询订单失败：{exc}"


agent_card = AgentCard(
    name="TicketOrderAssistant",
    description="通过MCP提供交通票务预定与订单查询服务的助手",
    url="http://localhost:5007",
    version="1.1.0",
    capabilities={"streaming": True, "memory": True},
    skills=[
        AgentSkill(
            name="execute ticket order",
            description="根据客户端提供的输入执行票务预定或查询当前用户订单，返回执行结果",
            examples=[
                "当前用户：demo_user\n帮我预订2026-03-21北京到上海的高铁票，二等座1张",
                "当前用户：demo_user\n查询我的订单",
            ],
        )
    ],
)


class TicketOrderServer(A2AServer):
    def __init__(self):
        super().__init__(agent_card=agent_card)
        self.ticket_client = A2AClient("http://localhost:5006")

    def handle_task(self, task):
        content = (task.message or {}).get("content", {})
        conversation = content.get("text", "") if isinstance(content, dict) else ""
        logger.info(f"对话历史及用户问题: {conversation}")

        try:
            username = extract_username(conversation)
            latest_query = latest_user_request(conversation)
            if is_order_query(conversation):
                order_result = asyncio.run(query_my_orders(username, extract_departure_date(latest_query)))
                task.artifacts = [{"parts": [{"type": "text", "text": order_result}]}]
                task.status = TaskStatus(state=TaskState.COMPLETED)
                return task

            message_ticket = Message(content=TextContent(text=conversation), role=MessageRole.USER)
            task_ticket = Task(id="task-" + str(uuid.uuid4()), message=message_ticket.to_dict())
            ticket_result_task = asyncio.run(self.ticket_client.send_task_async(task_ticket))
            logger.info(f"原始响应: {ticket_result_task}")

            if ticket_result_task.status.state != "completed":
                required_message = ticket_result_task.status.message["content"]["text"]
                logger.info(f"余票未查到：{required_message}")
                task.status = TaskStatus(
                    state=TaskState.INPUT_REQUIRED,
                    message={"role": "agent", "content": {"text": required_message}},
                )
                return task

            ticket_result = ticket_result_task.artifacts[0]["parts"][0]["text"]
            logger.info(f"余票信息: {ticket_result}")

            order_result = asyncio.run(
                order_tickets(f"{conversation}\n当前用户：{username}\n余票信息：{ticket_result}")
            )
            logger.info(f"MCP 返回: {order_result}")

            data = order_result.get("message", "")
            if order_result.get("status") == "success":
                result = "余票信息：" + ticket_result + "\n订票结果：" + data
                task.artifacts = [{"parts": [{"type": "text", "text": result}]}]
                task.status = TaskStatus(state=TaskState.COMPLETED)
            else:
                task.status = TaskStatus(
                    state=TaskState.FAILED,
                    message={"role": "agent", "content": {"text": data}},
                )
            return task
        except Exception as e:
            logger.error(f"查询失败: {str(e)}")
            task.status = TaskStatus(
                state=TaskState.FAILED,
                message={"role": "agent", "content": {"text": f"查询失败: {str(e)} 请重试或提供更多细节。"}},
            )
            return task


if __name__ == "__main__":
    ticket_server = TicketOrderServer()
    print("\n=== 服务器信息 ===")
    print(f"名称: {ticket_server.agent_card.name}")
    print(f"描述: {ticket_server.agent_card.description}")
    print("\n技能:")
    for skill in ticket_server.agent_card.skills:
        print(f"- {skill.name}: {skill.description}")
    run_server(ticket_server, host="127.0.0.1", port=5007)
