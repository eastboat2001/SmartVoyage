from __future__ import annotations

import asyncio
import json
from typing import Any

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from config import Config
from create_logger import logger
from main_prompts import SmartVoyagePrompts
from utils.agent_protocol import LocalAgentRequest, LocalAgentResponse
from utils.error_utils import format_exception_details
from utils.request_context import ensure_request_id, set_request_id
from utils.resilient_llm import ResilientModelInvoker
from utils.structured_outputs import TicketQueryPlanResult, TravelReadKindResult, WeatherQueryPlanResult
from utils.time_utils import get_current_date_str
from utils.travel_read_context import extract_travel_read_kind, strip_travel_read_kind


AGENT_NAME = "TravelReadSubagent"
AGENT_DESCRIPTION = "统一处理天气、时间、票务读取的只读专家子代理"
AGENT_SKILLS = [
    {
        "name": "travel-read",
        "description": "执行天气查询、当前时间查询、火车票与机票查询，支持自然语言输入",
        "examples": [
            "今天北京天气如何",
            "现在几点",
            "查询2026-03-21北京到上海的高铁票",
            "查询2026-03-21北京到上海的机票",
        ],
    }
]


async def call_travel_read_tool(tool_name: str, params: dict) -> str:
    try:
        async with streamablehttp_client("http://127.0.0.1:8001/mcp") as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(tool_name, params)
                result_data = json.loads(result) if isinstance(result, str) else result
                return result_data.content[0].text
    except Exception as exc:
        error_detail = format_exception_details(exc)
        logger.error(f"TravelReadTools 调用失败: tool={tool_name}, error={error_detail}")
        return json.dumps({"status": "error", "message": f"TravelReadTools 调用失败：{error_detail}"}, ensure_ascii=False)


