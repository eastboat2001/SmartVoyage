import os
import sys
import unittest

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.order import OrderSubagent
from agents.travel_read import TravelReadSubagent
from config import Config
from utils.agent_protocol import LocalAgentRequest


class _FakeWorkflow:
    def __init__(self, result):
        self.result = result
        self.last_payload = None
        self.last_config = None

    async def ainvoke(self, payload, config=None):
        self.last_payload = payload
        self.last_config = config
        return self.result


class OrderSubagentSmokeTest(unittest.TestCase):
    def test_order_agent_wraps_completed_workflow_result(self):
        agent = OrderSubagent(Config(), TravelReadSubagent(Config()))
        fake_workflow = _FakeWorkflow(
            {
                "final_text": "已找到你的订单，共 1 条。",
                "final_state": "completed",
                "final_data": {"kind": "transport_order", "orders": [{"order_id": "demo-1"}]},
                "action": "my_orders",
            }
        )
        agent.workflow = fake_workflow

        response = agent.invoke(LocalAgentRequest(text="当前用户：demo_user\n查询我的订单", request_id="req-order-1"))

        self.assertEqual(response.state, "completed")
        self.assertEqual(response.text, "已找到你的订单，共 1 条。")
        self.assertEqual(response.meta["action"], "my_orders")
        self.assertEqual(response.meta["thread_id"], "req-order-1")
        self.assertEqual(fake_workflow.last_config, {"configurable": {"thread_id": "req-order-1"}})

    def test_order_agent_surfaces_hitl_interrupt_as_input_required(self):
        interrupt = type("InterruptValue", (), {"value": {"summary": "下单审批：北京到上海 G5 二等座 1张。", "action": "create_order"}})()
        agent = OrderSubagent(Config(), TravelReadSubagent(Config()))
        agent.workflow = _FakeWorkflow({"__interrupt__": [interrupt]})

        response = agent.invoke(LocalAgentRequest(text="当前用户：demo_user\n帮我订一张北京到上海的高铁票", request_id="req-order-2"))

        self.assertEqual(response.state, "input_required")
        self.assertIn("请回复 yes 确认执行", response.text)
        self.assertEqual(response.pending_order_context["thread_id"], "req-order-2")
        self.assertEqual(response.pending_order_context["resume_intent"], "create_order")


if __name__ == "__main__":
    unittest.main()
