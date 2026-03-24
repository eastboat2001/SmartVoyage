from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from utils.metrics import create_metrics


AgentState = Literal["completed", "input_required", "failed"]


class LocalAgentRequest(BaseModel):
    text: str
    conversation_history: str = ""
    request_id: str = ""
    now_override: str = ""
    metrics: dict[str, Any] = Field(default_factory=create_metrics)


class LocalAgentResponse(BaseModel):
    state: AgentState
    text: str
    pending_order_context: dict[str, Any] = Field(default_factory=dict)
    data: dict[str, Any] = Field(default_factory=dict)
    meta: dict[str, Any] = Field(default_factory=dict)
