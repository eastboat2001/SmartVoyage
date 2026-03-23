import asyncio
import json
import os
import subprocess
import sys
import time
import unittest
from pathlib import Path

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client


ROOT = Path(__file__).resolve().parents[1]
SERVER_URL = "http://127.0.0.1:8001/mcp"


def _unwrap(result):
    if hasattr(result, "content") and result.content:
        texts = []
        for item in result.content:
            text = getattr(item, "text", None)
            if text is not None:
                texts.append(text)
        if len(texts) == 1:
            try:
                parsed = json.loads(texts[0])
                if isinstance(parsed, dict) and isinstance(parsed.get("result"), str):
                    try:
                        parsed["result"] = json.loads(parsed["result"])
                    except Exception:
                        pass
                return parsed
            except Exception:
                return texts[0]
        return texts
    return str(result)


class TravelReadMCPIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"
        cls.process = subprocess.Popen(
            [sys.executable, "-u", "mcp_server/mcp_travel_read_server.py"],
            cwd=ROOT,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        time.sleep(3)

    @classmethod
    def tearDownClass(cls):
        if getattr(cls, "process", None) and cls.process.poll() is None:
            cls.process.terminate()
            try:
                cls.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                cls.process.kill()
                cls.process.wait(timeout=5)

    def test_travel_read_tools_return_expected_data(self):
        async def _run():
            async with streamablehttp_client(SERVER_URL) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()

                    time_result = _unwrap(
                        await session.call_tool(
                            "get_current_time",
                            {
                                "timezone_name": "Asia/Shanghai",
                                "now_override": "2026-03-21T09:00:00+08:00",
                            },
                        )
                    )
                    weather_result = _unwrap(
                        await session.call_tool(
                            "query_weather",
                            {
                                "sql": (
                                    "SELECT city, fx_date, temp_max, temp_min, text_day, text_night, "
                                    "humidity, wind_dir_day, precip "
                                    "FROM weather_data WHERE city = '北京' AND fx_date = '2026-03-21'"
                                )
                            },
                        )
                    )
                    ticket_result = _unwrap(
                        await session.call_tool(
                            "query_tickets",
                            {
                                "sql": (
                                    "SELECT departure_city, arrival_city, departure_time, train_number, seat_type, "
                                    "price, remaining_seats "
                                    "FROM train_tickets WHERE departure_city = '北京' AND arrival_city = '上海' "
                                    "AND DATE(departure_time) = '2026-03-21' AND seat_type = '二等座'"
                                )
                            },
                        )
                    )
                    return time_result, weather_result, ticket_result

        time_result, weather_result, ticket_result = asyncio.run(_run())

        self.assertEqual(time_result["status"], "success")
        self.assertEqual(time_result["data"]["current_time"], "2026-03-21 09:00:00")
        self.assertEqual(weather_result["status"], "success")
        self.assertEqual(weather_result["data"][0]["city"], "北京")
        self.assertEqual(ticket_result["status"], "success")
        self.assertEqual(ticket_result["data"][0]["train_number"], "G5")


if __name__ == "__main__":
    unittest.main()
