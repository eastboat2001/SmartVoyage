"""
功能：提供各 agent 所需 Prompt 的统一构建入口。
作用：把业务代码与 Skill Runtime 解耦，通过 role、capability 和 flags 选择提示资产。
实现方式：封装 Prompt builder，根据查询内容和上下文条件组装 Skill 调用参数。
"""

from skills import SkillBuildContext, skill_runtime


RELATIVE_DATE_TOKENS = ("今天", "明天", "后天", "未来")
TRANSPORT_DECISION_TOKENS = ("高铁还是飞机", "坐高铁还是飞机", "更适合坐高铁还是飞机", "交通建议", "出行建议")


class SmartVoyagePrompts:
    @staticmethod
    def _contains_relative_date(*texts: str) -> bool:
        return any(token in (text or "") for token in RELATIVE_DATE_TOKENS for text in texts)

    @staticmethod
    def _has_query_rewrite_context(conversation_history: str) -> bool:
        lines = [line for line in (conversation_history or "").splitlines() if line.strip()]
        return len(lines) > 1

    @staticmethod
    def _has_transport_decision_request(query: str) -> bool:
        text = query or ""
        return any(token in text for token in TRANSPORT_DECISION_TOKENS)

    @staticmethod
    def _has_pending_context(pending_context: str) -> bool:
        normalized = (pending_context or "").strip()
        return bool(normalized and normalized not in {"无", "{}"})

    @staticmethod
    def _is_weather_degraded(weather_result: str) -> bool:
        text = weather_result or ""
        return any(token in text for token in ("天气服务暂不可用", "协作降级", "保守策略"))

    @staticmethod
    def _is_weather_no_data(weather_result: str) -> bool:
        text = weather_result or ""
        return any(token in text for token in ("没有命中", "未找到天气数据", "缺失天气数据"))

    @staticmethod
    def intent_prompt(*, conversation_history: str = "", query: str = ""):
        flags = []
        if SmartVoyagePrompts._has_query_rewrite_context(conversation_history):
            flags.append("has_query_rewrite_context")
        if SmartVoyagePrompts._has_transport_decision_request(query):
            flags.append("has_transport_decision_request")
        if SmartVoyagePrompts._contains_relative_date(query, conversation_history):
            flags.append("has_relative_date")
        return skill_runtime.build(
            role="supervisor",
            capability="intent_recognition",
            build_context=SkillBuildContext.from_flags(*flags),
        )

    @staticmethod
    def travel_read_kind_prompt():
        return skill_runtime.build(role="travel_read", capability="read_kind")

    @staticmethod
    def transport_decision_prompt(*, query: str = "", weather_result: str = ""):
        flags = []
        if SmartVoyagePrompts._contains_relative_date(query):
            flags.append("has_relative_date")
        if SmartVoyagePrompts._is_weather_degraded(weather_result):
            flags.append("weather_degraded")
        if SmartVoyagePrompts._is_weather_no_data(weather_result):
            flags.append("weather_no_data")
        return skill_runtime.build(
            role="supervisor",
            capability="decision_plan",
            build_context=SkillBuildContext.from_flags(*flags),
        )

    @staticmethod
    def order_action_prompt(*, pending_context: str = ""):
        flags = []
        if SmartVoyagePrompts._has_pending_context(pending_context):
            flags.append("has_pending_context")
        return skill_runtime.build(
            role="order",
            capability="action_classify",
            build_context=SkillBuildContext.from_flags(*flags),
        )

    @staticmethod
    def review_decision_prompt():
        return skill_runtime.build(role="order", capability="review_decision")

    @staticmethod
    def date_resolution_prompt(*, query: str = ""):
        flags = []
        if SmartVoyagePrompts._contains_relative_date(query):
            flags.append("has_relative_date")
        return skill_runtime.build(
            role="order",
            capability="date_resolution",
            build_context=SkillBuildContext.from_flags(*flags),
        )

    @staticmethod
    def weather_query_plan_prompt(*, conversation_history: str = "", query: str = ""):
        flags = []
        if SmartVoyagePrompts._contains_relative_date(query, conversation_history):
            flags.append("has_relative_date")
        return skill_runtime.build(
            role="travel_read",
            capability="weather_plan",
            build_context=SkillBuildContext.from_flags(*flags),
        )

    @staticmethod
    def ticket_query_plan_prompt(*, conversation_history: str = "", query: str = ""):
        flags = []
        if SmartVoyagePrompts._contains_relative_date(query, conversation_history):
            flags.append("has_relative_date")
        return skill_runtime.build(
            role="travel_read",
            capability="ticket_plan",
            build_context=SkillBuildContext.from_flags(*flags),
        )

    @staticmethod
    def order_operation_extraction_prompt(*, action: str = "", pending_context: str = ""):
        flags = []
        if SmartVoyagePrompts._has_pending_context(pending_context):
            flags.append("has_pending_context")
        if action == "change_order":
            flags.append("is_change_order")
        return skill_runtime.build(
            role="order",
            capability="operation_extraction",
            build_context=SkillBuildContext.from_flags(*flags),
        )

