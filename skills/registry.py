from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from langchain_core.prompts import ChatPromptTemplate

from create_logger import logger
from skills.builder import PromptSkillBuilder


@dataclass(frozen=True)
class PromptBuildContext:
    flags: frozenset[str] = field(default_factory=frozenset)

    @classmethod
    def from_flags(cls, *flags: str) -> "PromptBuildContext":
        return cls(flags=frozenset(flag for flag in flags if flag))

    def has(self, flag: str) -> bool:
        return flag in self.flags


@dataclass(frozen=True)
class PromptSpec:
    prompt_id: str
    skill_name: str
    template_path: str
    optional_references: dict[str, tuple[str, ...]] = field(default_factory=dict)


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
                optional_references={
                    "has_query_rewrite_context": ("references/query_rewrite_context.md",),
                    "has_transport_decision_request": ("references/transport_decision_focus.md",),
                    "has_relative_date": ("references/relative_date_focus.md",),
                },
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
                optional_references={
                    "has_relative_date": ("references/relative_date_rules.md",),
                },
            ),
            "travel-read.ticket-plan": PromptSpec(
                prompt_id="travel-read.ticket-plan",
                skill_name="travel-read",
                template_path="assets/ticket_plan.md",
                optional_references={
                    "has_relative_date": ("references/relative_date_rules.md",),
                },
            ),
            "transport-decision.plan": PromptSpec(
                prompt_id="transport-decision.plan",
                skill_name="transport-decision",
                template_path="assets/plan.md",
                optional_references={
                    "has_relative_date": ("references/relative_date_rules.md",),
                    "weather_degraded": ("references/weather_degradation_rules.md",),
                    "weather_no_data": ("references/weather_no_data_rules.md",),
                },
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
                optional_references={
                    "has_pending_context": ("references/pending_context_rules.md",),
                },
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
                optional_references={
                    "has_relative_date": ("references/relative_date_rules.md",),
                },
            ),
            "order.operation-extraction": PromptSpec(
                prompt_id="order.operation-extraction",
                skill_name="order-operation",
                template_path="assets/operation_extraction.md",
                optional_references={
                    "has_pending_context": ("references/pending_context_rules.md",),
                    "is_change_order": ("references/change_order_rules.md",),
                },
            ),
        }

    def build(
        self,
        prompt_id: str,
        *,
        build_context: PromptBuildContext | None = None,
    ) -> ChatPromptTemplate:
        try:
            spec = self._specs[prompt_id]
        except KeyError as exc:
            known = ", ".join(sorted(self._specs))
            raise KeyError(f"Unknown prompt_id: {prompt_id}. Known prompt ids: {known}") from exc

        selected_references = self._select_references(spec, build_context)
        result = self.builder.build_template(
            skill_name=spec.skill_name,
            template_path=spec.template_path,
            selected_references=selected_references,
        )
        logger.info(
            "PromptRegistry 加载 prompt: "
            f"prompt_id={prompt_id}, "
            f"skill={spec.skill_name}, "
            f"asset={spec.template_path}, "
            f"flags={sorted((build_context.flags if build_context else frozenset()))}, "
            f"selected_references={list(selected_references)}, "
            f"loaded_files={list(result.loaded_files)}"
        )
        return result.prompt

    @staticmethod
    def _select_references(
        spec: PromptSpec,
        build_context: PromptBuildContext | None,
    ) -> tuple[str, ...]:
        if build_context is None or not spec.optional_references:
            return ()
        selected: list[str] = []
        for flag, paths in spec.optional_references.items():
            if build_context.has(flag):
                selected.extend(paths)
        return tuple(dict.fromkeys(selected))


prompt_registry = PromptRegistry()
