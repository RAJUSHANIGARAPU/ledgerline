"""In-memory sliding-window limiters; per process, reset on restart (see docs/SECURITY.md)."""
import time
from collections import defaultdict, deque


class Limiter:
    def __init__(self, limit: int, window_seconds: int):
        self.limit = limit
        self.window = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def _trim(self, key: str) -> deque[float]:
        hits = self._hits[key]
        cutoff = time.monotonic() - self.window
        while hits and hits[0] < cutoff:
            hits.popleft()
        return hits

    def hit(self, key: str) -> bool:
        """Count one attempt; False when the key is over its limit (the attempt still counts)."""
        hits = self._trim(key)
        hits.append(time.monotonic())
        return len(hits) <= self.limit

    def blocked(self, key: str) -> bool:
        return len(self._trim(key)) >= self.limit

    def reset(self) -> None:
        self._hits.clear()


reset_by_ip = Limiter(10, 3600)
reset_by_email = Limiter(3, 3600)
invite_by_user = Limiter(30, 3600)
totp_failures = Limiter(10, 15 * 60)
password_failures_by_ip = Limiter(30, 15 * 60)


def reset_all() -> None:
    for limiter in (reset_by_ip, reset_by_email, invite_by_user, totp_failures,
                    password_failures_by_ip):
        limiter.reset()
