import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.order import classify_order_action
from agents.supervisor import SmartVoyageSupervisor, UserPreferenceProfile
from agents.travel_read import TravelReadSubagent
from config import Config
from utils.structured_outputs import IntentRecognitionResult, OrderActionDecisionResult, TicketQuerySpec, TransportDecisionPlanResult


class TravelReadFormattingTest(unittest.TestCase):
    def test_ticket_plan_with_explicit_transport_no_and_no_date_clears_hallucinated_date(self):
        agent = TravelReadSubagent(Config())
        plan = agent._normalize_ticket_plan(
            "查询G5次列车余票",
            {
                "status": "ready",
                "type": "train",
                "departure_city": "",
                "arrival_city": "",
                "date_from": "2026-03-24",
                "date_to": "2026-03-24",
                "transport_no": "G5",
                "ticket_type": "",
                "limit": 10,
                "message": "",
            },
        )
        self.assertEqual(plan["transport_no"], "G5")
        self.assertEqual(plan["date_from"], "")
        self.assertEqual(plan["date_to"], "")

    def test_weather_summary_uses_deterministic_template(self):
        text = TravelReadSubagent.build_weather_summary(
            [
                {
                    "city": "上海",
                    "fx_date": "2026-03-21",
                    "text_day": "多云",
                    "temp_min": 13,
                    "temp_max": 21,
                    "humidity": 61,
                    "wind_dir_day": "东南风",
                    "precip": 0.1,
                }
            ]
        )
        self.assertIn("上海", text)
        self.assertIn("2026-03-21", text)
        self.assertIn("13-21", text)

    def test_ticket_summary_uses_deterministic_template(self):
        text = TravelReadSubagent.build_ticket_summary(
            [
                {
                    "departure_city": "北京",
                    "arrival_city": "上海",
                    "departure_time": "2026-03-21 08:00:00",
                    "transport_no": "G5",
                    "ticket_type": "二等座",
                    "price": 553,
                    "remaining_seats": 12,
                },
                {
                    "departure_city": "北京",
                    "arrival_city": "上海",
                    "departure_time": "2026-03-21 09:00:00",
                    "transport_no": "G7",
                    "ticket_type": "二等座",
                    "price": 560,
                    "remaining_seats": 8,
                },
            ],
            "train",
        )
        self.assertIn("共找到 2 条高铁票", text)
        self.assertIn("1. 2026-03-21 08:00:00 北京到上海", text)
        self.assertIn("2. 2026-03-21 09:00:00 北京到上海", text)


class OrderActionOptimizationTest(unittest.TestCase):
    @patch("agents.order.model_invoker.invoke_structured")
    def test_order_action_classify_passes_phase_name_for_task_model_routing(self, mock_invoke_structured):
        mock_invoke_structured.return_value = OrderActionDecisionResult(action="query_orders")

        action = classify_order_action(
            "User: 当前用户：demo_user\n查询我的订单",
            "当前用户：demo_user\n查询我的订单",
            "当前用户：demo_user\n查询我的订单",
            {},
        )

        self.assertEqual(action, "query_orders")
        self.assertEqual(mock_invoke_structured.call_args.kwargs.get("phase_name"), "order_action_classify")

    @patch("agents.order.model_invoker.invoke_structured", side_effect=AssertionError("should not call llm"))
    def test_explicit_order_action_bypasses_llm_classification(self, _mock_llm):
        action = classify_order_action(
            "User: 当前用户：demo_user\n[ORDER_ACTION]query_orders[/ORDER_ACTION]\n查询我的订单",
            "当前用户：demo_user\n[ORDER_ACTION]query_orders[/ORDER_ACTION]\n查询我的订单",
            "当前用户：demo_user\n查询我的订单",
            {},
        )
        self.assertEqual(action, "query_orders")


