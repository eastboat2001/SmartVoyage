from typing import Literal

from pydantic import BaseModel, Field, model_validator


SupportedIntent = Literal[
    "weather",
    "flight",
    "train",
    "concert",
    "order",
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
    type: Literal["train", "flight", "concert"] | None = None
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
