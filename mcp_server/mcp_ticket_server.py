"""
mcp_ticket_server.py：票务 MCP 服务器，提供 train_tickets 和 flight_tickets 表的 SELECT 查询接口，返回 JSON 格式结果。

核心功能：
    初始化 MySQL 数据库连接。
    执行 SELECT 查询，返回 JSON 格式结果。
    格式化日期和数值字段，确保 JSON 序列化兼容。
    通过 FastAPI 提供 HTTP 接口，响应 MCP 工具调用。
"""
import os
import sys
import json
from datetime import date, datetime, timedelta
from decimal import Decimal
from mcp.server.fastmcp import FastMCP

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import Config
from create_logger import logger
from utils.format import DateEncoder, default_encoder
from utils.db import get_db_connection

conf = Config()


# 票务服务类
class TicketService:  # 定义票务服务类，封装数据库操作逻辑
    # 定义执行SQL查询方法，输入SQL字符串，返回JSON字符串
    def execute_query(self, sql: str) -> str:
        conn = None
        cursor = None
        try:
            conn = get_db_connection(conf)
            # 执行SQL查询
            cursor = conn.cursor(dictionary=True)
            # 执行查询
            cursor.execute(sql)
            # 获取查询结果
            results = cursor.fetchall()
            # 格式化结果
            for result in results:  # 遍历每个结果字典
                for key, value in result.items():
                    if isinstance(value, (date, datetime, timedelta, Decimal)):  # 检查值是否为特殊类型
                        result[key] = default_encoder(value)  # 使用自定义编码器格式化该值
            # 序列化为JSON，如果有结果返回success，否则no_data；使用DateEncoder，非ASCII不转义
            return json.dumps({"status": "success", "data": results} if results else {"status": "no_data",
                                                                                      "message": "未找到票务数据，请确认查询条件。"},
                              cls=DateEncoder, ensure_ascii=False)
        except Exception as e:
            logger.error(f"票务查询错误: {str(e)}")
            # 返回错误JSON响应
            return json.dumps({"status": "error", "message": str(e)}, ensure_ascii=False)
        finally:
            if cursor is not None:
                cursor.close()
            if conn is not None and conn.is_connected():
                conn.close()

# 创建票务MCP服务器
def create_ticket_mcp_server():
    # 创建FastMCP实例
    ticket_mcp = FastMCP(name="TicketTools",
                         instructions="票务查询工具，基于 train_tickets, flight_tickets 表。只支持查询。",
                         log_level="ERROR",
                         host="127.0.0.1", port=8001)

    # 实例化票务服务对象
    service = TicketService()

    @ticket_mcp.tool(
        name="query_tickets",
        description="查询票务数据，输入 SQL，如 'SELECT * FROM train_tickets WHERE departure_city = \"北京\" AND arrival_city = \"上海\"'"
    )
    def query_tickets(sql: str) -> str:
        logger.info(f"执行票务查询: {sql}")
        return service.execute_query(sql)

    # 打印服务器信息
    logger.info("=== 票务MCP服务器信息 ===")
    logger.info(f"名称: {ticket_mcp.name}")
    logger.info(f"描述: {ticket_mcp.instructions}")

    # 运行服务器
    try:
        print("服务器已启动，请访问 http://127.0.0.1:8001/mcp")
        ticket_mcp.run(transport="streamable-http")  # 使用 streamable-http 传输方式
    except Exception as e:
        print(f"服务器启动失败: {e}")

if __name__ == "__main__":
    # service = TicketService()
    # sql = "SELECT * FROM flight_tickets WHERE departure_city = '上海' AND arrival_city = '北京' AND DATE(departure_time) = '2025-12-17' AND cabin_type = '经济舱'"
    # print(service.execute_query(sql))
    create_ticket_mcp_server()
