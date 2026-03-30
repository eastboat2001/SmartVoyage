"""
功能：实现 TravelRead MCP 服务，提供时间、天气和票务只读工具。
作用：作为只读工具边界，把数据库访问、缓存和 JSON 返回从 agent 中剥离出来。
实现方式：基于 FastMCP 注册工具函数，内部接数据库和 Redis 并返回结构化结果。
"""

import hashlib
import json
import os
import sys
import time
from datetime import date, datetime, timedelta
from decimal import Decimal

from mcp.server.fastmcp import FastMCP

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.config import Config
from core.logging import logger
from infra.cache import RedisCacheClient
from infra.db import get_db_connection
from infra.json_encoder import DateEncoder, default_encoder
from core.clock import get_current_time_payload


conf = Config()


class TravelReadService:
    def __init__(self, config: Config):
        self.config = config
        self.cache = RedisCacheClient(config.cache_enabled, config.redis_url)

    @staticmethod
    def _hash_sql(sql: str) -> str:
        return hashlib.sha256(sql.encode("utf-8")).hexdigest()

    def _payload_with_cache_meta(self, payload: dict, cache_status: str) -> str:
        enriched = dict(payload)
        enriched["meta"] = {"cache_status": cache_status}
        return json.dumps(enriched, cls=DateEncoder, ensure_ascii=False)

    def execute_select(self, sql: str, *, no_data_message: str, cache_prefix: str, ttl_seconds: int) -> str:
        cache_key = f"smartvoyage:travel_read:{cache_prefix}:{self._hash_sql(sql)}"
        cached = self.cache.get_json(cache_key)
        if cached is not None:
            return self._payload_with_cache_meta(cached, "hit")

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
            self.cache.set_json(cache_key, payload, ttl_seconds)
            return self._payload_with_cache_meta(payload, "miss")
        except Exception as exc:
            logger.error(f"TravelReadTools 查询失败: {exc}")
            return json.dumps({"status": "error", "message": str(exc), "meta": {"cache_status": "bypass"}}, ensure_ascii=False)
        finally:
            if cursor is not None:
                cursor.close()
            if conn is not None and conn.is_connected():
                conn.close()

    def current_time(self, timezone_name: str = "Asia/Shanghai", now_override: str = "") -> str:
        bucket = now_override.strip() or str(int(time.time() // max(self.config.cache_time_ttl_seconds, 1)))
        cache_key = f"smartvoyage:travel_read:time:{timezone_name}:{bucket}"
        cached = self.cache.get_json(cache_key)
        if cached is not None:
            return self._payload_with_cache_meta(cached, "hit")

        payload = {
            "status": "success",
            "data": get_current_time_payload(conf, timezone_name=timezone_name, override=now_override),
        }
        self.cache.set_json(cache_key, payload, self.config.cache_time_ttl_seconds)
        return self._payload_with_cache_meta(payload, "miss")


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
        return service.execute_select(
            sql,
            no_data_message="未找到天气数据，请确认城市和日期。",
            cache_prefix="weather",
            ttl_seconds=conf.cache_weather_ttl_seconds,
        )

    @travel_read_mcp.tool(
        name="query_tickets",
        description="查询票务数据，输入 SQL，如 SELECT ... FROM train_tickets WHERE departure_city = '北京' AND arrival_city = '上海'",
    )
    def query_tickets(sql: str) -> str:
        logger.info(f"TravelReadTools 执行票务查询: {sql}")
        return service.execute_select(
            sql,
            no_data_message="未找到票务数据，请确认查询条件。",
            cache_prefix="tickets",
            ttl_seconds=conf.cache_ticket_ttl_seconds,
        )

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
