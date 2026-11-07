from fastapi import APIRouter

from ledgerline.platform import contacts, gdpr, legal, settings

router = APIRouter()
for module in (contacts, settings, gdpr, legal):
    router.include_router(module.router)
