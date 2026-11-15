from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from ledgerline.config import get_settings
from ledgerline.i18n import CATALOGUES, COOKIE, COOKIE_MAX_AGE
from ledgerline.web import verify_csrf

router = APIRouter()


def _local_path(target: str) -> str:
    """Only a path on this site: no scheme, no protocol-relative or backslash tricks."""
    if target.startswith("/") and not target.startswith("//") and "\\" not in target:
        return target
    return "/"


@router.post("/language", dependencies=[Depends(verify_csrf)])
def choose_language(request: Request, lang: Annotated[str, Form()],
                    next: Annotated[str, Form()] = "/"):
    if lang not in CATALOGUES:
        raise HTTPException(400, "unsupported language")
    response = RedirectResponse(_local_path(next), 303)
    response.set_cookie(COOKIE, lang, max_age=COOKIE_MAX_AGE, samesite="lax",
                        secure=get_settings().cookie_secure)
    return response
