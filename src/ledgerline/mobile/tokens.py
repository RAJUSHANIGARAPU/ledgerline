"""Upload-only API tokens for the phone shortcut. Only a SHA-256 of each token is stored."""

import hashlib
import secrets
import threading
import time
from collections import defaultdict, deque
from datetime import UTC, datetime

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from ledgerline import audit
from ledgerline.db import set_tenant
from ledgerline.models import WRITE_ROLES, ApiToken, Membership, User

PREFIX = "ll_"
RATE_LIMIT = 30
RATE_WINDOW_SECONDS = 60.0

FAILURE_LIMIT = 20
FAILURE_WINDOW_SECONDS = 600.0
MAX_TRACKED_CLIENTS = 10_000

_hits: dict[int, deque[float]] = defaultdict(deque)
_failures: dict[str, deque[float]] = defaultdict(deque)
_lock = threading.Lock()


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_token(
    session: Session, user: User, administration_id: int, name: str
) -> tuple[ApiToken, str]:
    token = PREFIX + secrets.token_urlsafe(32)
    row = ApiToken(
        user_id=user.id,
        administration_id=administration_id,
        name=name.strip()[:60] or "Phone",
        token_prefix=token[:8],
        token_sha256=hash_token(token),
    )
    session.add(row)
    session.flush()
    audit.record(
        session,
        "api_token.create",
        user_id=user.id,
        administration_id=administration_id,
        entity_type="api_token",
        entity_id=row.id,
        details={"name": row.name},
    )
    return row, token


def revoke_token(
    session: Session, user: User, administration_id: int, token_id: int
) -> ApiToken | None:
    row = session.scalar(
        select(ApiToken).where(
            ApiToken.id == token_id, ApiToken.administration_id == administration_id
        )
    )
    if row is None:
        return None
    if row.revoked_at is None:
        row.revoked_at = datetime.now(UTC)
        audit.record(
            session,
            "api_token.revoke",
            user_id=user.id,
            administration_id=administration_id,
            entity_type="api_token",
            entity_id=row.id,
            details={"name": row.name},
        )
    return row


def authenticate(session: Session, token: str) -> ApiToken | None:
    """Find the token by its hash and put the session in its tenant.

    None when unknown, revoked, the user is inactive or no longer writes in that tenant.
    """
    if not token.startswith(PREFIX) or len(token) > 200:
        return None
    digest = hash_token(token)
    tenant_id = session.scalar(text("SELECT ledgerline_tenant_of_api_token(:sha)"),
                               {"sha": digest})
    if tenant_id is None:
        return None
    set_tenant(session, tenant_id)
    row = session.scalar(select(ApiToken).where(ApiToken.token_sha256 == digest))
    if row is None or row.revoked_at is not None:
        return None
    user = session.get(User, row.user_id)
    if user is None or not user.is_active:
        return None
    role = session.scalar(select(Membership.role).where(
        Membership.user_id == user.id, Membership.tenant_id == tenant_id,
        Membership.status == "active"))
    return row if role in WRITE_ROLES else None


def allow_request(token_id: int) -> bool:
    """Sliding-window limiter, in memory per process."""
    now = time.monotonic()
    with _lock:
        hits = _hits[token_id]
        while hits and now - hits[0] > RATE_WINDOW_SECONDS:
            hits.popleft()
        if len(hits) >= RATE_LIMIT:
            return False
        hits.append(now)
        return True


def _recent_failures(client: str, now: float) -> deque[float]:
    failures = _failures[client]
    while failures and now - failures[0] > FAILURE_WINDOW_SECONDS:
        failures.popleft()
    return failures


def too_many_failures(client: str) -> bool:
    """True when this client address failed token authentication too often recently."""
    with _lock:
        failures = _recent_failures(client, time.monotonic())
        if not failures:
            _failures.pop(client, None)
        return len(failures) >= FAILURE_LIMIT


def record_failure(client: str) -> None:
    now = time.monotonic()
    with _lock:
        if len(_failures) >= MAX_TRACKED_CLIENTS and client not in _failures:
            for key in [k for k in _failures if not _recent_failures(k, now)]:
                del _failures[key]
            if len(_failures) >= MAX_TRACKED_CLIENTS:
                return
        _recent_failures(client, now).append(now)


def reset_rate_limits() -> None:
    with _lock:
        _hits.clear()
        _failures.clear()
