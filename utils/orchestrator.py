from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from datetime import datetime

import httpx
import pytz

from config import Config
from create_logger import logger
from main_prompts import SmartVoyagePrompts
from utils.db import get_db_connection
from utils.request_context import clear_request_id, ensure_request_id
from utils.resilient_llm import ResilientModelInvoker
from utils.service_protocol import AgentInvokeRequest, AgentInvokeResponse
from utils.structured_outputs import (
    IntentRecognitionResult,
    TransportDecisionPlanResult,
)
from utils.travel_read_context import extract_travel_read_kind, strip_travel_read_kind, with_travel_read_kind


DEFAULT_AGENT_URLS = {
    "TravelDecisionAgent": "http://localhost:5005",
    "TransportOrderAgent": "http://localhost:5007",
}

DEFAULT_AGENT_METADATA = {
    "TravelDecisionAgent": {
        "name": "TravelDecisionAgent",
        "description": "统一处理天气、时间、票务读取的交通决策前置助手",
        "url": "http://localhost:5005",
        "skills": [
            {
                "name": "travel-read",
                "description": "执行天气查询、当前时间查询、火车票与机票查询，支持自然语言输入",
            }
        ],
    },
    "TransportOrderAgent": {
        "name": "TransportOrderAgent",
        "description": "负责交通订单创建、查询、退票与改签的订单生命周期助手",
        "url": "http://localhost:5007",
        "skills": [
            {
                "name": "transport-order",
                "description": "执行交通订单创建、查询、退票和改签",
            }
        ],
    },
}