class TravelReadSubagent:
    def __init__(self, config: Config):
        self.config = config
        self.invoker = ResilientModelInvoker(config)
        self.metadata = {
            "name": AGENT_NAME,
            "description": AGENT_DESCRIPTION,
            "skills": AGENT_SKILLS,
        }

    @staticmethod
    def latest_query(conversation: str) -> str:
        marker = "\nUser:"
        if marker in conversation:
            return conversation.rsplit(marker, 1)[-1].strip()
        if conversation.strip().startswith("User:"):
            return conversation.split("User:", 1)[-1].strip()
        return conversation.strip()

    def infer_kind(self, conversation: str) -> str:
        explicit_kind = extract_travel_read_kind(conversation)
        if explicit_kind:
            return explicit_kind
        query = self.latest_query(conversation)
        result = self.invoker.invoke_structured(
            SmartVoyagePrompts.travel_read_kind_prompt(),
            TravelReadKindResult,
            {
                "conversation_history": conversation,
                "query": query,
            },
            description="TravelRead 读取类型分类",
        )
        logger.info(f"TravelRead 读取类型结构化输出: {result.model_dump()}")
        return result.kind

    @staticmethod
    def _sql_literal(value: str) -> str:
        return value.replace("'", "''")

    def generate_weather_plan(self, conversation: str, now_override: str = "") -> dict:
        query = strip_travel_read_kind(self.latest_query(conversation))
        result = self.invoker.invoke_structured(
            SmartVoyagePrompts.weather_query_plan_prompt(conversation_history=conversation, query=query),
            WeatherQueryPlanResult,
            {
                "conversation_history": conversation,
                "query": query,
                "current_date": get_current_date_str(self.config, override=now_override),
            },
            description="TravelRead 天气查询计划生成",
        )
        logger.info(f"TravelRead 天气查询计划输出: {result.model_dump()}")
        return result.model_dump()

    def generate_ticket_plan(self, conversation: str, now_override: str = "") -> dict:
        query = strip_travel_read_kind(self.latest_query(conversation))
        result = self.invoker.invoke_structured(
            SmartVoyagePrompts.ticket_query_plan_prompt(conversation_history=conversation, query=query),
            TicketQueryPlanResult,
            {
                "conversation_history": conversation,
                "query": query,
                "current_date": get_current_date_str(self.config, override=now_override),
            },
            description="TravelRead 票务查询计划生成",
        )
        logger.info(f"TravelRead 票务查询计划输出: {result.model_dump()}")
        return result.model_dump()

    def compile_weather_sql(self, plan: dict) -> str:
        city = self._sql_literal(plan["city"].strip())
        date_from = self._sql_literal(plan["date_from"].strip())
        date_to = self._sql_literal((plan.get("date_to") or plan["date_from"]).strip())
        return (
            "SELECT city, fx_date, temp_max, temp_min, text_day, text_night, humidity, wind_dir_day, precip "
            "FROM weather_data "
            f"WHERE city = '{city}' "
            f"AND fx_date >= '{date_from}' "
            f"AND fx_date <= '{date_to}' "
            "ORDER BY fx_date ASC "
            "LIMIT 7"
        )

    def compile_ticket_sql(self, plan: dict) -> str:
        query_type = plan["type"]
        table_name = "train_tickets" if query_type == "train" else "flight_tickets"
        transport_column = "train_number" if query_type == "train" else "flight_number"
        ticket_type_column = "seat_type" if query_type == "train" else "cabin_type"
        select_columns = (
            f"id, departure_city, arrival_city, departure_time, arrival_time, {transport_column}, "
            f"{ticket_type_column}, price, remaining_seats"
        )
        filters: list[str] = []
        if plan.get("departure_city", "").strip():
            filters.append(f"departure_city = '{self._sql_literal(plan['departure_city'].strip())}'")
        if plan.get("arrival_city", "").strip():
            filters.append(f"arrival_city = '{self._sql_literal(plan['arrival_city'].strip())}'")
        if plan.get("date_from", "").strip():
            filters.append(f"DATE(departure_time) >= '{self._sql_literal(plan['date_from'].strip())}'")
        if plan.get("date_to", "").strip():
            filters.append(f"DATE(departure_time) <= '{self._sql_literal(plan['date_to'].strip())}'")
        if plan.get("transport_no", "").strip():
            filters.append(f"{transport_column} = '{self._sql_literal(plan['transport_no'].strip())}'")
        if plan.get("ticket_type", "").strip():
            filters.append(f"{ticket_type_column} = '{self._sql_literal(plan['ticket_type'].strip())}'")
        where_clause = " AND ".join(filters) if filters else "1=1"
        limit = int(plan.get("limit", 10) or 10)
        return (
            f"SELECT {select_columns} "
            f"FROM {table_name} "
            f"WHERE {where_clause} "
            "ORDER BY departure_time ASC "
            f"LIMIT {min(max(limit, 1), 20)}"
        )

    @staticmethod
    def format_weather_response(response: dict) -> tuple[str, str, dict]:
        if response.get("status") == "success":
            data = response.get("data", [])
            response_text = "\n".join(
                [
                    f"{d['city']} {d['fx_date']}: {d['text_day']}（夜间 {d['text_night']}），温度 {d['temp_min']}-{d['temp_max']}°C，湿度 {d['humidity']}%，风向 {d['wind_dir_day']}，降水 {d['precip']}mm"
                    for d in data
                ]
            )
            return "completed", response_text, {"kind": "weather", "weather_days": data}
        if response.get("status") == "no_data":
            return "input_required", response.get("message", "未找到天气数据，请确认城市和日期。"), {"kind": "weather", "weather_days": []}
        return "failed", response.get("message", "天气查询失败，请稍后重试。"), {"kind": "weather", "weather_days": []}

    @staticmethod
    def format_ticket_response(response: dict, query_type: str) -> tuple[str, str, dict]:
        if response.get("status") == "success":
            data = response.get("data", [])
            lines: list[str] = []
            tickets: list[dict] = []
            for item in data:
                if query_type == "train":
                    tickets.append(
                        {
                            "departure_city": item["departure_city"],
                            "arrival_city": item["arrival_city"],
                            "departure_time": item["departure_time"],
                            "arrival_time": item["arrival_time"],
                            "transport_no": item["train_number"],
                            "ticket_type": item["seat_type"],
                            "price": item["price"],
                            "remaining_seats": item["remaining_seats"],
                            "order_type": "train",
                        }
                    )
                    lines.append(
                        f"{item['departure_city']} 到 {item['arrival_city']} {item['departure_time']}: 车次 {item['train_number']}，{item['seat_type']}，票价 {item['price']}元，剩余 {item['remaining_seats']} 张"
                    )
                else:
                    tickets.append(
                        {
                            "departure_city": item["departure_city"],
                            "arrival_city": item["arrival_city"],
                            "departure_time": item["departure_time"],
                            "arrival_time": item["arrival_time"],
                            "transport_no": item["flight_number"],
                            "ticket_type": item["cabin_type"],
                            "price": item["price"],
                            "remaining_seats": item["remaining_seats"],
                            "order_type": "flight",
                        }
                    )
                    lines.append(
                        f"{item['departure_city']} 到 {item['arrival_city']} {item['departure_time']}: 航班 {item['flight_number']}，{item['cabin_type']}，票价 {item['price']}元，剩余 {item['remaining_seats']} 张"
                    )
            return "completed", "\n".join(lines) if lines else "无结果。如果需要其他日期，请补充。", {
                "kind": "ticket",
                "query_type": query_type,
                "tickets": tickets,
            }
        if response.get("status") == "no_data":
            return "input_required", response.get("message", "未找到票务数据，请确认查询条件。"), {
                "kind": "ticket",
                "query_type": query_type,
                "tickets": [],
            }
        return "failed", response.get("message", "票务查询失败，请稍后重试。"), {
            "kind": "ticket",
            "query_type": query_type,
            "tickets": [],
        }

    @staticmethod
    def format_time_response(response: dict) -> tuple[str, str, dict]:
        if response.get("status") != "success":
            return "failed", response.get("message", "时间查询失败，请稍后重试。"), {"kind": "time"}
        data = response.get("data", {})
        text = (
            f"当前时间为 {data.get('current_time', '')}，"
            f"当前日期 {data.get('current_date', '')}，"
            f"时区 {data.get('timezone', 'Asia/Shanghai')}，"
            f"星期 {data.get('weekday', '')}。"
        )
        return "completed", text, {"kind": "time", "current_time": data}

    async def ainvoke(self, request: LocalAgentRequest) -> LocalAgentResponse:
        request_id = request.request_id or ensure_request_id()
        set_request_id(request_id)
        logger.info(f"[{request_id}] TravelRead 收到对话: {request.text}")
        now_override = request.now_override.strip()
        kind = self.infer_kind(request.text)
        logger.info(f"[{request_id}] TravelRead 读取类型: {kind}")

        if kind == "time":
            raw = await call_travel_read_tool(
                "get_current_time",
                {"timezone_name": "Asia/Shanghai", "now_override": now_override},
            )
            response = json.loads(raw) if isinstance(raw, str) else raw
            state, text, data = self.format_time_response(response)
            return LocalAgentResponse(state=state, text=text, data=data, meta={"kind": "time", "tool": "get_current_time"})

        if kind == "weather":
            plan = self.generate_weather_plan(request.text, now_override=now_override)
            if plan["status"] == "input_required":
                return LocalAgentResponse(
                    state="input_required",
                    text=plan["message"],
                    data={"kind": "weather", "weather_days": [], "query_plan": plan},
                    meta={"kind": "weather", "query_plan": plan},
                )
            sql = self.compile_weather_sql(plan)
            logger.info(f"[{request_id}] TravelRead 天气 SQL 编译结果: {sql}")
            raw = await call_travel_read_tool("query_weather", {"sql": sql})
            response = json.loads(raw) if isinstance(raw, str) else raw
            state, text, data = self.format_weather_response(response)
            data["query_plan"] = plan
            return LocalAgentResponse(
                state=state,
                text=text,
                data=data,
                meta={"kind": "weather", "tool": "query_weather", "query_plan": plan, "sql": sql, "row_count": len(data.get("weather_days", []))},
            )

        plan = self.generate_ticket_plan(request.text, now_override=now_override)
        if plan["status"] == "input_required":
            return LocalAgentResponse(
                state="input_required",
                text=plan["message"],
                data={"kind": "ticket", "query_type": plan.get("type", ""), "tickets": [], "query_plan": plan},
                meta={"kind": "ticket", "query_plan": plan},
            )
        sql = self.compile_ticket_sql(plan)
        logger.info(f"[{request_id}] TravelRead 票务 SQL 编译结果: {sql}")
        raw = await call_travel_read_tool("query_tickets", {"sql": sql})
        response = json.loads(raw) if isinstance(raw, str) else raw
        state, text, data = self.format_ticket_response(response, plan["type"])
        data["query_plan"] = plan
        return LocalAgentResponse(
            state=state,
            text=text,
            data=data,
            meta={"kind": "ticket", "tool": "query_tickets", "query_plan": plan, "sql": sql, "row_count": len(data.get("tickets", []))},
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
