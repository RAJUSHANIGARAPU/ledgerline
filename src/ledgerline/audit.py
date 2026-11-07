from sqlalchemy.orm import Session

from ledgerline.models import AuditEvent


def record(
    session: Session,
    action: str,
    *,
    user_id: int | None = None,
    administration_id: int | None = None,
    entity_type: str | None = None,
    entity_id: int | None = None,
    details: dict | None = None,
) -> None:
    session.add(
        AuditEvent(
            action=action,
            user_id=user_id,
            administration_id=administration_id,
            entity_type=entity_type,
            entity_id=entity_id,
            details=details,
        )
    )
