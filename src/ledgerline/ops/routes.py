import threading
import time
from collections import defaultdict, deque

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ledgerline.config import get_settings
from ledgerline.ops import health

router = APIRouter()
WINDOW_SECONDS = 60.0
MAX_TRACKED_CLIENTS = 1024


class RateLimiter:
    """Sliding window per client address, in memory (one app process per container)."""

    def __init__(self, per_minute: int) -> None:
        self.per_minute = per_minute
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, client: str, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        with self._lock:
            if len(self._hits) > MAX_TRACKED_CLIENTS:
                self._hits.clear()
            hits = self._hits[client]
            while hits and now - hits[0] >= WINDOW_SECONDS:
                hits.popleft()
            if len(hits) >= self.per_minute:
                return False
            hits.append(now)
            return True


_limiter: RateLimiter | None = None


def limiter() -> RateLimiter:
    global _limiter
    if _limiter is None:
        _limiter = RateLimiter(get_settings().health_rate_limit_per_minute)
    return _limiter


@router.get("/healthz/deep", include_in_schema=False)
def healthz_deep(request: Request):
    client = request.client.host if request.client else "unknown"
    if not limiter().allow(client):
        return JSONResponse({"status": "rate_limited"}, 429, headers={"Retry-After": "60"})
    report = health.run_checks(get_settings())
    status = 200 if report["status"] == "ok" else 503
    return JSONResponse(report, status, headers={"Cache-Control": "no-store"})
