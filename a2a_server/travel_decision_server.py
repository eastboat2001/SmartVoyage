"""
travel_decision_server.py：统一交通读取与决策前置 FastAPI 服务，负责天气、时间、票务查询。
"""
import asyncio
import json
import os
import sys
from datetime import datetime

import pytz
import uvicorn
from fastapi import FastAPI
from langchain_core.prompts import ChatPromptTemplate
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import Config
from create_logger import logger
from utils.fastapi_middleware import install_common_middleware
from utils.request_context import ensure_request_id, set_request_id
from utils.resilient_llm import ResilientModelInvoker
from utils.service_protocol import (
    AgentInvokeRequest,
    AgentInvokeResponse,
    AgentMetadataResponse,
    AgentSkillDescriptor,
)
from utils.structured_outputs import TicketSqlResult, WeatherSqlResult
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

WEATHER_SCHEMA = """
CREATE TABLE IF NOT EXISTS weather_data (
id INT AUTO_INCREMENT PRIMARY KEY,
city VARCHAR(50) NOT NULL,
fx_date DATE NOT NULL,
sunrise TIME,
sunset TIME,
temp_max INT,
temp_min INT,
text_day VARCHAR(20),
text_night VARCHAR(20),
wind_dir_day VARCHAR(20),
precip DECIMAL(5,1),
humidity INT,
UNIQUE KEY unique_city_date (city, fx_date)
) ENGINE=INNODB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='天气数据表';
"""

TICKET_SCHEMA = """
CREATE TABLE train_tickets (
    id INT AUTO_INCREMENT PRIMARY KEY,
    departure_city VARCHAR(50) NOT NULL,
    arrival_city VARCHAR(50) NOT NULL,
    departure_time DATETIME NOT NULL,
    arrival_time DATETIME NOT NULL,
    train_number VARCHAR(20) NOT NULL,
    seat_type VARCHAR(20) NOT NULL,
    total_seats INT NOT NULL,
    remaining_seats INT NOT NULL,
    price DECIMAL(10, 2) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY unique_train (departure_time, train_number)
);

CREATE TABLE flight_tickets (
    id INT AUTO_INCREMENT PRIMARY KEY,
    departure_city VARCHAR(50) NOT NULL,
    arrival_city VARCHAR(50) NOT NULL,
    departure_time DATETIME NOT NULL,
    arrival_time DATETIME NOT NULL,
    flight_number VARCHAR(20) NOT NULL,
    cabin_type VARCHAR(20) NOT NULL,
    total_seats INT NOT NULL,
    remaining_seats INT NOT NULL,
    price DECIMAL(10, 2) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY unique_flight (departure_time, flight_number)
);
"""

WEATHER_SQL_PROMPT = ChatPromptTemplate.from_template(
    """
系统提示：你是一个专业的天气 SQL 生成器，需要从对话历史中提取天气查询所需字段，并基于 weather_data 表生成 SELECT 语句。
- 如果用户需要查天气，则至少需要城市和时间信息。
- 信息不足时，返回 status='input_required' 和 message。
- 信息足够时，返回 status='sql' 和 sql。
- 只返回符合结构化 schema 的字段值，不要输出 markdown 代码块，不要补充解释。

weather_data表结构：{table_schema_string}
对话历史：{conversation}
当前日期：{current_date} (Asia/Shanghai)
"""
)

