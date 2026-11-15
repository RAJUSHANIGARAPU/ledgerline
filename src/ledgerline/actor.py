"""Who a write is attributed to: a user, the worker, a CLI command, or nobody known."""
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

USER, WORKER, SYSTEM, UNKNOWN = "user", "worker", "system", "unknown"
ACTOR_TYPES = (USER, WORKER, SYSTEM, UNKNOWN)

_background: ContextVar[str | None] = ContextVar("ledgerline_actor", default=None)


@contextmanager
def acting_as(kind: str) -> Iterator[None]:
    """Entrypoints (worker loop, CLI commands) wrap their run in this."""
    if kind not in (WORKER, SYSTEM):
        raise ValueError(f"not a background actor: {kind!r}")
    token = _background.set(kind)
    try:
        yield
    finally:
        _background.reset(token)


def actor_type(user_id: int | None) -> str:
    """A user id always means a user; otherwise the entrypoint's actor, else unknown."""
    if user_id is not None:
        return USER
    return _background.get() or UNKNOWN
