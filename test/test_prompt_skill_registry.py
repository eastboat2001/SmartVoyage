"""
功能：验证 Skill Runtime 当前暴露的 capability 与 Prompt builder 一致。
作用：防止 skill 清理或重构后留下失效入口。
实现方式：通过 unittest 检查 role-capability 组合和上下文引用装配结果。
"""

import os
import sys
import unittest

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.prompts import SmartVoyagePrompts
from skills.runtime import SkillBuildContext, skill_runtime


class PromptSkillRegistryTest(unittest.TestCase):
    def test_skill_registry_discovers_all_known_role_capability_pairs(self):
        required = [
            ("supervisor", "intent_recognition"),
            ("travel_read", "read_kind"),
            ("travel_read", "weather_plan"),
            ("travel_read", "ticket_plan"),
            ("supervisor", "decision_plan"),
            ("order", "action_classify"),
            ("order", "review_decision"),
            ("order", "date_resolution"),
            ("order", "operation_extraction"),
        ]

        for role, capability in required:
            with self.subTest(role=role, capability=capability):
                prompt = skill_runtime.build(role=role, capability=capability)
                rendered = prompt.format(
                    current_date="2026-03-21",
                    conversation_history="User: test",
                    query="test",
                    raw_response="test",
                    weather_result="test",
                    user_preferences="test",
                    pending_context="test",
                    review_summary="test",
                    action="cancel_order",
                )
                self.assertTrue(rendered.strip())

    def test_prompt_builders_expose_current_entrypoints(self):
        builders = [
            SmartVoyagePrompts.intent_prompt,
            SmartVoyagePrompts.travel_read_kind_prompt,
            SmartVoyagePrompts.transport_decision_prompt,
            SmartVoyagePrompts.order_action_prompt,
            SmartVoyagePrompts.review_decision_prompt,
            SmartVoyagePrompts.date_resolution_prompt,
            SmartVoyagePrompts.weather_query_plan_prompt,
            SmartVoyagePrompts.ticket_query_plan_prompt,
            SmartVoyagePrompts.order_operation_extraction_prompt,
        ]

        for builder in builders:
            with self.subTest(builder=builder.__name__):
                prompt = builder()
                self.assertIsNotNone(prompt)

    def test_contextual_references_are_loaded_when_flags_match(self):
        prompt = skill_runtime.build(
            role="supervisor",
            capability="decision_plan",
            build_context=SkillBuildContext.from_flags("has_relative_date", "weather_degraded"),
        )
        rendered = prompt.format(
            current_date="2026-03-21",
            query="明天从北京去上海坐高铁还是飞机更合适",
            weather_result="天气服务暂不可用",
            user_preferences="预算中等",
        )
        self.assertIn("补充规则：", rendered)
        self.assertIn("相对日期表达", rendered)
        self.assertIn("服务降级或不可用", rendered)


if __name__ == "__main__":
    unittest.main()
