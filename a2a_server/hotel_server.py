"""
hotel_server.py：酒店代理服务器，支持酒店查询、酒店预订、酒店订单查询、取消与改期。
统一采用 LangGraph state + LLM 结构化抽取 slots + 后端强校验 + pending_context 多轮补参。
"""
import asyncio
import json
import os
import re
import sys
from datetime import datetime
from typing import Any
from typing_extensions import Literal, TypedDict

import pytz
from langgraph.graph import END, START, StateGraph
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from python_a2a import A2AServer, AgentCard, AgentSkill, TaskState, TaskStatus, run_server

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import Config
from create_logger import logger
from main_prompts import SmartVoyagePrompts
from utils.db import get_db_connection
from utils.resilient_llm import ResilientModelInvoker
from utils.structured_outputs import HotelWorkflowExtractionResult, PendingContextPayload

conf = Config()

PENDING_CONTEXT_PATTERN = re.compile(
    r"\[PENDING_CONTEXT\](?P<payload>.*?)\[/PENDING_CONTEXT\]",
    re.DOTALL,
)

agent_card = AgentCard(
    name="HotelAssistant",
    description="提供酒店查询、酒店预订、酒店订单查询、取消与改期服务的助手",
    url="http://localhost:5008",
    version="1.2.0",
    capabilities={"streaming": True, "memory": True},
    skills=[
        AgentSkill(
            name="execute hotel workflow",
            description="根据客户端提供的输入执行酒店查询、酒店预订、酒店订单查询、取消与改期",
            examples=[
                "查询2026-03-21上海的酒店",
                "帮我订2026-03-21上海外滩云际酒店的高级大床房，住2晚1间",
                "查询我的酒店订单",
                "取消我订的2026-03-21上海外滩云际酒店",
                "把我2026-03-21上海外滩云际酒店改到2026-03-22",
            ],
        )
    ],
)


class HotelWorkflowState(TypedDict, total=False):
    conversation: str
    latest_query: str
    clean_query: str
    username: str
    domain: Literal["hotel"]
    action: Literal[
        "query_hotels",
        "query_hotel_orders",
        "create_hotel_order",
        "cancel_hotel_order",
        "change_hotel_order",
    ]
    slots: dict[str, Any]
    missing_slots: list[str]
    pending_context: dict[str, Any]
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


def merge_hotel_slots(extraction: HotelWorkflowExtractionResult, pending_context: dict[str, Any]) -> dict[str, Any]:
    pending_slots = pending_context.get("slots", {}) if isinstance(pending_context, dict) else {}
    merged: dict[str, Any] = dict(pending_slots) if isinstance(pending_slots, dict) else {}
    explicit_slots = {
        "city": extraction.city,
        "hotel_name": extraction.hotel_name,
        "room_type": extraction.room_type,
        "check_in_date": extraction.check_in_date,
        "nights": extraction.nights,
        "rooms": extraction.rooms,
        "new_city": extraction.new_city,
        "new_hotel_name": extraction.new_hotel_name,
        "new_room_type": extraction.new_room_type,
        "new_check_in_date": extraction.new_check_in_date,
        "new_nights": extraction.new_nights,
    }
    for key, value in explicit_slots.items():
        if isinstance(value, str):
            if value.strip():
                merged[key] = value.strip()
        elif value is not None and value != 0:
            merged[key] = value
    if "nights" not in merged or int(merged.get("nights", 1)) <= 0:
        merged["nights"] = 1
    if "rooms" not in merged or int(merged.get("rooms", 1)) <= 0:
        merged["rooms"] = 1
    return merged


