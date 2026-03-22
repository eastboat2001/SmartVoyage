"""
mcp_travel_read_server.py：统一只读 MCP 服务器，负责天气、票务与当前时间查询。
"""
import json
import os
import sys
from datetime import date, datetime, timedelta
from decimal import Decimal

from mcp.server.fastmcp import FastMCP

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import Config
from create_logger import logger
from utils.db import get_db_connection
from utils.format import DateEncoder, default_encoder
from utils.time_utils import get_current_time_payload


conf = Config()


class TravelReadService:
    def __init__(self, config: Config):
        self.config = config

    def execute_select(self, sql: str, *, no_data_message: str) -> str:
        conn = None
        cursor = None
        try:
            conn = get_db_connection(self.config)
            cursor = conn.cursor(dictionary=True)
            cursor.execute(sql)
            results = cursor.fetchall()
            for result in results:
                for key, value in result.items():
                    if isinstance(value, (date, datetime, timedelta, Decimal)):
                        result[key] = default_encoder(value)
            payload = {"status": "success", "data": results} if results else {"status": "no_data", "message": no_data_message}
            return json.dumps(payload, cls=DateEncoder, ensure_ascii=False)
        except Exception as exc:
            logger.error(f"TravelReadTools 查询失败: {exc}")
            return json.dumps({"status": "error", "message": str(exc)}, ensure_ascii=False)
        finally:
            if cursor is not None:
                cursor.close()
            if conn is not None and conn.is_connected():
                conn.close()

    @staticmethod
    def current_time(timezone_name: str = "Asia/Shanghai", now_override: str = "") -> str:
        payload = {
            "status": "success",
            "data": get_current_time_payload(conf, timezone_name=timezone_name, override=now_override),
        }
        return json.dumps(payload, ensure_ascii=False)


def create_travel_read_mcp_server():
    travel_read_mcp = FastMCP(
        name="TravelReadTools",
        instructions="统一只读工具，支持天气查询、票务查询和当前时间查询。",
        log_level="ERROR",
        host="127.0.0.1",
        port=8001,
    )
    service = TravelReadService(conf)

    @travel_read_mcp.tool(
        name="query_weather",
        description="查询天气数据，输入 SQL，如 SELECT ... FROM weather_data WHERE city = '北京' AND fx_date = '2026-03-21'",
    )
    def query_weather(sql: str) -> str:
        logger.info(f"TravelReadTools 执行天气查询: {sql}")
        return service.execute_select(sql, no_data_message="未找到天气数据，请确认城市和日期。")

    @travel_read_mcp.tool(
        name="query_tickets",
        description="查询票务数据，输入 SQL，如 SELECT ... FROM train_tickets WHERE departure_city = '北京' AND arrival_city = '上海'",
    )
    def query_tickets(sql: str) -> str:
        logger.info(f"TravelReadTools 执行票务查询: {sql}")
        return service.execute_select(sql, no_data_message="未找到票务数据，请确认查询条件。")

    @travel_read_mcp.tool(
        name="get_current_time",
        description="获取当前时间，默认时区为 Asia/Shanghai。",
    )
    def get_current_time(timezone_name: str = "Asia/Shanghai", now_override: str = "") -> str:
        logger.info(f"TravelReadTools 获取当前时间: timezone={timezone_name}, now_override={now_override or '<real-time>'}")
        return service.current_time(timezone_name, now_override)

    logger.info("=== TravelRead MCP 服务器信息 ===")
    logger.info(f"名称: {travel_read_mcp.name}")
    logger.info(f"描述: {travel_read_mcp.instructions}")

    try:
        print("服务器已启动，请访问 http://127.0.0.1:8001/mcp")
        travel_read_mcp.run(transport="streamable-http")
    except Exception as exc:
        print(f"服务器启动失败: {exc}")


if __name__ == "__main__":
    create_travel_read_mcp_server()
