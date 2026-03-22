from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


AgentState = Literal["completed", "input_required", "failed"]


class AgentInvokeRequest(BaseModel):
    text: str
    conversation_history: str = ""
    request_id: str = ""


class AgentInvokeResponse(BaseModel):
    state: AgentState
    text: str
    pending_order_context: dict[str, Any] = Field(default_factory=dict)
    data: dict[str, Any] = Field(default_factory=dict)
    meta: dict[str, Any] = Field(default_factory=dict)


class AgentSkillDescriptor(BaseModel):
    name: str
    description: str
    examples: list[str] = Field(default_factory=list)


class AgentMetadataResponse(BaseModel):
    name: str
    description: str
    version: str
    url: str
    skills: list[AgentSkillDescriptor] = Field(default_factory=list)
