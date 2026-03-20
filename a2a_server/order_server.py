"""
order_server.py：
    订单代理服务器，负责交通票务下单、查询我的订单、退票与改签。
    下单时先调用票务查询 Agent 获取余票，再调用订单 MCP 完成订票和落库。
"""
import asyncio
import json
import os
import re
import sys
import uuid
from datetime import datetime
from typing import Any
from typing_extensions import Literal, TypedDict

from langgraph.graph import END, START, StateGraph
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
import pytz

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from create_logger import logger
from config import Config
from main_prompts import SmartVoyagePrompts
from utils.model_factory import build_order_agent, extract_text_from_agent_result
from utils.resilient_llm import ResilientModelInvoker
from utils.structured_outputs import OrderOperationExtractionResult

conf = Config()
model_invoker = ResilientModelInvoker(conf)


class OrderWorkflowState(TypedDict, total=False):
    conversation: str
    latest_query: str
    clean_query: str
    username: str
    action: Literal["query_orders", "cancel_order", "change_order", "create_order"]
    pending_context: dict[str, Any]
    operation_payload: dict[str, str]
    missing_fields: list[str]
    pending_order_context: dict[str, Any]
    ticket_result_text: str
    ticket_task_state: str
    final_text: str
    final_state: Literal["completed", "failed", "input_required"]


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


def is_cancel_query(conversation: str) -> bool:
    latest_query = latest_user_request(conversation)
    return any(keyword in latest_query for keyword in ("退票", "退掉", "取消订单", "取消这张票", "取消机票", "取消高铁票"))


def is_change_query(conversation: str) -> bool:
    latest_query = latest_user_request(conversation)
    return any(keyword in latest_query for keyword in ("改签", "改票", "改签到", "改成"))


PENDING_CONTEXT_PATTERN = re.compile(
    r"\[PENDING_ORDER_CONTEXT\](?P<payload>.*?)\[/PENDING_ORDER_CONTEXT\]",
    re.DOTALL,
)


def extract_pending_context(query: str) -> dict[str, Any]:
    match = PENDING_CONTEXT_PATTERN.search(query)
    if not match:
        return {}
    try:
        payload = json.loads(match.group("payload").strip())
        return payload if isinstance(payload, dict) else {}
    except json.JSONDecodeError:
        return {}


def strip_pending_context(query: str) -> str:
    return PENDING_CONTEXT_PATTERN.sub("", query).strip()


def summarize_conversation(conversation: str, limit: int = 8) -> str:
    lines = [line for line in conversation.splitlines() if line.strip()]
    return "\n".join(lines[-limit:])


def determine_action(query: str, pending_context: dict[str, Any]) -> Literal["query_orders", "cancel_order", "change_order", "create_order"]:
    pending_action = pending_context.get("action", "")
    if pending_action in {"cancel_order", "change_order"}:
        return pending_action
    if is_order_query(query):
        return "query_orders"
    if is_cancel_query(query):
        return "cancel_order"
    if is_change_query(query):
        return "change_order"
    return "create_order"


def normalize_missing_fields(action: str, result: OrderOperationExtractionResult) -> list[str]:
    missing = {field.strip() for field in result.missing_fields if field.strip()}
    if not result.order_type:
        missing.add("order_type")
    has_current_selector = bool(
        result.current_transport_no
        or (
            result.current_departure_date
            and result.departure_city
            and result.arrival_city
        )
    )
    if not has_current_selector:
        missing.add("current_order_selector")
    if action == "change_order" and not (
        result.new_departure_date
        or result.new_transport_no
        or result.new_ticket_type
    ):
        missing.add("new_target")
    return sorted(missing)


