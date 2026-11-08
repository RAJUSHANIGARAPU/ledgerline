"""TOTP (RFC 6238) enrolment and verification, encrypted secrets, hashed recovery codes."""
import base64
import hashlib
import hmac
import io
import re
import secrets

import pyotp
import segno
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from ledgerline.accounts import clock
from ledgerline.config import get_settings
from ledgerline.models import RecoveryCode, UserTotp

STEP_SECONDS = 30
DRIFT_STEPS = 1
RECOVERY_CODE_COUNT = 10
_RECOVERY_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"
_DIGITS = re.compile(r"\d{6}")


def _derive(info: bytes) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=info).derive(
        get_settings().secret_key.encode())


def _fernet() -> Fernet:
    return Fernet(base64.urlsafe_b64encode(_derive(b"ledgerline-totp-secret-v1")))


def encrypt_secret(secret: str) -> str:
    return _fernet().encrypt(secret.encode()).decode()


def decrypt_secret(token: str) -> str:
    return _fernet().decrypt(token.encode()).decode()


def new_secret() -> str:
    return pyotp.random_base32()


def provisioning_uri(secret: str, email: str) -> str:
    return pyotp.TOTP(secret).provisioning_uri(name=email, issuer_name="Ledgerline")


def qr_data_uri(uri: str) -> str:
    buffer = io.BytesIO()
    segno.make(uri, error="m").save(buffer, kind="svg", scale=5, border=2)
    return "data:image/svg+xml;base64," + base64.b64encode(buffer.getvalue()).decode()


def current_step() -> int:
    return int(clock.now().timestamp()) // STEP_SECONDS


def normalise_code(code: str) -> str:
    return re.sub(r"[\s-]", "", code).lower()


def is_totp_code(code: str) -> bool:
    return bool(_DIGITS.fullmatch(normalise_code(code)))


def verify_code(session: Session, row: UserTotp, code: str) -> bool:
    """Accept a code from the current step +-1 that is newer than the last one used.

    The step is recorded with a conditional UPDATE, so a replayed code (also from two
    concurrent requests) is accepted at most once.
    """
    code = normalise_code(code)
    if not _DIGITS.fullmatch(code):
        return False
    totp = pyotp.TOTP(decrypt_secret(row.secret_enc), interval=STEP_SECONDS)
    now = current_step()
    matched = [step for step in range(now - DRIFT_STEPS, now + DRIFT_STEPS + 1)
               if hmac.compare_digest(totp.at(step * STEP_SECONDS), code)]
    step = max(matched, default=None)
    if step is None or step <= row.last_used_step:
        return False
    result = session.execute(
        update(UserTotp).where(UserTotp.user_id == row.user_id, UserTotp.last_used_step < step)
        .values(last_used_step=step))
    if result.rowcount != 1:
        return False
    row.last_used_step = step
    return True


def enabled(session: Session, user_id: int) -> UserTotp | None:
    row = session.get(UserTotp, user_id)
    return row if row is not None and row.enabled_at is not None else None


def _recovery_hash(code: str) -> str:
    return hmac.new(_derive(b"ledgerline-recovery-v1"), normalise_code(code).encode(),
                    hashlib.sha256).hexdigest()


def generate_recovery_codes(session: Session, user_id: int) -> list[str]:
    session.execute(delete(RecoveryCode).where(RecoveryCode.user_id == user_id))
    codes = []
    for _ in range(RECOVERY_CODE_COUNT):
        raw = "".join(secrets.choice(_RECOVERY_ALPHABET) for _ in range(16))
        code = "-".join(raw[i:i + 4] for i in range(0, 16, 4))
        codes.append(code)
        session.add(RecoveryCode(user_id=user_id, code_hash=_recovery_hash(code)))
    session.flush()
    return codes


def use_recovery_code(session: Session, user_id: int, code: str) -> bool:
    digest = _recovery_hash(code)
    row = session.scalar(select(RecoveryCode).where(
        RecoveryCode.user_id == user_id, RecoveryCode.code_hash == digest,
        RecoveryCode.used_at.is_(None)))
    if row is None or not hmac.compare_digest(row.code_hash, digest):
        return False
    result = session.execute(
        update(RecoveryCode).where(RecoveryCode.id == row.id, RecoveryCode.used_at.is_(None))
        .values(used_at=clock.now()))
    return result.rowcount == 1


def remaining_recovery_codes(session: Session, user_id: int) -> int:
    return len(list(session.scalars(select(RecoveryCode.id).where(
        RecoveryCode.user_id == user_id, RecoveryCode.used_at.is_(None)))))
