from typing import Literal

from pydantic import BaseModel, Field, model_validator


SupportedIntent = Literal[
    "weather",
    "flight",
    "train",
    "order",
    "my_orders",
    "cancel_order",
    "change_order",
    "travel_plan",
    "attraction",
    "out_of_scope",
]


class IntentRecognitionResult(BaseModel):
    intents: list[SupportedIntent] = Field(default_factory=list)
    user_queries: dict[str, str] = Field(default_factory=dict)
    follow_up_message: str = ""


class TravelPlanResult(BaseModel):
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


class WeatherSqlResult(BaseModel):
    status: Literal["sql", "input_required"]
    sql: str = ""
    message: str = ""

    @model_validator(mode="after")
    def validate_payload(self):
        if self.status == "sql" and not self.sql.strip():
            raise ValueError("sql status requires a non-empty sql field")
        if self.status == "input_required" and not self.message.strip():
            raise ValueError("input_required status requires a non-empty message field")
        return self


class TicketSqlResult(BaseModel):
    status: Literal["sql", "input_required"]
    type: Literal["train", "flight"] | None = None
    sql: str = ""
    message: str = ""

    @model_validator(mode="after")
    def validate_payload(self):
        if self.status == "sql":
            if self.type is None:
                raise ValueError("sql status requires a ticket type")
            if not self.sql.strip():
                raise ValueError("sql status requires a non-empty sql field")
        if self.status == "input_required" and not self.message.strip():
            raise ValueError("input_required status requires a non-empty message field")
        return self
