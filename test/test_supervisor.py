import os
import sys
import unittest
from unittest.mock import patch

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.supervisor import AgentExecutionResult, SmartVoyageSupervisor, UserPreferenceProfile
from config import Config
from utils.structured_outputs import IntentRecognitionResult


class SupervisorSmokeTest(unittest.TestCase):
    @patch.object(SmartVoyageSupervisor, "_load_user_preferences", return_value=UserPreferenceProfile(username="demo_user"))
    @patch.object(
        SmartVoyageSupervisor,
        "recognize_intent",
        return_value=IntentRecognitionResult(intents=["time"], user_queries={"time": "现在几点"}, follow_up_message=""),
    )
    @patch.object(
        SmartVoyageSupervisor,
        "_call_agent",
        return_value=AgentExecutionResult(
            agent_name="TravelReadSubagent",
            state="completed",
            text="当前时间为 2026-03-21 10:00:00，当前日期 2026-03-21，时区 Asia/Shanghai，星期 Saturday。",
            data={"kind": "time"},
            meta={"kind": "time", "metrics": {}},
        ),
    )
    def test_supervisor_routes_time_query_to_travel_read_subagent(self, mock_call_agent, _mock_recognize_intent, _mock_user_profile):
        supervisor = SmartVoyageSupervisor(Config())

        result = supervisor.process_user_input("现在几点", "", {})

        self.assertEqual(result["intents"], ["time"])
        self.assertEqual(result["routed_agents"], ["TravelReadSubagent"])
        self.assertIn("当前时间为 2026-03-21 10:00:00", result["response"])
        self.assertIn("metrics", result)
        mock_call_agent.assert_called_once()


class SupervisorOrderFollowUpRegressionTest(unittest.TestCase):
    @patch.object(SmartVoyageSupervisor, "_load_user_preferences", return_value=UserPreferenceProfile(username="demo_user"))
    @patch.object(
        SmartVoyageSupervisor,
        "recognize_intent",
        return_value=IntentRecognitionResult(
            intents=["cancel_order"],
            user_queries={"cancel_order": "帮我退票"},
            follow_up_message="请问您要退的是哪张票，可以提供订单信息吗？",
        ),
    )
    @patch.object(
        SmartVoyageSupervisor,
        "_call_agent",
        return_value=AgentExecutionResult(
            agent_name="OrderSubagent",
            state="input_required",
            text="请问您要退的是火车票还是飞机票？另外，请提供出发城市、到达城市、日期或车次/航班号。",
            pending_order_context={"action": "cancel_order", "missing_fields": ["order_type"]},
            meta={"metrics": {}},
        ),
    )
    def test_supervisor_does_not_let_intent_follow_up_swallow_order_pending_context(
        self,
        mock_call_agent,
        _mock_recognize_intent,
        _mock_user_profile,
    ):
        supervisor = SmartVoyageSupervisor(Config())

        result = supervisor.process_user_input("帮我退票", "", {})

        self.assertEqual(result["intents"], ["cancel_order"])
        self.assertEqual(result["routed_agents"], ["OrderSubagent"])
        self.assertFalse(result["pending_order_context"] == {})
        self.assertEqual(result["pending_order_context"].get("action"), "cancel_order")
        self.assertIn("火车票还是飞机票", result["response"])
        mock_call_agent.assert_called_once()


if __name__ == "__main__":
    unittest.main()
