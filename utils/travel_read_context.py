from __future__ import annotations

import re
from typing import Literal


TravelReadKind = Literal["weather", "ticket", "time"]

TRAVEL_READ_KIND_PATTERN = re.compile(
    r"\[TRAVEL_READ_KIND\](?P<kind>weather|ticket|time)\[/TRAVEL_READ_KIND\]",
    re.DOTALL,
)


def with_travel_read_kind(query: str, kind: TravelReadKind) -> str:
    return f"[TRAVEL_READ_KIND]{kind}[/TRAVEL_READ_KIND]\n{query}".strip()


def extract_travel_read_kind(query: str) -> TravelReadKind | None:
    match = TRAVEL_READ_KIND_PATTERN.search(query)
    if not match:
        return None
    return match.group("kind")  # type: ignore[return-value]


def strip_travel_read_kind(query: str) -> str:
    return TRAVEL_READ_KIND_PATTERN.sub("", query).strip()
