import os
import sys
import unittest

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from main_prompts import SmartVoyagePrompts
from prompt_skills.registry import prompt_registry


class PromptSkillRegistryTest(unittest.TestCase):
    def test_registry_builds_all_known_prompts(self):
        required = [
            "intent.recognize",
            "intent.travel-query-context",
            "travel-read.kind",
            "travel-read.weather-summary",
            "travel-read.ticket-summary",
            "travel-read.weather-plan",
            "travel-read.ticket-plan",
            "transport-decision.plan",
            "transport-decision.auto-order",
            "order.action",
            "order.review-decision",
            "order.date-resolution",
            "order.operation-extraction",
        ]

        for prompt_id in required:
            with self.subTest(prompt_id=prompt_id):
                prompt = prompt_registry.build(prompt_id)
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

    def test_main_prompts_facade_still_exposes_current_entrypoints(self):
        builders = [
            SmartVoyagePrompts.intent_prompt,
            SmartVoyagePrompts.summarize_weather_prompt,
            SmartVoyagePrompts.summarize_ticket_prompt,
            SmartVoyagePrompts.travel_read_kind_prompt,
            SmartVoyagePrompts.travel_query_context_prompt,
            SmartVoyagePrompts.transport_decision_prompt,
            SmartVoyagePrompts.order_action_prompt,
            SmartVoyagePrompts.review_decision_prompt,
            SmartVoyagePrompts.date_resolution_prompt,
            SmartVoyagePrompts.weather_query_plan_prompt,
            SmartVoyagePrompts.ticket_query_plan_prompt,
            SmartVoyagePrompts.auto_order_intent_prompt,
            SmartVoyagePrompts.order_operation_extraction_prompt,
        ]

        for builder in builders:
            with self.subTest(builder=builder.__name__):
                prompt = builder()
                self.assertIsNotNone(prompt)


if __name__ == "__main__":
    unittest.main()