TICKET_SQL_PROMPT = ChatPromptTemplate.from_template(
    """
系统提示：你是一个专业的票务 SQL 生成器，需要从对话历史中提取用户查询火车票或机票所需字段，并基于 train_tickets、flight_tickets 表生成 SELECT 语句。
- 意图有2种：train 或 flight。
- 只查询指定字段：
  - train_tickets: id, departure_city, arrival_city, departure_time, arrival_time, train_number, seat_type, price, remaining_seats
  - flight_tickets: id, departure_city, arrival_city, departure_time, arrival_time, flight_number, cabin_type, price, remaining_seats
- 如果缺少必要信息，则返回 status='input_required' 并填写 message。
- 必要信息为：
  - flight/train: 【departure_city, arrival_city, date】 或 【train_number/flight_number】
- 只返回符合结构化 schema 的字段值，不要输出 markdown 代码块，不要补充解释。

表结构：{table_schema_string}
对话历史：{conversation}
当前日期：{current_date} (Asia/Shanghai)
"""
)


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
    def current_date() -> str:
        return datetime.now(pytz.timezone("Asia/Shanghai")).strftime("%Y-%m-%d")

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
        if any(keyword in query for keyword in ("几点", "当前时间", "现在时间", "今天几号", "日期", "星期几")):
            return "time"
        if any(keyword in query for keyword in ("天气", "气温", "降水", "湿度", "风力", "风向")):
            return "weather"
        return "ticket"

    def generate_weather_sql(self, conversation: str) -> dict:
        result = self.invoker.invoke_structured(
            WEATHER_SQL_PROMPT,
            WeatherSqlResult,
            {
                "conversation": strip_travel_read_kind(conversation),
                "current_date": self.current_date(),
                "table_schema_string": WEATHER_SCHEMA,
            },
            description="TravelDecision 天气 SQL 生成",
        )
        logger.info(f"TravelDecision 天气 SQL 输出: {result.model_dump()}")
        return result.model_dump()

    def generate_ticket_sql(self, conversation: str) -> dict:
        result = self.invoker.invoke_structured(
            TICKET_SQL_PROMPT,
            TicketSqlResult,
            {
                "conversation": strip_travel_read_kind(conversation),
                "current_date": self.current_date(),
                "table_schema_string": TICKET_SCHEMA,
            },
            description="TravelDecision 票务 SQL 生成",
        )
        logger.info(f"TravelDecision 票务 SQL 输出: {result.model_dump()}")
        return result.model_dump()

    @staticmethod
    def format_weather_response(response: dict) -> tuple[str, str]:
        if response.get("status") == "success":
            data = response.get("data", [])
            response_text = "\n".join(
                [
                    f"{d['city']} {d['fx_date']}: {d['text_day']}（夜间 {d['text_night']}），温度 {d['temp_min']}-{d['temp_max']}°C，湿度 {d['humidity']}%，风向 {d['wind_dir_day']}，降水 {d['precip']}mm"
                    for d in data
                ]
            )
            return "completed", response_text
        if response.get("status") == "no_data":
            return "input_required", response.get("message", "未找到天气数据，请确认城市和日期。")
        return "failed", response.get("message", "天气查询失败，请稍后重试。")

    @staticmethod
    def format_ticket_response(response: dict, query_type: str) -> tuple[str, str]:
        if response.get("status") == "success":
            data = response.get("data", [])
            lines: list[str] = []
            for item in data:
                if query_type == "train":
                    lines.append(
                        f"{item['departure_city']} 到 {item['arrival_city']} {item['departure_time']}: 车次 {item['train_number']}，{item['seat_type']}，票价 {item['price']}元，剩余 {item['remaining_seats']} 张"
                    )
                else:
                    lines.append(
                        f"{item['departure_city']} 到 {item['arrival_city']} {item['departure_time']}: 航班 {item['flight_number']}，{item['cabin_type']}，票价 {item['price']}元，剩余 {item['remaining_seats']} 张"
                    )
            return "completed", "\n".join(lines) if lines else "无结果。如果需要其他日期，请补充。"
        if response.get("status") == "no_data":
            return "input_required", response.get("message", "未找到票务数据，请确认查询条件。")
        return "failed", response.get("message", "票务查询失败，请稍后重试。")

    @staticmethod
    def format_time_response(response: dict) -> tuple[str, str]:
        if response.get("status") != "success":
            return "failed", response.get("message", "时间查询失败，请稍后重试。")
        data = response.get("data", {})
        text = (
            f"当前时间为 {data.get('current_time', '')}，"
            f"当前日期 {data.get('current_date', '')}，"
            f"时区 {data.get('timezone', 'Asia/Shanghai')}，"
            f"星期 {data.get('weekday', '')}。"
        )
        return "completed", text

    async def invoke(self, request: AgentInvokeRequest) -> AgentInvokeResponse:
        request_id = request.request_id or ensure_request_id()
        set_request_id(request_id)
        logger.info(f"[{request_id}] TravelDecision 收到对话: {request.text}")
        kind = self.infer_kind(request.text)
        logger.info(f"[{request_id}] TravelDecision 读取类型: {kind}")

        if kind == "time":
            raw = await call_travel_read_tool("get_current_time", {"timezone_name": "Asia/Shanghai"})
            response = json.loads(raw) if isinstance(raw, str) else raw
            state, text = self.format_time_response(response)
            return AgentInvokeResponse(state=state, text=text)

        if kind == "weather":
            gen_result = self.generate_weather_sql(request.text)
            if gen_result["status"] == "input_required":
                return AgentInvokeResponse(state="input_required", text=gen_result["message"])
            raw = await call_travel_read_tool("query_weather", {"sql": gen_result["sql"]})
            response = json.loads(raw) if isinstance(raw, str) else raw
            state, text = self.format_weather_response(response)
            return AgentInvokeResponse(state=state, text=text)

        gen_result = self.generate_ticket_sql(request.text)
        if gen_result["status"] == "input_required":
            return AgentInvokeResponse(state="input_required", text=gen_result["message"])
        raw = await call_travel_read_tool("query_tickets", {"sql": gen_result["sql"]})
        response = json.loads(raw) if isinstance(raw, str) else raw
        state, text = self.format_ticket_response(response, gen_result["type"])
        return AgentInvokeResponse(state=state, text=text)


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
