from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


SupportedIntent = Literal[
    "weather",
    "flight",
    "train",
    "hotel",
    "order",
    "my_orders",
    "cancel_order",
    "change_order",
    "travel_plan",
    "attraction",
    "out_of_scope",
]

OrderAction = Literal["create_order", "query_orders", "cancel_order", "change_order"]
HotelAction = Literal["query_hotels", "query_hotel_orders", "create_hotel_order"]


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


class PendingContextPayload(BaseModel):
    domain: Literal["order", "hotel"]
    action: str
    missing_slots: list[str] = Field(default_factory=list)
    slots: dict[str, Any] = Field(default_factory=dict)
    original_query: str = ""


class OrderWorkflowExtractionResult(BaseModel):
    domain: Literal["order"] = "order"
    action: OrderAction
    query_order_type: Literal["", "transport", "train", "flight", "hotel"] = ""
    order_type: Literal["train", "flight", ""] = ""
    departure_date: str = ""
    departure_city: str = ""
    arrival_city: str = ""
    transport_no: str = ""
    ticket_type: str = ""
    quantity: int = 1
    new_departure_date: str = ""
    new_transport_no: str = ""
    new_ticket_type: str = ""
    missing_slots: list[str] = Field(default_factory=list)
    follow_up_message: str = ""
    is_complete: bool = False

    @model_validator(mode="after")
    def validate_payload(self):
        if self.quantity <= 0:
            self.quantity = 1
        if self.is_complete and self.missing_slots:
            raise ValueError("complete extraction should not contain missing_slots")
        if not self.is_complete and not self.follow_up_message.strip():
            raise ValueError("incomplete extraction requires follow_up_message")
        if self.action == "change_order" and self.is_complete:
            has_new_target = any(
                [
                    self.new_departure_date.strip(),
                    self.new_transport_no.strip(),
                    self.new_ticket_type.strip(),
                ]
            )
            if not has_new_target:
                raise ValueError("change_order requires at least one new target field when complete")
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


class HotelWorkflowExtractionResult(BaseModel):
    domain: Literal["hotel"] = "hotel"
    action: HotelAction
    city: str = ""
    hotel_name: str = ""
    room_type: str = ""
    check_in_date: str = ""
    nights: int = 1
    rooms: int = 1
    missing_slots: list[str] = Field(default_factory=list)
    follow_up_message: str = ""
    is_complete: bool = False

    @model_validator(mode="after")
    def validate_payload(self):
        if self.nights <= 0:
            self.nights = 1
        if self.rooms <= 0:
            self.rooms = 1
        if self.is_complete and self.missing_slots:
            raise ValueError("complete extraction should not contain missing_slots")
        if not self.is_complete and not self.follow_up_message.strip():
            raise ValueError("incomplete extraction requires follow_up_message")
        return self


class HotelSqlResult(BaseModel):
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
