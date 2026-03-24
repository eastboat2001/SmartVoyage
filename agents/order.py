"""
order.py：本地订单子代理，负责交通票务下单、查询我的订单、退票与改签。
"""
import asyncio
import json
import re
from datetime import datetime, timedelta
from typing import Any

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from langchain_mcp_adapters.tools import load_mcp_tools
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from typing_extensions import Literal, TypedDict

from config import Config
from create_logger import logger
from utils.agent_protocol import LocalAgentRequest, LocalAgentResponse
from utils.error_utils import format_exception_details
from main_prompts import SmartVoyagePrompts
from utils.model_factory import build_order_agent, extract_text_from_agent_result
from utils.metrics import clone_metrics, create_metrics, ensure_metrics, increment_metric, merge_metrics, track_phase
from utils.order_action_context import extract_order_action, strip_order_action
from utils.persistent_checkpointer import PersistentInMemorySaver
from utils.request_context import ensure_request_id, set_request_id
from utils.resilient_llm import ResilientModelInvoker
from utils.structured_outputs import DateResolutionResult, OrderActionDecisionResult, OrderOperationExtractionResult, ReviewDecisionResult
from utils.time_utils import get_current_date_str
from utils.travel_read_context import with_travel_read_kind


conf = Config()
model_invoker = ResilientModelInvoker(conf)

AGENT_NAME = "OrderSubagent"
AGENT_DESCRIPTION = "负责交通订单创建、查询、退票与改签的订单生命周期子代理"
AGENT_SKILLS = [
    {
        "name": "transport-order",
        "description": "根据客户端提供的输入执行票务预定、查询当前用户订单、退票或改签，返回执行结果",
        "examples": [
            "当前用户：demo_user\n帮我预订2026-03-21北京到上海的高铁票，二等座1张",
            "当前用户：demo_user\n查询我的订单",
            "当前用户：demo_user\n帮我退掉2026-03-21北京到上海的高铁票",
            "当前用户：demo_user\n把我2026-03-21北京到上海的高铁票改签到2026-03-22二等座",
        ],
    }
]


class OrderWorkflowState(TypedDict, total=False):
    conversation: str
    now_override: str
    metrics: dict[str, Any]
    latest_query: str
    clean_query: str
    username: str
    action: Literal["query_orders", "cancel_order", "change_order", "create_order"]
    pending_context: dict[str, Any]
    operation_payload: dict[str, str]
    missing_fields: list[str]
    pending_order_context: dict[str, Any]
    ticket_result_text: str
    ticket_result_data: dict[str, Any]
    ticket_task_state: str
    final_text: str
    final_state: Literal["completed", "failed", "input_required"]
    final_data: dict[str, Any]
    review_payload: dict[str, Any]
    review_decision: Literal["approved", "rejected"]


def extract_username(conversation: str) -> str:
    lines = [line.strip() for line in conversation.splitlines() if line.strip()]
    for line in reversed(lines):
        if not line.startswith("当前用户"):
            continue
        _, _, value = line.partition("：")
        if not value:
            _, _, value = line.partition(":")
        username = value.strip().split()[0] if value.strip() else ""
        if username:
            return username
    return conf.default_username


def _fast_normalize_date(query: str, current_date: str) -> str | None:
    normalized = query.strip()
    if not normalized:
        return ""

    base_date = datetime.fromisoformat(current_date)
    relative_map = {"今天": 0, "明天": 1, "后天": 2}
    for token, offset in relative_map.items():
        if token in normalized:
            return (base_date + timedelta(days=offset)).strftime("%Y-%m-%d")

    full_match = re.search(r"(?P<year>20\d{2})[-/.年](?P<month>\d{1,2})[-/.月](?P<day>\d{1,2})日?", normalized)
    if full_match:
        return f"{int(full_match.group('year')):04d}-{int(full_match.group('month')):02d}-{int(full_match.group('day')):02d}"

    month_day_match = re.search(r"(?P<month>\d{1,2})月(?P<day>\d{1,2})日?", normalized)
    if month_day_match:
        return f"{base_date.year:04d}-{int(month_day_match.group('month')):02d}-{int(month_day_match.group('day')):02d}"

    if not any(token in normalized for token in ("年", "月", "日", "号", "今天", "明天", "后天")):
        return ""
    return None


