from fastapi import APIRouter

from ledgerline.platform import contacts, feedback, gdpr, legal, settings

router = APIRouter()
for module in (contacts, settings, gdpr, legal, feedback):
    router.include_router(module.router)
