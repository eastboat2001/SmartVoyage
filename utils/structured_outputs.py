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
HotelAction = Literal[
    "query_hotels",
    "query_hotel_orders",
    "create_hotel_order",
    "cancel_hotel_order",
    "change_hotel_order",
]


class IntentRecognitionResult(BaseModel):
    intents: list[SupportedIntent] = Field(default_factory=list)
    user_queries: dict[str, str] = Field(default_factory=dict)
    follow_up_message: str = ""


TravelPlanOrderIntent = Literal["", "none", "any", "train_if_suitable", "flight_if_suitable"]


class TravelPlanResult(BaseModel):
    transport_mode: Literal["train", "flight"]
    weather_brief: str = ""
    trip_status_summary: str
    recommendation_reason: str
    ticket_query: str = ""
    hotel_query: str = ""
    hotel_reason: str = ""

    @model_validator(mode="after")
    def validate_payload(self):
        if not self.trip_status_summary.strip():
            raise ValueError("trip_status_summary is required")
        if not self.recommendation_reason.strip():
            raise ValueError("recommendation_reason is required")
        if self.hotel_query.strip() and not self.hotel_reason.strip():
            raise ValueError("hotel_reason is required when hotel_query is provided")
        return self


class PendingContextPayload(BaseModel):
    domain: Literal["order", "hotel", "travel_plan"]
    action: str
    missing_slots: list[str] = Field(default_factory=list)
    slots: dict[str, Any] = Field(default_factory=dict)
    original_query: str = ""


class TravelPlanWorkflowExtractionResult(BaseModel):
    domain: Literal["travel_plan"] = "travel_plan"
    action: Literal["plan_trip"] = "plan_trip"
    departure_city: str = ""
    arrival_city: str = ""
    travel_date: str = ""
    travel_date_text: str = ""
    stay_days: int = 0
    include_hotel: bool | None = None
    order_intent: TravelPlanOrderIntent = ""
    missing_slots: list[str] = Field(default_factory=list)
    follow_up_message: str = ""
    is_complete: bool = False

    @model_validator(mode="after")
    def validate_payload(self):
        if self.stay_days < 0:
            self.stay_days = 0
        if self.is_complete and self.missing_slots:
            raise ValueError("complete extraction should not contain missing_slots")
        if not self.is_complete and not self.follow_up_message.strip():
            raise ValueError("incomplete extraction requires follow_up_message")
        return self


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
    new_city: str = ""
    new_hotel_name: str = ""
    new_room_type: str = ""
    new_check_in_date: str = ""
    new_nights: int = 0
    missing_slots: list[str] = Field(default_factory=list)
    follow_up_message: str = ""
    is_complete: bool = False

    @model_validator(mode="after")
    def validate_payload(self):
        if self.nights <= 0:
            self.nights = 1
        if self.rooms <= 0:
            self.rooms = 1
        if self.new_nights < 0:
            self.new_nights = 0
        if self.is_complete and self.missing_slots:
            raise ValueError("complete extraction should not contain missing_slots")
        if not self.is_complete and not self.follow_up_message.strip():
            raise ValueError("incomplete extraction requires follow_up_message")
        if self.action == "change_hotel_order" and self.is_complete:
            has_new_target = any(
                [
                    self.new_city.strip(),
                    self.new_hotel_name.strip(),
                    self.new_room_type.strip(),
                    self.new_check_in_date.strip(),
                    self.new_nights > 0,
                ]
            )
            if not has_new_target:
                raise ValueError("change_hotel_order requires at least one new target field when complete")
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