def extract_departure_date(conversation: str, metrics: dict[str, Any] | None = None) -> str:
    query = latest_user_request(conversation)
    current_date = get_current_date_str(conf)
    fast_date = _fast_normalize_date(query, current_date)
    if fast_date is not None:
        return fast_date

    result = model_invoker.invoke_structured(
        SmartVoyagePrompts.date_resolution_prompt(query=query),
        DateResolutionResult,
        {
            "current_date": current_date,
            "query": query,
        },
        description="订单查询日期归一化",
        metrics=metrics,
        phase_name="order_date_resolution",
    )
    logger.info(f"订单查询日期归一化结果: {result.model_dump()}")
    return result.normalized_date.strip()


def latest_user_request(conversation: str) -> str:
    marker = "\nUser:"
    if marker in conversation:
        return conversation.rsplit(marker, 1)[-1].strip()
    if conversation.strip().startswith("User:"):
        return conversation.split("User:", 1)[-1].strip()
    return conversation.strip()


PENDING_CONTEXT_PATTERN = re.compile(r"\[PENDING_ORDER_CONTEXT\](?P<payload>.*?)\[/PENDING_ORDER_CONTEXT\]", re.DOTALL)


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


def classify_order_action(
    conversation: str,
    raw_query: str,
    query: str,
    pending_context: dict[str, Any],
    metrics: dict[str, Any] | None = None,
) -> Literal["query_orders", "cancel_order", "change_order", "create_order"]:
    pending_action = pending_context.get("action", "")
    if pending_action in {"cancel_order", "change_order"}:
        return pending_action

    explicit_action = extract_order_action(raw_query)
    if explicit_action:
        return explicit_action

    result = model_invoker.invoke_structured(
        SmartVoyagePrompts.order_action_prompt(pending_context=pending_context_summary(pending_context)),
        OrderActionDecisionResult,
        {
            "pending_context": pending_context_summary(pending_context),
            "conversation_history": summarize_conversation(conversation),
            "query": query,
        },
        description="订单动作分类",
        metrics=metrics,
        phase_name="order_action_classify",
    )
    logger.info(f"订单动作分类结果: {result.model_dump()}")
    return result.action


def is_hitl_review_pending(pending_context: dict[str, Any]) -> bool:
    return pending_context.get("action") == "hitl_review" and bool(pending_context.get("thread_id"))


def parse_review_decision(query: str, review_payload: dict[str, Any], metrics: dict[str, Any] | None = None) -> tuple[Literal["approved", "rejected"] | None, str]:
    normalized_query = strip_order_action(strip_pending_context(query)).strip()
    result = model_invoker.invoke_structured(
        SmartVoyagePrompts.review_decision_prompt(),
        ReviewDecisionResult,
        {
            "review_summary": review_payload.get("summary", "待审批操作"),
            "query": normalized_query,
        },
        description="审批回复解析",
        metrics=metrics,
        phase_name="review_decision",
    )
    logger.info(f"审批回复解析结果: {result.model_dump()}")
    if result.decision == "approved":
        return "approved", ""
    if result.decision == "rejected":
        return "rejected", ""
    return None, (result.follow_up_message.strip() or "这是一个待审批操作。请明确回复确认执行或取消执行。")


def normalize_missing_fields(action: str, result: OrderOperationExtractionResult) -> list[str]:
    missing = {field.strip() for field in result.missing_fields if field.strip()}
    if not result.order_type:
        missing.add("order_type")
    has_current_selector = bool(result.current_transport_no or (result.current_departure_date and result.departure_city and result.arrival_city))
    if not has_current_selector:
        missing.add("current_order_selector")
    if action == "change_order" and not (result.new_departure_date or result.new_transport_no or result.new_ticket_type):
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


