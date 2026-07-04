"""Simple in-memory rate limiter for auth and scan endpoints."""
import time
from collections import defaultdict
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

RATE_LIMIT_PATHS = {
    "/api/auth/login": (5, 60),
    "/api/lp/scan": (10, 60),
    "/api/arbitrage/scan": (5, 60),
    "/api/industry/scan": (5, 60),
}

_MAX_BUCKETS = 10000
_buckets: dict[str, list[float]] = defaultdict(list)
_last_cleanup = 0.0


def _cleanup():
    """Remove stale buckets to prevent unbounded memory growth."""
    global _last_cleanup
    now = time.time()
    if now - _last_cleanup < 60:
        return
    _last_cleanup = now
    stale = [k for k, v in _buckets.items() if not v or now - v[-1] > 120]
    for k in stale:
        del _buckets[k]


class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        _cleanup()

        path = request.url.path
        limit_config = RATE_LIMIT_PATHS.get(path)
        if not limit_config:
            return await call_next(request)

        max_requests, window = limit_config
        client_ip = request.client.host if request.client else "unknown"
        key = f"{client_ip}:{path}"
        now = time.time()

        # Enforce max bucket count
        if key not in _buckets and len(_buckets) >= _MAX_BUCKETS:
            return await call_next(request)

        _buckets[key] = [t for t in _buckets[key] if now - t < window]

        if len(_buckets[key]) >= max_requests:
            return JSONResponse(
                {"error": "Rate limit exceeded"},
                status_code=429,
                headers={"Retry-After": str(window)},
            )

        _buckets[key].append(now)
        return await call_next(request)
