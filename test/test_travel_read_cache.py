import json
import os
import sys
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(ROOT)
sys.path.append(os.path.join(ROOT, "mcp_server"))

from config import Config
from mcp_travel_read_server import TravelReadService


class _FakeCursor:
    def __init__(self, rows):
        self.rows = rows

    def execute(self, _sql):
        return None

    def fetchall(self):
        return list(self.rows)

    def close(self):
        return None


class _FakeConnection:
    def __init__(self, rows):
        self.rows = rows

    def cursor(self, dictionary=True):
        return _FakeCursor(self.rows)

    def is_connected(self):
        return True

    def close(self):
        return None


class _FakeCache:
    def __init__(self):
        self.data = {}

    def get_json(self, key):
        return self.data.get(key)

    def set_json(self, key, value, ttl_seconds):
        self.data[key] = dict(value)


class TravelReadCacheTest(unittest.TestCase):
    @patch("mcp_travel_read_server.get_db_connection")
    def test_execute_select_uses_cache_after_first_query(self, mock_conn):
        mock_conn.return_value = _FakeConnection([
            {"city": "北京", "fx_date": "2026-03-21", "temp_max": 20, "temp_min": 10}
        ])
        service = TravelReadService(Config())
        service.cache = _FakeCache()
        sql = "SELECT * FROM weather_data WHERE city = '北京'"

        first = json.loads(service.execute_select(sql, no_data_message="无", cache_prefix="weather", ttl_seconds=600))
        second = json.loads(service.execute_select(sql, no_data_message="无", cache_prefix="weather", ttl_seconds=600))

        self.assertEqual(first["meta"]["cache_status"], "miss")
        self.assertEqual(second["meta"]["cache_status"], "hit")
        self.assertEqual(first["data"][0]["city"], "北京")
        self.assertEqual(second["data"][0]["city"], "北京")


if __name__ == "__main__":
    unittest.main()
