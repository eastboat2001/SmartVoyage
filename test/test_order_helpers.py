"""
功能：验证 Order 域辅助函数与审批解析逻辑。
作用：确保补问、日期归一化、审批恢复和 pending context 相关逻辑稳定。
实现方式：通过 unittest + patch 覆盖纯逻辑和轻量分支。
"""

import os
import sys
import unittest
from unittest.mock import patch

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.order import (
    _fast_normalize_date,
    build_pending_order_context,
    default_follow_up_message,
    extract_username,
    is_hitl_review_pending,
    normalize_missing_fields,
    parse_review_decision,
)
from contracts.structured_outputs import (
    OrderOperationExtractionResult,
    ReviewDecisionResult,
)


class OrderHelperTest(unittest.TestCase):
    def test_extract_username_prefers_latest_current_user_line(self):
        conversation = (
            "当前用户：demo_user\nUser: 查询我的订单\n当前用户：alice\nUser: 帮我退票"
        )

        username = extract_username(conversation)

        self.assertEqual(username, "alice")

    def test_fast_normalize_date_supports_relative_date(self):
        normalized = _fast_normalize_date("帮我查明天的订单", "2026-03-20")

        self.assertEqual(normalized, "2026-03-21")

    def test_fast_normalize_date_supports_full_date(self):
        normalized = _fast_normalize_date("查询2026年3月22日的订单", "2026-03-20")

        self.assertEqual(normalized, "2026-03-22")

    def test_is_hitl_review_pending_requires_thread_id(self):
        self.assertTrue(
            is_hitl_review_pending({"action": "hitl_review", "thread_id": "req-1"})
        )
        self.assertFalse(is_hitl_review_pending({"action": "hitl_review"}))
        self.assertFalse(
            is_hitl_review_pending({"action": "cancel_order", "thread_id": "req-1"})
        )

    @patch("agents.order.model_invoker.invoke_structured")
    def test_parse_review_decision_returns_approved_for_yes(
        self, mock_invoke_structured
    ):
        mock_invoke_structured.return_value = ReviewDecisionResult(decision="approved")

        decision, follow_up = parse_review_decision("yes", {"summary": "待审批操作"})

        self.assertEqual(decision, "approved")
        self.assertEqual(follow_up, "")

    @patch("agents.order.model_invoker.invoke_structured")
    def test_parse_review_decision_returns_follow_up_when_unclear(
        self, mock_invoke_structured
    ):
        mock_invoke_structured.return_value = ReviewDecisionResult(
            decision="unclear",
            follow_up_message="请明确回复确认执行或取消执行。",
        )

        decision, follow_up = parse_review_decision(
            "我再想想", {"summary": "待审批操作"}
        )

        self.assertIsNone(decision)
        self.assertIn("请明确回复", follow_up)

    def test_normalize_missing_fields_for_cancel_adds_order_type_and_selector(self):
        extraction = OrderOperationExtractionResult(
            action="cancel_order",
            is_complete=False,
            follow_up_message="请补充订单信息。",
        )

        missing_fields = normalize_missing_fields("cancel_order", extraction)

        self.assertIn("order_type", missing_fields)
        self.assertIn("current_order_selector", missing_fields)

    def test_normalize_missing_fields_for_change_requires_new_target(self):
        extraction = OrderOperationExtractionResult(
            action="change_order",
            order_type="train",
            current_departure_date="2026-03-21",
            departure_city="北京",
            arrival_city="上海",
            is_complete=False,
            follow_up_message="请补充新的改签目标。",
        )

        missing_fields = normalize_missing_fields("change_order", extraction)

        self.assertIn("new_target", missing_fields)
        self.assertNotIn("current_order_selector", missing_fields)

    def test_default_follow_up_message_for_cancel_is_specific(self):
        message = default_follow_up_message(
            "cancel_order", ["order_type", "current_order_selector"]
        )

        self.assertIn("高铁票还是机票", message)
        self.assertIn("车次/航班号", message)

    def test_build_pending_order_context_keeps_only_non_empty_fields(self):
        extraction = OrderOperationExtractionResult(
            action="cancel_order",
            order_type="train",
            current_departure_date="2026-03-21",
            departure_city="北京",
            arrival_city="上海",
            current_transport_no="G5",
            current_ticket_type="二等座",
            is_complete=False,
            follow_up_message="请补充订单信息。",
        )

        pending = build_pending_order_context(
            action="cancel_order",
            query="帮我退票",
            extraction=extraction,
            missing_fields=["current_order_selector"],
        )

        self.assertEqual(pending["action"], "cancel_order")
        self.assertEqual(pending["missing_fields"], ["current_order_selector"])
        self.assertEqual(pending["extracted_fields"]["order_type"], "train")
        self.assertEqual(pending["extracted_fields"]["current_transport_no"], "G5")
        self.assertNotIn("new_departure_date", pending["extracted_fields"])


if __name__ == "__main__":
    unittest.main()
