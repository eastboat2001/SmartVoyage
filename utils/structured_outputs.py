from typing import Literal

from pydantic import BaseModel, Field, model_validator


SupportedIntent = Literal[
    "weather",
    "time",
    "flight",
    "train",
    "order",
    "my_orders",
    "cancel_order",
    "change_order",
    "transport_decision",
    "out_of_scope",
]


class IntentRecognitionResult(BaseModel):
    intents: list[SupportedIntent] = Field(default_factory=list)
    user_queries: dict[str, str] = Field(default_factory=dict)
    follow_up_message: str = ""


class TravelReadKindResult(BaseModel):
    kind: Literal["weather", "ticket", "time"]


class TravelQueryContextResult(BaseModel):
    is_ticket_or_travel_query: bool = False
    has_explicit_departure_city: bool = False
    has_explicit_transport_no: bool = False
    needs_home_city_follow_up: bool = False


class OrderActionDecisionResult(BaseModel):
    action: Literal["query_orders", "cancel_order", "change_order", "create_order"]


class ReviewDecisionResult(BaseModel):
    decision: Literal["approved", "rejected", "unclear"]
    follow_up_message: str = ""


class DateResolutionResult(BaseModel):
    normalized_date: str = ""


class AutoOrderIntentResult(BaseModel):
    should_order: bool = False


class TransportDecisionPlanResult(BaseModel):
    transport_mode: Literal["train", "flight"]
    weather_brief: str = ""
    recommendation_reason: str
    ticket_query: str
    should_order: bool = False

    @model_validator(mode="after")
    def validate_payload(self):
        if not self.recommendation_reason.strip():
            raise ValueError("recommendation_reason is required")
        if not self.ticket_query.strip():
            raise ValueError("ticket_query is required")
        return self


class OrderOperationExtractionResult(BaseModel):
    action: Literal["cancel_order", "change_order"]
    order_type: Literal["train", "flight", ""] = ""
    current_departure_date: str = ""
    departure_city: str = ""
    arrival_city: str = ""
    current_transport_no: str = ""
    current_ticket_type: str = ""
    new_departure_date: str = ""
    new_transport_no: str = ""
    new_ticket_type: str = ""
    is_complete: bool = False
    missing_fields: list[str] = Field(default_factory=list)
    follow_up_message: str = ""

    @model_validator(mode="after")
    def validate_payload(self):
        if not self.follow_up_message.strip():
            self.follow_up_message = ""

        if self.action == "change_order":
            has_new_target = any(
                [
                    self.new_departure_date.strip(),
                    self.new_transport_no.strip(),
                    self.new_ticket_type.strip(),
                ]
            )
            if self.is_complete and not has_new_target:
                raise ValueError("change_order requires at least one new target field when complete")

        if self.is_complete and self.missing_fields:
            raise ValueError("complete extraction should not contain missing_fields")
        if not self.is_complete and not self.follow_up_message.strip():
            raise ValueError("incomplete extraction requires follow_up_message")
        return self


class WeatherQueryPlanResult(BaseModel):
    status: Literal["ready", "input_required"]
    city: str = ""
    date_from: str = ""
    date_to: str = ""
    message: str = ""

    @model_validator(mode="after")
    def validate_payload(self):
        if self.status == "ready":
            if not self.city.strip():
                raise ValueError("ready status requires city")
            if not self.date_from.strip():
                raise ValueError("ready status requires date_from")
            if not self.date_to.strip():
                self.date_to = self.date_from
        if self.status == "input_required" and not self.message.strip():
            raise ValueError("input_required status requires a non-empty message field")
        return self


class TicketQueryPlanResult(BaseModel):
    status: Literal["ready", "input_required"]
    type: Literal["train", "flight"] | None = None
    departure_city: str = ""
    arrival_city: str = ""
    date_from: str = ""
    date_to: str = ""
    transport_no: str = ""
    ticket_type: str = ""
    limit: int = 10
    message: str = ""

    @model_validator(mode="after")
    def validate_payload(self):
        if self.status == "ready":
            if self.type is None:
                raise ValueError("ready status requires a ticket type")
            if not self.transport_no.strip():
                if not (self.departure_city.strip() and self.arrival_city.strip() and self.date_from.strip()):
                    raise ValueError("ready status requires route + date_from or transport_no")
            if self.date_from.strip() and not self.date_to.strip():
                self.date_to = self.date_from
            self.limit = min(max(self.limit, 1), 20)
        if self.status == "input_required" and not self.message.strip():
            raise ValueError("input_required status requires a non-empty message field")
        return self
