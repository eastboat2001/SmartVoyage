from prompt_skills import prompt_registry


class SmartVoyagePrompts:
    @staticmethod
    def intent_prompt():
        return prompt_registry.build("intent.recognize")

    @staticmethod
    def summarize_weather_prompt():
        return prompt_registry.build("travel-read.weather-summary")

    @staticmethod
    def summarize_ticket_prompt():
        return prompt_registry.build("travel-read.ticket-summary")

    @staticmethod
    def travel_read_kind_prompt():
        return prompt_registry.build("travel-read.kind")

    @staticmethod
    def travel_query_context_prompt():
        return prompt_registry.build("intent.travel-query-context")

    @staticmethod
    def transport_decision_prompt():
        return prompt_registry.build("transport-decision.plan")

    @staticmethod
    def order_action_prompt():
        return prompt_registry.build("order.action")

    @staticmethod
    def review_decision_prompt():
        return prompt_registry.build("order.review-decision")

    @staticmethod
    def date_resolution_prompt():
        return prompt_registry.build("order.date-resolution")

    @staticmethod
    def weather_query_plan_prompt():
        return prompt_registry.build("travel-read.weather-plan")

    @staticmethod
    def ticket_query_plan_prompt():
        return prompt_registry.build("travel-read.ticket-plan")

    @staticmethod
    def auto_order_intent_prompt():
        return prompt_registry.build("transport-decision.auto-order")

    @staticmethod
    def order_operation_extraction_prompt():
        return prompt_registry.build("order.operation-extraction")


if __name__ == "__main__":
    print(SmartVoyagePrompts.intent_prompt())
