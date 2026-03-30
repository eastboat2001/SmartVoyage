"""
功能：封装 Redis JSON 缓存客户端。
作用：为 TravelRead 只读链路提供统一缓存读写和降级处理。
实现方式：在初始化阶段探测 Redis 可用性，并提供 get/set JSON 接口。
"""

from __future__ import annotations

import json
from typing import Any

from core.logging import logger

try:
    from redis import Redis
except ImportError:  # pragma: no cover
    Redis = None  # type: ignore[assignment]


class RedisCacheClient:
    def __init__(self, enabled: bool, redis_url: str):
        self.enabled = enabled and bool(redis_url.strip()) and Redis is not None
        self.redis_url = redis_url.strip()
        self._client = None
        if not self.enabled:
            return
        try:
            self._client = Redis.from_url(self.redis_url, decode_responses=True)
            self._client.ping()
        except Exception as exc:  # pragma: no cover
            logger.warning(f"Redis 缓存初始化失败，已降级为 no-cache: {exc}")
            self.enabled = False
            self._client = None

    def get_json(self, key: str) -> dict[str, Any] | None:
        if not self.enabled or self._client is None:
            return None
        try:
            raw = self._client.get(key)
            if not raw:
                return None
            data = json.loads(raw)
            return data if isinstance(data, dict) else None
        except Exception as exc:  # pragma: no cover
            logger.warning(f"Redis 读取失败: key={key}, error={exc}")
            return None

    def set_json(self, key: str, value: dict[str, Any], ttl_seconds: int) -> None:
        if not self.enabled or self._client is None:
            return
        try:
            self._client.setex(key, ttl_seconds, json.dumps(value, ensure_ascii=False))
        except Exception as exc:  # pragma: no cover
            logger.warning(f"Redis 写入失败: key={key}, error={exc}")
