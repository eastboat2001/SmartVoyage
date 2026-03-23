import os
import subprocess
import sys
import time
import unittest
from pathlib import Path

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.supervisor import SmartVoyageSupervisor
from config import Config


ROOT = Path(__file__).resolve().parents[1]


class SupervisorE2ETest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if os.getenv("SMARTVOYAGE_RUN_E2E", "").strip() != "1":
            raise unittest.SkipTest("Set SMARTVOYAGE_RUN_E2E=1 to run supervisor end-to-end tests.")

        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"
        cls.travel_read_process = subprocess.Popen(
            [sys.executable, "-u", "mcp_server/mcp_travel_read_server.py"],
            cwd=ROOT,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        cls.order_process = subprocess.Popen(
            [sys.executable, "-u", "mcp_server/mcp_order_server.py"],
            cwd=ROOT,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        time.sleep(4)
        cls.supervisor = SmartVoyageSupervisor(Config())

    @classmethod
    def tearDownClass(cls):
        for process_name in ("travel_read_process", "order_process"):
            process = getattr(cls, process_name, None)
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)

    def test_time_query_end_to_end(self):
        result = self.supervisor.process_user_input("现在几点", "", {})

        self.assertEqual(result["intents"], ["time"])
        self.assertEqual(result["routed_agents"], ["TravelReadSubagent"])
        self.assertIn("当前时间", result["response"])
        self.assertEqual(result["pending_order_context"], {})

    def test_transport_decision_end_to_end(self):
        result = self.supervisor.process_user_input(
            "根据2026-03-21上海的天气，帮我判断从北京去上海坐高铁还是飞机更合适",
            "",
            {},
        )

        self.assertEqual(result["intents"], ["transport_decision"])
        self.assertIn("TravelReadSubagent", result["routed_agents"])
        self.assertTrue(result["response"].strip())
        self.assertTrue("高铁" in result["response"] or "飞机" in result["response"])
        self.assertEqual(result["pending_order_context"], {})


if __name__ == "__main__":
    unittest.main()
