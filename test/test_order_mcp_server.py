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
SERVER_URL = "http://127.0.0.1:8003/mcp"


def _unwrap(result):
    if hasattr(result, "content") and result.content:
        texts = []
        for item in result.content:
            text = getattr(item, "text", None)
            if text is not None:
                texts.append(text)
        return "\n".join(texts)
    return str(result)


class OrderMCPIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"
        cls.process = subprocess.Popen(
            [sys.executable, "-u", "mcp_server/mcp_order_server.py"],
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

    def test_order_create_query_cancel_cycle(self):
        async def _run():
            async with streamablehttp_client(SERVER_URL) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()

                    before = _unwrap(await session.call_tool("query_user_orders", {"username": "demo_user", "departure_date": "2026-03-21"}))
                    created = _unwrap(
                        await session.call_tool(
                            "order_train",
                            {
                                "username": "demo_user",
                                "departure_date": "2026-03-21",
                                "train_number": "G5",
                                "seat_type": "二等座",
                                "number": 1,
                            },
                        )
                    )
                    try:
                        after_create = _unwrap(
                            await session.call_tool(
                                "query_user_orders",
                                {"username": "demo_user", "departure_date": "2026-03-21"},
                            )
                        )
                    finally:
                        cancelled = _unwrap(
                            await session.call_tool(
                                "cancel_ticket_order",
                                {
                                    "username": "demo_user",
                                    "departure_date": "2026-03-21",
                                    "departure_city": "北京",
                                    "arrival_city": "上海",
                                    "transport_no": "G5",
                                    "ticket_type": "二等座",
                                    "order_type": "train",
                                },
                            )
                        )
                    after_cancel = _unwrap(
                        await session.call_tool(
                            "query_user_orders",
                            {"username": "demo_user", "departure_date": "2026-03-21"},
                        )
                    )
                    return {
                        "before": before,
                        "created": created,
                        "after_create": after_create,
                        "cancelled": cancelled,
                        "after_cancel": after_cancel,
                    }

        result = asyncio.run(_run())

        self.assertIn("没有已预订订单", result["before"])
        self.assertIn("预订成功", result["created"])
        self.assertIn("订单#", result["after_create"])
        self.assertIn("退票成功", result["cancelled"])
        self.assertIn("没有已预订订单", result["after_cancel"])


if __name__ == "__main__":
    unittest.main()
