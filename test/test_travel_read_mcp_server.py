import asyncio
import json
import os
import sys

from langchain_mcp_adapters.tools import load_mcp_tools
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


server_url = "http://127.0.0.1:8001/mcp"


async def test_travel_read_mcp():
    try:
        async with streamablehttp_client(server_url) as (read, write, _):
            async with ClientSession(read, write) as session:
                try:
                    await session.initialize()
                    print("会话初始化成功，可以开始调用工具。")
                    tools = await load_mcp_tools(session)
                    print(f"tools-->{tools}")

                    weather_sql = "SELECT city, fx_date, temp_max, temp_min, text_day, text_night, humidity, wind_dir_day, precip FROM weather_data WHERE city = '北京' AND fx_date = '2026-03-21'"
                    weather_result = await session.call_tool("query_weather", {"sql": weather_sql})
                    weather_data = json.loads(weather_result) if isinstance(weather_result, str) else weather_result
                    print(f"天气查询结果：{weather_data}")

                    ticket_sql = "SELECT departure_city, arrival_city, departure_time, train_number, seat_type, price, remaining_seats FROM train_tickets WHERE departure_city = '北京' AND arrival_city = '上海' AND DATE(departure_time) = '2026-03-21' AND seat_type = '二等座'"
                    ticket_result = await session.call_tool("query_tickets", {"sql": ticket_sql})
                    ticket_data = json.loads(ticket_result) if isinstance(ticket_result, str) else ticket_result
                    print(f"票务查询结果：{ticket_data}")

                    time_result = await session.call_tool("get_current_time", {"timezone_name": "Asia/Shanghai"})
                    time_data = json.loads(time_result) if isinstance(time_result, str) else time_result
                    print(f"当前时间结果：{time_data}")
                except Exception as exc:
                    print(f"TravelRead MCP 测试出错：{exc}")
    except Exception as exc:
        print(f"连接或会话初始化时发生错误: {exc}")
        print("请确认服务端脚本已启动并运行在 http://127.0.0.1:8001/mcp")


if __name__ == "__main__":
    asyncio.run(test_travel_read_mcp())