def build_pending_order_context(*, action: str, query: str, extraction: OrderOperationExtractionResult, missing_fields: list[str]) -> dict[str, Any]:
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
    return "无" if not pending_context else json.dumps(pending_context, ensure_ascii=False)


async def run_order_agent(query: str, metrics: dict[str, Any] | None = None):
    try:
        async with streamablehttp_client("http://127.0.0.1:8003/mcp") as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = await load_mcp_tools(session)
                response = await model_invoker.ainvoke_agent(
                    lambda model: build_order_agent(model, tools),
                    {"messages": [{"role": "user", "content": query}]},
                    description="订单 Agent 执行",
                    metrics=metrics,
                    phase_name="order_agent",
                )
                return {"status": "success", "message": extract_text_from_agent_result(response)}
    except Exception as exc:
        error_detail = format_exception_details(exc)
        logger.error(f"订单 MCP 调用出错：{error_detail}")
        return {"status": "error", "message": f"订单 MCP 调用出错：{error_detail}"}


async def query_my_orders(username: str, departure_date: str, metrics: dict[str, Any] | None = None):
    try:
        async with streamablehttp_client("http://127.0.0.1:8003/mcp") as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                params = {"username": username}
                if departure_date:
                    params["departure_date"] = departure_date
                increment_metric(metrics, "tool_call_count")
                with track_phase(metrics, "query_orders_tool"):
                    result = await session.call_tool("query_user_orders", params)
                return result if isinstance(result, str) else result.content[0].text
    except Exception as exc:
        error_detail = format_exception_details(exc)
        logger.error(f"查询订单失败: {error_detail}")
        return f"查询订单失败：{error_detail}"


async def invoke_order_tool(tool_name: str, params: dict[str, Any], metrics: dict[str, Any] | None = None) -> str:
    try:
        async with streamablehttp_client("http://127.0.0.1:8003/mcp") as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                increment_metric(metrics, "tool_call_count")
                with track_phase(metrics, tool_name):
                    result = await session.call_tool(tool_name, params)
                return result if isinstance(result, str) else result.content[0].text
    except Exception as exc:
        error_detail = format_exception_details(exc)
        logger.error(f"调用订单工具失败: tool={tool_name}, error={error_detail}")
        return f"调用订单工具失败：{error_detail}"


