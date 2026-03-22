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
