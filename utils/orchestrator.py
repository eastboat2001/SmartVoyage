from __future__ import annotations

import asyncio
import json
import re
import uuid
from dataclasses import dataclass
from datetime import datetime

import pytz
from python_a2a import AgentNetwork, Message, MessageRole, Task, TextContent

from config import Config
from create_logger import logger
from main_prompts import SmartVoyagePrompts
from utils.db import get_db_connection
from utils.resilient_llm import ResilientModelInvoker
from utils.structured_outputs import (
    IntentRecognitionResult,
    TravelPlanResult,
)


DEFAULT_AGENT_URLS = {
    "WeatherQueryAssistant": "http://localhost:5005",
    "TicketQueryAssistant": "http://localhost:5006",
    "TicketOrderAssistant": "http://localhost:5007",
}


@dataclass
class AgentExecutionResult:
    agent_name: str
    state: str
    text: str
    degraded: bool = False
    no_data: bool = False
    pending_order_context: dict | None = None


class SmartVoyageOrchestrator:
    def __init__(self, config: Config):
        self.config = config
        self.invoker = ResilientModelInvoker(config)
        self.agent_urls = dict(DEFAULT_AGENT_URLS)
        self.agent_network = self._build_network()
        self.current_username = config.default_username

    def _build_network(self) -> AgentNetwork:
        network = AgentNetwork(name="Travel Assistant Network")
        for agent_name, agent_url in self.agent_urls.items():
            network.add(agent_name, agent_url)
        return network

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

        if "travel_plan" in intents:
            result = self._handle_travel_plan(prompt, conversation_history, user_queries, intents)
            result["pending_order_context"] = {}
            return result

        responses: list[str] = []
        routed_agents: list[str] = []
        next_pending_order_context: dict = pending_order_context if self._has_order_intent(intents) else {}
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

    def _handle_travel_plan(
        self,
        prompt: str,
        conversation_history: str,
        user_queries: dict[str, str],
        intents: list[str],
    ) -> dict:
        weather_query = user_queries.get("weather", prompt)
        travel_query = user_queries.get("travel_plan", prompt)
        weather_result = self._call_agent(
            "WeatherQueryAssistant",
            weather_query,
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
            SmartVoyagePrompts.travel_planner_prompt(),
            TravelPlanResult,
            {
                "query": travel_query,
                "weather_result": weather_text,
                "current_date": datetime.now(pytz.timezone("Asia/Shanghai")).strftime("%Y-%m-%d"),
            },
            description="跨 Agent 出行规划",
        )

        ticket_result = self._call_agent(
            "TicketQueryAssistant",
            plan.ticket_query,
            conversation_history,
        )
        routed_agents = ["WeatherQueryAssistant", "TicketQueryAssistant"]

        sections = [
            f"天气判断：{plan.weather_brief or weather_text}",
            f"出行建议：建议优先选择{self._transport_label(plan.transport_mode)}。{plan.recommendation_reason}",
            f"票务结果：{self._finalize_agent_response('TicketQueryAssistant', plan.ticket_query, ticket_result)}",
        ]

        if plan.should_order:
            if ticket_result.state == "completed":
                order_query = self._build_order_query(
                    travel_query=travel_query,
                    transport_mode=plan.transport_mode,
                    ticket_result_text=ticket_result.text,
                )
                order_query = self._with_user_context("order", order_query)
                order_result = self._call_agent(
                    "TicketOrderAssistant",
                    order_query,
                    conversation_history,
                )
                routed_agents.append("TicketOrderAssistant")
                sections.append(
                    f"预订结果：{self._finalize_agent_response('TicketOrderAssistant', order_query, order_result)}"
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
            pending_order_context=content.get("pending_order_context") if isinstance(content, dict) else None,
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
        if intent in {"order", "my_orders", "cancel_order", "change_order"}:
            return "TicketOrderAssistant"
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

        if any(intent in {"weather", "flight", "train", "travel_plan", "attraction"} for intent in intents):
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
