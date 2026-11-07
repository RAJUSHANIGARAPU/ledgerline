from sqlalchemy import select

from ledgerline.models import Account, Administration, Contact, ContactKind


def account(session, admin: Administration, code: str) -> Account:
    return session.scalar(
        select(Account).where(Account.administration_id == admin.id, Account.code == code)
    )


def contact(session, admin: Administration, name="Klant BV", kind=ContactKind.CUSTOMER, **fields):
    c = Contact(administration_id=admin.id, name=name, kind=kind, **fields)
    session.add(c)
    session.flush()
    return c