def default_follow_up_message(action: str, missing_fields: list[str]) -> str:
    if action == "cancel_order":
        if "order_type" in missing_fields and "current_order_selector" in missing_fields:
            return "请补充要退的是高铁票还是机票，以及至少一组订单条件，例如车次/航班号，或日期加出发到达城市。"
        if "order_type" in missing_fields:
            return "请补充要退的是高铁票还是机票。"
        return "请补充更具体的订单信息，例如车次/航班号，或日期加出发到达城市。"

    messages: list[str] = []
    if "order_type" in missing_fields:
        messages.append("高铁票还是机票")
    if "current_order_selector" in missing_fields:
        messages.append("当前订单的车次/航班号，或日期加路线")
    if "new_target" in missing_fields:
        messages.append("新的日期、车次/航班号或席位/舱位")
    joined = "、".join(messages) if messages else "改签信息"
    return f"请补充{joined}，我再继续帮你改签。"


def build_pending_order_context(
    *,
    action: str,
    query: str,
    extraction: OrderOperationExtractionResult,
    missing_fields: list[str],
) -> dict[str, Any]:
    extracted_fields = {
        key: value
        for key, value in {
            "order_type": extraction.order_type,
            "current_departure_date": extraction.current_departure_date,
            "departure_city": extraction.departure_city,
            "arrival_city": extraction.arrival_city,
            "current_transport_no": extraction.current_transport_no,
            "current_ticket_type": extraction.current_ticket_type,
            "new_departure_date": extraction.new_departure_date,
            "new_transport_no": extraction.new_transport_no,
            "new_ticket_type": extraction.new_ticket_type,
        }.items()
        if value
    }
    return {
        "action": action,
        "original_query": query,
        "missing_fields": missing_fields,
        "extracted_fields": extracted_fields,
    }


def pending_context_summary(pending_context: dict[str, Any]) -> str:
    if not pending_context:
        return "无"
    return json.dumps(pending_context, ensure_ascii=False)


async def run_order_agent(query: str):
    try:
        async with streamablehttp_client("http://127.0.0.1:8003/mcp") as (read, write, _):
            async with ClientSession(read, write) as session:
                try:
                    await session.initialize()
                    tools = await load_mcp_tools(session)
                    response = await model_invoker.ainvoke_agent(
                        lambda model: build_order_agent(model, tools),
                        {"messages": [{"role": "user", "content": query}]},
                        description="订单 Agent 执行",
                    )
                    return {
                        "status": "success",
                        "message": extract_text_from_agent_result(response),
                    }
                except Exception as e:
                    logger.error(f"订单 MCP 调用出错：{str(e)}")
                    return {"status": "error", "message": f"订单 MCP 调用出错：{str(e)}"}
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


async def invoke_order_tool(tool_name: str, params: dict[str, Any]) -> str:
    try:
        async with streamablehttp_client("http://127.0.0.1:8003/mcp") as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(tool_name, params)
                if isinstance(result, str):
                    return result
                return result.content[0].text
    except Exception as exc:
        logger.error(f"调用订单工具失败: tool={tool_name}, error={exc}")
        return f"调用订单工具失败：{exc}"


agent_card = AgentCard(
    name="TicketOrderAssistant",
    description="通过MCP提供交通票务预定、订单查询、退票与改签服务的助手",
    url="http://localhost:5007",
    version="1.1.0",
    capabilities={"streaming": True, "memory": True},
    skills=[
        AgentSkill(
            name="execute ticket order",
            description="根据客户端提供的输入执行票务预定、查询当前用户订单、退票或改签，返回执行结果",
            examples=[
                "当前用户：demo_user\n帮我预订2026-03-21北京到上海的高铁票，二等座1张",
                "当前用户：demo_user\n查询我的订单",
                "当前用户：demo_user\n帮我退掉2026-03-21北京到上海的高铁票",
                "当前用户：demo_user\n把我2026-03-21北京到上海的高铁票改签到2026-03-22二等座",
            ],
        )
    ],
)


