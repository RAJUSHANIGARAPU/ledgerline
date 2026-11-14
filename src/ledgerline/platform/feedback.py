"""In-app feedback form for owners and bookkeepers. The text is stored exactly as typed."""
from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from ledgerline import audit
from ledgerline.models import WRITE_ROLES, FeedbackEntry
from ledgerline.web import AdminDep, OwnerDep, SessionDep, UserDep, flash, render

router = APIRouter()
MAX_LENGTH = 5000


@router.get("/feedback")
def feedback_form(request: Request, user: UserDep, admin: AdminDep):
    if user.role not in WRITE_ROLES:
        raise HTTPException(403, "read-only account")
    return render(request, "settings/feedback.html",
                  {"admin": admin, "max_length": MAX_LENGTH, "active_nav": "feedback"})


@router.post("/feedback")
def feedback_submit(request: Request, user: OwnerDep, admin: AdminDep, session: SessionDep,
                    message: str = Form("")):
    if not message.strip():
        flash(request, "Write your feedback first.", "error")
        return RedirectResponse("/feedback", 303)
    if len(message) > MAX_LENGTH:
        flash(request, f"Feedback is limited to {MAX_LENGTH} characters.", "error")
        return RedirectResponse("/feedback", 303)
    entry = FeedbackEntry(administration_id=admin.id, user_id=user.id, body=message)
    session.add(entry)
    session.flush()
    audit.record(session, "feedback.submit", user_id=user.id, administration_id=admin.id,
                 entity_type="feedback", entity_id=entry.id)
    session.commit()
    flash(request, "Thank you, your feedback was sent.")
    return RedirectResponse("/feedback", 303)
