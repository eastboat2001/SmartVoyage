"""
功能：验证 Order 状态机的路由和审批载荷构造逻辑。
作用：确保事务链路关键分支在不跑完整工作流时也能被稳定校验。
实现方式：通过 unittest 覆盖静态路由函数和 review payload 组装逻辑。
"""

import os
import sys
import unittest
from unittest.mock import patch

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.order import OrderSubagent
from agents.travel_read import TravelReadSubagent
from core.config import Config


class OrderWorkflowHelperTest(unittest.TestCase):
    def setUp(self):
        self.agent = OrderSubagent(Config(), TravelReadSubagent(Config()))

    def test_route_action_returns_finish_when_input_required(self):
        route = self.agent._route_action(
            {"final_state": "input_required", "action": "cancel_order"}
        )

        self.assertEqual(route, "finish")

    def test_route_action_returns_action_when_ready(self):
        route = self.agent._route_action({"action": "query_orders"})

        self.assertEqual(route, "query_orders")

    def test_route_after_ticket_lookup_returns_review_when_completed(self):
        route = self.agent._route_after_ticket_lookup(
            {"ticket_task_state": "completed"}
        )

        self.assertEqual(route, "review")

    def test_route_after_ticket_lookup_returns_finish_when_failed(self):
        route = self.agent._route_after_ticket_lookup({"ticket_task_state": "failed"})

        self.assertEqual(route, "finish")

    def test_route_after_review_returns_finish_when_rejected(self):
        route = self.agent._route_after_review(
            {"review_decision": "rejected", "action": "cancel_order"}
        )

        self.assertEqual(route, "finish")

    def test_route_after_review_returns_action_when_approved(self):
        route = self.agent._route_after_review(
            {"review_decision": "approved", "action": "change_order"}
        )

        self.assertEqual(route, "change_order")

    def test_build_review_payload_for_create_order_uses_first_ticket(self):
        payload = self.agent._build_review_payload(
            {
                "action": "create_order",
                "username": "demo_user",
                "ticket_result_data": {
                    "tickets": [
                        {
                            "order_type": "train",
                            "departure_city": "北京",
                            "arrival_city": "上海",
                            "departure_time": "2026-03-21 07:00:00",
                            "transport_no": "G5",
                            "ticket_type": "二等座",
                            "price": 598.0,
                        }
                    ]
                },
            }
        )

        self.assertEqual(payload["action"], "create_order")
        self.assertEqual(payload["transport_no"], "G5")
        self.assertEqual(payload["ticket_type"], "二等座")
        self.assertIn("下单审批", payload["summary"])

    def test_build_review_payload_for_cancel_order_uses_operation_payload(self):
        payload = self.agent._build_review_payload(
            {
                "action": "cancel_order",
                "username": "demo_user",
                "operation_payload": {
                    "order_type": "train",
                    "departure_city": "北京",
                    "arrival_city": "上海",
                    "departure_date": "2026-03-21",
                    "transport_no": "G5",
                    "ticket_type": "二等座",
                },
            }
        )

        self.assertEqual(payload["action"], "cancel_order")
        self.assertEqual(payload["order_type"], "train")
        self.assertEqual(payload["transport_no"], "G5")
        self.assertIn("退票审批", payload["summary"])

    @patch(
        "agents.order.invoke_order_tool",
        return_value="预订成功，订单号 1。2026-03-21 07:00:00 北京到上海 G5 二等座 1张，总价 598.00 元。",
    )
    def test_create_order_node_uses_direct_order_tool_for_selected_ticket(
        self, mock_invoke_order_tool
    ):
        import asyncio

        result = asyncio.run(
            self.agent._create_order_node(
                {
                    "username": "demo_user",
                    "ticket_result_text": "共找到 1 条高铁票，优先展示前 1 条。",
                    "ticket_result_data": {
                        "tickets": [
                            {
                                "order_type": "train",
                                "departure_time": "2026-03-21 07:00:00",
                                "departure_city": "北京",
                                "arrival_city": "上海",
                                "transport_no": "G5",
                                "ticket_type": "二等座",
                            }
                        ]
                    },
                    "metrics": {},
                }
            )
        )

        self.assertEqual(result["final_state"], "completed")
        self.assertIn("订票结果：预订成功", result["final_text"])
        mock_invoke_order_tool.assert_called_once()
        tool_name, params, metrics = mock_invoke_order_tool.call_args.args
        self.assertEqual(tool_name, "order_train")
        self.assertEqual(
            params,
            {
                "username": "demo_user",
                "departure_date": "2026-03-21",
                "train_number": "G5",
                "seat_type": "二等座",
                "number": 1,
            },
        )
        self.assertIn("tool_call_count", metrics)

    def test_create_order_node_fails_when_no_ticket_selected(self):
        import asyncio

        result = asyncio.run(
            self.agent._create_order_node(
                {
                    "username": "demo_user",
                    "ticket_result_text": "未找到可下单票务。",
                    "ticket_result_data": {"tickets": []},
                    "metrics": {},
                }
            )
        )

        self.assertEqual(result["final_state"], "failed")
        self.assertIn("未找到可用于下单的真实票务结果", result["final_text"])


if __name__ == "__main__":
    unittest.main()
