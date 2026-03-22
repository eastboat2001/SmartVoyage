from __future__ import annotations

import time
from typing import Callable

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from create_logger import logger
from utils.request_context import clear_request_id, ensure_request_id, set_request_id


def install_common_middleware(app: FastAPI) -> None:
    @app.middleware("http")
    async def request_id_middleware(request: Request, call_next: Callable):
        request_id = request.headers.get("x-request-id", "").strip() or ensure_request_id()
        set_request_id(request_id)
        request.state.request_id = request_id
        start = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception as exc:
            logger.exception(f"[{request_id}] unhandled server error: {exc}")
            response = JSONResponse(
                status_code=500,
                content={"detail": "internal_server_error", "request_id": request_id},
            )
        duration_ms = round((time.perf_counter() - start) * 1000, 2)
        response.headers["x-request-id"] = request_id
        logger.info(
            f"[{request_id}] {request.method} {request.url.path} -> {response.status_code} ({duration_ms} ms)"
        )
        clear_request_id()
        return response