class SupervisorOptimizationTest(unittest.TestCase):
    @patch.object(SmartVoyageSupervisor, "_load_user_preferences", return_value=UserPreferenceProfile(username="demo_user"))
    @patch.object(SmartVoyageSupervisor, "_analyze_travel_query_context", side_effect=AssertionError("should not call travel_query_context"))
    @patch.object(SmartVoyageSupervisor, "_call_agent")
    def test_process_user_input_skips_travel_query_context_llm_when_intent_has_signal(
        self,
        mock_call_agent,
        _mock_query_context,
        _mock_user_profile,
    ):
        supervisor = SmartVoyageSupervisor(Config())
        supervisor.recognize_intent = MagicMock(
            return_value=IntentRecognitionResult(
                intents=["time"],
                user_queries={"time": "现在几点"},
                follow_up_message="",
                has_explicit_departure_city=False,
                needs_home_city_follow_up=False,
            )
        )
        mock_call_agent.return_value.text = "当前时间为 2026-03-21 10:00:00，当前日期 2026-03-21，时区 Asia/Shanghai，星期 Saturday。"
        mock_call_agent.return_value.state = "completed"
        mock_call_agent.return_value.data = {"kind": "time"}
        mock_call_agent.return_value.pending_order_context = None

        result = supervisor.process_user_input("现在几点", "", {})

        self.assertEqual(result["intents"], ["time"])
        self.assertEqual(result["routed_agents"], ["TravelReadSubagent"])

    @patch.object(SmartVoyageSupervisor, "_load_user_preferences", return_value=UserPreferenceProfile(username="demo_user", home_city="北京"))
    def test_train_query_with_explicit_transport_no_skips_home_city_follow_up(self, _mock_user_profile):
        supervisor = SmartVoyageSupervisor(Config())
        supervisor.recognize_intent = MagicMock(
            return_value=IntentRecognitionResult(
                intents=["train"],
                user_queries={"train": "查询G5次列车余票"},
                follow_up_message="",
                has_explicit_departure_city=False,
                needs_home_city_follow_up=True,
            )
        )
        supervisor._call_agent = MagicMock()
        supervisor._call_agent.return_value.text = "2026-03-21 07:00:00 北京到上海，车次 G5，二等座当前剩余 75 张，票价 598.0 元。"
        supervisor._call_agent.return_value.state = "completed"
        supervisor._call_agent.return_value.data = {
            "query_plan": {"transport_no": "G5"},
            "tickets": [
                {
                    "departure_time": "2026-03-21 07:00:00",
                    "departure_city": "北京",
                    "arrival_city": "上海",
                    "transport_no": "G5",
                    "ticket_type": "二等座",
                    "remaining_seats": 75,
                    "price": 598.0,
                }
            ],
        }
        supervisor._call_agent.return_value.pending_order_context = None

        result = supervisor.process_user_input("查询G5次列车余票", "", {})

        self.assertEqual(result["intents"], ["train"])
        self.assertEqual(result["routed_agents"], ["TravelReadSubagent"])
        self.assertIn("G5", result["response"])

    def test_transport_decision_plan_node_only_calls_decision_planner_once(self):
        supervisor = SmartVoyageSupervisor(Config())
        supervisor.invoker.invoke_structured = MagicMock(
            return_value=TransportDecisionPlanResult(
                transport_mode="train",
                weather_brief="上海多云，适合地面交通。",
                recommendation_reason="天气稳定且高铁更准点。",
                ticket_plan=TicketQuerySpec(
                    type="train",
                    departure_city="北京",
                    arrival_city="上海",
                    date_from="2026-03-21",
                    date_to="2026-03-21",
                    ticket_type="二等座",
                    transport_no="",
                    limit=10,
                ),
                should_order=False,
            )
        )
        state = {
            "decision_query": "根据上海天气判断从北京去上海坐高铁还是飞机更合适",
            "weather_result_text": "上海 2026-03-21 多云，13-21°C。",
            "user_profile_summary": "交通方式平衡；预算中等；偏好直达",
            "metrics": {},
        }

        result = supervisor._transport_decision_plan_node(state)

        self.assertEqual(result["plan"]["transport_mode"], "train")
        self.assertIn("ticket_plan", result["plan"])
        self.assertEqual(supervisor.invoker.invoke_structured.call_count, 1)


if __name__ == "__main__":
    unittest.main()