def normalize_missing_slots(action: str, slots: dict[str, Any]) -> list[str]:
    if action == "query_hotels":
        missing = []
        if not str(slots.get("city", "")).strip():
            missing.append("city")
        if not str(slots.get("check_in_date", "")).strip():
            missing.append("check_in_date")
        return missing

    if action == "create_hotel_order":
        missing = []
        for field in ("hotel_name", "room_type", "check_in_date"):
            if not str(slots.get(field, "")).strip():
                missing.append(field)
        return missing

    if action == "cancel_hotel_order":
        has_selector = bool(
            (str(slots.get("hotel_name", "")).strip() and str(slots.get("check_in_date", "")).strip())
            or (
                str(slots.get("city", "")).strip()
                and str(slots.get("check_in_date", "")).strip()
                and str(slots.get("room_type", "")).strip()
            )
        )
        return [] if has_selector else ["current_hotel_selector"]

    if action == "change_hotel_order":
        missing: list[str] = []
        has_selector = bool(
            (str(slots.get("hotel_name", "")).strip() and str(slots.get("check_in_date", "")).strip())
            or (
                str(slots.get("city", "")).strip()
                and str(slots.get("check_in_date", "")).strip()
                and str(slots.get("room_type", "")).strip()
            )
        )
        if not has_selector:
            missing.append("current_hotel_selector")
        has_new_target = any(
            [
                str(slots.get("new_city", "")).strip(),
                str(slots.get("new_hotel_name", "")).strip(),
                str(slots.get("new_room_type", "")).strip(),
                str(slots.get("new_check_in_date", "")).strip(),
                int(slots.get("new_nights", 0)) > 0,
            ]
        )
        if not has_new_target:
            missing.append("new_hotel_target")
        return missing

    return []


def default_follow_up_message(action: str, missing_slots: list[str]) -> str:
    if action == "query_hotels":
        messages = {"city": "城市", "check_in_date": "入住日期"}
        joined = "、".join(messages[item] for item in missing_slots if item in messages) or "酒店查询条件"
        return f"请补充{joined}，例如查询2026-03-21上海的酒店。"

    if action == "create_hotel_order":
        messages = {"hotel_name": "酒店名", "room_type": "房型", "check_in_date": "入住日期"}
        joined = "、".join(messages[item] for item in missing_slots if item in messages) or "酒店预订信息"
        return f"请补充{joined}，我再继续帮你预订酒店。"

    if action == "cancel_hotel_order":
        return "请补充更具体的酒店订单信息，例如酒店名加入住日期，或城市、入住日期和房型。"

    if action == "change_hotel_order":
        messages: list[str] = []
        if "current_hotel_selector" in missing_slots:
            messages.append("当前酒店订单的酒店名+入住日期，或城市+入住日期+房型")
        if "new_hotel_target" in missing_slots:
            messages.append("新的入住日期、酒店、房型或晚数")
        joined = "、".join(messages) if messages else "酒店改期信息"
        return f"请补充{joined}，我再继续帮你改期。"

    return "请补充更具体的酒店信息。"


def build_pending_context_payload(action: str, query: str, slots: dict[str, Any], missing_slots: list[str]) -> dict[str, Any]:
    payload = PendingContextPayload(
        domain="hotel",
        action=action,
        missing_slots=missing_slots,
        slots={key: value for key, value in slots.items() if value not in ("", None, 0)},
        original_query=query,
    )
    return payload.model_dump()


