# ruff: noqa: E501
"""Independent model of the expected Dutch bookkeeping results.

Written from the accounting rules, not from the application code: nothing here imports
ledgerline. Money is integer cents, maths is Decimal.

Rules modelled (belastingdienst.nl rules as documented in docs/COMPLIANCE_NL.md):
- Line amount = quantity x unit price, rounded half-up to the cent.
- Sales VAT is computed once per invoice per VAT code on the summed net amount, half-up.
- Purchase VAT is the amount stated by the supplier (default: rate x line net, per line).
  Self-assessed (reverse-charge) VAT is computed per code on the summed net amount.
- A credit note mirrors the invoice exactly (every amount negated).
- VAT return: sales and self-assessed VAT per rubriek, deductible VAT in 5b; the form takes
  whole euros: bases and owed VAT rounded down, deductible VAT rounded up.
"""
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

ASSET, LIABILITY, EQUITY, REVENUE, EXPENSE = "asset", "liability", "equity", "revenue", "expense"

TYPE_BY_PREFIX = {"0": ASSET, "1": ASSET, "4": EXPENSE, "7": EXPENSE, "8": REVENUE}
LIABILITY_CODES = {"1600", "1700", "1710", "1720", "1790", "1800"}
EQUITY_CODES = {"0500", "0510", "0520", "0590"}

BANK, DEBTORS, CREDITORS, VAT_RECEIVABLE = "1100", "1300", "1600", "1400"
VAT_HIGH, VAT_LOW, VAT_REVERSE = "1700", "1710", "1720"

RATE = {
    "V21": 21, "V9": 9, "V0": 0, "VRV": 0, "VEU": 0, "VEUG": 0, "VEX": 0, "VNEU": 0, "VVR": 0,
    "VKOR": 0, "I21": 21, "I9": 9, "I0": 0, "IRV21": 21, "IEU21": 21, "IEU9": 9, "INEU21": 21,
}
# Rubriek of the net amount (grondslag) per VAT code.
BASE_RUBRIEK = {
    "V21": "1a", "V9": "1b", "V0": "1e", "VRV": "1e", "VEU": "3b", "VEUG": "3b", "VEX": "3a",
    "IRV21": "2a", "IEU21": "4b", "IEU9": "4b", "INEU21": "4a",
}
# Rubriek where VAT owed is reported.
VAT_RUBRIEK = {
    "V21": "1a", "V9": "1b", "IRV21": "2a", "IEU21": "4b", "IEU9": "4b", "INEU21": "4a",
}
REVERSE_CHARGE = {"IRV21", "IEU21", "IEU9", "INEU21"}
ICP_CODES = {"VEU", "VEUG"}
RUBRIEKEN = ["1a", "1b", "1c", "1d", "1e", "2a", "3a", "3b", "3c", "4a", "4b"]


def account_type(code: str) -> str:
    if code in LIABILITY_CODES:
        return LIABILITY
    if code in EQUITY_CODES:
        return EQUITY
    return TYPE_BY_PREFIX[code[0]]


def half_up(value: Decimal) -> int:
    return int(value.quantize(Decimal(1), rounding=ROUND_HALF_UP))


def line_net(quantity: Decimal | str | int, price_cents: int) -> int:
    return half_up(Decimal(price_cents) * Decimal(str(quantity)))


def vat_of(net_cents: int, code: str) -> int:
    return half_up(Decimal(net_cents) * RATE[code] / 100)


def vat_in_gross(gross_cents: int, code: str) -> int:
    rate = Decimal(RATE[code])
    return half_up(Decimal(gross_cents) * rate / (100 + rate))


def euros_down(cents: int) -> int:
    return cents // 100


