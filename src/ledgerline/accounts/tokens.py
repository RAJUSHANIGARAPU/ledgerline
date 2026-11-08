"""Single-use invite and reset tokens. The database holds only sha256(token)."""
import hashlib
import hmac
import secrets
from datetime import timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ledgerline.accounts import clock
from ledgerline.models import AuthToken

INVITE_TTL = timedelta(days=7)
RESET_TTL = timedelta(hours=1)
TTL = {"invite": INVITE_TTL, "reset": RESET_TTL}


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def issue(session: Session, user_id: int, purpose: str, membership_id: int | None = None) -> str:
    token = secrets.token_urlsafe(32)
    session.add(AuthToken(user_id=user_id, membership_id=membership_id, purpose=purpose,
                          token_hash=hash_token(token), expires_at=clock.now() + TTL[purpose]))
    session.flush()
    return token


def lookup(session: Session, token: str, purpose: str) -> AuthToken | None:
    """The live token row for this purpose, or None (unknown, tampered, used or expired)."""
    digest = hash_token(token)
    row = session.scalar(select(AuthToken).where(AuthToken.token_hash == digest))
    if row is None or not hmac.compare_digest(row.token_hash, digest):
        return None
    if row.purpose != purpose or row.used_at is not None or row.expires_at <= clock.now():
        return None
    return row


def consume(session: Session, row: AuthToken) -> bool:
    """Mark the token used; False when another request already did (race-safe)."""
    result = session.execute(
        update(AuthToken).where(AuthToken.id == row.id, AuthToken.used_at.is_(None))
        .values(used_at=clock.now())
    )
    return result.rowcount == 1


def invalidate_resets(session: Session, user_id: int) -> None:
    session.execute(
        update(AuthToken)
        .where(AuthToken.user_id == user_id, AuthToken.purpose == "reset",
               AuthToken.used_at.is_(None))
        .values(used_at=clock.now())
    )
