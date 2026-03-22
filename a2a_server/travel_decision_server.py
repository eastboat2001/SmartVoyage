"""
travel_decision_server.py：统一交通读取与决策前置 FastAPI 服务，负责天气、时间、票务查询。
"""
import json
import os
import sys

import uvicorn
from fastapi import FastAPI
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import Config
from create_logger import logger
from main_prompts import SmartVoyagePrompts
from utils.fastapi_middleware import install_common_middleware
from utils.request_context import ensure_request_id, set_request_id
from utils.resilient_llm import ResilientModelInvoker
from utils.service_protocol import (
    AgentInvokeRequest,
    AgentInvokeResponse,
    AgentMetadataResponse,
    AgentSkillDescriptor,
)
from utils.structured_outputs import TicketQueryPlanResult, TravelReadKindResult, WeatherQueryPlanResult
from utils.time_utils import get_current_date_str
from utils.travel_read_context import extract_travel_read_kind, strip_travel_read_kind


conf = Config()

SERVICE_NAME = "TravelDecisionAgent"
SERVICE_URL = "http://localhost:5005"
SERVICE_VERSION = "2.0.0"
SERVICE_DESCRIPTION = "统一处理天气、时间、票务读取的交通决策前置助手"
SERVICE_SKILLS = [
    AgentSkillDescriptor(
        name="travel-read",
        description="执行天气查询、当前时间查询、火车票与机票查询，支持自然语言输入",
        examples=[
            "今天北京天气如何",
            "现在几点",
            "查询2026-03-21北京到上海的高铁票",
            "查询2026-03-21北京到上海的机票",
        ],
    )
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
        logger.error(f"TravelReadTools 调用失败: tool={tool_name}, error={exc}")
        return json.dumps({"status": "error", "message": f"TravelReadTools 调用失败：{exc}"}, ensure_ascii=False)


class TravelDecisionService:
    def __init__(self):
        self.invoker = ResilientModelInvoker(conf)

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
            description="TravelDecision 读取类型分类",
        )
        logger.info(f"TravelDecision 读取类型结构化输出: {result.model_dump()}")
        return result.kind

    @staticmethod
    def _sql_literal(value: str) -> str:
        return value.replace("'", "''")

    def generate_weather_plan(self, conversation: str, now_override: str = "") -> dict:
        result = self.invoker.invoke_structured(
            SmartVoyagePrompts.weather_query_plan_prompt(),
            WeatherQueryPlanResult,
            {
                "conversation_history": conversation,
                "query": strip_travel_read_kind(self.latest_query(conversation)),
                "current_date": get_current_date_str(conf, override=now_override),
            },
            description="TravelDecision 天气查询计划生成",
        )
        logger.info(f"TravelDecision 天气查询计划输出: {result.model_dump()}")
        return result.model_dump()

    def generate_ticket_plan(self, conversation: str, now_override: str = "") -> dict:
        result = self.invoker.invoke_structured(
            SmartVoyagePrompts.ticket_query_plan_prompt(),
            TicketQueryPlanResult,
            {
                "conversation_history": conversation,
                "query": strip_travel_read_kind(self.latest_query(conversation)),
                "current_date": get_current_date_str(conf, override=now_override),
            },
            description="TravelDecision 票务查询计划生成",
        )
        logger.info(f"TravelDecision 票务查询计划输出: {result.model_dump()}")
        return result.model_dump()

    def compile_weather_sql(self, plan: dict) -> str:
        city = self._sql_literal(plan["city"].strip())
        date_from = self._sql_literal(plan["date_from"].strip())
        date_to = self._sql_literal((plan.get("date_to") or plan["date_from"]).strip())
        # Query plan is structured by the LLM; SQL shape stays under backend control.
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
        # Query plan controls filters; backend controls table, fields, sort and limit.
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

    async def invoke(self, request: AgentInvokeRequest) -> AgentInvokeResponse:
        request_id = request.request_id or ensure_request_id()
        set_request_id(request_id)
        logger.info(f"[{request_id}] TravelDecision 收到对话: {request.text}")
        now_override = request.now_override.strip()
        kind = self.infer_kind(request.text)
        logger.info(f"[{request_id}] TravelDecision 读取类型: {kind}")

        if kind == "time":
            raw = await call_travel_read_tool(
                "get_current_time",
                {"timezone_name": "Asia/Shanghai", "now_override": now_override},
            )
            response = json.loads(raw) if isinstance(raw, str) else raw
            state, text, data = self.format_time_response(response)
            return AgentInvokeResponse(state=state, text=text, data=data, meta={"kind": "time", "tool": "get_current_time"})

        if kind == "weather":
            plan = self.generate_weather_plan(request.text, now_override=now_override)
            if plan["status"] == "input_required":
                return AgentInvokeResponse(
                    state="input_required",
                    text=plan["message"],
                    data={"kind": "weather", "weather_days": [], "query_plan": plan},
                    meta={"kind": "weather", "query_plan": plan},
                )
            sql = self.compile_weather_sql(plan)
            logger.info(f"[{request_id}] TravelDecision 天气 SQL 编译结果: {sql}")
            raw = await call_travel_read_tool("query_weather", {"sql": sql})
            response = json.loads(raw) if isinstance(raw, str) else raw
            state, text, data = self.format_weather_response(response)
            data["query_plan"] = plan
            return AgentInvokeResponse(
                state=state,
                text=text,
                data=data,
                meta={"kind": "weather", "tool": "query_weather", "query_plan": plan, "sql": sql, "row_count": len(data.get("weather_days", []))},
            )

        plan = self.generate_ticket_plan(request.text, now_override=now_override)
        if plan["status"] == "input_required":
            return AgentInvokeResponse(
                state="input_required",
                text=plan["message"],
                data={"kind": "ticket", "query_type": plan.get("type", ""), "tickets": [], "query_plan": plan},
                meta={"kind": "ticket", "query_plan": plan},
            )
        sql = self.compile_ticket_sql(plan)
        logger.info(f"[{request_id}] TravelDecision 票务 SQL 编译结果: {sql}")
        raw = await call_travel_read_tool("query_tickets", {"sql": sql})
        response = json.loads(raw) if isinstance(raw, str) else raw
        state, text, data = self.format_ticket_response(response, plan["type"])
        data["query_plan"] = plan
        return AgentInvokeResponse(
            state=state,
            text=text,
            data=data,
            meta={"kind": "ticket", "tool": "query_tickets", "query_plan": plan, "sql": sql, "row_count": len(data.get("tickets", []))},
        )


app = FastAPI(title=SERVICE_NAME)
install_common_middleware(app)
service = TravelDecisionService()


@app.get("/health")
async def health():
    return {"status": "ok", "service": SERVICE_NAME}


@app.get("/metadata", response_model=AgentMetadataResponse)
async def metadata():
    return AgentMetadataResponse(
        name=SERVICE_NAME,
        description=SERVICE_DESCRIPTION,
        version=SERVICE_VERSION,
        url=SERVICE_URL,
        skills=SERVICE_SKILLS,
    )


@app.post("/invoke", response_model=AgentInvokeResponse)
async def invoke(request: AgentInvokeRequest):
    return await service.invoke(request)


if __name__ == "__main__":
    uvicorn.run("a2a_server.travel_decision_server:app", host="127.0.0.1", port=5005, reload=False)
