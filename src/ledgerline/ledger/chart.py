"""Default chart of accounts for a Dutch sole trader or small BV.

The codes are our own. The RGS reference column is left empty until the RGS
dataset's reuse terms are confirmed; `rgs_code` can be filled in per account.
"""
from sqlalchemy.orm import Session

from ledgerline.models import Account, AccountType, Administration, SystemRole

A, L, E, R, X = (AccountType.ASSET, AccountType.LIABILITY, AccountType.EQUITY,
                 AccountType.REVENUE, AccountType.EXPENSE)

# (code, name, type, system role, default VAT code)
DEFAULT_CHART: list[tuple[str, str, AccountType, SystemRole | None, str | None]] = [
    ("0200", "Equipment and furniture", A, None, None),
    ("0290", "Accumulated depreciation", A, None, None),
    ("0500", "Owner's equity", E, None, None),
    ("0510", "Owner's drawings", E, None, None),
    ("0520", "Owner's contributions", E, None, None),
    ("0590", "Retained earnings", E, SystemRole.RETAINED_EARNINGS, None),
    ("1000", "Cash", A, SystemRole.CASH, None),
    ("1100", "Bank", A, SystemRole.BANK, None),
    ("1300", "Accounts receivable", A, SystemRole.DEBTORS, None),
    ("1310", "Card payments to receive", A, SystemRole.CARD_CLEARING, None),
    ("1320", "Vouchers and other receipts to settle", A, SystemRole.TAKINGS_OTHER, None),
    ("1330", "Uber Eats to settle", A, SystemRole.UBER_EATS_CLEARING, None),
    ("1340", "Thuisbezorgd to settle", A, SystemRole.THUISBEZORGD_CLEARING, None),
    ("1350", "Deposits paid (emballage)", A, None, "I0"),
    ("1400", "VAT receivable (input VAT)", A, SystemRole.VAT_RECEIVABLE, None),
    ("1500", "Suspense", A, SystemRole.SUSPENSE, None),
    ("1600", "Accounts payable", L, SystemRole.CREDITORS, None),
    ("1700", "VAT payable 21%", L, SystemRole.VAT_PAYABLE_HIGH, None),
    ("1710", "VAT payable 9%", L, SystemRole.VAT_PAYABLE_LOW, None),
    ("1720", "VAT payable, reverse charge", L, SystemRole.VAT_PAYABLE_REVERSE, None),
    ("1790", "VAT settlement", L, SystemRole.VAT_SETTLEMENT, None),
    ("1800", "Other liabilities", L, None, None),
    ("4000", "Rent and premises", X, None, "I21"),
    ("4100", "Travel and vehicle", X, None, "I21"),
    ("4200", "Office supplies", X, None, "I21"),
    ("4300", "Software and subscriptions", X, None, "I21"),
    ("4400", "Phone and internet", X, None, "I21"),
    ("4500", "Training and books", X, None, "I9"),
    ("4600", "Marketing", X, None, "I21"),
    ("4700", "Accounting and advice", X, None, "I21"),
    ("4800", "Bank charges", X, SystemRole.BANK_COSTS, "I0"),
    ("4610", "Delivery platform fees", X, SystemRole.PLATFORM_FEES, "I21"),
    ("4900", "Other expenses", X, None, "I21"),
    ("4950", "Depreciation", X, None, "I0"),
    ("7000", "Cost of goods sold", X, None, "I21"),
    ("8000", "Revenue 21%", R, None, "V21"),
    ("8010", "Revenue 9%", R, None, "V9"),
    ("8020", "Revenue 0% / reverse charge", R, None, "VRV"),
    ("8030", "Revenue EU", R, None, "VEU"),
    ("8040", "Revenue export", R, None, "VEX"),
    ("8050", "Revenue exempt / KOR", R, None, "VVR"),
    ("8900", "Other income", R, None, None),
]


def seed_chart(session: Session, administration: Administration) -> list[Account]:
    accounts = [
        Account(
            administration_id=administration.id,
            code=code,
            name=name,
            type=kind,
            system_role=role,
            default_vat_code=vat,
        )
        for code, name, kind, role, vat in DEFAULT_CHART
    ]
    session.add_all(accounts)
    session.flush()
    return accounts


def create_administration(session: Session, **fields) -> Administration:
    administration = Administration(**fields)
    session.add(administration)
    session.flush()
    seed_chart(session, administration)
    return administration
