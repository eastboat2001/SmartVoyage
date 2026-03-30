"""
功能：维护请求级 request id 上下文。
作用：让日志、Web 请求和 agent 调用能关联到同一链路。
实现方式：基于 ContextVar 提供设置、获取、生成和清理接口。
"""

from __future__ import annotations

from contextvars import ContextVar
from uuid import uuid4


_request_id_var: ContextVar[str] = ContextVar("smartvoyage_request_id", default="")


def set_request_id(request_id: str) -> None:
    _request_id_var.set(request_id)


def get_request_id() -> str:
    return _request_id_var.get("")


def ensure_request_id() -> str:
    request_id = get_request_id()
    if request_id:
        return request_id
    request_id = f"req-{uuid4().hex[:12]}"
    set_request_id(request_id)
    return request_id


def clear_request_id() -> None:
    _request_id_var.set("")
