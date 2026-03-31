"""
功能：验证 metrics、时钟和结构化 schema 的基础行为。
作用：为性能统计和确定性时间语义提供稳定回归保护。
实现方式：通过 unittest 覆盖聚合、计时和 schema 校验逻辑。
"""

import os
import sys
import unittest

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.clock import get_current_time_payload
from contracts.structured_outputs import OrderOperationExtractionResult, TicketQuerySpec
from observability.metrics import create_metrics, merge_metrics, track_phase


class MetricsAndSchemaTest(unittest.TestCase):
    def test_merge_metrics_accumulates_phase_timings_and_counters(self):
        base = create_metrics()
        base["phase_timings_ms"]["intent_recognition"] = 10.5
        base["llm_call_count"] = 1

        incoming = create_metrics()
        incoming["phase_timings_ms"]["intent_recognition"] = 3.25
        incoming["phase_timings_ms"]["ticket_plan"] = 7.0
        incoming["llm_call_count"] = 2
        incoming["tool_call_count"] = 1

        merged = merge_metrics(base, incoming)

        self.assertEqual(merged["phase_timings_ms"]["intent_recognition"], 13.75)
        self.assertEqual(merged["phase_timings_ms"]["ticket_plan"], 7.0)
        self.assertEqual(merged["llm_call_count"], 3)
        self.assertEqual(merged["tool_call_count"], 1)

    def test_track_phase_records_elapsed_time(self):
        metrics = create_metrics()

        with track_phase(metrics, "weather_plan"):
            pass

        self.assertIn("weather_plan", metrics["phase_timings_ms"])
        self.assertGreaterEqual(metrics["phase_timings_ms"]["weather_plan"], 0.0)

    def test_get_current_time_payload_uses_override(self):
        payload = get_current_time_payload(
            timezone_name="Asia/Shanghai", override="2026-03-21T09:00:00+08:00"
        )

        self.assertEqual(payload["current_time"], "2026-03-21 09:00:00")
        self.assertEqual(payload["current_date"], "2026-03-21")
        self.assertEqual(payload["timezone"], "Asia/Shanghai")

    def test_ticket_query_spec_fills_date_to_and_caps_limit(self):
        spec = TicketQuerySpec(
            type="train",
            departure_city="北京",
            arrival_city="上海",
            date_from="2026-03-21",
            date_to="",
            transport_no="",
            ticket_type="二等座",
            limit=999,
        )

        self.assertEqual(spec.date_to, "2026-03-21")
        self.assertEqual(spec.limit, 20)

    def test_order_operation_extraction_requires_follow_up_when_incomplete(self):
        with self.assertRaises(ValueError):
            OrderOperationExtractionResult(
                action="cancel_order",
                is_complete=False,
                follow_up_message="",
            )


if __name__ == "__main__":
    unittest.main()
