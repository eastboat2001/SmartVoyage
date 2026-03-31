"""
功能：验证 TravelRead 的确定性 SQL、格式化和缓存指标逻辑。
作用：防止只读查询链路在重构后出现格式化回归或查询条件错误。
实现方式：通过 unittest 覆盖纯逻辑与静态格式化函数。
"""

import os
import sys
import unittest

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.travel_read import TravelReadSubagent
from core.config import Config
from observability.metrics import create_metrics


class TravelReadHelperTest(unittest.TestCase):
    def setUp(self):
        self.agent = TravelReadSubagent(Config())

    def test_compile_weather_sql_uses_date_range(self):
        sql = self.agent.compile_weather_sql(
            {
                "city": "杭州",
                "date_from": "2026-03-21",
                "date_to": "2026-03-23",
            }
        )

        self.assertIn("WHERE city = '杭州'", sql)
        self.assertIn("fx_date >= '2026-03-21'", sql)
        self.assertIn("fx_date <= '2026-03-23'", sql)
        self.assertIn("LIMIT 7", sql)

    def test_compile_ticket_sql_caps_limit_and_keeps_filters(self):
        sql = self.agent.compile_ticket_sql(
            {
                "type": "train",
                "departure_city": "北京",
                "arrival_city": "上海",
                "date_from": "2026-03-21",
                "date_to": "2026-03-21",
                "transport_no": "G5",
                "ticket_type": "二等座",
                "limit": 999,
            }
        )

        self.assertIn("FROM train_tickets", sql)
        self.assertIn("departure_city = '北京'", sql)
        self.assertIn("arrival_city = '上海'", sql)
        self.assertIn("train_number = 'G5'", sql)
        self.assertIn("seat_type = '二等座'", sql)
        self.assertIn("LIMIT 20", sql)

    def test_format_weather_response_returns_input_required_for_no_data(self):
        state, text, data = TravelReadSubagent.format_weather_response(
            {"status": "no_data", "message": "未找到天气数据，请确认城市和日期。"}
        )

        self.assertEqual(state, "input_required")
        self.assertIn("未找到天气数据", text)
        self.assertEqual(data["weather_days"], [])

    def test_format_ticket_response_maps_train_fields(self):
        state, text, data = TravelReadSubagent.format_ticket_response(
            {
                "status": "success",
                "data": [
                    {
                        "departure_city": "北京",
                        "arrival_city": "上海",
                        "departure_time": "2026-03-21 07:00:00",
                        "arrival_time": "2026-03-21 11:42:00",
                        "train_number": "G5",
                        "seat_type": "二等座",
                        "price": 598.0,
                        "remaining_seats": 75,
                    }
                ],
            },
            "train",
        )

        self.assertEqual(state, "completed")
        self.assertIn("共找到 1 条高铁票", text)
        self.assertEqual(data["tickets"][0]["transport_no"], "G5")
        self.assertEqual(data["tickets"][0]["order_type"], "train")

    def test_format_time_response_returns_failed_on_error(self):
        state, text, data = TravelReadSubagent.format_time_response(
            {"status": "error", "message": "时间查询失败"}
        )

        self.assertEqual(state, "failed")
        self.assertEqual(text, "时间查询失败")
        self.assertEqual(data["kind"], "time")

    def test_apply_cache_metrics_counts_hit_and_miss(self):
        metrics = create_metrics()

        TravelReadSubagent._apply_cache_metrics(
            metrics, {"meta": {"cache_status": "hit"}}
        )
        TravelReadSubagent._apply_cache_metrics(
            metrics, {"meta": {"cache_status": "miss"}}
        )

        self.assertEqual(metrics["cache_hits"], 1)
        self.assertEqual(metrics["cache_misses"], 1)

    def test_tool_meta_keeps_cache_status_and_metrics_snapshot(self):
        metrics = create_metrics()
        metrics["tool_call_count"] = 2

        meta = TravelReadSubagent._tool_meta(
            "query_weather",
            {"meta": {"cache_status": "hit"}},
            metrics,
        )

        self.assertEqual(meta["tool"], "query_weather")
        self.assertEqual(meta["cache_status"], "hit")
        self.assertEqual(meta["metrics"]["tool_call_count"], 2)

    def test_normalize_ticket_plan_keeps_date_when_query_has_explicit_date(self):
        plan = self.agent._normalize_ticket_plan(
            "查询2026-03-21 G5次列车余票",
            {
                "status": "ready",
                "type": "train",
                "date_from": "2026-03-21",
                "date_to": "2026-03-21",
                "transport_no": "G5",
                "ticket_type": "二等座",
            },
        )

        self.assertEqual(plan["date_from"], "2026-03-21")
        self.assertEqual(plan["date_to"], "2026-03-21")


if __name__ == "__main__":
    unittest.main()
