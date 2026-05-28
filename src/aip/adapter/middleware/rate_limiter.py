"""Token-bucket rate limiter middleware (CHUNK-9.0c)."""

from __future__ import annotations

import time
from collections import defaultdict
from typing import Dict

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response, JSONResponse

from aip.foundation.schemas import RateLimitConfig


class TokenBucketRateLimiter:
    def __init__(self, config: RateLimitConfig) -> None:
        self.config = config
        self._buckets: Dict[str, Dict] = defaultdict(lambda: {"tokens": float(config.burst_size), "last": time.time()})

    def _refill(self, key: str) -> None:
        bucket = self._buckets[key]
        now = time.time()
        elapsed = now - bucket["last"]
        refill_rate = self.config.requests_per_minute / 60.0
        bucket["tokens"] = min(
            float(self.config.burst_size),
            bucket["tokens"] + elapsed * refill_rate
        )
        bucket["last"] = now

    def allow_request(self, key: str) -> bool:
        if not self.config.enabled:
            return True
        self._refill(key)
        bucket = self._buckets[key]
        if bucket["tokens"] >= 1.0:
            bucket["tokens"] -= 1.0
            return True
        return False

    def get_remaining(self, key: str) -> int:
        self._refill(key)
        return int(self._buckets[key]["tokens"])


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, rate_limiter: TokenBucketRateLimiter, config: RateLimitConfig) -> None:
        super().__init__(app)
        self.rate_limiter = rate_limiter
        self.config = config

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if not self.config.enabled:
            return await call_next(request)

        # When model_budget_protection is True, don't reject read-only requests or health checks
        path = request.url.path
        if self.config.model_budget_protection:
            # Health checks are never rate-limited
            if path.startswith("/api/v1/health") or path == "/":
                return await call_next(request)
            # Read-only (GET) requests are not rate-limited when model_budget_protection is on
            if request.method == "GET":
                return await call_next(request)

        # Determine key (per-DEFINER preferred when auth present)
        auth_identity = getattr(request.state, "auth_identity", None)
        if auth_identity:
            key = f"definer:{auth_identity}"
        else:
            key = f"ip:{request.client.host if request.client else 'unknown'}"

        # Per-endpoint override (simple prefix match)
        rpm = self.config.requests_per_minute
        for pattern, override_rpm in self.config.per_endpoint_overrides.items():
            if path.startswith(pattern):
                rpm = override_rpm
                break

        if not self.rate_limiter.allow_request(key):
            return JSONResponse(
                {"detail": "Rate limit exceeded"},
                status_code=429,
                headers={"X-RateLimit-Remaining": "0"}
            )

        response = await call_next(request)
        remaining = self.rate_limiter.get_remaining(key)
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        return response
