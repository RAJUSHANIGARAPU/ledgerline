"""In-app feedback form for owners and bookkeepers. The text is stored exactly as typed."""
from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from ledgerline import audit
from ledgerline.accounts import ratelimit
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
    error, status = None, 400
    if not message.strip():
        error = "Write your feedback first."
    elif len(message) > MAX_LENGTH:
        error = f"Feedback is limited to {MAX_LENGTH} characters."
    elif "\x00" in message:
        error = "Feedback cannot contain null characters."
    elif not ratelimit.feedback_by_user.hit(str(user.id)):
        error, status = "Too many messages. Try again later.", 429
    if error:
        flash(request, error, "error")
        return render(request, "settings/feedback.html",
                      {"admin": admin, "max_length": MAX_LENGTH, "active_nav": "feedback",
                       "message": message[:MAX_LENGTH].replace("\x00", "")}, status_code=status)
    entry = FeedbackEntry(administration_id=admin.id, user_id=user.id, body=message)
    session.add(entry)
    session.flush()
    audit.record(session, "feedback.submit", user_id=user.id, administration_id=admin.id,
                 entity_type="feedback", entity_id=entry.id)
    session.commit()
    flash(request, "Thank you, your feedback was sent.")
    return RedirectResponse("/feedback", 303)
