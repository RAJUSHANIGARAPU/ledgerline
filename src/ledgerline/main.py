from importlib import import_module
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from ledgerline.config import get_settings
from ledgerline.web import LoginRequired, TenantRequired

# Each module exposes `router`. Order sets nothing but readability.
MODULES = [
    "ledgerline.auth",
    "ledgerline.reports.routes",
    "ledgerline.ledger.routes",
    "ledgerline.invoicing.routes",
    "ledgerline.banking.routes",
    "ledgerline.vat.routes",
    "ledgerline.platform.routes",
    "ledgerline.inbox.routes",
    "ledgerline.mobile.routes",
    "ledgerline.onboarding.routes",
]

SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
        "frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
    ),
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "same-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="Ledgerline", docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.secret_key,
        session_cookie="ledgerline_session",
        max_age=settings.session_max_age_seconds,
        same_site="lax",
        https_only=settings.cookie_secure,
    )

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        for name, value in SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        return response

    @app.exception_handler(LoginRequired)
    async def to_login(request: Request, _exc: LoginRequired):
        if request.headers.get("hx-request"):
            return RedirectResponse("/login", 303, headers={"HX-Redirect": "/login"})
        return RedirectResponse("/login", 303)

    @app.exception_handler(TenantRequired)
    async def to_tenants(request: Request, _exc: TenantRequired):
        if request.headers.get("hx-request"):
            return RedirectResponse("/tenants", 303, headers={"HX-Redirect": "/tenants"})
        return RedirectResponse("/tenants", 303)

    @app.get("/healthz", include_in_schema=False)
    def healthz():
        return {"status": "ok"}

    app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")
    for module in MODULES:
        app.include_router(import_module(module).router)
    return app
