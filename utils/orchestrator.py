from __future__ import annotations

import asyncio
import json
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import pytz
from langgraph.graph import END, START, StateGraph
from python_a2a import AgentNetwork, Message, MessageRole, Task, TextContent
from typing_extensions import TypedDict

from config import Config
from create_logger import logger
from main_prompts import SmartVoyagePrompts
from utils.db import get_db_connection
from utils.resilient_llm import ResilientModelInvoker
from utils.structured_outputs import (
    IntentRecognitionResult,
    PendingContextPayload,
    TravelPlanResult,
    TravelPlanWorkflowExtractionResult,
)


DEFAULT_AGENT_URLS = {
    "WeatherQueryAssistant": "http://localhost:5005",
    "TicketQueryAssistant": "http://localhost:5006",
    "TicketOrderAssistant": "http://localhost:5007",
    "HotelAssistant": "http://localhost:5008",
}


@dataclass
class AgentExecutionResult:
    agent_name: str
    state: str
    text: str
    degraded: bool = False
    no_data: bool = False
    pending_context: dict | None = None


class TravelPlanState(TypedDict):
    action: str
    slots: dict[str, Any]
    missing_slots: list[str]
    original_query: str


class TravelPlanWorkflowState(TypedDict, total=False):
    conversation_history: str
    latest_query: str
    raw_prompt: str
    intents: list[str]
    pending_context: dict[str, Any]
    user_profile_summary: str
    user_profile_home_city: str
    travel_state: TravelPlanState
    slots: dict[str, Any]
    weather_query: str
    normalized_travel_query: str
    weather_text: str
    weather_result_state: str
    weather_result_degraded: bool
    weather_result_no_data: bool
    weather_result_raw_text: str
    weather_brief: str
    trip_status_summary: str
    transport_mode: str
    recommendation_reason: str
    ticket_query: str
    hotel_query: str
    hotel_reason: str
    should_order: bool
    existing_transport_summary: str
    existing_hotel_summary: str
    ticket_result_state: str
    ticket_final_text: str
    ticket_result_raw_text: str
    hotel_result_state: str
    hotel_final_text: str
    order_result_state: str
    order_final_text: str
    routed_agents: list[str]
    final_text: str
    final_state: str
    next_pending_context: dict[str, Any]


@dataclass
class UserPreferenceProfile:
    username: str
    home_city: str = ""
    transport_preference: str = "balanced"
    seat_preference: str = ""
    cabin_preference: str = ""
    budget_level: str = "medium"
    prefer_direct: bool = True
    prefer_morning_departure: bool = False

    def summary_text(self) -> str:
        parts: list[str] = []
        if self.home_city:
            parts.append(f"常住地：{self.home_city}")
        transport_map = {"train": "偏好高铁", "flight": "偏好飞机", "balanced": "交通方式平衡"}
        parts.append(transport_map.get(self.transport_preference, "交通方式平衡"))
        if self.seat_preference:
            parts.append(f"高铁席位偏好：{self.seat_preference}")
        if self.cabin_preference:
            parts.append(f"机票舱位偏好：{self.cabin_preference}")
        budget_map = {"low": "预算敏感", "medium": "预算中等", "high": "预算充裕"}
        parts.append(budget_map.get(self.budget_level, "预算中等"))
        parts.append("偏好直达" if self.prefer_direct else "可接受中转")
        parts.append("偏好上午出发" if self.prefer_morning_departure else "出发时间无明显偏好")
        return "；".join(parts)


