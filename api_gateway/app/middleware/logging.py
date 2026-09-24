"""请求日志中间件:记录每个请求的耗时与状态码。"""

import time
import uuid
from collections.abc import Awaitable, Callable

from fastapi import Request, Response
from loguru import logger
from starlette.middleware.base import BaseHTTPMiddleware


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = uuid.uuid4().hex[:12]
        request.state.request_id = request_id
        start = time.perf_counter()
        response = await call_next(request)
        latency_ms = int((time.perf_counter() - start) * 1000)
        logger.info(
            "{} {} {} {}ms req={}",
            request.method,
            request.url.path,
            response.status_code,
            latency_ms,
            request_id,
        )
        response.headers["X-Request-ID"] = request_id
        return response
