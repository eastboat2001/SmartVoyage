"""
功能：验证 Supervisor 的辅助逻辑与 transport_decision 收尾分支。
作用：确保上下文拼装、pending context 合并和复合流程收尾稳定。
实现方式：通过 unittest + mock 聚焦确定性辅助逻辑。
"""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.supervisor import SmartVoyageSupervisor, UserPreferenceProfile
from core.config import Config
from contracts.structured_outputs import IntentRecognitionResult


class SupervisorHelperTest(unittest.TestCase):
    @patch.object(
        SmartVoyageSupervisor,
        "_load_user_preferences",
        return_value=UserPreferenceProfile(username="demo_user"),
    )
    def test_with_user_context_adds_action_tag_and_username(self, _mock_user_profile):
        supervisor = SmartVoyageSupervisor(Config())

        query = supervisor._with_user_context("order", "帮我订票")

        self.assertIn("当前用户：demo_user", query)
        self.assertIn("[ORDER_ACTION]create_order[/ORDER_ACTION]", query)

    @patch.object(
        SmartVoyageSupervisor,
        "_load_user_preferences",
        return_value=UserPreferenceProfile(username="demo_user"),
    )
    def test_with_user_context_respects_existing_username(self, _mock_user_profile):
        supervisor = SmartVoyageSupervisor(Config())

        query = supervisor._with_user_context(
            "my_orders", "当前用户：alice\n查询我的订单"
        )

        self.assertEqual(query.count("当前用户："), 1)
        self.assertIn("alice", query)
        self.assertIn("[ORDER_ACTION]query_orders[/ORDER_ACTION]", query)

    @patch.object(
        SmartVoyageSupervisor,
        "_load_user_preferences",
        return_value=UserPreferenceProfile(username="demo_user"),
    )
    def test_merge_pending_order_context_clears_for_non_order_intent(
        self, _mock_user_profile
    ):
        supervisor = SmartVoyageSupervisor(Config())
        intent_result = IntentRecognitionResult(
            intents=["time"], user_queries={"time": "现在几点"}
        )

        merged_result, pending = supervisor._merge_pending_order_context(
            "现在几点",
            "",
            intent_result,
            {"action": "cancel_order", "missing_fields": ["order_type"]},
            {},
        )

        self.assertEqual(merged_result.intents, ["time"])
        self.assertEqual(pending, {})

    @patch.object(
        SmartVoyageSupervisor,
        "_load_user_preferences",
        return_value=UserPreferenceProfile(username="demo_user"),
    )
    def test_merge_pending_order_context_re_recognizes_when_follow_up_looks_like_order(
        self, _mock_user_profile
    ):
        supervisor = SmartVoyageSupervisor(Config())
        supervisor.recognize_intent = MagicMock(
            return_value=IntentRecognitionResult(
                intents=["cancel_order"],
                user_queries={"cancel_order": "退2026-03-21北京到上海的高铁票"},
            )
        )
        initial = IntentRecognitionResult(
            intents=["out_of_scope"], user_queries={}, follow_up_message=""
        )
        pending = {
            "action": "cancel_order",
            "missing_fields": ["current_order_selector"],
        }

        merged_result, merged_pending = supervisor._merge_pending_order_context(
            "退2026-03-21北京到上海的高铁票",
            "",
            initial,
            pending,
            {},
        )

        self.assertEqual(merged_result.intents, ["cancel_order"])
        self.assertEqual(merged_pending, pending)
        supervisor.recognize_intent.assert_called_once()
        self.assertIn(
            "继续处理之前的订单操作", supervisor.recognize_intent.call_args.args[0]
        )

    def test_build_order_query_uses_first_ticket_when_available(self):
        query = SmartVoyageSupervisor._build_order_query(
            travel_query="帮我订票",
            transport_mode="train",
            ticket_result_text="共找到 1 条高铁票",
            ticket_result_data={
                "tickets": [
                    {
                        "departure_time": "2026-03-21 07:00:00",
                        "departure_city": "北京",
                        "arrival_city": "上海",
                        "transport_no": "G5",
                        "ticket_type": "二等座",
                    }
                ]
            },
        )

        self.assertIn("请直接预订2026-03-21北京到上海的高铁票", query)
        self.assertIn("车次G5", query)
        self.assertIn("二等座1张", query)

    @patch.object(
        SmartVoyageSupervisor,
        "_load_user_preferences",
        return_value=UserPreferenceProfile(username="demo_user"),
    )
    def test_transport_decision_finalize_adds_degraded_prefix_and_pending_context(
        self, _mock_user_profile
    ):
        supervisor = SmartVoyageSupervisor(Config())
        result = supervisor._transport_decision_finalize_node(
            {
                "decision_query": "根据2026-03-21上海的天气判断从北京去上海坐高铁还是飞机更合适，可自动下单",
                "plan": {
                    "transport_mode": "train",
                    "weather_brief": "上海多云，天气较稳定。",
                    "recommendation_reason": "用户偏好高铁且天气稳定，高铁整体更稳妥。",
                    "ticket_plan": {
                        "type": "train",
                        "departure_city": "北京",
                        "arrival_city": "上海",
                        "date_from": "2026-03-21",
                        "date_to": "2026-03-21",
                        "transport_no": "G5",
                        "ticket_type": "二等座",
                    },
                    "should_order": True,
                },
                "ticket_result_state": "completed",
                "ticket_result_text": "共找到 1 条高铁票，优先展示前 1 条。",
                "ticket_result_data": {
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
                },
                "order_result_text": "下单审批：2026-03-21 北京到上海 G5 二等座 1张。请回复 yes 确认执行，或回复 no 取消执行。",
                "order_result_state": "input_required",
                "order_result_pending_context": {
                    "action": "hitl_review",
                    "thread_id": "req-td-1",
                },
                "routed_agents": ["TravelReadSubagent", "OrderSubagent"],
                "weather_degraded": True,
            }
        )

        self.assertIn("协作降级", result["final_response"])
        self.assertIn("天气判断：上海多云，天气较稳定。", result["final_response"])
        self.assertIn("预订结果：", result["final_response"])
        self.assertEqual(
            result["order_result_pending_context"]["source_intent"],
            "transport_decision",
        )
        self.assertIn("response_prefix", result["order_result_pending_context"])

    def test_maybe_follow_up_with_home_city_returns_prompt_when_needed(self):
        supervisor = SmartVoyageSupervisor.__new__(SmartVoyageSupervisor)

        message = supervisor._maybe_follow_up_with_home_city(
            ["train"],
            UserPreferenceProfile(username="demo_user", home_city="北京"),
            True,
        )

        self.assertIn("你这次是从北京出发吗", message)

    def test_maybe_follow_up_with_home_city_skips_when_not_needed(self):
        supervisor = SmartVoyageSupervisor.__new__(SmartVoyageSupervisor)

        message = supervisor._maybe_follow_up_with_home_city(
            ["train"],
            UserPreferenceProfile(username="demo_user", home_city="北京"),
            False,
        )

        self.assertEqual(message, "")

    def test_out_of_scope_response_is_normalized_to_domain_boundary(self):
        message = SmartVoyageSupervisor._out_of_scope_response(
            "你好，我是智能旅行助手，欢迎您向我提问"
        )

        self.assertIn("交通", message)
        self.assertIn("出行", message)
        self.assertIn("票务", message)
        self.assertIn("订单", message)


if __name__ == "__main__":
    unittest.main()
