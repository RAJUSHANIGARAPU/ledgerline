from fastapi import APIRouter, Request

from ledgerline.config import get_settings
from ledgerline.web import render

router = APIRouter(prefix="/legal")

PAGES = {
    "privacy": "Privacy notice",
    "terms": "Terms of use",
    "cookies": "Cookie notice",
    "disclaimer": "Disclaimer",
}

_PLACEHOLDERS = {
    "legal_name": "[OPERATOR NAME]",
    "kvk_number": "[KVK NUMBER]",
    "address": "[ADDRESS]",
    "contact_email": "[PRIVACY CONTACT EMAIL]",
}


def operator_context() -> dict:
    """The operator's identity for the legal pages; an unset field shows its old placeholder, so
    a half-configured installation is visibly incomplete (and production health fails)."""
    identity = get_settings().operator_identity
    return {name: getattr(identity, name) or placeholder
            for name, placeholder in _PLACEHOLDERS.items()}


def _page(request: Request, slug: str):
    return render(request, "legal/page.html",
                  {"title": PAGES[slug], "partial": f"legal/_{slug}.html", "pages": PAGES,
                   "operator": operator_context()})


@router.get("/privacy")
def privacy(request: Request):
    return _page(request, "privacy")


@router.get("/terms")
def terms(request: Request):
    return _page(request, "terms")


@router.get("/cookies")
def cookies(request: Request):
    return _page(request, "cookies")


@router.get("/disclaimer")
def disclaimer(request: Request):
    return _page(request, "disclaimer")