class SmartVoyageOrchestrator:
    def __init__(self, config: Config):
        self.config = config
        self.invoker = ResilientModelInvoker(config)
        self.agent_urls = dict(DEFAULT_AGENT_URLS)
        self.agent_network = self._build_network()
        self.current_username = config.default_username
        self.travel_plan_workflow = self._build_travel_plan_workflow()

    def _load_user_preferences(self) -> UserPreferenceProfile:
        conn = None
        cursor = None
        try:
            conn = get_db_connection(self.config)
            cursor = conn.cursor(dictionary=True)
            cursor.execute(
                """
                SELECT
                    u.username,
                    p.home_city,
                    p.transport_preference,
                    p.seat_preference,
                    p.cabin_preference,
                    p.budget_level,
                    p.prefer_direct,
                    p.prefer_morning_departure
                FROM users u
                LEFT JOIN user_preferences p ON p.user_id = u.id
                WHERE u.username = %s
                LIMIT 1
                """,
                (self.current_username,),
            )
            row = cursor.fetchone()
            if not row:
                return UserPreferenceProfile(username=self.current_username)
            return UserPreferenceProfile(
                username=row.get("username") or self.current_username,
                home_city=row.get("home_city") or "",
                transport_preference=row.get("transport_preference") or "balanced",
                seat_preference=row.get("seat_preference") or "",
                cabin_preference=row.get("cabin_preference") or "",
                budget_level=row.get("budget_level") or "medium",
                prefer_direct=bool(row.get("prefer_direct")) if row.get("prefer_direct") is not None else True,
                prefer_morning_departure=bool(row.get("prefer_morning_departure")) if row.get("prefer_morning_departure") is not None else False,
            )
        except Exception as exc:
            logger.warning(f"读取用户偏好失败: {exc}")
            return UserPreferenceProfile(username=self.current_username)
        finally:
            if cursor is not None:
                cursor.close()
            if conn is not None and conn.is_connected():
                conn.close()

    def _build_network(self) -> AgentNetwork:
        network = AgentNetwork(name="Travel Assistant Network")
        for agent_name, agent_url in self.agent_urls.items():
            network.add(agent_name, agent_url)
        return network

    def _build_travel_plan_workflow(self):
        workflow = StateGraph(TravelPlanWorkflowState)
        workflow.add_node("prepare", self._prepare_travel_plan_state)
        workflow.add_node("weather", self._travel_plan_weather_node)
        workflow.add_node("plan", self._travel_plan_plan_node)
        workflow.add_node("ticket", self._travel_plan_ticket_node)
        workflow.add_node("hotel", self._travel_plan_hotel_node)
        workflow.add_node("order", self._travel_plan_order_node)
        workflow.add_node("finalize", self._travel_plan_finalize_node)

        workflow.add_edge(START, "prepare")
        workflow.add_conditional_edges(
            "prepare",
            self._route_travel_plan_after_prepare,
            {"finish": END, "weather": "weather"},
        )
        workflow.add_edge("weather", "plan")
        workflow.add_edge("plan", "ticket")
        workflow.add_conditional_edges(
            "ticket",
            self._route_travel_plan_after_ticket,
            {"hotel": "hotel", "order": "order", "finalize": "finalize"},
        )
        workflow.add_conditional_edges(
            "hotel",
            self._route_travel_plan_after_hotel,
            {"order": "order", "finalize": "finalize"},
        )
        workflow.add_edge("order", "finalize")
        workflow.add_edge("finalize", END)
        return workflow.compile()

    def recognize_intent(self, user_input: str, conversation_history: str):
        current_date = datetime.now(pytz.timezone("Asia/Shanghai")).strftime("%Y-%m-%d")
        result = self.invoker.invoke_structured(
            SmartVoyagePrompts.intent_prompt(),
            IntentRecognitionResult,
            {
                "conversation_history": "\n".join(conversation_history.split("\n")[-6:]),
                "query": user_input,
                "current_date": current_date,
            },
            description="意图识别",
        )
        intents, user_queries, follow_up_message = self._normalize_intent_result(
            user_input,
            result.intents,
            result.user_queries,
            result.follow_up_message,
        )
        logger.info(
            f"意图识别结构化响应: intents={intents}, user_queries={user_queries}, follow_up_message={follow_up_message}"
        )
        return intents, user_queries, follow_up_message

    @staticmethod
    def _looks_like_order_query_for_hotel(text: str) -> bool:
        keywords = ("我的", "订单", "已订", "已预订", "我订了", "查询")
        return "酒店" in text and any(keyword in text for keyword in keywords)

    @staticmethod
    def _looks_like_hotel_cancel_or_change(text: str) -> bool:
        hotel_keywords = ("酒店", "房型", "入住")
        cancel_keywords = ("取消", "退掉", "退订")
        change_keywords = ("改期", "改到", "改成", "改一下", "换房型", "换到")
        return any(keyword in text for keyword in hotel_keywords) and (
            any(keyword in text for keyword in cancel_keywords)
            or any(keyword in text for keyword in change_keywords)
        )

    def _normalize_intent_result(
        self,
        user_input: str,
        intents: list[str],
        user_queries: dict[str, str],
        follow_up_message: str,
    ) -> tuple[list[str], dict[str, str], str]:
        normalized_intents = list(dict.fromkeys(intents))
        normalized_queries = dict(user_queries)

        if "my_orders" in normalized_intents and "hotel" in normalized_intents:
            hotel_query = normalized_queries.get("hotel", "")
            base_query = normalized_queries.get("my_orders", user_input)
            if self._looks_like_order_query_for_hotel(user_input) or self._looks_like_order_query_for_hotel(hotel_query):
                normalized_intents = [intent for intent in normalized_intents if intent != "hotel"]
                normalized_queries.pop("hotel", None)
                normalized_queries["my_orders"] = base_query if "酒店" in base_query else user_input

        if "cancel_order" in normalized_intents and self._looks_like_hotel_cancel_or_change(user_input):
            cancel_query = normalized_queries.pop("cancel_order", user_input)
            normalized_intents = [intent for intent in normalized_intents if intent != "cancel_order"]
            if "hotel" not in normalized_intents:
                normalized_intents.append("hotel")
            normalized_queries["hotel"] = cancel_query

        if "change_order" in normalized_intents and self._looks_like_hotel_cancel_or_change(user_input):
            change_query = normalized_queries.pop("change_order", user_input)
            normalized_intents = [intent for intent in normalized_intents if intent != "change_order"]
            if "hotel" not in normalized_intents:
                normalized_intents.append("hotel")
            normalized_queries["hotel"] = change_query

        return normalized_intents, normalized_queries, follow_up_message

    def process_user_input(
        self,
        prompt: str,
        conversation_history: str,
        pending_context: dict | None = None,
    ) -> dict:
        user_profile = self._load_user_preferences()
        precheck_follow_up = self._precheck_home_city_follow_up(prompt, user_profile, pending_context or {})
        if precheck_follow_up:
            return {
                "response": precheck_follow_up,
                "intents": [],
                "routed_agents": [],
                "pending_context": pending_context or {},
            }

        intents, user_queries, follow_up_message = self.recognize_intent(
            prompt,
            conversation_history,
        )
        pending_context = pending_context or {}
        intents, user_queries, follow_up_message, pending_context = self._merge_pending_context(
            prompt,
            conversation_history,
            intents,
            user_queries,
            follow_up_message,
            pending_context,
        )

        if "out_of_scope" in intents:
            return {
                "response": follow_up_message,
                "intents": intents,
                "routed_agents": [],
                "pending_context": {},
            }

        if follow_up_message and not any(
            intent in {"hotel", "order", "my_orders", "cancel_order", "change_order", "travel_plan"}
            for intent in intents
        ):
            return {
                "response": follow_up_message,
                "intents": intents,
                "routed_agents": [],
                "pending_context": pending_context,
            }

        home_city_follow_up = self._maybe_follow_up_with_home_city(intents, user_queries, user_profile, pending_context)
        if home_city_follow_up:
            return {
                "response": home_city_follow_up,
                "intents": intents,
                "routed_agents": [],
                "pending_context": pending_context,
            }

        if "travel_plan" in intents:
            result = self._handle_travel_plan(prompt, conversation_history, user_queries, intents, user_profile, pending_context)
            return result

        responses: list[str] = []
        routed_agents: list[str] = []
        next_pending_context: dict = pending_context if self._has_pending_domain_intent(intents) else {}
        for intent in intents:
            if intent == "attraction":
                responses.append(
                    self.invoker.invoke_text(
                        SmartVoyagePrompts.attraction_prompt(),
                        {"query": prompt},
                        description="景点推荐生成",
                    )
                )
                continue

            agent_name = self._agent_name_for_intent(intent)
            if not agent_name:
                responses.append("暂不支持此意图。")
                continue

            query_str = user_queries.get(intent, prompt)
            query_str = self._with_user_context(intent, query_str)
            if intent in {"order", "my_orders", "cancel_order", "change_order", "hotel"} and pending_context:
                query_str = self._with_pending_context(query_str, pending_context)
            result = self._call_agent(agent_name, query_str, conversation_history)
            routed_agents.append(agent_name)
            responses.append(self._finalize_agent_response(agent_name, query_str, result))
            if intent in {"order", "my_orders", "cancel_order", "change_order", "hotel"}:
                if result.state == "input_required" and result.pending_context:
                    next_pending_context = result.pending_context
                elif result.state in {"completed", "failed"}:
                    next_pending_context = {}

        return {
            "response": "\n\n".join(responses),
            "intents": intents,
            "routed_agents": routed_agents,
            "pending_context": next_pending_context,
        }

    def _handle_travel_plan(
        self,
        prompt: str,
        conversation_history: str,
        user_queries: dict[str, str],
        intents: list[str],
        user_profile: UserPreferenceProfile,
        pending_context: dict | None,
    ) -> dict:
        workflow_state = self.travel_plan_workflow.invoke(
            {
                "conversation_history": conversation_history,
                "latest_query": user_queries.get("travel_plan", prompt),
                "raw_prompt": prompt,
                "intents": intents,
                "pending_context": pending_context or {},
                "user_profile_summary": user_profile.summary_text(),
                "user_profile_home_city": user_profile.home_city,
                "routed_agents": [],
            }
        )
        return {
            "response": workflow_state.get("final_text", "暂时无法生成出行方案。"),
            "intents": intents,
            "routed_agents": workflow_state.get("routed_agents", []),
            "pending_context": workflow_state.get("next_pending_context", {}),
        }

    def _prepare_travel_plan_state(self, state: TravelPlanWorkflowState) -> dict[str, Any]:
        travel_query = state["latest_query"]
        pending_context = state.get("pending_context", {})
        travel_state = self._extract_travel_plan_state(
            query=travel_query,
            raw_prompt=state.get("raw_prompt", ""),
            conversation_history=state["conversation_history"],
            pending_context=pending_context,
        )
        next_state: dict[str, Any] = {
            "travel_state": travel_state,
            "slots": travel_state["slots"],
            "routed_agents": state.get("routed_agents", []),
        }
        if travel_state["missing_slots"]:
            next_state["final_text"] = self._travel_plan_follow_up_message(
                travel_state["missing_slots"],
                state.get("user_profile_home_city", ""),
            )
            next_state["final_state"] = "input_required"
            next_state["next_pending_context"] = self._build_travel_plan_pending_context(travel_state)
            return next_state

        next_state["weather_query"] = self._build_weather_query(travel_state["slots"])
        next_state["normalized_travel_query"] = self._build_travel_plan_query(travel_state["slots"])
        existing_orders = self._load_existing_travel_plan_orders(travel_state["slots"])
        next_state["existing_transport_summary"] = existing_orders["transport_summary"]
        next_state["existing_hotel_summary"] = existing_orders["hotel_summary"]
        next_state["final_state"] = "in_progress"
        return next_state

    def _travel_plan_weather_node(self, state: TravelPlanWorkflowState) -> dict[str, Any]:
        weather_result = self._call_agent(
            "WeatherQueryAssistant",
            state["weather_query"],
            state["conversation_history"],
        )
        weather_text = weather_result.text
        if weather_result.state == "completed":
            weather_text = self.invoker.invoke_text(
                SmartVoyagePrompts.summarize_weather_prompt(),
                {"query": state["weather_query"], "raw_response": weather_result.text},
                description="天气总结",
            )
        elif weather_result.no_data:
            weather_text = weather_result.text
        return {
            "weather_result_state": weather_result.state,
            "weather_result_degraded": weather_result.degraded,
            "weather_result_no_data": weather_result.no_data,
            "weather_result_raw_text": weather_result.text,
            "weather_text": weather_text,
            "routed_agents": [*state.get("routed_agents", []), "WeatherQueryAssistant"],
        }

    def _travel_plan_plan_node(self, state: TravelPlanWorkflowState) -> dict[str, Any]:
        plan = self.invoker.invoke_structured(
            SmartVoyagePrompts.travel_planner_prompt(),
            TravelPlanResult,
            {
                "query": state["normalized_travel_query"],
                "weather_result": state["weather_text"],
                "user_preferences": state["user_profile_summary"],
                "existing_transport_orders": state.get("existing_transport_summary", "") or "无",
                "existing_hotel_orders": state.get("existing_hotel_summary", "") or "无",
                "stay_days": int(state["slots"].get("stay_days", 1)),
                "current_date": datetime.now(pytz.timezone("Asia/Shanghai")).strftime("%Y-%m-%d"),
            },
            description="跨 Agent 出行规划",
        )
        order_intent = str(state.get("slots", {}).get("order_intent", "none") or "none")
        should_order = False
        if order_intent == "any":
            should_order = True
        elif order_intent == "train_if_suitable":
            should_order = plan.transport_mode == "train"
        elif order_intent == "flight_if_suitable":
            should_order = plan.transport_mode == "flight"
        return {
            "weather_brief": plan.weather_brief,
            "trip_status_summary": plan.trip_status_summary,
            "transport_mode": plan.transport_mode,
            "recommendation_reason": plan.recommendation_reason,
            "ticket_query": plan.ticket_query,
            "hotel_query": plan.hotel_query,
            "hotel_reason": plan.hotel_reason,
            "should_order": should_order,
        }

    def _travel_plan_ticket_node(self, state: TravelPlanWorkflowState) -> dict[str, Any]:
        if str(state.get("existing_transport_summary", "")).strip():
            return {
                "ticket_result_state": "completed",
                "ticket_final_text": f"已有关联交通订单：{state['existing_transport_summary']}",
                "ticket_result_raw_text": "",
            }
        ticket_result = self._call_agent(
            "TicketQueryAssistant",
            state["ticket_query"],
            state["conversation_history"],
        )
        return {
            "ticket_result_state": ticket_result.state,
            "ticket_final_text": self._finalize_agent_response("TicketQueryAssistant", state["ticket_query"], ticket_result),
            "ticket_result_raw_text": ticket_result.text,
            "routed_agents": [*state.get("routed_agents", []), "TicketQueryAssistant"],
        }

    def _travel_plan_hotel_node(self, state: TravelPlanWorkflowState) -> dict[str, Any]:
        if str(state.get("existing_hotel_summary", "")).strip():
            return {
                "hotel_result_state": "completed",
                "hotel_final_text": f"已有关联酒店订单：{state['existing_hotel_summary']}",
            }
        hotel_query = self._with_user_context("hotel", state["hotel_query"])
        hotel_result = self._call_agent(
            "HotelAssistant",
            hotel_query,
            state["conversation_history"],
        )
        return {
            "hotel_result_state": hotel_result.state,
            "hotel_final_text": self._finalize_agent_response("HotelAssistant", hotel_query, hotel_result),
            "routed_agents": [*state.get("routed_agents", []), "HotelAssistant"],
        }

    def _travel_plan_order_node(self, state: TravelPlanWorkflowState) -> dict[str, Any]:
        if state.get("ticket_result_state") != "completed":
            return {
                "order_result_state": "skipped",
                "order_final_text": "由于前一步没有查到可用票务数据，本次没有继续提交订票请求。",
            }
        ticket_result_text = str(state.get("ticket_result_raw_text", "") or "").strip()
        if not ticket_result_text:
            return {
                "order_result_state": "skipped",
                "order_final_text": "票务查询结果为空，本次没有继续提交订票请求。",
            }
        order_query = self._build_order_query(
            travel_query=state["normalized_travel_query"],
            transport_mode=state["transport_mode"],
            ticket_result_text=ticket_result_text,
        )
        order_query = self._with_user_context("order", order_query)
        order_result = self._call_agent(
            "TicketOrderAssistant",
            order_query,
            state["conversation_history"],
        )
        return {
            "order_result_state": order_result.state,
            "order_final_text": self._finalize_agent_response("TicketOrderAssistant", order_query, order_result),
            "routed_agents": [*state.get("routed_agents", []), "TicketOrderAssistant"],
        }

    def _travel_plan_finalize_node(self, state: TravelPlanWorkflowState) -> dict[str, Any]:
        sections = [
            f"当前行程状态：{state['trip_status_summary']}",
            f"天气判断：{state.get('weather_brief') or state.get('weather_text', '')}",
            f"出行建议：建议优先选择{self._transport_label(state['transport_mode'])}。{state['recommendation_reason']}",
            f"票务结果：{state['ticket_final_text']}",
        ]
        if state.get("hotel_query", "").strip():
            hotel_intro = (
                f"住宿建议：{state['hotel_reason']}"
                if str(state.get("hotel_reason", "")).strip()
                else "住宿建议：已结合当前行程补充酒店候选。"
            )
            sections.append(hotel_intro)
            sections.append(f"酒店结果：{state.get('hotel_final_text', '暂无酒店结果。')}")
        if state.get("should_order"):
            sections.append(f"预订结果：{state.get('order_final_text', '本次没有继续提交订票请求。')}")

        if state.get("weather_result_degraded"):
            sections.insert(0, "协作降级：天气服务暂时不可用，当前建议基于保守策略继续完成票务协作。")
        elif state.get("weather_result_no_data"):
            sections.insert(0, "天气数据提醒：当前数据库里没有命中对应日期的天气数据，以下建议基于缺失天气数据时的保守策略。")
        return {
            "final_text": "\n\n".join(sections),
            "final_state": "completed",
            "next_pending_context": {},
        }

    def _load_existing_travel_plan_orders(self, slots: dict[str, str | int | bool]) -> dict[str, str]:
        departure_city = str(slots.get("departure_city", "")).strip()
        arrival_city = str(slots.get("arrival_city", "")).strip()
        travel_date = str(slots.get("travel_date", "")).strip()
        stay_days = int(slots.get("stay_days", 1))
        if not departure_city or not arrival_city or not travel_date:
            return {"transport_summary": "", "hotel_summary": ""}
        trip_end_date = (datetime.strptime(travel_date, "%Y-%m-%d") + timedelta(days=max(stay_days, 1))).strftime("%Y-%m-%d")

        conn = None
        cursor = None
        try:
            conn = get_db_connection(self.config)
            cursor = conn.cursor(dictionary=True)
            cursor.execute(
                """
                SELECT
                    order_type,
                    departure_city,
                    arrival_city,
                    departure_time,
                    transport_no,
                    hotel_name,
                    stay_nights,
                    ticket_or_room_type,
                    quantity,
                    total_price
                FROM orders o
                JOIN users u ON u.id = o.user_id
                WHERE u.username = %s
                  AND o.status = 'booked'
                  AND (
                    (
                      o.order_type IN ('train', 'flight')
                      AND DATE(o.departure_time) = %s
                      AND o.departure_city = %s
                      AND o.arrival_city = %s
                    )
                    OR
                    (
                      o.order_type = 'hotel'
                      AND o.departure_city = %s
                      AND DATE(o.departure_time) < %s
                      AND DATE_ADD(DATE(o.departure_time), INTERVAL COALESCE(o.stay_nights, 0) DAY) > %s
                    )
                  )
                ORDER BY o.departure_time ASC, o.id ASC
                """,
                (
                    self.current_username,
                    travel_date,
                    departure_city,
                    arrival_city,
                    arrival_city,
                    trip_end_date,
                    travel_date,
                ),
            )
            rows = cursor.fetchall()
        except Exception as exc:
            logger.warning(f"读取 travel_plan 关联订单失败: {exc}")
            return {"transport_summary": "", "hotel_summary": ""}
        finally:
            if cursor is not None:
                cursor.close()
            if conn is not None and conn.is_connected():
                conn.close()

        transport_parts: list[str] = []
        hotel_parts: list[str] = []
        for row in rows or []:
            if row["order_type"] == "hotel":
                check_in_date = str(row["departure_time"])[:10]
                check_out_date = (
                    datetime.strptime(check_in_date, "%Y-%m-%d") + timedelta(days=int(row["stay_nights"] or 0))
                ).strftime("%Y-%m-%d")
                hotel_parts.append(
                    f"{check_in_date} 入住，{check_out_date} 离店，{row['departure_city']} {row['hotel_name']}，"
                    f"{row['ticket_or_room_type']} {row['quantity']}间，{row['stay_nights']}晚，总价 {row['total_price']} 元"
                )
            else:
                transport_parts.append(
                    f"{str(row['departure_time'])} {row['departure_city']}到{row['arrival_city']} "
                    f"{row['transport_no']} {row['ticket_or_room_type']} {row['quantity']}张，总价 {row['total_price']} 元"
                )
        return {
            "transport_summary": "；".join(transport_parts),
            "hotel_summary": "；".join(hotel_parts),
        }

    @staticmethod
    def _route_travel_plan_after_prepare(state: TravelPlanWorkflowState) -> str:
        return "finish" if state.get("final_state") == "input_required" else "weather"

    @staticmethod
    def _route_travel_plan_after_ticket(state: TravelPlanWorkflowState) -> str:
        if str(state.get("hotel_query", "")).strip() or str(state.get("existing_hotel_summary", "")).strip():
            return "hotel"
        if state.get("should_order"):
            return "order"
        return "finalize"

    @staticmethod
    def _route_travel_plan_after_hotel(state: TravelPlanWorkflowState) -> str:
        return "order" if state.get("should_order") else "finalize"

    def _extract_travel_plan_state(
        self,
        *,
        query: str,
        raw_prompt: str,
        conversation_history: str,
        pending_context: dict,
    ) -> TravelPlanState:
        pending_slots = pending_context.get("slots", {}) if pending_context.get("domain") == "travel_plan" else {}
        extraction = self.invoker.invoke_structured(
            SmartVoyagePrompts.travel_plan_workflow_extraction_prompt(),
            TravelPlanWorkflowExtractionResult,
            {
                "conversation_history": "\n".join(conversation_history.split("\n")[-6:]),
                "query": query,
                "raw_prompt": raw_prompt or query,
                "current_date": datetime.now(pytz.timezone("Asia/Shanghai")).strftime("%Y-%m-%d"),
                "pending_context": json.dumps(pending_context, ensure_ascii=False) if pending_context else "无",
            },
            description="travel_plan 状态抽取",
        )
        slots: dict[str, str | int | bool] = dict(pending_slots) if isinstance(pending_slots, dict) else {}

        string_fields = {
            "departure_city": extraction.departure_city,
            "arrival_city": extraction.arrival_city,
            "travel_date": extraction.travel_date,
            "travel_date_text": extraction.travel_date_text,
        }
        for key, value in string_fields.items():
            normalized = str(value or "").strip()
            if normalized:
                slots[key] = normalized

        if extraction.stay_days > 0:
            slots["stay_days"] = extraction.stay_days
        if extraction.include_hotel is not None:
            slots["include_hotel"] = extraction.include_hotel
        if str(extraction.order_intent or "").strip():
            slots["order_intent"] = str(extraction.order_intent).strip()

        if int(slots.get("stay_days", 0) or 0) <= 0:
            slots["stay_days"] = 1
        if "include_hotel" not in slots:
            slots["include_hotel"] = False
        if not str(slots.get("order_intent", "")).strip():
            slots["order_intent"] = "none"

        missing_slots = self._compute_travel_plan_missing_slots(slots)
        return {
            "action": "plan_trip",
            "slots": slots,
            "missing_slots": missing_slots,
            "original_query": raw_prompt.strip() or query,
        }

    @staticmethod
    def _compute_travel_plan_missing_slots(slots: dict[str, str | int | bool]) -> list[str]:
        missing: list[str] = []
        for field in ("departure_city", "arrival_city", "travel_date"):
            if not str(slots.get(field, "")).strip():
                missing.append(field)
        return missing

    def _travel_plan_follow_up_message(
        self,
        missing_slots: list[str],
        home_city: str,
    ) -> str:
        if missing_slots == ["departure_city"] and home_city:
            return (
                f"你这次是从{home_city}出发吗？"
                f"如果按你的常住地{home_city}出发，我可以继续给你做交通和酒店联动方案；"
                "如果不是，请直接告诉我出发城市。"
            )
        labels = {
            "departure_city": "出发城市",
            "arrival_city": "目的地城市",
            "travel_date": "出发日期",
        }
        joined = "、".join(labels[item] for item in missing_slots if item in labels) or "出行规划信息"
        return f"请补充{joined}，我再继续帮你做交通和酒店联动方案。"

    @staticmethod
    def _build_travel_plan_pending_context(state: TravelPlanState) -> dict:
        payload = PendingContextPayload(
            domain="travel_plan",
            action=state["action"],
            missing_slots=state["missing_slots"],
            slots=state["slots"],
            original_query=state["original_query"],
        )
        return payload.model_dump()

    @staticmethod
    def _build_weather_query(slots: dict[str, str | int | bool]) -> str:
        return f"查询{slots['arrival_city']}{slots['travel_date']}的天气"

    @staticmethod
    def _build_travel_plan_query(slots: dict[str, str | int | bool]) -> str:
        base = f"结合{slots['travel_date']}{slots['departure_city']}到{slots['arrival_city']}的交通方案"
        if bool(slots.get("include_hotel", False)):
            days = int(slots.get("stay_days", 1))
            return f"{base}，并结合{slots['arrival_city']}从{slots['travel_date']}开始{days}天的酒店住宿，帮我做一个出行方案"
        return f"{base}，帮我做一个出行方案"

    def _maybe_follow_up_with_home_city(
        self,
        intents: list[str],
        user_queries: dict[str, str],
        user_profile: UserPreferenceProfile,
        pending_context: dict,
    ) -> str:
        if not user_profile.home_city:
            return ""
        if pending_context.get("domain") == "travel_plan":
            return ""

        target_intent = next((intent for intent in intents if intent in {"flight", "train", "order"}), "")
        if not target_intent:
            return ""

        query = user_queries.get(target_intent, "")
        if not query:
            return ""
        if self._has_explicit_departure_city(query):
            return ""
        if not self._looks_like_ticket_or_travel_query(query):
            return ""
        return (
            f"你这次是从{user_profile.home_city}出发吗？"
            f"如果按你的常住地{user_profile.home_city}出发，我可以继续帮你查票或做出行建议。"
        )

    def _precheck_home_city_follow_up(self, prompt: str, user_profile: UserPreferenceProfile, pending_context: dict) -> str:
        if not user_profile.home_city:
            return ""
        if pending_context.get("domain") == "travel_plan":
            return ""
        normalized = prompt.strip()
        if not normalized:
            return ""
        if self._has_explicit_departure_city(normalized):
            return ""
        if not self._looks_like_ticket_or_travel_query(normalized):
            return ""
        if not self._looks_like_missing_departure_query(normalized):
            return ""
        return (
            f"你这次是从{user_profile.home_city}出发吗？"
            f"如果按你的常住地{user_profile.home_city}出发，我可以继续帮你查高铁票和机票；"
            "如果不是，请直接告诉我出发城市。"
        )

    def _finalize_agent_response(
        self,
        agent_name: str,
        query_str: str,
        result: AgentExecutionResult,
    ) -> str:
        if result.state != "completed":
            return result.text

        if agent_name == "WeatherQueryAssistant":
            return self.invoker.invoke_text(
                SmartVoyagePrompts.summarize_weather_prompt(),
                {"query": query_str, "raw_response": result.text},
                description="天气总结",
            )

        if agent_name == "TicketQueryAssistant":
            direct_response = self._build_ticket_fact_response(query_str, result.text)
            if direct_response:
                return direct_response
            return self.invoker.invoke_text(
                SmartVoyagePrompts.summarize_ticket_prompt(),
                {"query": query_str, "raw_response": result.text},
                description="票务总结",
            )

        if agent_name == "HotelAssistant":
            return result.text

        return result.text

    def _call_agent(
        self,
        agent_name: str,
        query_str: str,
        conversation_history: str,
    ) -> AgentExecutionResult:
        try:
            return self._run_sync(
                self._call_agent_async(agent_name, query_str, conversation_history)
            )
        except Exception as exc:
            logger.error(f"{agent_name} 调用失败: {exc}")
            return AgentExecutionResult(
                agent_name=agent_name,
                state="failed",
                text=self._agent_error_message(agent_name, "服务暂时不可用，请稍后重试。"),
                degraded=True,
            )

    async def _call_agent_async(
        self,
        agent_name: str,
        query_str: str,
        conversation_history: str,
    ) -> AgentExecutionResult:
        agent = self.agent_network.get_agent(agent_name)
        chat_history = "\n".join(conversation_history.split("\n")[-7:-1]) + f"\nUser: {query_str}"
        message = Message(content=TextContent(text=chat_history), role=MessageRole.USER)
        task = Task(id="task-" + str(uuid.uuid4()), message=message.to_dict())

        try:
            raw_response = await asyncio.wait_for(
                agent.send_task_async(task),
                timeout=self.config.agent_timeout_seconds,
            )
        except asyncio.TimeoutError:
            logger.warning(f"{agent_name} 调用超时，已触发降级。")
            return AgentExecutionResult(
                agent_name=agent_name,
                state="failed",
                text=self._agent_timeout_message(agent_name),
                degraded=True,
            )

        state = str(getattr(raw_response.status, "state", "")).split(".")[-1].lower()
        content: dict = {}
        if state == "completed":
            text = raw_response.artifacts[0]["parts"][0]["text"]
        else:
            message_payload = getattr(raw_response.status, "message", {}) or {}
            content = message_payload.get("content", {}) if isinstance(message_payload, dict) else {}
            text = content.get("text", "服务暂时不可用，请稍后重试。")

        return AgentExecutionResult(
            agent_name=agent_name,
            state=state,
            text=text,
            degraded=state == "failed",
            no_data="未找到" in text,
            pending_context=content.get("pending_context") if isinstance(content, dict) else None,
        )

    def _run_sync(self, coro):
        try:
            return asyncio.run(coro)
        except RuntimeError:
            loop = asyncio.new_event_loop()
            try:
                return loop.run_until_complete(coro)
            finally:
                loop.close()

    @staticmethod
    def _agent_name_for_intent(intent: str) -> str | None:
        if intent == "weather":
            return "WeatherQueryAssistant"
        if intent in {"flight", "train"}:
            return "TicketQueryAssistant"
        if intent == "hotel":
            return "HotelAssistant"
        if intent in {"order", "my_orders", "cancel_order", "change_order"}:
            return "TicketOrderAssistant"
        return None

    @staticmethod
    def _has_order_intent(intents: list[str]) -> bool:
        return any(intent in {"order", "my_orders", "cancel_order", "change_order"} for intent in intents)

    @staticmethod
    def _has_pending_domain_intent(intents: list[str]) -> bool:
        return any(intent in {"order", "my_orders", "cancel_order", "change_order", "hotel", "travel_plan"} for intent in intents)

    def _merge_pending_context(
        self,
        prompt: str,
        conversation_history: str,
        intents: list[str],
        user_queries: dict[str, str],
        follow_up_message: str,
        pending_context: dict,
    ) -> tuple[list[str], dict[str, str], str, dict]:
        if not pending_context:
            return intents, user_queries, follow_up_message, {}

        if any(intent in {"weather", "flight", "train", "attraction"} for intent in intents):
            return intents, user_queries, follow_up_message, {}

        if self._has_pending_domain_intent(intents):
            return intents, user_queries, follow_up_message, pending_context

        combined_prompt = self._pending_context_user_prompt(prompt, pending_context)
        combined_intents, combined_user_queries, combined_follow_up = self.recognize_intent(
            combined_prompt,
            conversation_history,
        )
        if self._has_pending_domain_intent(combined_intents):
            return combined_intents, combined_user_queries, combined_follow_up, pending_context
        return intents, user_queries, follow_up_message, {}

    def _with_user_context(self, intent: str, query: str) -> str:
        if intent not in {"order", "my_orders", "cancel_order", "change_order", "hotel"}:
            return query
        if "当前用户" in query:
            return query
        return f"当前用户：{self.current_username}\n{query}"

    @staticmethod
    def _with_pending_context(query: str, pending_context: dict) -> str:
        if not pending_context:
            return query
        payload = json.dumps(pending_context, ensure_ascii=False)
        return f"[PENDING_CONTEXT]{payload}[/PENDING_CONTEXT]\n{query}"

    @staticmethod
    def _pending_context_user_prompt(prompt: str, pending_context: dict) -> str:
        payload = json.dumps(pending_context, ensure_ascii=False)
        domain = pending_context.get("domain", "当前任务")
        return f"继续处理之前的{domain}操作。待补上下文：{payload}。本轮补充：{prompt}"

    def _build_ticket_fact_response(self, query_str: str, raw_text: str) -> str:
        normalized_query = query_str.replace("当前用户：", "")
        is_inventory_query = any(keyword in normalized_query for keyword in ("余票", "还有多少", "多少张", "剩余"))
        if not is_inventory_query:
            return ""

        first_line = next((line.strip() for line in raw_text.splitlines() if line.strip()), "")
        if not first_line:
            return raw_text

        train_match = re.search(
            r"(?P<departure>\S+)\s+到\s+(?P<arrival>\S+)\s+(?P<departure_time>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}): "
            r"车次\s+(?P<transport_no>\S+)，(?P<ticket_type>[^，]+)，票价\s+(?P<price>[\d.]+)元，剩余\s+(?P<remaining>\d+)\s+张",
            first_line,
        )
        flight_match = re.search(
            r"(?P<departure>\S+)\s+到\s+(?P<arrival>\S+)\s+(?P<departure_time>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}): "
            r"航班\s+(?P<transport_no>\S+)，(?P<ticket_type>[^，]+)，票价\s+(?P<price>[\d.]+)元，剩余\s+(?P<remaining>\d+)\s+张",
            first_line,
        )
        match = train_match or flight_match
        if not match:
            return raw_text

        info = match.groupdict()
        response = (
            f"{info['departure_time']} {info['departure']}到{info['arrival']} "
            f"{info['transport_no']} {info['ticket_type']}当前剩余 {info['remaining']} 张，"
            f"票价 {info['price']} 元。"
        )
        order_context = self._related_order_context(
            departure_time=info["departure_time"],
            transport_no=info["transport_no"],
            ticket_type=info["ticket_type"],
        )
        if order_context:
            response += order_context
        return response

    @staticmethod
    def _has_explicit_departure_city(query: str) -> bool:
        normalized = query.replace("当前用户：", "")
        patterns = [
            r"从[\u4e00-\u9fa5]{2,10}到[\u4e00-\u9fa5]{2,10}",
            r"[\u4e00-\u9fa5]{2,10}到[\u4e00-\u9fa5]{2,10}",
            r"从[\u4e00-\u9fa5]{2,10}去[\u4e00-\u9fa5]{2,10}",
            r"[\u4e00-\u9fa5]{2,10}飞[\u4e00-\u9fa5]{2,10}",
            r"从[\u4e00-\u9fa5]{2,10}出发",
            r"[\u4e00-\u9fa5]{2,10}出发",
        ]
        return any(re.search(pattern, normalized) for pattern in patterns)

    @staticmethod
    def _looks_like_ticket_or_travel_query(query: str) -> bool:
        keywords = (
            "票",
            "高铁",
            "火车",
            "机票",
            "航班",
            "飞机",
            "出发",
            "去",
            "坐高铁",
            "坐飞机",
        )
        return any(keyword in query for keyword in keywords)

    @staticmethod
    def _looks_like_travel_plan_query(query: str) -> bool:
        keywords = (
            "交通",
            "酒店",
            "住宿",
            "行程",
            "方案",
            "玩几天",
            "出行方案",
        )
        return any(keyword in query for keyword in keywords)

    @staticmethod
    def _looks_like_missing_departure_query(query: str) -> bool:
        normalized = query.replace("当前用户：", "")
        missing_departure_patterns = [
            r"去[\u4e00-\u9fa5]{2,10}的?(高铁票|火车票|机票|票)",
            r"到[\u4e00-\u9fa5]{2,10}的?(高铁票|火车票|机票|票)",
            r"去[\u4e00-\u9fa5]{2,10}(坐高铁|坐飞机|怎么去|的票)",
            r"到[\u4e00-\u9fa5]{2,10}(坐高铁|坐飞机|的票)",
        ]
        return any(re.search(pattern, normalized) for pattern in missing_departure_patterns)

    def _related_order_context(self, *, departure_time: str, transport_no: str, ticket_type: str) -> str:
        conn = None
        cursor = None
        try:
            conn = get_db_connection(self.config)
            cursor = conn.cursor(dictionary=True)
            cursor.execute(
                """
                SELECT quantity
                FROM orders o
                JOIN users u ON u.id = o.user_id
                WHERE u.username = %s
                  AND o.status = 'booked'
                  AND o.departure_time = %s
                  AND o.transport_no = %s
                  AND o.ticket_or_room_type = %s
                LIMIT 1
                """,
                (self.current_username, departure_time, transport_no, ticket_type),
            )
            row = cursor.fetchone()
            if not row:
                return ""
            return f" 你当前已预订该车次/航班 {row['quantity']} 张。"
        except Exception as exc:
            logger.warning(f"读取用户订单上下文失败: {exc}")
            return ""
        finally:
            if cursor is not None:
                cursor.close()
            if conn is not None and conn.is_connected():
                conn.close()

    @staticmethod
    def _transport_label(transport_mode: str) -> str:
        return "高铁/火车" if transport_mode == "train" else "飞机"

    @staticmethod
    def _build_order_query(
        *,
        travel_query: str,
        transport_mode: str,
        ticket_result_text: str,
    ) -> str:
        transport_label = "高铁票" if transport_mode == "train" else "机票"
        first_ticket_line = next(
            (line.strip() for line in ticket_result_text.splitlines() if line.strip()),
            "",
        )
        if not first_ticket_line:
            return (
                f"{travel_query}\n"
                f"请继续协助预订{transport_label}。如果当前缺少可用票务明细，请先追问，不要虚构车次或航班信息。"
            )
        return (
            f"{travel_query}\n"
            f"请基于以下已查询到的真实票务结果，选择最合适的一张{transport_label}完成预订；"
            "如果信息不足再追问，不要虚构车次或航班信息。\n"
            f"可用票务：{first_ticket_line}"
        )

    @staticmethod
    def _agent_timeout_message(agent_name: str) -> str:
        messages = {
            "WeatherQueryAssistant": "天气服务响应超时，我先不中断会话。你可以稍后重试天气查询，或继续让我直接查票。",
            "TicketQueryAssistant": "票务查询服务响应超时，暂时无法返回余票信息。你可以稍后重试，或调整日期后再查。",
            "TicketOrderAssistant": "订票服务响应超时，当前没有完成实际下单。请稍后重试，避免重复提交。",
        }
        return messages.get(agent_name, "服务响应超时，请稍后重试。")

    @staticmethod
    def _agent_error_message(agent_name: str, default_message: str) -> str:
        messages = {
            "WeatherQueryAssistant": "天气服务当前不可用，请稍后再查天气，或直接继续票务查询。",
            "TicketQueryAssistant": "票务查询服务当前不可用，请稍后重试或更换查询条件。",
            "TicketOrderAssistant": "订票服务当前不可用，当前没有完成实际下单。",
        }
        return messages.get(agent_name, default_message)
