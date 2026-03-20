"""
order_server.py：
    订单代理服务器，负责交通票务下单、查询我的订单、退票与改签。
    统一采用 LangGraph state + LLM 结构化抽取 slots + 后端强校验 + pending_context 多轮补参。
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

import pytz
from langgraph.graph import END, START, StateGraph
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from python_a2a import (
    A2AClient,
    A2AServer,
    AgentCard,
    AgentSkill,
    Message,
    MessageRole,
    Task,
    TaskState,
    TaskStatus,
    TextContent,
    run_server,
)

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import Config
from create_logger import logger
from main_prompts import SmartVoyagePrompts
from utils.resilient_llm import ResilientModelInvoker
from utils.structured_outputs import OrderWorkflowExtractionResult, PendingContextPayload

conf = Config()
model_invoker = ResilientModelInvoker(conf)

PENDING_CONTEXT_PATTERN = re.compile(
    r"\[PENDING_CONTEXT\](?P<payload>.*?)\[/PENDING_CONTEXT\]",
    re.DOTALL,
)
TRAIN_TICKET_PATTERN = re.compile(
    r"(?P<departure>\S+)\s+到\s+(?P<arrival>\S+)\s+(?P<departure_time>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}): "
    r"车次\s+(?P<transport_no>\S+)，(?P<ticket_type>[^，]+)，票价\s+(?P<price>[\d.]+)元，剩余\s+(?P<remaining>\d+)\s+张"
)
FLIGHT_TICKET_PATTERN = re.compile(
    r"(?P<departure>\S+)\s+到\s+(?P<arrival>\S+)\s+(?P<departure_time>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}): "
    r"航班\s+(?P<transport_no>\S+)，(?P<ticket_type>[^，]+)，票价\s+(?P<price>[\d.]+)元，剩余\s+(?P<remaining>\d+)\s+张"
)


class OrderWorkflowState(TypedDict, total=False):
    conversation: str
    latest_query: str
    clean_query: str
    username: str
    domain: Literal["order"]
    action: Literal["query_orders", "cancel_order", "change_order", "create_order"]
    slots: dict[str, Any]
    missing_slots: list[str]
    pending_context: dict[str, Any]
    execution_payload: dict[str, Any]
    ticket_result_text: str
    ticket_task_state: str
    final_text: str
    final_state: Literal["completed", "failed", "input_required"]
    next_pending_context: dict[str, Any]


def latest_user_request(conversation: str) -> str:
    marker = "\nUser:"
    if marker in conversation:
        return conversation.rsplit(marker, 1)[-1].strip()
    if conversation.strip().startswith("User:"):
        return conversation.split("User:", 1)[-1].strip()
    return conversation.strip()


def extract_username(conversation: str) -> str:
    matches = re.findall(r"当前用户[:：]\s*([^\s，。,\.]+)", conversation)
    if matches:
        return matches[-1].strip()
    return conf.default_username


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


def pending_context_summary(pending_context: dict[str, Any]) -> str:
    if not pending_context:
        return "无"
    return json.dumps(pending_context, ensure_ascii=False)


def merge_order_slots(extraction: OrderWorkflowExtractionResult, pending_context: dict[str, Any]) -> dict[str, Any]:
    pending_slots = pending_context.get("slots", {}) if isinstance(pending_context, dict) else {}
    merged: dict[str, Any] = dict(pending_slots) if isinstance(pending_slots, dict) else {}
    explicit_slots = {
        "query_order_type": extraction.query_order_type,
        "order_type": extraction.order_type,
        "departure_date": extraction.departure_date,
        "departure_city": extraction.departure_city,
        "arrival_city": extraction.arrival_city,
        "transport_no": extraction.transport_no,
        "ticket_type": extraction.ticket_type,
        "quantity": extraction.quantity,
        "new_departure_date": extraction.new_departure_date,
        "new_transport_no": extraction.new_transport_no,
        "new_ticket_type": extraction.new_ticket_type,
    }
    for key, value in explicit_slots.items():
        if isinstance(value, str):
            if value.strip():
                merged[key] = value.strip()
        elif value is not None:
            merged[key] = value
    if "quantity" not in merged or int(merged.get("quantity", 1)) <= 0:
        merged["quantity"] = 1
    return merged


def normalize_query_order_type(query: str, query_order_type: str) -> str:
    normalized_query = query.strip()
    explicit_transport_keywords = ("交通订单", "交通票订单", "交通票", "车票订单")
    explicit_train_keywords = ("高铁订单", "高铁票订单", "火车订单", "火车票订单", "高铁票", "火车票")
    explicit_flight_keywords = ("机票订单", "航班订单", "飞机票订单", "机票", "航班", "飞机票")
    explicit_hotel_keywords = ("酒店订单", "我的酒店", "酒店")

    if query_order_type == "transport" and any(keyword in normalized_query for keyword in explicit_transport_keywords):
        return "transport"
    if query_order_type == "train" and any(keyword in normalized_query for keyword in explicit_train_keywords):
        return "train"
    if query_order_type == "flight" and any(keyword in normalized_query for keyword in explicit_flight_keywords):
        return "flight"
    if query_order_type == "hotel" and any(keyword in normalized_query for keyword in explicit_hotel_keywords):
        return "hotel"
    return ""


def extract_query_order_types(query: str) -> list[str]:
    normalized_query = query.strip()
    detected: list[str] = []

    if any(keyword in normalized_query for keyword in ("交通订单", "交通票订单", "交通票", "车票订单")):
        return ["train", "flight"]

    if any(keyword in normalized_query for keyword in ("高铁订单", "高铁票订单", "火车订单", "火车票订单", "高铁票", "火车票")):
        detected.append("train")
    if any(keyword in normalized_query for keyword in ("机票订单", "航班订单", "飞机票订单", "机票", "航班", "飞机票")):
        detected.append("flight")
    if any(keyword in normalized_query for keyword in ("酒店订单", "我的酒店", "酒店")):
        detected.append("hotel")

    deduplicated: list[str] = []
    for item in detected:
        if item not in deduplicated:
            deduplicated.append(item)
    return deduplicated


def normalize_missing_slots(action: str, slots: dict[str, Any]) -> list[str]:
    missing: list[str] = []
    if action == "create_order":
        for field in ("order_type", "departure_date", "departure_city", "arrival_city"):
            if not str(slots.get(field, "")).strip():
                missing.append(field)
        return missing

    if action == "query_orders":
        return missing

    if not str(slots.get("order_type", "")).strip():
        missing.append("order_type")

    has_selector = bool(
        str(slots.get("transport_no", "")).strip()
        or (
            str(slots.get("departure_date", "")).strip()
            and str(slots.get("departure_city", "")).strip()
            and str(slots.get("arrival_city", "")).strip()
        )
    )
    if not has_selector:
        missing.append("current_order_selector")

    if action == "change_order":
        has_new_target = any(
            str(slots.get(field, "")).strip()
            for field in ("new_departure_date", "new_transport_no", "new_ticket_type")
        )
        if not has_new_target:
            missing.append("new_target")
    return missing


def default_follow_up_message(action: str, missing_slots: list[str]) -> str:
    if action == "create_order":
        messages = {
            "order_type": "高铁票还是机票",
            "departure_date": "出发日期",
            "departure_city": "出发城市",
            "arrival_city": "到达城市",
        }
        joined = "、".join(messages[item] for item in missing_slots if item in messages) or "订票信息"
        return f"请补充{joined}，我再继续帮你下单。"

    if action == "cancel_order":
        if "order_type" in missing_slots and "current_order_selector" in missing_slots:
            return "请补充要退的是高铁票还是机票，以及至少一组订单条件，例如车次/航班号，或日期加出发到达城市。"
        if "order_type" in missing_slots:
            return "请补充要退的是高铁票还是机票。"
        return "请补充更具体的订单信息，例如车次/航班号，或日期加出发到达城市。"

    if action == "change_order":
        messages: list[str] = []
        if "order_type" in missing_slots:
            messages.append("高铁票还是机票")
        if "current_order_selector" in missing_slots:
            messages.append("当前订单的车次/航班号，或日期加路线")
        if "new_target" in missing_slots:
            messages.append("新的日期、车次/航班号或席位/舱位")
        joined = "、".join(messages) if messages else "改签信息"
        return f"请补充{joined}，我再继续帮你改签。"

    return "请补充更具体的订单信息。"


def build_pending_context_payload(action: str, query: str, slots: dict[str, Any], missing_slots: list[str]) -> dict[str, Any]:
    payload = PendingContextPayload(
        domain="order",
        action=action,
        missing_slots=missing_slots,
        slots={key: value for key, value in slots.items() if value not in ("", None)},
        original_query=query,
    )
    return payload.model_dump()


def parse_ticket_candidates(raw_text: str) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for line in raw_text.splitlines():
        normalized = line.strip()
        if not normalized:
            continue
        train_match = TRAIN_TICKET_PATTERN.search(normalized)
        flight_match = FLIGHT_TICKET_PATTERN.search(normalized)
        match = train_match or flight_match
        if not match:
            continue
        candidate = match.groupdict()
        candidate["order_type"] = "train" if train_match else "flight"
        candidates.append(candidate)
    return candidates


def select_ticket_candidate(candidates: list[dict[str, Any]], slots: dict[str, Any]) -> dict[str, Any] | None:
    selected = candidates
    for field in ("order_type", "transport_no", "ticket_type", "departure_city", "arrival_city"):
        expected = str(slots.get(field, "")).strip()
        if expected:
            field_name = "departure" if field == "departure_city" else "arrival" if field == "arrival_city" else field
            filtered = [item for item in selected if str(item.get(field_name, "")).strip() == expected]
            if filtered:
                selected = filtered
    departure_date = str(slots.get("departure_date", "")).strip()
    if departure_date:
        filtered = [item for item in selected if str(item.get("departure_time", "")).startswith(departure_date)]
        if filtered:
            selected = filtered
    return selected[0] if selected else None


async def query_my_orders_with_filter(
    username: str,
    departure_date: str,
    order_type: str = "",
    order_types: list[str] | None = None,
) -> str:
    try:
        async with streamablehttp_client("http://127.0.0.1:8003/mcp") as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                params = {"username": username}
                if departure_date:
                    params["departure_date"] = departure_date
                if order_type:
                    params["order_type"] = order_type
                if order_types:
                    params["order_types"] = ",".join(order_types)
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
    description="通过 MCP 提供交通票务预定、订单查询、退票与改签服务的助手",
    url="http://localhost:5007",
    version="1.2.0",
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

    def _extract_workflow(self, conversation: str, query: str, pending_context: dict[str, Any]) -> tuple[str, dict[str, Any], list[str], str]:
        current_date = datetime.now(pytz.timezone("Asia/Shanghai")).strftime("%Y-%m-%d")
        extraction = model_invoker.invoke_structured(
            SmartVoyagePrompts.order_workflow_extraction_prompt(),
            OrderWorkflowExtractionResult,
            {
                "conversation_history": summarize_conversation(conversation),
                "query": query,
                "current_date": current_date,
                "pending_context": pending_context_summary(pending_context),
            },
            description="订单域状态抽取",
        )
        logger.info(f"订单域状态抽取结果: {extraction.model_dump()}")
        slots = merge_order_slots(extraction, pending_context)
        action = pending_context.get("action", extraction.action) if pending_context.get("domain") == "order" else extraction.action
        if action == "query_orders":
            slots["query_order_type"] = normalize_query_order_type(query, str(slots.get("query_order_type", "")))
        missing_slots = normalize_missing_slots(action, slots)
        follow_up_message = extraction.follow_up_message.strip() or default_follow_up_message(action, missing_slots)
        return action, slots, missing_slots, follow_up_message

    def _prepare_state(self, state: OrderWorkflowState) -> dict[str, Any]:
        conversation = state["conversation"]
        latest_query = latest_user_request(conversation)
        pending_context = extract_pending_context(latest_query)
        clean_query = strip_pending_context(latest_query)
        username = extract_username(conversation)
        action, slots, missing_slots, follow_up_message = self._extract_workflow(conversation, clean_query, pending_context)
        next_state: dict[str, Any] = {
            "latest_query": latest_query,
            "clean_query": clean_query,
            "username": username,
            "domain": "order",
            "action": action,
            "slots": slots,
            "missing_slots": missing_slots,
            "pending_context": pending_context,
        }
        if missing_slots:
            next_state["next_pending_context"] = build_pending_context_payload(action, clean_query, slots, missing_slots)
            next_state["final_text"] = follow_up_message
            next_state["final_state"] = "input_required"
        return next_state

    @staticmethod
    def _route_action(state: OrderWorkflowState) -> str:
        if state.get("final_state") == "input_required":
            return "finish"
        return state["action"]

    def _query_orders_node(self, state: OrderWorkflowState) -> dict[str, Any]:
        slots = state.get("slots", {})
        order_types = extract_query_order_types(state.get("clean_query", ""))
        data = asyncio.run(
            query_my_orders_with_filter(
                state["username"],
                str(slots.get("departure_date", "")),
                str(slots.get("query_order_type", "")),
                order_types,
            )
        )
        return {"final_text": data, "final_state": "completed"}

    def _cancel_order_node(self, state: OrderWorkflowState) -> dict[str, Any]:
        slots = state.get("slots", {})
        payload = {
            "username": state["username"],
            "departure_date": str(slots.get("departure_date", "")),
            "departure_city": str(slots.get("departure_city", "")),
            "arrival_city": str(slots.get("arrival_city", "")),
            "transport_no": str(slots.get("transport_no", "")),
            "ticket_type": str(slots.get("ticket_type", "")),
            "order_type": str(slots.get("order_type", "")),
        }
        data = asyncio.run(invoke_order_tool("cancel_ticket_order", payload))
        return {"final_text": data, "final_state": "completed"}

    def _change_order_node(self, state: OrderWorkflowState) -> dict[str, Any]:
        slots = state.get("slots", {})
        payload = {
            "username": state["username"],
            "current_departure_date": str(slots.get("departure_date", "")),
            "departure_city": str(slots.get("departure_city", "")),
            "arrival_city": str(slots.get("arrival_city", "")),
            "current_transport_no": str(slots.get("transport_no", "")),
            "current_ticket_type": str(slots.get("ticket_type", "")),
            "new_departure_date": str(slots.get("new_departure_date", "")),
            "new_transport_no": str(slots.get("new_transport_no", "")),
            "new_ticket_type": str(slots.get("new_ticket_type", "")),
            "order_type": str(slots.get("order_type", "")),
        }
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
        candidates = parse_ticket_candidates(ticket_result)
        selected = select_ticket_candidate(candidates, state.get("slots", {}))
        if not selected:
            return {
                "ticket_task_state": "failed",
                "final_text": "未能从查票结果中定位可下单的具体车次/航班，请补充更明确的车次、航班号或席位信息。",
                "final_state": "input_required",
                "next_pending_context": build_pending_context_payload(
                    "create_order",
                    state["clean_query"],
                    state.get("slots", {}),
                    ["transport_no_or_ticket_type"],
                ),
            }
        execution_payload = {
            "order_type": selected["order_type"],
            "departure_date": selected["departure_time"][:10],
            "transport_no": selected["transport_no"],
            "ticket_type": selected["ticket_type"],
            "quantity": int(state.get("slots", {}).get("quantity", 1)),
        }
        return {
            "ticket_task_state": "completed",
            "ticket_result_text": ticket_result,
            "execution_payload": execution_payload,
        }

    @staticmethod
    def _route_after_ticket_lookup(state: OrderWorkflowState) -> str:
        return "create_order" if state.get("ticket_task_state") == "completed" else "finish"

    def _create_order_node(self, state: OrderWorkflowState) -> dict[str, Any]:
        payload = dict(state.get("execution_payload", {}))
        payload["username"] = state["username"]
        order_type = payload.get("order_type", "")
        if order_type == "train":
            tool_name = "order_train"
            tool_params = {
                "username": payload["username"],
                "departure_date": payload["departure_date"],
                "train_number": payload["transport_no"],
                "seat_type": payload["ticket_type"],
                "number": payload["quantity"],
            }
        else:
            tool_name = "order_flight"
            tool_params = {
                "username": payload["username"],
                "departure_date": payload["departure_date"],
                "flight_number": payload["transport_no"],
                "seat_type": payload["ticket_type"],
                "number": payload["quantity"],
            }

        data = asyncio.run(invoke_order_tool(tool_name, tool_params))
        final_text = (
            "余票信息：" + state["ticket_result_text"] + "\n"
            f"本次下单选择：{payload['departure_date']} {payload['transport_no']} {payload['ticket_type']} {payload['quantity']}张。\n"
            "订票结果：" + data
        )
        return {"final_text": final_text, "final_state": "completed"}

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
                if result.get("next_pending_context"):
                    content["pending_context"] = result["next_pending_context"]
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
        except Exception as exc:
            logger.error(f"订单处理失败: {exc}")
            task.status = TaskStatus(
                state=TaskState.FAILED,
                message={"role": "agent", "content": {"text": f"订单处理失败: {exc} 请重试或提供更多细节。"}},
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