class OrderSubagent:
    def __init__(self, config: Config, travel_read_agent):
        self.config = config
        self.travel_read_agent = travel_read_agent
        self.checkpointer = PersistentInMemorySaver(config.order_checkpoint_path)
        self.metadata = {
            "name": AGENT_NAME,
            "description": AGENT_DESCRIPTION,
            "skills": AGENT_SKILLS,
        }
        self.workflow = self._build_workflow()

    def _build_workflow(self):
        workflow = StateGraph(OrderWorkflowState)
        workflow.add_node("prepare", self._prepare_state)
        workflow.add_node("review", self._review_node)
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
                "cancel_order": "review",
                "change_order": "review",
                "create_order": "lookup_tickets",
            },
        )
        workflow.add_edge("query_orders", END)
        workflow.add_edge("cancel_order", END)
        workflow.add_edge("change_order", END)
        workflow.add_conditional_edges(
            "lookup_tickets",
            self._route_after_ticket_lookup,
            {"review": "review", "finish": END},
        )
        workflow.add_conditional_edges(
            "review",
            self._route_after_review,
            {
                "create_order": "create_order",
                "cancel_order": "cancel_order",
                "change_order": "change_order",
                "finish": END,
            },
        )
        workflow.add_edge("create_order", END)
        return workflow.compile(checkpointer=self.checkpointer)

    def _extract_operation_payload(
        self,
        *,
        action: Literal["cancel_order", "change_order"],
        conversation: str,
        query: str,
        pending_context: dict[str, Any],
        metrics: dict[str, Any] | None = None,
    ) -> tuple[dict[str, str], list[str], str, dict[str, Any] | None]:
        current_date = get_current_date_str(conf)
        extraction = model_invoker.invoke_structured(
            SmartVoyagePrompts.order_operation_extraction_prompt(action=action, pending_context=pending_context_summary(pending_context)),
            OrderOperationExtractionResult,
            {
                "conversation_history": summarize_conversation(conversation),
                "query": query,
                "action": action,
                "current_date": current_date,
                "pending_context": pending_context_summary(pending_context),
            },
            description=f"订单操作参数抽取:{action}",
            metrics=metrics,
            phase_name=f"order_operation_extract_{action}",
        )
        extraction_data = extraction.model_dump()
        pending_fields = pending_context.get("extracted_fields", {}) if isinstance(pending_context, dict) else {}
        if isinstance(pending_fields, dict):
            for key, value in pending_fields.items():
                if key in extraction_data and not extraction_data.get(key) and value:
                    extraction_data[key] = value
        extraction = OrderOperationExtractionResult(**extraction_data)
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

        payload = {"order_type": extraction.order_type, "departure_city": extraction.departure_city, "arrival_city": extraction.arrival_city}
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
        metrics = ensure_metrics(state.get("metrics"))
        latest_query = latest_user_request(conversation)
        pending_context = extract_pending_context(latest_query)
        clean_query = strip_order_action(strip_pending_context(latest_query))
        username = extract_username(conversation)
        action = classify_order_action(conversation, latest_query, clean_query, pending_context, metrics)
        next_state: dict[str, Any] = {
            "latest_query": latest_query,
            "clean_query": clean_query,
            "username": username,
            "action": action,
            "pending_context": pending_context,
            "metrics": metrics,
        }
        if action in {"cancel_order", "change_order"}:
            payload, missing_fields, follow_up_message, pending = self._extract_operation_payload(
                action=action,
                conversation=conversation,
                query=clean_query,
                pending_context=pending_context,
                metrics=metrics,
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

    async def _query_orders_node(self, state: OrderWorkflowState) -> dict[str, Any]:
        metrics = ensure_metrics(state.get("metrics"))
        departure_date = extract_departure_date(state["clean_query"], metrics)
        data = await query_my_orders(state["username"], departure_date, metrics)
        return {"final_text": data, "final_state": "completed", "metrics": metrics}

    async def _cancel_order_node(self, state: OrderWorkflowState) -> dict[str, Any]:
        metrics = ensure_metrics(state.get("metrics"))
        payload = dict(state["operation_payload"])
        payload["username"] = state["username"]
        data = await invoke_order_tool("cancel_ticket_order", payload, metrics)
        return {"final_text": data, "final_state": "completed", "metrics": metrics}

    async def _change_order_node(self, state: OrderWorkflowState) -> dict[str, Any]:
        metrics = ensure_metrics(state.get("metrics"))
        payload = dict(state["operation_payload"])
        payload["username"] = state["username"]
        data = await invoke_order_tool("change_ticket_order", payload, metrics)
        return {"final_text": data, "final_state": "completed", "metrics": metrics}

    async def _lookup_tickets_node(self, state: OrderWorkflowState) -> dict[str, Any]:
        metrics = ensure_metrics(state.get("metrics"))
        raw_query = latest_user_request(state["conversation"])
        ticket_query = with_travel_read_kind(strip_order_action(strip_pending_context(raw_query)), "ticket")
        request_id = ensure_request_id()
        ticket_result = self.travel_read_agent.invoke(
            LocalAgentRequest(
                text=f"User: {ticket_query}",
                conversation_history=f"User: {ticket_query}",
                request_id=request_id,
                now_override=state.get("now_override", ""),
                metrics=create_metrics(),
            )
        )
        merge_metrics(metrics, (ticket_result.meta or {}).get("metrics"))
        if ticket_result.state != "completed":
            logger.info(f"余票未查到：{ticket_result.text}")
            final_state: Literal["completed", "failed", "input_required"] = (
                "input_required" if ticket_result.state == "input_required" else "failed"
            )
            return {
                "ticket_task_state": ticket_result.state,
                "final_text": ticket_result.text,
                "final_state": final_state,
                "metrics": metrics,
            }

        logger.info(f"余票信息: {ticket_result.text}")
        return {
            "ticket_task_state": "completed",
            "ticket_result_text": ticket_result.text,
            "ticket_result_data": ticket_result.data or {},
            "metrics": metrics,
        }

    @staticmethod
    def _route_after_ticket_lookup(state: OrderWorkflowState) -> str:
        return "review" if state.get("ticket_task_state") == "completed" else "finish"

    def _build_review_payload(self, state: OrderWorkflowState) -> dict[str, Any]:
        action = state["action"]
        if action == "create_order":
            tickets = state.get("ticket_result_data", {}).get("tickets", [])
            ticket = tickets[0] if tickets else {}
            return {
                "kind": "transport_order_review",
                "action": action,
                "username": state["username"],
                "order_type": ticket.get("order_type", ""),
                "departure_city": ticket.get("departure_city", ""),
                "arrival_city": ticket.get("arrival_city", ""),
                "departure_time": ticket.get("departure_time", ""),
                "transport_no": ticket.get("transport_no", ""),
                "ticket_type": ticket.get("ticket_type", ""),
                "quantity": 1,
                "price": ticket.get("price", ""),
                "summary": f"下单审批：{ticket.get('departure_time', '')} {ticket.get('departure_city', '')}到{ticket.get('arrival_city', '')} {ticket.get('transport_no', '')} {ticket.get('ticket_type', '')} 1张。",
            }

        payload = state.get("operation_payload", {})
        return {
            "kind": "transport_order_review",
            "action": action,
            "username": state["username"],
            "order_type": payload.get("order_type", ""),
            "departure_city": payload.get("departure_city", ""),
            "arrival_city": payload.get("arrival_city", ""),
            "departure_date": payload.get("departure_date") or payload.get("current_departure_date", ""),
            "transport_no": payload.get("transport_no") or payload.get("current_transport_no", ""),
            "ticket_type": payload.get("ticket_type") or payload.get("current_ticket_type", ""),
            "new_departure_date": payload.get("new_departure_date", ""),
            "new_transport_no": payload.get("new_transport_no", ""),
            "new_ticket_type": payload.get("new_ticket_type", ""),
            "summary": (
                f"{'退票' if action == 'cancel_order' else '改签'}审批："
                f"{payload.get('departure_date') or payload.get('current_departure_date', '')} "
                f"{payload.get('departure_city', '')}到{payload.get('arrival_city', '')} "
                f"{payload.get('transport_no') or payload.get('current_transport_no', '')} "
                f"{payload.get('ticket_type') or payload.get('current_ticket_type', '')}"
            ),
        }

    def _review_node(self, state: OrderWorkflowState) -> dict[str, Any]:
        review_payload = self._build_review_payload(state)
        decision = interrupt(review_payload)
        if isinstance(decision, dict):
            normalized = decision.get("decision", "")
        else:
            normalized = decision
        if normalized == "approved":
            return {
                "review_payload": review_payload,
                "review_decision": "approved",
            }
        return {
            "review_payload": review_payload,
            "review_decision": "rejected",
            "final_state": "completed",
            "final_text": "已取消本次操作，未执行实际下单、退票或改签。",
            "final_data": {"kind": "transport_order_review", "review_payload": review_payload},
        }

    @staticmethod
    def _route_after_review(state: OrderWorkflowState) -> str:
        if state.get("review_decision") != "approved":
            return "finish"
        return state["action"]

    async def _create_order_node(self, state: OrderWorkflowState) -> dict[str, Any]:
        metrics = ensure_metrics(state.get("metrics"))
        username = state["username"]
        conversation = state["conversation"]
        ticket_result = state["ticket_result_text"]
        ticket_data = state.get("ticket_result_data", {})
        tickets = ticket_data.get("tickets", [])
        if tickets:
            selected = tickets[0]
            order_type = selected.get("order_type", "")
            transport_label = "高铁票" if order_type == "train" else "机票"
            transport_field = "车次" if order_type == "train" else "航班"
            deterministic_query = (
                f"当前用户：{username}\n"
                f"请直接预订{str(selected.get('departure_time', ''))[:10]}"
                f"{selected.get('departure_city', '')}到{selected.get('arrival_city', '')}的{transport_label}，"
                f"{transport_field}{selected.get('transport_no', '')}，"
                f"{selected.get('ticket_type', '')}1张。"
            )
            order_result = await run_order_agent(deterministic_query, metrics)
        else:
            order_result = await run_order_agent(f"{conversation}\n当前用户：{username}\n余票信息：{ticket_result}", metrics)
        logger.info(f"MCP 返回: {order_result}")
        data = order_result.get("message", "")
        final_state: Literal["completed", "failed", "input_required"] = "completed" if order_result.get("status") == "success" else "failed"
        final_text = "余票信息：" + ticket_result + "\n订票结果：" + data if final_state == "completed" else data
        return {
            "final_text": final_text,
            "final_state": final_state,
            "final_data": {
                "kind": "transport_order",
                "ticket_result": ticket_data,
            },
            "metrics": metrics,
        }

    async def ainvoke(self, request: LocalAgentRequest) -> LocalAgentResponse:
        request_id = request.request_id or ensure_request_id()
        set_request_id(request_id)
        metrics = ensure_metrics(request.metrics)
        logger.info(f"[{request_id}] 订单域收到对话: {request.text}")
        latest_query = latest_user_request(request.text)
        pending_context = extract_pending_context(latest_query)

        if is_hitl_review_pending(pending_context):
            thread_id = pending_context["thread_id"]
            review_payload = pending_context.get("review_payload", {})
            decision, follow_up_message = parse_review_decision(latest_query, review_payload, metrics)
            if not decision:
                return LocalAgentResponse(
                    state="input_required",
                    text=follow_up_message,
                    pending_order_context=pending_context,
                    data={"kind": "hitl_review", "review_payload": review_payload},
                    meta={"thread_id": thread_id, "review_required": True, "metrics": clone_metrics(metrics)},
                )
            result = await self.workflow.ainvoke(
                Command(resume={"decision": decision}),
                config={"configurable": {"thread_id": thread_id}},
            )
        else:
            thread_id = request_id
            result = await self.workflow.ainvoke(
                {"conversation": request.text, "now_override": request.now_override, "metrics": metrics},
                config={"configurable": {"thread_id": thread_id}},
            )

        if "__interrupt__" in result:
            interrupt_payload = result["__interrupt__"][0].value
            return LocalAgentResponse(
                state="input_required",
                text=(
                    f"{interrupt_payload.get('summary', '检测到待审批操作。')}\n"
                    "请回复 yes 确认执行，或回复 no 取消执行。"
                ),
                pending_order_context={
                    "action": "hitl_review",
                    "thread_id": thread_id,
                    "review_payload": interrupt_payload,
                    "resume_intent": interrupt_payload.get("action", "order"),
                },
                data={"kind": "hitl_review", "review_payload": interrupt_payload},
                meta={"thread_id": thread_id, "review_required": True, "metrics": clone_metrics(metrics)},
            )

        final_text = result.get("final_text", "订单流程执行失败，请重试。")
        final_state = result.get("final_state", "failed")
        return LocalAgentResponse(
            state=final_state,
            text=final_text,
            pending_order_context=result.get("pending_order_context", {}),
            data=result.get("final_data", {}),
            meta={
                "kind": "transport_order",
                "action": result.get("action", ""),
                "thread_id": thread_id,
                "metrics": clone_metrics(result.get("metrics", metrics)),
            },
        )
    def invoke(self, request: LocalAgentRequest) -> LocalAgentResponse:
        try:
            return asyncio.run(self.ainvoke(request))
        except RuntimeError:
            loop = asyncio.new_event_loop()
            try:
                return loop.run_until_complete(self.ainvoke(request))
            finally:
                loop.close()


