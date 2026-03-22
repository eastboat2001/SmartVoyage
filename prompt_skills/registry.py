from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from langchain_core.prompts import ChatPromptTemplate

from prompt_skills.builder import PromptSkillBuilder


@dataclass(frozen=True)
class PromptSpec:
    prompt_id: str
    skill_name: str
    template_path: str


class PromptRegistry:
    def __init__(self, base_dir: Path | None = None):
        self.base_dir = Path(base_dir or Path(__file__).resolve().parent)
        self.builder = PromptSkillBuilder(self.base_dir)
        self._specs = self._build_specs()

    @staticmethod
    def _build_specs() -> dict[str, PromptSpec]:
        return {
            "intent.recognize": PromptSpec(
                prompt_id="intent.recognize",
                skill_name="intent-routing",
                template_path="assets/intent_recognize.md",
            ),
            "intent.travel-query-context": PromptSpec(
                prompt_id="intent.travel-query-context",
                skill_name="intent-routing",
                template_path="assets/travel_query_context.md",
            ),
            "travel-read.kind": PromptSpec(
                prompt_id="travel-read.kind",
                skill_name="travel-read",
                template_path="assets/read_kind.md",
            ),
            "travel-read.weather-summary": PromptSpec(
                prompt_id="travel-read.weather-summary",
                skill_name="travel-read",
                template_path="assets/weather_summary.md",
            ),
            "travel-read.ticket-summary": PromptSpec(
                prompt_id="travel-read.ticket-summary",
                skill_name="travel-read",
                template_path="assets/ticket_summary.md",
            ),
            "travel-read.weather-plan": PromptSpec(
                prompt_id="travel-read.weather-plan",
                skill_name="travel-read",
                template_path="assets/weather_plan.md",
            ),
            "travel-read.ticket-plan": PromptSpec(
                prompt_id="travel-read.ticket-plan",
                skill_name="travel-read",
                template_path="assets/ticket_plan.md",
            ),
            "transport-decision.plan": PromptSpec(
                prompt_id="transport-decision.plan",
                skill_name="transport-decision",
                template_path="assets/plan.md",
            ),
            "transport-decision.auto-order": PromptSpec(
                prompt_id="transport-decision.auto-order",
                skill_name="transport-decision",
                template_path="assets/auto_order.md",
            ),
            "order.action": PromptSpec(
                prompt_id="order.action",
                skill_name="order-operation",
                template_path="assets/action.md",
            ),
            "order.review-decision": PromptSpec(
                prompt_id="order.review-decision",
                skill_name="order-operation",
                template_path="assets/review_decision.md",
            ),
            "order.date-resolution": PromptSpec(
                prompt_id="order.date-resolution",
                skill_name="order-operation",
                template_path="assets/date_resolution.md",
            ),
            "order.operation-extraction": PromptSpec(
                prompt_id="order.operation-extraction",
                skill_name="order-operation",
                template_path="assets/operation_extraction.md",
            ),
        }

    def build(self, prompt_id: str) -> ChatPromptTemplate:
        try:
            spec = self._specs[prompt_id]
        except KeyError as exc:
            known = ", ".join(sorted(self._specs))
            raise KeyError(f"Unknown prompt_id: {prompt_id}. Known prompt ids: {known}") from exc
        return self.builder.build_template(
            skill_name=spec.skill_name,
            template_path=spec.template_path,
        )


prompt_registry = PromptRegistry()
