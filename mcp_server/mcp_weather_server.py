"""
mcp_weather_server天气 MCP 服务器，提供 weather_data 表的 SELECT 查询接口，返回 JSON 格式结果。

核心功能：
    初始化 MySQL 数据库连接。
    执行 SELECT 查询，返回 JSON 格式结果。
    格式化日期和数值字段，确保 JSON 序列化兼容。
    通过 FastAPI 提供 HTTP 接口，响应 MCP 工具调用。
"""
import os
import sys
import mysql.connector
import json
from datetime import date, datetime, timedelta
from decimal import Decimal

from mcp.server import FastMCP

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import Config
from create_logger import logger
from utils.format import DateEncoder, default_encoder

conf = Config()

# 天气服务类
class WeatherService:  # 定义天气服务类，封装数据库操作逻辑
    def __init__(self):
        # 连接数据库
        self.conn = mysql.connector.connect(
            host=conf.host,
            user=conf.user,
            password=conf.password,
            database=conf.database
        )

    # 定义执行SQL查询方法，输入SQL字符串，返回JSON字符串
    def execute_query(self, sql: str) -> str:
        try:
            logger.info(f"执行SQL查询: {sql}")
            cursor = self.conn.cursor(dictionary=True)
            cursor.execute(sql)
            results = cursor.fetchall()
            cursor.close()
            # 格式化结果
            for result in results:  # 遍历每个结果字典
                for key, value in result.items():
                    if isinstance(value, (date, datetime, timedelta, Decimal)):  # 检查值是否为特殊类型
                        result[key] = default_encoder(value)  # 使用自定义编码器格式化该值
            # 序列化为JSON，如果有结果返回success，否则no_data；使用DateEncoder，非ASCII不转义
            return json.dumps({"status": "success", "data": results} if results else {"status": "no_data", "message": "未找到天气数据，请确认城市和日期。"}, cls=DateEncoder, ensure_ascii=False)
        except Exception as e:
            logger.error(f"天气查询错误: {str(e)}")
            # 返回错误JSON响应
            return json.dumps({"status": "error", "message": str(e)}, ensure_ascii=False)

# 创建天气MCP服务器
def create_weather_mcp_server():
    # 创建FastMCP实例
    weather_mcp = FastMCP(name="WeatherTools",
                         instructions="天气查询工具，基于 weather_data 表。",
                         log_level="ERROR",
                         host="127.0.0.1", port=8002)

    # 实例化天气服务对象
    service = WeatherService()

    @weather_mcp.tool(
        name="query_weather",
        description="查询天气数据，输入 SQL，如 'SELECT * FROM weather_data WHERE city = \"北京\" AND fx_date = \"2025-07-30\"'"
    )
    def query_weather(sql: str) -> str:
        logger.info(f"执行天气查询: {sql}")
        return service.execute_query(sql)

    # 打印服务器信息
    logger.info("=== 天气MCP服务器信息 ===")
    logger.info(f"名称: {weather_mcp.name}")
    logger.info(f"描述: {weather_mcp.instructions}")

    # 运行服务器
    try:
        print("服务器已启动，请访问 http://127.0.0.1:8002/mcp")
        weather_mcp.run(transport="streamable-http")  # 使用 streamable-http 传输方式
    except Exception as e:
        print(f"服务器启动失败: {e}")

if __name__ == "__main__":
    service = WeatherService()
    sql = "SELECT city, fx_date, temp_max, temp_min, text_day, text_night, humidity, wind_dir_day, precip FROM weather_data WHERE city = '上海' AND fx_date BETWEEN '2025-12-23' AND '2025-12-27' ORDER BY fx_date"
    print(service.execute_query(sql))
    create_weather_mcp_server()
