"""
mcp_order_server.py：订票 MCP 服务器，通过调用API完成火车票、飞机票和演唱会票的预定。

核心功能：
    火车票预定、飞机票预定、演出票预定。
    通过 FastAPI 提供 HTTP 接口，响应 MCP 工具调用。
"""
import os
import sys

from mcp.server.fastmcp import FastMCP

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import Config
from create_logger import logger

conf = Config()

# 创建FastMCP实例
order_mcp = FastMCP(name="OrderTools",
                    instructions="票务预定工具，通过调用API完成火车票、飞机票和演唱会票的预定。",
                    log_level="ERROR",
                    host="127.0.0.1", port=8003)


@order_mcp.tool(
    name="order_train",
    description="根据时间、车次、座位类型、数量预定火车票"
)
def order_train(departure_date: str, train_number: str, seat_type: str, number: int) -> str:
    '''
    Args:
        departure_date (str): 出发日期，如 '2025-10-30'
        train_number (str): 火车车次，如 'G346'
        seat_type (str): 座位类型，如 '二等座'
        number (int): 订购张数
    '''
    logger.info(f"正在订购火车票: {departure_date}, {train_number}, {seat_type}, {number}")
    logger.info(f"恭喜，火车票预定成功！")
    return "恭喜，火车票预定成功！"

@order_mcp.tool(
    name="order_flight",
    description="根据时间、班次、座位类型、数量预定飞机票"
)
def order_flight(departure_date: str, flight_number: str, seat_type: str, number: int) -> str:
    '''
    Args:
        departure_date (str): 出发日期，如 '2025-10-30'
        flight_number (str): 飞机班次，如 'CA6557'
        seat_type (str): 座位类型，如 '经济舱'
        number (int): 订购张数
    '''
    logger.info(f"正在订购飞机票: {departure_date}, {flight_number}, {seat_type}, {number}")
    logger.info(f"恭喜，飞机票预定成功！")
    return "恭喜，飞机票预定成功！"


@order_mcp.tool(
    name="order_concert",
    description="根据时间、明星、场地、座位类型、数量预定演出票"
)
def order_concert(start_date: str, aritist: str, venue: str, seat_type: str, number: int) -> str:
    '''
    Args:
        start_date (str): 开始日期，如 '2025-10-30'
        aritist (str): 明星，如 '刀郎'
        venue (str): 场地，如 '上海体育馆'
        seat_type (str): 座位类型，如 '看台'
        number (int): 订购张数
    '''
    logger.info(f"正在订购演出票: {start_date}, {aritist}, {venue}, {seat_type}, {number}")
    logger.info(f"恭喜，演出票预定成功！")
    return "恭喜，演出票预定成功！"


# 创建票务预定MCP服务器
def create_order_mcp_server():
    # 打印服务器信息
    logger.info("=== 票务预定MCP服务器信息 ===")
    logger.info(f"名称: {order_mcp.name}")
    logger.info(f"描述: {order_mcp.instructions}")

    # 运行服务器
    try:
        print("服务器已启动，请访问 http://127.0.0.1:8003/mcp")
        order_mcp.run(transport="streamable-http")  # 使用 streamable-http 传输方式
    except Exception as e:
        print(f"服务器启动失败: {e}")


if __name__ == "__main__":
    # 调用创建服务器函数
    create_order_mcp_server()
