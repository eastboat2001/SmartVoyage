"""
功能：验证 transport_decision 链路的服务侧行为。
作用：确保复合查询与决策流程在服务协议层保持稳定。
实现方式：通过异步 mock 和请求对象断言 agent 服务响应。
"""

import json
import os
import sys
import unittest
from unittest.mock import AsyncMock, patch

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.travel_read import TravelReadSubagent
from core.config import Config
from contracts.agent_protocol import LocalAgentRequest


class TravelReadSubagentSmokeTest(unittest.TestCase):
    @patch("agents.travel_read.call_travel_read_tool", new_callable=AsyncMock)
    @patch.object(TravelReadSubagent, "infer_kind", return_value="time")
    def test_time_query_returns_completed_response(self, _mock_infer_kind, mock_call_tool):
        mock_call_tool.return_value = json.dumps(
            {
                "status": "success",
                "data": {
                    "current_time": "2026-03-21 10:00:00",
                    "current_date": "2026-03-21",
                    "timezone": "Asia/Shanghai",
                    "weekday": "Saturday",
                },
            }
        )

        agent = TravelReadSubagent(Config())
        response = agent.invoke(LocalAgentRequest(text="现在几点"))

        self.assertEqual(response.state, "completed")
        self.assertIn("当前时间为 2026-03-21 10:00:00", response.text)
        self.assertEqual(response.meta["kind"], "time")
        self.assertEqual(response.meta["tool"], "get_current_time")

    @patch.object(TravelReadSubagent, "infer_kind", return_value="weather")
    @patch.object(
        TravelReadSubagent,
        "generate_weather_plan",
        return_value={"status": "input_required", "message": "请补充要查询的城市和日期。"},
    )
    def test_weather_query_returns_input_required_when_plan_is_incomplete(self, _mock_plan, _mock_infer_kind):
        agent = TravelReadSubagent(Config())
        response = agent.invoke(LocalAgentRequest(text="帮我查天气"))

        self.assertEqual(response.state, "input_required")
        self.assertEqual(response.text, "请补充要查询的城市和日期。")
        self.assertEqual(response.data["kind"], "weather")


if __name__ == "__main__":
    unittest.main()
