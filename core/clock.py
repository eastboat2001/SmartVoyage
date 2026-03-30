"""
功能：统一处理当前时间、时区和 now override。
作用：保证查询、评测和测试在同一时间语义下运行。
实现方式：基于时区配置和可选覆盖时间生成标准日期与时间载荷。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pytz


DEFAULT_TIMEZONE = "Asia/Shanghai"


def get_timezone_name(config: Any | None = None, override: str = "") -> str:
    if override.strip():
        return override.strip()
    if config is not None:
        value = getattr(config, "timezone_name", "")
        if isinstance(value, str) and value.strip():
            return value.strip()
    return DEFAULT_TIMEZONE


def get_timezone(config: Any | None = None, override: str = ""):
    return pytz.timezone(get_timezone_name(config, override=override))


def get_now(config: Any | None = None, override: str = "", timezone_name: str = "") -> datetime:
    tz = get_timezone(config, override=timezone_name)
    raw_override = override.strip()
    if not raw_override and config is not None:
        config_override = getattr(config, "now_override", "")
        if isinstance(config_override, str):
            raw_override = config_override.strip()

    if raw_override:
        normalized = raw_override.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is None:
            return tz.localize(parsed)
        return parsed.astimezone(tz)

    return datetime.now(tz)


def get_current_date_str(config: Any | None = None, override: str = "", timezone_name: str = "") -> str:
    return get_now(config, override=override, timezone_name=timezone_name).strftime("%Y-%m-%d")


def get_current_time_payload(config: Any | None = None, timezone_name: str = "", override: str = "") -> dict[str, str]:
    tz_name = get_timezone_name(config, override=timezone_name)
    now = get_now(config, override=override, timezone_name=tz_name)
    return {
        "timezone": tz_name,
        "current_time": now.strftime("%Y-%m-%d %H:%M:%S"),
        "current_date": now.strftime("%Y-%m-%d"),
        "weekday": now.strftime("%A"),
    }