@dataclass
class AgentExecutionResult:
    agent_name: str
    state: str
    text: str
    degraded: bool = False
    no_data: bool = False
    pending_order_context: dict | None = None


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
        self.agent_metadata = dict(DEFAULT_AGENT_METADATA)
        self.current_username = config.default_username

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
        logger.info(f"意图识别结构化响应: {result.model_dump()}")
        return result.intents, result.user_queries, result.follow_up_message

    def process_user_input(
        self,
        prompt: str,
        conversation_history: str,
        pending_order_context: dict | None = None,
    ) -> dict:
        request_id = ensure_request_id()
        logger.info(f"[{request_id}] orchestrator received prompt")
        user_profile = self._load_user_preferences()
        try:
            precheck_follow_up = self._precheck_home_city_follow_up(prompt, user_profile)
            if precheck_follow_up:
                return {
                    "response": precheck_follow_up,
                    "intents": [],
                    "routed_agents": [],
                    "pending_order_context": pending_order_context or {},
                }

            intents, user_queries, follow_up_message = self.recognize_intent(
                prompt,
                conversation_history,
            )
            pending_order_context = pending_order_context or {}
            intents, user_queries, follow_up_message, pending_order_context = self._merge_pending_order_context(
                prompt,
                conversation_history,
                intents,
                user_queries,
                follow_up_message,
                pending_order_context,
            )

            if "out_of_scope" in intents:
                return {
                    "response": follow_up_message,
                    "intents": intents,
                    "routed_agents": [],
                    "pending_order_context": {},
                }

            if follow_up_message:
                return {
                    "response": follow_up_message,
                    "intents": intents,
                    "routed_agents": [],
                    "pending_order_context": pending_order_context,
                }

            home_city_follow_up = self._maybe_follow_up_with_home_city(intents, user_queries, user_profile)
            if home_city_follow_up:
                return {
                    "response": home_city_follow_up,
                    "intents": intents,
                    "routed_agents": [],
                    "pending_order_context": pending_order_context,
                }

            if "transport_decision" in intents:
                result = self._handle_transport_decision(prompt, conversation_history, user_queries, intents, user_profile)
                result["pending_order_context"] = {}
                return result

            responses: list[str] = []
            routed_agents: list[str] = []
            next_pending_order_context: dict = pending_order_context if self._has_order_intent(intents) else {}
            for intent in intents:
                agent_name = self._agent_name_for_intent(intent)
                if not agent_name:
                    responses.append("暂不支持此意图。")
                    continue

                query_str = user_queries.get(intent, prompt)
                if intent == "weather":
                    query_str = with_travel_read_kind(query_str, "weather")
                elif intent == "time":
                    query_str = with_travel_read_kind(query_str, "time")
                elif intent in {"flight", "train"}:
                    query_str = with_travel_read_kind(query_str, "ticket")
                query_str = self._with_user_context(intent, query_str)
                if intent in {"cancel_order", "change_order"} and pending_order_context:
                    query_str = self._with_pending_order_context(query_str, pending_order_context)
                result = self._call_agent(agent_name, query_str, conversation_history)
                routed_agents.append(agent_name)
                responses.append(self._finalize_agent_response(agent_name, query_str, result))
                if intent in {"order", "my_orders", "cancel_order", "change_order"}:
                    if result.state == "input_required" and result.pending_order_context:
                        next_pending_order_context = result.pending_order_context
                    elif result.state in {"completed", "failed"}:
                        next_pending_order_context = {}

            return {
                "response": "\n\n".join(responses),
                "intents": intents,
                "routed_agents": routed_agents,
                "pending_order_context": next_pending_order_context,
            }
        finally:
            clear_request_id()

    def _handle_transport_decision(
        self,
        prompt: str,
        conversation_history: str,
        user_queries: dict[str, str],
        intents: list[str],
        user_profile: UserPreferenceProfile,
    ) -> dict:
        weather_query = user_queries.get("weather", prompt)
        decision_query = user_queries.get("transport_decision", prompt)
        weather_result = self._call_agent(
            "TravelDecisionAgent",
            with_travel_read_kind(weather_query, "weather"),
            conversation_history,
        )

        weather_text = weather_result.text
        if weather_result.state == "completed":
            weather_text = self.invoker.invoke_text(
                SmartVoyagePrompts.summarize_weather_prompt(),
                {"query": weather_query, "raw_response": weather_result.text},
                description="天气总结",
            )
        elif weather_result.no_data:
            weather_text = weather_result.text

        plan = self.invoker.invoke_structured(
            SmartVoyagePrompts.transport_decision_prompt(),
            TransportDecisionPlanResult,
            {
                "query": decision_query,
                "weather_result": weather_text,
                "user_preferences": user_profile.summary_text(),
                "current_date": datetime.now(pytz.timezone("Asia/Shanghai")).strftime("%Y-%m-%d"),
            },
            description="交通决策规划",
        )

        ticket_result = self._call_agent(
            "TravelDecisionAgent",
            with_travel_read_kind(plan.ticket_query, "ticket"),
            conversation_history,
        )
        routed_agents = ["TravelDecisionAgent"]

        sections = [
            f"天气判断：{plan.weather_brief or weather_text}",
            f"出行建议：建议优先选择{self._transport_label(plan.transport_mode)}。{plan.recommendation_reason}",
            f"票务结果：{self._finalize_agent_response('TravelDecisionAgent', with_travel_read_kind(plan.ticket_query, 'ticket'), ticket_result)}",
        ]

        if plan.should_order:
            if ticket_result.state == "completed":
                order_query = self._build_order_query(
                    travel_query=decision_query,
                    transport_mode=plan.transport_mode,
                    ticket_result_text=ticket_result.text,
                )
                order_query = self._with_user_context("order", order_query)
                order_result = self._call_agent(
                    "TransportOrderAgent",
                    order_query,
                    conversation_history,
                )
                routed_agents.append("TransportOrderAgent")
                sections.append(
                    f"预订结果：{self._finalize_agent_response('TransportOrderAgent', order_query, order_result)}"
                )
            else:
                sections.append("预订结果：由于前一步没有查到可用票务数据，本次没有继续提交订票请求。")

        if weather_result.degraded:
            sections.insert(0, "协作降级：天气服务暂时不可用，当前建议基于保守策略继续完成票务协作。")
        elif weather_result.no_data:
            sections.insert(0, "天气数据提醒：当前数据库里没有命中对应日期的天气数据，以下建议基于缺失天气数据时的保守策略。")

        return {
            "response": "\n\n".join(sections),
            "intents": intents,
            "routed_agents": routed_agents,
        }

    def _maybe_follow_up_with_home_city(
        self,
        intents: list[str],
        user_queries: dict[str, str],
        user_profile: UserPreferenceProfile,
    ) -> str:
        if not user_profile.home_city:
            return ""

        target_intent = next((intent for intent in intents if intent in {"flight", "train", "order", "transport_decision"}), "")
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

    def _precheck_home_city_follow_up(self, prompt: str, user_profile: UserPreferenceProfile) -> str:
        if not user_profile.home_city:
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

        read_kind = extract_travel_read_kind(query_str)

        if agent_name == "TravelDecisionAgent" and read_kind == "weather":
            return self.invoker.invoke_text(
                SmartVoyagePrompts.summarize_weather_prompt(),
                {"query": strip_travel_read_kind(query_str), "raw_response": result.text},
                description="天气总结",
            )

        if agent_name == "TravelDecisionAgent" and read_kind == "ticket":
            clean_query = strip_travel_read_kind(query_str)
            direct_response = self._build_ticket_fact_response(clean_query, result.text)
            if direct_response:
                return direct_response
            return self.invoker.invoke_text(
                SmartVoyagePrompts.summarize_ticket_prompt(),
                {"query": clean_query, "raw_response": result.text},
                description="票务总结",
            )

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
        chat_history = "\n".join(conversation_history.split("\n")[-7:-1]) + f"\nUser: {query_str}"
        request_id = ensure_request_id()

        try:
            async with httpx.AsyncClient(timeout=self.config.agent_timeout_seconds) as client:
                response = await client.post(
                    f"{self.agent_urls[agent_name]}/invoke",
                    json=AgentInvokeRequest(
                        text=chat_history,
                        conversation_history=conversation_history,
                        request_id=request_id,
                    ).model_dump(),
                    headers={"x-request-id": request_id},
                )
                response.raise_for_status()
                payload = AgentInvokeResponse.model_validate(response.json())
        except httpx.TimeoutException:
            logger.warning(f"{agent_name} 调用超时，已触发降级。")
            return AgentExecutionResult(
                agent_name=agent_name,
                state="failed",
                text=self._agent_timeout_message(agent_name),
                degraded=True,
            )
        except Exception as exc:
            logger.error(f"{agent_name} HTTP 调用失败: {exc}")
            raise

        state = payload.state
        text = payload.text

        return AgentExecutionResult(
            agent_name=agent_name,
            state=state,
            text=text,
            degraded=state == "failed",
            no_data="未找到" in text,
            pending_order_context=payload.pending_order_context or None,
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
        if intent in {"weather", "time", "flight", "train"}:
            return "TravelDecisionAgent"
        if intent in {"order", "my_orders", "cancel_order", "change_order"}:
            return "TransportOrderAgent"
        return None

    @staticmethod
    def _has_order_intent(intents: list[str]) -> bool:
        return any(intent in {"order", "my_orders", "cancel_order", "change_order"} for intent in intents)

    def _merge_pending_order_context(
        self,
        prompt: str,
        conversation_history: str,
        intents: list[str],
        user_queries: dict[str, str],
        follow_up_message: str,
        pending_order_context: dict,
    ) -> tuple[list[str], dict[str, str], str, dict]:
        if not pending_order_context:
            return intents, user_queries, follow_up_message, {}

        if any(intent in {"weather", "time", "flight", "train", "transport_decision"} for intent in intents):
            return intents, user_queries, follow_up_message, {}

        if self._has_order_intent(intents):
            return intents, user_queries, follow_up_message, pending_order_context

        combined_prompt = self._pending_context_user_prompt(prompt, pending_order_context)
        combined_intents, combined_user_queries, combined_follow_up = self.recognize_intent(
            combined_prompt,
            conversation_history,
        )
        if self._has_order_intent(combined_intents):
            return combined_intents, combined_user_queries, combined_follow_up, pending_order_context
        return intents, user_queries, follow_up_message, {}

    def _with_user_context(self, intent: str, query: str) -> str:
        if intent not in {"order", "my_orders", "cancel_order", "change_order"}:
            return query
        if "当前用户" in query:
            return query
        return f"当前用户：{self.current_username}\n{query}"

    @staticmethod
    def _with_pending_order_context(query: str, pending_order_context: dict) -> str:
        if not pending_order_context:
            return query
        payload = json.dumps(pending_order_context, ensure_ascii=False)
        return f"[PENDING_ORDER_CONTEXT]{payload}[/PENDING_ORDER_CONTEXT]\n{query}"

    @staticmethod
    def _pending_context_user_prompt(prompt: str, pending_order_context: dict) -> str:
        payload = json.dumps(pending_order_context, ensure_ascii=False)
        return f"继续处理之前的订单操作。待补上下文：{payload}。本轮补充：{prompt}"

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
        first_ticket_line = ticket_result_text.splitlines()[0].strip()
        return (
            f"{travel_query}\n"
            f"请基于以下已查询到的真实票务结果，选择最合适的一张{transport_label}完成预订；"
            "如果信息不足再追问，不要虚构车次或航班信息。\n"
            f"可用票务：{first_ticket_line}"
        )

    @staticmethod
    def _agent_timeout_message(agent_name: str) -> str:
        messages = {
            "TravelDecisionAgent": "交通读取服务响应超时，当前无法完成天气、时间或票务查询。请稍后重试。",
            "TransportOrderAgent": "订单服务响应超时，当前没有完成实际下单或订单变更。请稍后重试，避免重复提交。",
        }
        return messages.get(agent_name, "服务响应超时，请稍后重试。")

    @staticmethod
    def _agent_error_message(agent_name: str, default_message: str) -> str:
        messages = {
            "TravelDecisionAgent": "交通读取服务当前不可用，请稍后重试天气、时间或票务查询。",
            "TransportOrderAgent": "订单服务当前不可用，当前没有完成实际下单或订单变更。",
        }
        return messages.get(agent_name, default_message)
