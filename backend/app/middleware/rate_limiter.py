from __future__ import annotations

import time
from collections import defaultdict

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.config import get_settings

_COUNTS: dict[str, int] = defaultdict(int)


class RateLimiterMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        settings = get_settings()
        if settings.environment == "test":
            return await call_next(request)

        # 🔥 CRITICAL FIX: allow preflight requests
        if request.method == "OPTIONS":
            return await call_next(request)

        # optional skip paths
        if request.url.path in {"/health", "/docs", "/openapi.json"}:
            return await call_next(request)

        client_host = request.client.host if request.client else "unknown"
        minute = int(time.time() // 60)
        key = f"{client_host}:{minute}"
        for old_key in list(_COUNTS):
            try:
                if int(old_key.rsplit(":", 1)[1]) < minute - 1:
                    _COUNTS.pop(old_key, None)
            except ValueError:
                _COUNTS.pop(old_key, None)

        _COUNTS[key] += 1

        if _COUNTS[key] > settings.rate_limit_requests:
            return JSONResponse(
                status_code=429,
                content={"detail": f"Rate limit exceeded. Max {settings.rate_limit_requests} requests per minute."},
                headers={"Retry-After": "60"},
            )

        return await call_next(request)