class TicketOrderServer(A2AServer):
    def __init__(self):
        super().__init__(agent_card=agent_card)
        self.ticket_client = A2AClient("http://localhost:5006")
        self.workflow = self._build_workflow()

    def _build_workflow(self):
        workflow = StateGraph(OrderWorkflowState)
        workflow.add_node("prepare", self._prepare_state)
        workflow.add_node("query_orders", self._query_orders_node)
        workflow.add_node("cancel_order", self._cancel_order_node)
        workflow.add_node("change_order", self._change_order_node)
        workflow.add_node("lookup_tickets", self._lookup_tickets_node)
        workflow.add_node("create_order", self._create_order_node)

        workflow.add_edge(START, "prepare")
        workflow.add_conditional_edges(
            "prepare",
            self._route_action,
            {
                "finish": END,
                "query_orders": "query_orders",
                "cancel_order": "cancel_order",
                "change_order": "change_order",
                "create_order": "lookup_tickets",
            },
        )
        workflow.add_edge("query_orders", END)
        workflow.add_edge("cancel_order", END)
        workflow.add_edge("change_order", END)
        workflow.add_conditional_edges(
            "lookup_tickets",
            self._route_after_ticket_lookup,
            {
                "create_order": "create_order",
                "finish": END,
            },
        )
        workflow.add_edge("create_order", END)
        return workflow.compile()

    def _extract_operation_payload(
        self,
        *,
        action: Literal["cancel_order", "change_order"],
        conversation: str,
        query: str,
        pending_context: dict[str, Any],
    ) -> tuple[dict[str, str], list[str], str, dict[str, Any] | None]:
        current_date = datetime.now(pytz.timezone("Asia/Shanghai")).strftime("%Y-%m-%d")
        extraction = model_invoker.invoke_structured(
            SmartVoyagePrompts.order_operation_extraction_prompt(),
            OrderOperationExtractionResult,
            {
                "conversation_history": summarize_conversation(conversation),
                "query": query,
                "action": action,
                "current_date": current_date,
                "pending_context": pending_context_summary(pending_context),
            },
            description=f"订单操作参数抽取:{action}",
        )
        logger.info(f"订单参数抽取结果: {extraction.model_dump()}")

        missing_fields = normalize_missing_fields(action, extraction)
        follow_up_message = extraction.follow_up_message.strip() or default_follow_up_message(action, missing_fields)
        if missing_fields:
            pending = build_pending_order_context(
                action=action,
                query=query,
                extraction=extraction,
                missing_fields=missing_fields,
            )
            return {}, missing_fields, follow_up_message, pending

        payload = {
            "order_type": extraction.order_type,
            "departure_city": extraction.departure_city,
            "arrival_city": extraction.arrival_city,
        }
        if action == "cancel_order":
            payload.update(
                {
                    "departure_date": extraction.current_departure_date,
                    "transport_no": extraction.current_transport_no,
                    "ticket_type": extraction.current_ticket_type,
                }
            )
        else:
            payload.update(
                {
                    "current_departure_date": extraction.current_departure_date,
                    "current_transport_no": extraction.current_transport_no,
                    "current_ticket_type": extraction.current_ticket_type,
                    "new_departure_date": extraction.new_departure_date,
                    "new_transport_no": extraction.new_transport_no,
                    "new_ticket_type": extraction.new_ticket_type,
                }
            )
        return payload, [], "", None

    def _prepare_state(self, state: OrderWorkflowState) -> dict[str, Any]:
        conversation = state["conversation"]
        latest_query = latest_user_request(conversation)
        pending_context = extract_pending_context(latest_query)
        clean_query = strip_pending_context(latest_query)
        username = extract_username(conversation)
        action = determine_action(clean_query, pending_context)
        next_state: dict[str, Any] = {
            "latest_query": latest_query,
            "clean_query": clean_query,
            "username": username,
            "action": action,
            "pending_context": pending_context,
        }
        if action in {"cancel_order", "change_order"}:
            payload, missing_fields, follow_up_message, pending = self._extract_operation_payload(
                action=action,
                conversation=conversation,
                query=clean_query,
                pending_context=pending_context,
            )
            next_state["operation_payload"] = payload
            next_state["missing_fields"] = missing_fields
            if missing_fields:
                next_state["pending_order_context"] = pending
                next_state["final_text"] = follow_up_message
                next_state["final_state"] = "input_required"
        return next_state

    @staticmethod
    def _route_action(state: OrderWorkflowState) -> str:
        if state.get("final_state") == "input_required":
            return "finish"
        return state["action"]

    def _query_orders_node(self, state: OrderWorkflowState) -> dict[str, Any]:
        data = asyncio.run(query_my_orders(state["username"], extract_departure_date(state["clean_query"])))
        return {"final_text": data, "final_state": "completed"}

    def _cancel_order_node(self, state: OrderWorkflowState) -> dict[str, Any]:
        payload = dict(state["operation_payload"])
        payload["username"] = state["username"]
        data = asyncio.run(invoke_order_tool("cancel_ticket_order", payload))
        return {"final_text": data, "final_state": "completed"}

    def _change_order_node(self, state: OrderWorkflowState) -> dict[str, Any]:
        payload = dict(state["operation_payload"])
        payload["username"] = state["username"]
        data = asyncio.run(invoke_order_tool("change_ticket_order", payload))
        return {"final_text": data, "final_state": "completed"}

    def _lookup_tickets_node(self, state: OrderWorkflowState) -> dict[str, Any]:
        conversation = state["conversation"]
        message_ticket = Message(content=TextContent(text=conversation), role=MessageRole.USER)
        task_ticket = Task(id="task-" + str(uuid.uuid4()), message=message_ticket.to_dict())
        ticket_result_task = asyncio.run(self.ticket_client.send_task_async(task_ticket))
        logger.info(f"原始响应: {ticket_result_task}")

        if ticket_result_task.status.state != "completed":
            required_message = ticket_result_task.status.message["content"]["text"]
            logger.info(f"余票未查到：{required_message}")
            task_state = str(ticket_result_task.status.state).split(".")[-1].lower()
            final_state: Literal["completed", "failed", "input_required"] = (
                "input_required" if task_state == "input_required" else "failed"
            )
            return {
                "ticket_task_state": task_state,
                "final_text": required_message,
                "final_state": final_state,
            }

        ticket_result = ticket_result_task.artifacts[0]["parts"][0]["text"]
        logger.info(f"余票信息: {ticket_result}")
        return {
            "ticket_task_state": "completed",
            "ticket_result_text": ticket_result,
        }

    @staticmethod
    def _route_after_ticket_lookup(state: OrderWorkflowState) -> str:
        return "create_order" if state.get("ticket_task_state") == "completed" else "finish"

    def _create_order_node(self, state: OrderWorkflowState) -> dict[str, Any]:
        username = state["username"]
        conversation = state["conversation"]
        ticket_result = state["ticket_result_text"]
        order_result = asyncio.run(
            run_order_agent(f"{conversation}\n当前用户：{username}\n余票信息：{ticket_result}")
        )
        logger.info(f"MCP 返回: {order_result}")
        data = order_result.get("message", "")
        final_state: Literal["completed", "failed", "input_required"] = (
            "completed" if order_result.get("status") == "success" else "failed"
        )
        final_text = "余票信息：" + ticket_result + "\n订票结果：" + data if final_state == "completed" else data
        return {"final_text": final_text, "final_state": final_state}

    def handle_task(self, task):
        content = (task.message or {}).get("content", {})
        conversation = content.get("text", "") if isinstance(content, dict) else ""
        logger.info(f"对话历史及用户问题: {conversation}")

        try:
            result = self.workflow.invoke({"conversation": conversation})
            final_text = result.get("final_text", "订单流程执行失败，请重试。")
            final_state = result.get("final_state", "failed")
            if final_state == "completed":
                task.artifacts = [{"parts": [{"type": "text", "text": final_text}]}]
                task.status = TaskStatus(state=TaskState.COMPLETED)
            elif final_state == "input_required":
                content = {"text": final_text}
                if result.get("pending_order_context"):
                    content["pending_order_context"] = result["pending_order_context"]
                task.status = TaskStatus(
                    state=TaskState.INPUT_REQUIRED,
                    message={"role": "agent", "content": content},
                )
            else:
                task.status = TaskStatus(
                    state=TaskState.FAILED,
                    message={"role": "agent", "content": {"text": final_text}},
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
