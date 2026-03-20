from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from datetime import datetime

import pytz
from python_a2a import AgentNetwork, Message, MessageRole, Task, TextContent

from config import Config
from create_logger import logger
from main_prompts import SmartVoyagePrompts
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


class SmartVoyageOrchestrator:
    def __init__(self, config: Config):
        self.config = config
        self.invoker = ResilientModelInvoker(config)
        self.agent_urls = dict(DEFAULT_AGENT_URLS)
        self.agent_network = self._build_network()

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

    def process_user_input(self, prompt: str, conversation_history: str) -> dict:
        intents, user_queries, follow_up_message = self.recognize_intent(
            prompt,
            conversation_history,
        )

        if "out_of_scope" in intents:
            return {"response": follow_up_message, "intents": intents, "routed_agents": []}

        if follow_up_message:
            return {"response": follow_up_message, "intents": intents, "routed_agents": []}

        if "travel_plan" in intents:
            return self._handle_travel_plan(prompt, conversation_history, user_queries, intents)

        responses: list[str] = []
        routed_agents: list[str] = []
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
            result = self._call_agent(agent_name, query_str, conversation_history)
            routed_agents.append(agent_name)
            responses.append(self._finalize_agent_response(agent_name, query_str, result))

        return {
            "response": "\n\n".join(responses),
            "intents": intents,
            "routed_agents": routed_agents,
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
        if intent in {"flight", "train", "concert"}:
            return "TicketQueryAssistant"
        if intent == "order":
            return "TicketOrderAssistant"
        return None

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
