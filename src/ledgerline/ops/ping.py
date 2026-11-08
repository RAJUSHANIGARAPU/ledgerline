"""Dead-man's-switch pings (healthchecks.io style). Failures never reach the caller."""

import logging
import time
import urllib.request
from collections.abc import Callable

log = logging.getLogger("ledgerline.ops")
TIMEOUT_SECONDS = 5


def send(url: str, suffix: str = "") -> bool:
    if not url.startswith(("https://", "http://")):
        return False
    target = url.rstrip("/") + suffix if suffix else url
    try:
        with urllib.request.urlopen(target, timeout=TIMEOUT_SECONDS) as response:  # noqa: S310
            response.read(1)
        return True
    except Exception as exc:
        log.warning("health ping failed: %s", type(exc).__name__)
        return False


class Pinger:
    def __init__(
        self,
        url: str,
        interval_seconds: float,
        clock: Callable[[], float] = time.monotonic,
        sender: Callable[[str], bool] = send,
    ) -> None:
        self.url = url
        self.interval = interval_seconds
        self.clock = clock
        self.sender = sender
        self._next = 0.0

    def tick(self) -> bool:
        """Ping when the interval has elapsed. The first call pings straight away."""
        if not self.url:
            return False
        now = self.clock()
        if now < self._next:
            return False
        self._next = now + self.interval
        return self.sender(self.url)
