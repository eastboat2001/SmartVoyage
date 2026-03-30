"""
功能：定义订单动作标签的注入、提取和清理逻辑。
作用：在编排层和订单子代理之间显式传递订单动作，减少重复分类。
实现方式：通过正则包装和解析内嵌标签字符串。
"""

from __future__ import annotations

import re
from typing import Literal


OrderActionKind = Literal["query_orders", "cancel_order", "change_order", "create_order"]

ORDER_ACTION_PATTERN = re.compile(
    r"\[ORDER_ACTION\](?P<action>query_orders|cancel_order|change_order|create_order)\[/ORDER_ACTION\]",
    re.DOTALL,
)


def with_order_action(query: str, action: OrderActionKind) -> str:
    return f"[ORDER_ACTION]{action}[/ORDER_ACTION]\n{query}".strip()


def extract_order_action(query: str) -> OrderActionKind | None:
    match = ORDER_ACTION_PATTERN.search(query)
    if not match:
        return None
    return match.group("action")  # type: ignore[return-value]


def strip_order_action(query: str) -> str:
    return ORDER_ACTION_PATTERN.sub("", query).strip()