def euros_up(cents: int) -> int:
    return -((-cents) // 100)


def bucket(due: date, as_of: date) -> int:
    late = (as_of - due).days
    for index, limit in enumerate((0, 30, 60, 90)):
        if late <= limit:
            return index
    return 4


@dataclass
class Posting:
    on: date
    account: str
    net: int  # debit minus credit
    contact: str | None = None


@dataclass
class Fact:
    """One VAT-relevant amount: where it lands on the return and on the ICP."""
    on: date
    rubriek: str | None
    base: int = 0
    vat: int = 0
    deductible: int = 0
    icp_contact: str | None = None
    icp: int = 0


@dataclass
class Doc:
    key: str
    kind: str  # sales | purchase
    contact: str
    on: date
    due: date
    excl: int
    vat: int  # VAT that is part of what is owed to / by the other party
    incl: int
    status: str = "open"
    paid: int = 0
    payments: list = field(default_factory=list)
    credit_of: str | None = None
    postings: list[Posting] = field(default_factory=list)
    facts: list[Fact] = field(default_factory=list)


class Oracle:
    def __init__(self):
        self.postings: list[Posting] = []
        self.facts: list[Fact] = []
        self.docs: dict[str, Doc] = {}
        self.lines_of: dict[str, list] = {}

    # ------------------------------------------------------------- posting
    def post(self, on: date, account: str, net: int, contact: str | None = None):
        if net:
            self.postings.append(Posting(on, account, net, contact))

    # --------------------------------------------------------------- sales
    def sale(self, key, contact, on, due, lines, sign=1):
        """lines: (quantity, price_cents, vat_code, revenue_account)."""
        self.lines_of.setdefault(key, lines)
        nets = [(line_net(q, p) * sign, code, acct) for q, p, code, acct in lines]
        per_code: dict[str, int] = {}
        for net, code, _ in nets:
            per_code[code] = per_code.get(code, 0) + net
        vat = {code: vat_of(total, code) for code, total in per_code.items()}
        excl, vat_total = sum(n for n, _, _ in nets), sum(vat.values())
        incl = excl + vat_total
        doc = Doc(key, "sales", contact, on, due, excl, vat_total, incl)
        self.docs[key] = doc
        self.post(on, DEBTORS, incl, contact)
        for net, _, acct in nets:
            self.post(on, acct, -net, contact)
        for code, amount in vat.items():
            self.post(on, VAT_HIGH if RATE[code] == 21 else VAT_LOW, -amount)
        for code, total in per_code.items():
            self.facts.append(Fact(
                on, BASE_RUBRIEK.get(code), base=total, vat=vat[code],
                icp_contact=contact if code in ICP_CODES else None,
                icp=total if code in ICP_CODES else 0,
            ))
        return doc

    def credit_note(self, key, original_key, on):
        original = self.docs[original_key]
        lines = self.lines_of[original_key]
        note = self.sale(key, original.contact, on, on, lines, sign=-1)
        note.credit_of = original_key
        original.status = "cancelled"
        return note

    # ----------------------------------------------------------- purchases
    def purchase(self, key, contact, on, due, lines):
        """lines: (expense_account, net_cents, vat_code, stated_vat_cents or None)."""
        per_code_net: dict[str, int] = {}
        per_code_vat: dict[str, int] = {}
        owed_vat = 0
        for acct, net, code, _stated in lines:
            per_code_net[code] = per_code_net.get(code, 0) + net
            self.post(on, acct, net)
        for code, total in per_code_net.items():
            if code in REVERSE_CHARGE:
                per_code_vat[code] = vat_of(total, code)
            else:
                per_code_vat[code] = sum(
                    (s if s is not None else vat_of(n, code))
                    for _, n, c, s in lines if c == code
                )
                owed_vat += per_code_vat[code]
        excl = sum(per_code_net.values())
        incl = excl + owed_vat
        doc = Doc(key, "purchase", contact, on, due, excl, owed_vat, incl)
        self.docs[key] = doc
        for code, vat in per_code_vat.items():
            self.post(on, VAT_RECEIVABLE, vat)
            if code in REVERSE_CHARGE:
                self.post(on, VAT_REVERSE, -vat)
            self.facts.append(Fact(
                on, BASE_RUBRIEK.get(code), base=per_code_net[code],
                vat=vat if code in REVERSE_CHARGE else 0, deductible=vat,
            ))
        self.post(on, CREDITORS, -incl, contact)
        return doc

    # ---------------------------------------------------------------- bank
    def receipt(self, on, allocations: list[tuple[str, int]]):
        total = sum(a for _, a in allocations)
        self.post(on, BANK, total)
        for key, amount in allocations:
            doc = self.docs[key]
            self.post(on, DEBTORS, -amount, doc.contact)
            self._pay(doc, amount, on)

    def payment(self, on, allocations: list[tuple[str, int]]):
        total = sum(a for _, a in allocations)
        self.post(on, BANK, -total)
        for key, amount in allocations:
            doc = self.docs[key]
            self.post(on, CREDITORS, amount, doc.contact)
            self._pay(doc, amount, on)

    def _pay(self, doc: Doc, amount: int, on: date):
        doc.paid += amount
        doc.payments.append((on, amount))
        doc.status = "paid" if doc.paid >= doc.incl else "open"

    def unpay(self, on, allocations, sales: bool, total_sign: int):
        """Reverse a matched transaction (the reversal is dated `on`)."""
        total = sum(a for _, a in allocations)
        self.post(on, BANK, -total_sign * total)
        for key, amount in allocations:
            doc = self.docs[key]
            self.post(on, DEBTORS if sales else CREDITORS, amount if sales else -amount, doc.contact)
            self._pay(doc, -amount, on)

    def bank_booking(self, on, gross, account, code=None, money_in=False):
        """A bank transaction booked straight to an account, VAT split out of the gross."""
        vat = vat_in_gross(gross, code) if code and RATE.get(code) else 0
        base = gross - vat
        sign = 1 if money_in else -1
        self.post(on, BANK, sign * gross)
        self.post(on, account, -sign * base)
        if vat:
            if money_in:
                self.post(on, VAT_HIGH if RATE[code] >= 15 else VAT_LOW, -vat)
            else:
                self.post(on, VAT_RECEIVABLE, vat)
        if code:
            if money_in:
                self.facts.append(Fact(on, BASE_RUBRIEK.get(code), base=base, vat=vat))
            else:
                self.facts.append(Fact(on, None, deductible=vat))

    def settle_vat(self, start: date, end: date):
        """Clear the period's movement on the VAT accounts into the settlement account."""
        bal = self.balances(end, start)
        nets = {a: bal.get(a, 0) for a in (VAT_HIGH, VAT_LOW, VAT_REVERSE, VAT_RECEIVABLE)}
        for account, net in nets.items():
            self.post(end, account, -net)
        self.post(end, "1790", sum(nets.values()))

    def manual(self, on, lines: list[tuple[str, int]]):
        assert sum(n for _, n in lines) == 0
        for account, net in lines:
            self.post(on, account, net)

    # -------------------------------------------------------------- results
    def balances(self, end: date | None = None, start: date | None = None) -> dict[str, int]:
        out: dict[str, int] = {}
        for p in self.postings:
            if (end and p.on > end) or (start and p.on < start):
                continue
            out[p.account] = out.get(p.account, 0) + p.net
        return {a: v for a, v in out.items() if v}

    def contact_balances(self, account: str, end: date | None = None) -> dict[str, int]:
        out: dict[str, int] = {}
        for p in self.postings:
            if p.account == account and p.contact and not (end and p.on > end):
                out[p.contact] = out.get(p.contact, 0) + p.net
        return {c: v for c, v in out.items() if v}

    def trial(self, end: date, start: date | None = None) -> dict[str, tuple[int, int]]:
        return {
            a: (max(n, 0), max(-n, 0)) for a, n in self.balances(end, start).items() if n
        }

    def pnl(self, start: date, end: date) -> dict[str, int]:
        bal = self.balances(end, start)
        revenue = -sum(n for a, n in bal.items() if account_type(a) == REVENUE)
        expenses = sum(n for a, n in bal.items() if account_type(a) == EXPENSE)
        return {"revenue": revenue, "expenses": expenses, "result": revenue - expenses}

    def balance_sheet(self, as_of: date) -> dict[str, int]:
        bal = self.balances(as_of)
        year_start = date(as_of.year, 1, 1)
        previous = self.pnl(date(1900, 1, 1), date.fromordinal(year_start.toordinal() - 1))
        current = self.pnl(year_start, as_of)
        assets = sum(n for a, n in bal.items() if account_type(a) == ASSET)
        liabilities = -sum(n for a, n in bal.items() if account_type(a) == LIABILITY)
        equity = -sum(n for a, n in bal.items() if account_type(a) == EQUITY)
        return {
            "assets": assets, "liabilities": liabilities, "equity_accounts": equity,
            "previous": previous["result"], "current": current["result"],
            "equity": equity + previous["result"] + current["result"],
        }

    def vat_return(self, start: date, end: date) -> dict:
        base = {r: 0 for r in RUBRIEKEN}
        vat = {r: 0 for r in RUBRIEKEN}
        deductible = 0
        for f in self.facts:
            if not (start <= f.on <= end):
                continue
            if f.rubriek:
                base[f.rubriek] += f.base
                vat[f.rubriek] += f.vat
            deductible += f.deductible
        owed = sum(vat.values())
        return {
            "base": base, "vat": vat, "owed": owed, "deductible": deductible,
            "balance": owed - deductible,
            "form_base": {r: euros_down(v) for r, v in base.items()},
            "form_vat": {r: euros_down(v) for r, v in vat.items()},
            "form_owed": sum(euros_down(v) for v in vat.values()),
            "form_deductible": euros_up(deductible),
        }

    def icp(self, start: date, end: date) -> dict[str, int]:
        out: dict[str, int] = {}
        for f in self.facts:
            if f.icp_contact and start <= f.on <= end:
                out[f.icp_contact] = out.get(f.icp_contact, 0) + f.icp
        return {c: v for c, v in out.items() if v}

    def outstanding(self, kind: str, as_of: date | None = None) -> dict[str, int]:
        """Open amount per document as of a date (default: now).

        A credit note absorbs the unpaid rest of its original, so the pair leaves exactly
        what was paid on the original as a credit balance.
        """
        def paid(doc):
            return sum(a for d, a in doc.payments if as_of is None or d <= as_of)

        out = {}
        for doc in self.docs.values():
            if doc.kind != kind or (as_of and doc.on > as_of):
                continue
            if doc.credit_of:
                original = self.docs[doc.credit_of]
                out[doc.key] = -paid(original)
                out.pop(original.key, None)
            elif doc.key not in out:
                credited = any(
                    c.credit_of == doc.key and (as_of is None or c.on <= as_of)
                    for c in self.docs.values()
                )
                if not credited:
                    out[doc.key] = doc.incl - paid(doc)
        return {k: v for k, v in out.items() if v}

    def aging(self, kind: str, as_of: date) -> dict[str, list[int]]:
        out: dict[str, list[int]] = {}
        for key, amount in self.outstanding(kind, as_of).items():
            doc = self.docs[key]
            row = out.setdefault(doc.contact, [0] * 5)
            row[bucket(doc.due, as_of)] += amount
        return out
