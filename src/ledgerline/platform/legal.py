from fastapi import APIRouter, Request

from ledgerline.web import render

router = APIRouter(prefix="/legal")

PAGES = {
    "privacy": "Privacy notice",
    "terms": "Terms of use",
    "cookies": "Cookie notice",
    "disclaimer": "Disclaimer",
}


def _page(request: Request, slug: str):
    return render(request, "legal/page.html",
                  {"title": PAGES[slug], "partial": f"legal/_{slug}.html", "pages": PAGES})


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