class HotelAssistantServer(A2AServer):
    def __init__(self):
        super().__init__(agent_card=agent_card)
        self.invoker = ResilientModelInvoker(conf)
        self.workflow = self._build_workflow()

    def _build_workflow(self):
        workflow = StateGraph(HotelWorkflowState)
        workflow.add_node("prepare", self._prepare_state)
        workflow.add_node("query_hotels", self._query_hotels_node)
        workflow.add_node("query_hotel_orders", self._query_hotel_orders_node)
        workflow.add_node("create_hotel_order", self._create_hotel_order_node)
        workflow.add_node("cancel_hotel_order", self._cancel_hotel_order_node)
        workflow.add_node("change_hotel_order", self._change_hotel_order_node)

        workflow.add_edge(START, "prepare")
        workflow.add_conditional_edges(
            "prepare",
            self._route_action,
            {
                "finish": END,
                "query_hotels": "query_hotels",
                "query_hotel_orders": "query_hotel_orders",
                "create_hotel_order": "create_hotel_order",
                "cancel_hotel_order": "cancel_hotel_order",
                "change_hotel_order": "change_hotel_order",
            },
        )
        workflow.add_edge("query_hotels", END)
        workflow.add_edge("query_hotel_orders", END)
        workflow.add_edge("create_hotel_order", END)
        workflow.add_edge("cancel_hotel_order", END)
        workflow.add_edge("change_hotel_order", END)
        return workflow.compile()

    def _extract_workflow(self, conversation: str, query: str, pending_context: dict[str, Any]) -> tuple[str, dict[str, Any], list[str], str]:
        current_date = datetime.now(pytz.timezone("Asia/Shanghai")).strftime("%Y-%m-%d")
        extraction = self.invoker.invoke_structured(
            SmartVoyagePrompts.hotel_workflow_extraction_prompt(),
            HotelWorkflowExtractionResult,
            {
                "conversation_history": summarize_conversation(conversation),
                "query": query,
                "current_date": current_date,
                "pending_context": pending_context_summary(pending_context),
            },
            description="酒店域状态抽取",
        )
        logger.info(f"酒店域状态抽取结果: {extraction.model_dump()}")
        slots = merge_hotel_slots(extraction, pending_context)
        action = pending_context.get("action", extraction.action) if pending_context.get("domain") == "hotel" else extraction.action
        missing_slots = normalize_missing_slots(action, slots)
        follow_up_message = extraction.follow_up_message.strip() or default_follow_up_message(action, missing_slots)
        return action, slots, missing_slots, follow_up_message

    def _prepare_state(self, state: HotelWorkflowState) -> dict[str, Any]:
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
            "domain": "hotel",
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
    def _route_action(state: HotelWorkflowState) -> str:
        if state.get("final_state") == "input_required":
            return "finish"
        return state["action"]

    def _load_user_budget_level(self, username: str) -> str:
        conn = None
        cursor = None
        try:
            conn = get_db_connection(conf)
            cursor = conn.cursor(dictionary=True)
            cursor.execute(
                """
                SELECT p.budget_level
                FROM users u
                LEFT JOIN user_preferences p ON p.user_id = u.id
                WHERE u.username = %s
                LIMIT 1
                """,
                (username,),
            )
            row = cursor.fetchone()
            return (row or {}).get("budget_level") or "medium"
        except Exception as exc:
            logger.warning(f"读取酒店排序画像失败: {exc}")
            return "medium"
        finally:
            if cursor is not None:
                cursor.close()
            if conn is not None and conn.is_connected():
                conn.close()

    @staticmethod
    def _sort_clause_for_budget(budget_level: str) -> str:
        if budget_level == "low":
            return "ORDER BY r.price_per_night ASC, r.is_refundable DESC, h.star_rating DESC"
        if budget_level == "high":
            return "ORDER BY h.star_rating DESC, r.breakfast_included DESC, r.price_per_night ASC"
        return "ORDER BY r.is_refundable DESC, h.star_rating DESC, r.price_per_night ASC"

    @staticmethod
    def build_hotel_query_sql(slots: dict[str, Any], budget_level: str) -> str:
        city = str(slots.get("city", "")).replace("'", "''")
        check_in_date = str(slots.get("check_in_date", "")).replace("'", "''")
        hotel_name = str(slots.get("hotel_name", "")).replace("'", "''")
        room_type = str(slots.get("room_type", "")).replace("'", "''")
        filters = [
            "h.id = r.hotel_id",
            f"h.city = '{city}'",
            f"r.stay_date = '{check_in_date}'",
            "r.remaining_rooms > 0",
        ]
        if hotel_name:
            filters.append(f"h.name LIKE '%{hotel_name}%'")
        if room_type:
            filters.append(f"r.room_type LIKE '%{room_type}%'")
        return (
            "SELECT "
            "h.name, h.city, h.district, h.star_rating, r.stay_date, r.room_type, r.bed_type, "
            "r.breakfast_included, r.is_refundable, r.price_per_night, r.remaining_rooms "
            "FROM hotels h JOIN hotel_room_inventory r ON h.id = r.hotel_id "
            f"WHERE {' AND '.join(filters)} "
            f"{HotelAssistantServer._sort_clause_for_budget(budget_level)}"
        )

    async def invoke_hotel_tool(self, tool_name: str, params: dict) -> dict[str, str]:
        try:
            async with streamablehttp_client("http://127.0.0.1:8004/mcp") as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    result = await session.call_tool(tool_name, params)
                    if isinstance(result, str):
                        return {"status": "success", "message": result}
                    return {"status": "success", "message": result.content[0].text}
        except Exception as exc:
            logger.error(f"酒店工具调用失败: tool={tool_name}, error={exc}")
            return {"status": "error", "message": f"酒店工具调用失败：{exc}"}

    async def get_hotel_info(self, sql: str) -> str:
        tool_result = await self.invoke_hotel_tool("query_hotels", {"sql": sql})
        if tool_result["status"] != "success":
            return json.dumps({"status": "error", "message": tool_result["message"]}, ensure_ascii=False)
        return tool_result["message"]

    def _query_hotel_orders_node(self, state: HotelWorkflowState) -> dict[str, Any]:
        slots = state.get("slots", {})
        hotel_result = asyncio.run(
            self.invoke_hotel_tool(
                "query_user_hotel_orders",
                {
                    "username": state["username"],
                    "check_in_date": str(slots.get("check_in_date", "")),
                },
            )
        )
        if hotel_result["status"] != "success":
            return {"final_text": hotel_result["message"], "final_state": "failed"}
        return {"final_text": hotel_result["message"], "final_state": "completed"}

    def _create_hotel_order_node(self, state: HotelWorkflowState) -> dict[str, Any]:
        slots = state.get("slots", {})
        hotel_result = asyncio.run(
            self.invoke_hotel_tool(
                "order_hotel_room",
                {
                    "username": state["username"],
                    "city": str(slots.get("city", "")),
                    "hotel_name": str(slots.get("hotel_name", "")),
                    "room_type": str(slots.get("room_type", "")),
                    "check_in_date": str(slots.get("check_in_date", "")),
                    "nights": int(slots.get("nights", 1)),
                    "rooms": int(slots.get("rooms", 1)),
                },
            )
        )
        if hotel_result["status"] != "success":
            return {"final_text": hotel_result["message"], "final_state": "failed"}
        if "请确认" in hotel_result["message"] or "请补充" in hotel_result["message"] or "匹配到多家酒店" in hotel_result["message"]:
            return {
                "final_text": hotel_result["message"],
                "final_state": "input_required",
                "next_pending_context": build_pending_context_payload("create_hotel_order", state["clean_query"], slots, []),
            }
        return {"final_text": hotel_result["message"], "final_state": "completed"}

    def _cancel_hotel_order_node(self, state: HotelWorkflowState) -> dict[str, Any]:
        slots = state.get("slots", {})
        hotel_result = asyncio.run(
            self.invoke_hotel_tool(
                "cancel_hotel_order",
                {
                    "username": state["username"],
                    "city": str(slots.get("city", "")),
                    "hotel_name": str(slots.get("hotel_name", "")),
                    "room_type": str(slots.get("room_type", "")),
                    "check_in_date": str(slots.get("check_in_date", "")),
                },
            )
        )
        if hotel_result["status"] != "success":
            return {"final_text": hotel_result["message"], "final_state": "failed"}
        if "请补充" in hotel_result["message"] or "匹配到多条" in hotel_result["message"]:
            return {
                "final_text": hotel_result["message"],
                "final_state": "input_required",
                "next_pending_context": build_pending_context_payload("cancel_hotel_order", state["clean_query"], slots, []),
            }
        return {"final_text": hotel_result["message"], "final_state": "completed"}

    def _change_hotel_order_node(self, state: HotelWorkflowState) -> dict[str, Any]:
        slots = state.get("slots", {})
        hotel_result = asyncio.run(
            self.invoke_hotel_tool(
                "change_hotel_order",
                {
                    "username": state["username"],
                    "current_city": str(slots.get("city", "")),
                    "current_hotel_name": str(slots.get("hotel_name", "")),
                    "current_room_type": str(slots.get("room_type", "")),
                    "current_check_in_date": str(slots.get("check_in_date", "")),
                    "new_city": str(slots.get("new_city", "")),
                    "new_hotel_name": str(slots.get("new_hotel_name", "")),
                    "new_room_type": str(slots.get("new_room_type", "")),
                    "new_check_in_date": str(slots.get("new_check_in_date", "")),
                    "new_nights": int(slots.get("new_nights", 0)),
                },
            )
        )
        if hotel_result["status"] != "success":
            return {"final_text": hotel_result["message"], "final_state": "failed"}
        if "请补充" in hotel_result["message"] or "匹配到多条" in hotel_result["message"] or "未找到" in hotel_result["message"]:
            return {
                "final_text": hotel_result["message"],
                "final_state": "input_required",
                "next_pending_context": build_pending_context_payload("change_hotel_order", state["clean_query"], slots, []),
            }
        return {"final_text": hotel_result["message"], "final_state": "completed"}

    def _query_hotels_node(self, state: HotelWorkflowState) -> dict[str, Any]:
        budget_level = self._load_user_budget_level(state["username"])
        sql = self.build_hotel_query_sql(state.get("slots", {}), budget_level)
        hotel_result = asyncio.run(self.get_hotel_info(sql))
        response = json.loads(hotel_result) if isinstance(hotel_result, str) else hotel_result
        logger.info(f"酒店 MCP 返回: {response}")
        if response.get("status") == "success":
            lines: list[str] = [f"排序偏好：已按当前用户的预算画像（{budget_level}）做优先排序。"]
            for item in response.get("data", []):
                breakfast = "含早" if item["breakfast_included"] else "不含早"
                refundable = "可退" if item["is_refundable"] else "不可退"
                lines.append(
                    f"{item['city']} {item['name']}（{item['district']} {item['star_rating']}星）"
                    f"{item['stay_date']} {item['room_type']} {item['bed_type']}，"
                    f"{breakfast}，{refundable}，每晚 {item['price_per_night']} 元，余房 {item['remaining_rooms']} 间"
                )
            return {"final_text": "\n".join(lines), "final_state": "completed"}
        if response.get("status") == "no_data":
            return {
                "final_text": response.get("message", "未找到可预订酒店。"),
                "final_state": "input_required",
                "next_pending_context": build_pending_context_payload("query_hotels", state["clean_query"], state.get("slots", {}), []),
            }
        return {"final_text": response.get("message", "酒店查询失败，请重试。"), "final_state": "failed"}

    def handle_task(self, task):
        content = (task.message or {}).get("content", {})
        conversation = content.get("text", "") if isinstance(content, dict) else ""
        logger.info(f"酒店对话历史及用户问题: {conversation}")

        try:
            result = self.workflow.invoke({"conversation": conversation})
            final_text = result.get("final_text", "酒店流程执行失败，请重试。")
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
            logger.error(f"酒店处理失败: {exc}")
            task.status = TaskStatus(
                state=TaskState.FAILED,
                message={"role": "agent", "content": {"text": f"酒店处理失败: {exc} 请重试或补充更多条件。"}},
            )
            return task


if __name__ == "__main__":
    server = HotelAssistantServer()
    print("\n=== 服务器信息 ===")
    print(f"名称: {server.agent_card.name}")
    print(f"描述: {server.agent_card.description}")
    print("\n技能:")
    for skill in server.agent_card.skills:
        print(f"- {skill.name}: {skill.description}")
    run_server(server, host="127.0.0.1", port=5008)
