from __future__ import annotations

import time
from contextlib import contextmanager
from typing import Any


METRIC_KEYS = ("phase_timings_ms", "llm_call_count", "tool_call_count", "cache_hits", "cache_misses")


def create_metrics() -> dict[str, Any]:
    return {
        "phase_timings_ms": {},
        "llm_call_count": 0,
        "tool_call_count": 0,
        "cache_hits": 0,
        "cache_misses": 0,
    }


def ensure_metrics(metrics: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = metrics if isinstance(metrics, dict) else {}
    payload.setdefault("phase_timings_ms", {})
    payload.setdefault("llm_call_count", 0)
    payload.setdefault("tool_call_count", 0)
    payload.setdefault("cache_hits", 0)
    payload.setdefault("cache_misses", 0)
    return payload


def clone_metrics(metrics: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = ensure_metrics(metrics)
    return {
        "phase_timings_ms": dict(payload.get("phase_timings_ms", {})),
        "llm_call_count": int(payload.get("llm_call_count", 0) or 0),
        "tool_call_count": int(payload.get("tool_call_count", 0) or 0),
        "cache_hits": int(payload.get("cache_hits", 0) or 0),
        "cache_misses": int(payload.get("cache_misses", 0) or 0),
    }


def increment_metric(metrics: dict[str, Any] | None, key: str, amount: int = 1) -> dict[str, Any]:
    payload = ensure_metrics(metrics)
    payload[key] = int(payload.get(key, 0) or 0) + amount
    return payload


def record_phase_time(metrics: dict[str, Any] | None, phase: str, elapsed_ms: float) -> dict[str, Any]:
    payload = ensure_metrics(metrics)
    timings = payload.setdefault("phase_timings_ms", {})
    timings[phase] = round(float(timings.get(phase, 0.0) or 0.0) + float(elapsed_ms), 2)
    return payload


def merge_metrics(base: dict[str, Any] | None, incoming: dict[str, Any] | None) -> dict[str, Any]:
    target = ensure_metrics(base)
    source = ensure_metrics(incoming)
    for phase, duration in source.get("phase_timings_ms", {}).items():
        record_phase_time(target, str(phase), float(duration or 0.0))
    for key in ("llm_call_count", "tool_call_count", "cache_hits", "cache_misses"):
        target[key] = int(target.get(key, 0) or 0) + int(source.get(key, 0) or 0)
    return target


@contextmanager
def track_phase(metrics: dict[str, Any] | None, phase: str):
    start = time.perf_counter()
    try:
        yield ensure_metrics(metrics)
    finally:
        record_phase_time(metrics, phase, (time.perf_counter() - start) * 1000)
