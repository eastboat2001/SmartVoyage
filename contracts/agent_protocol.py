"""
功能：定义本地 agent 之间的请求和响应协议。
作用：统一 Supervisor 与子代理的输入输出结构，便于测试和 metrics 透传。
实现方式：使用 Pydantic 模型约束文本、上下文和元信息字段。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from observability.metrics import create_metrics


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
