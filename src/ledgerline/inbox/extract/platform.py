"""Delivery-platform statements: the Uber Eats weekly payout overview and the Thuisbezorgd
invoice with its overview. Both are read deterministically from the PDF text layer.

Nothing here decides a fiscal treatment. The VAT rate on fees is only taken when the document
prints it, and the VAT on sales is never guessed: neither document splits sales by rate.
"""
import re
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta

from ledgerline.inbox.extract import ExtractionError
from ledgerline.money import MoneyError, percent_of, to_cents

UBER = "uber_eats"
THUISBEZORGD = "thuisbezorgd"
PLATFORM_NAMES = {UBER: "Uber Eats", THUISBEZORGD: "Thuisbezorgd"}
AMT = r"-?\d{1,3}(?:[. ]\d{3})*,\d{2}|-?\d+,\d{2}"
UBER_LINE = re.compile(rf"^(?P<label>[A-Za-zÀ-ÿ][^\n]*?)\s+(?P<amount>{AMT})\s*€\s*$")
NL_DATE = r"(\d{2})-(\d{2})-(\d{4})"
REVERSE_RE = re.compile(r"btw[\s\-]*verlegd|reverse[\s\-]*charge", re.IGNORECASE)
TOLERANCE_CENTS = 1


@dataclass
class PlatformStatement:
    platform: str
    period_start: date
    period_end: date
    gross_sales_cents: int
    fees_cents: int
    fee_vat_cents: int
    payout_cents: int
    fee_vat_rate_percent: int | None = None
    fee_vat_rate_printed: bool = False
    tips_cents: int = 0
    cash_collected_cents: int = 0
    # Signed: negative reduces the payout (promotions, refunds, order corrections).
    adjustments_cents: int = 0
    adjustment_lines: list[list] = field(default_factory=list)
    orders: int | None = None
    reference: str | None = None
    statement_date: date | None = None
    invoice_date: date | None = None  # the date printed on the invoice part, when there is one
    reverse_charge: bool = False
    arithmetic_ok: bool = True
    warnings: list[str] = field(default_factory=list)

    @property
    def name(self) -> str:
        return PLATFORM_NAMES[self.platform]

    @property
    def label(self) -> str:
        return f"{self.name} {self.period_start.isoformat()}..{self.period_end.isoformat()}"

    def to_json(self) -> dict:
        data = asdict(self)
        for key in ("period_start", "period_end", "statement_date", "invoice_date"):
            if data[key] is not None:
                data[key] = data[key].isoformat()
        return data

    @classmethod
    def from_json(cls, data: dict) -> "PlatformStatement":
        data = dict(data)
        for key in ("period_start", "period_end", "statement_date", "invoice_date"):
            if data.get(key):
                data[key] = date.fromisoformat(data[key])
        return cls(**data)


def _cents(raw: str) -> int:
    try:
        return to_cents(raw.replace("\xa0", "").replace(" ", "").replace(".", "").replace(",", "."))
    except MoneyError as exc:
        raise ExtractionError(f"unreadable amount {raw!r}") from exc


def _lines(text: str) -> list[str]:
    return [ln.strip() for ln in text.replace("\xa0", " ").splitlines() if ln.strip()]


def _find(lines: list[str], pattern: str, group: int = 1) -> str | None:
    regex = re.compile(pattern)
    for ln in lines:
        match = regex.search(ln)
        if match:
            return match.group(group)
    return None


def _need(value, what: str, platform: str):
    if value is None:
        raise ExtractionError(f"{PLATFORM_NAMES[platform]} statement: could not read {what}")
    return value


def _nl_date(raw: str) -> date:
    day, month, year = (int(x) for x in re.match(NL_DATE, raw).groups())
    try:
        return date(year, month, day)
    except ValueError as exc:
        raise ExtractionError(f"impossible date {raw}") from exc


def _us_or_nl_period(first: str, second: str) -> tuple[date, date]:
    """Uber prints M/D/YY in the saved email; fall back to D/M/YY. A pay period is 7 days."""
    options = []
    for month_first in (True, False):
        parsed = []
        for raw in (first, second):
            a, b, year = (int(x) for x in raw.split("/"))
            month, day = (a, b) if month_first else (b, a)
            try:
                parsed.append(date(2000 + year if year < 100 else year, month, day))
            except ValueError:
                break
        if len(parsed) == 2 and parsed[1] - parsed[0] == timedelta(days=6):
            options.append((parsed[0], parsed[1]))
    if len(options) != 1:
        raise ExtractionError("Uber Eats statement: the pay period dates are ambiguous")
    return options[0]


def _reconcile(st: PlatformStatement, differences: list[tuple[str, int]]) -> None:
    for what, diff in differences:
        if abs(diff) > TOLERANCE_CENTS:
            st.arithmetic_ok = False
            st.warnings.append(
                f"{what} does not add up: off by {abs(diff) / 100:.2f} EUR. "
                "Check the document before booking."
            )


def parse_uber(text: str) -> PlatformStatement:
    lines = _lines(text)
    period = re.search(r"Betaaloverzicht voor (\d+/\d+/\d+) - (\d+/\d+/\d+)", text)
    start, end = _us_or_nl_period(*(_need(period, "the pay period", UBER).groups()))
    total = re.search(rf"^Totaal\s+(\d+)\s+({AMT})\s*€\s*$", text, re.MULTILINE)
    _need(total, "the sales total", UBER)
    fee = _find(lines, rf"^Uber Eats-servicekosten\s+({AMT})\s*€")
    vat = _find(lines, rf"^Btw op servicekosten\s+({AMT})\s*€")
    payouts = re.findall(rf"^Totale betaling\s+({AMT})\s*€\s*$", text, re.MULTILINE)
    _need(fee, "the service fee", UBER)
    _need(vat, "the VAT on the service fee", UBER)
    _need(payouts or None, "the total payment", UBER)
    net = _find(lines, rf"^Netto verkopen\s+({AMT})\s*€")

    tips, adjustments = 0, []
    start_at = next((i for i, ln in enumerate(lines) if ln.startswith("Netto verkopen")), None)
    if start_at is None:
        raise ExtractionError("Uber Eats statement: could not read the net sales line")
    for ln in lines[start_at + 1:]:
        if ln.startswith("Totale betaling"):
            break
        match = UBER_LINE.match(ln)
        if not match:
            continue
        cents = _cents(match["amount"])
        if match["label"].lower().startswith("fooi"):
            tips += cents
        else:
            adjustments.append([match["label"], cents])

    st = PlatformStatement(
        UBER, start, end,
        gross_sales_cents=_cents(total.group(2)), fees_cents=abs(_cents(fee)),
        fee_vat_cents=abs(_cents(vat)), payout_cents=_cents(payouts[-1]),
        tips_cents=tips, adjustments_cents=sum(c for _, c in adjustments),
        adjustment_lines=adjustments, orders=int(total.group(1)),
    )
    rate = next((r for r in (21, 9) if abs(percent_of(st.fees_cents, r) - st.fee_vat_cents)
                 <= TOLERANCE_CENTS), None)
    st.fee_vat_rate_percent = rate
    if rate is None and st.fee_vat_cents:
        st.warnings.append("The VAT rate on the service fee is not printed and could not be "
                           "worked out from the amounts.")
    elif rate is not None:
        st.warnings.append(f"The document does not print the VAT rate on the fee; {rate}% was "
                           "worked out from the amounts. Confirm on the Uber invoice.")
    days = [_cents(m) for m in re.findall(rf"^\d+/\d+/\d+\s+\d+\s+({AMT})\s*€\s*$", text,
                                            re.MULTILINE)]
    checks = [("Net sales (sales - fee - VAT)",
               (st.gross_sales_cents - st.fees_cents - st.fee_vat_cents) - _cents(net))
              if net else None,
              ("Total payment", (_cents(net) if net else st.gross_sales_cents - st.fees_cents
                                 - st.fee_vat_cents) + tips + st.adjustments_cents
               - st.payout_cents)]
    if days:
        checks.append(("The daily sales", sum(days) - st.gross_sales_cents))
    _reconcile(st, [c for c in checks if c])
    return st


def parse_thuisbezorgd(text: str) -> PlatformStatement:
    lines = _lines(text)
    p = THUISBEZORGD
    period = re.search(rf"Periode:\s*{NL_DATE}\s*t/m\s*{NL_DATE}", text)
    _need(period, "the period", p)
    start = _nl_date("-".join(period.groups()[0:3]))
    end = _nl_date("-".join(period.groups()[3:6]))

    def count_amount(label: str) -> int | None:
        raw = _find(lines, rf"^{label}\s+\*?\s*\d+ bestelling(?:en)? t\.w\.v\. €\s*({AMT})\s*$")
        return _cents(raw) if raw is not None else None

    total = _need(count_amount("Totaal"), "the order total", p)
    order_count = _find(lines, r"^Totaal\s+(\d+) bestelling(?:en)?")
    cash = count_amount("Betaald bij bezorging") or 0
    online = count_amount("Online betaald") or 0
    tips = count_amount("Fooien") or 0
    known = ("totaal", "betaald bij bezorging", "online betaald", "fooien")
    adjustments = []
    in_section = False
    for ln in lines:
        if ln.startswith("Specificaties"):
            in_section = True
        elif ln.startswith("Uitstaand saldo"):
            break
        match = re.match(rf"^(\D+?)\s+\*?\s*\d+ bestelling(?:en)? t\.w\.v\. €\s*({AMT})\s*$", ln)
        if in_section and match and match.group(1).strip().lower() not in known:
            adjustments.append([match.group(1).strip(), _cents(match.group(2))])
    balance_line = rf"^Uitstaand saldo online betalingen op {NL_DATE}.*?€\s*({AMT})\s*$"
    outstanding = _find(lines, balance_line, 4)
    statement_date = _find(lines, rf"^Uitstaand saldo online betalingen op ({NL_DATE})", 1)
    settled = _find(lines, rf"^Verrekening van factuur \d+\s+€\s*({AMT})\s*$")
    payout = _find(lines, rf"^Uitbetaling naar rekening .*?€\s*({AMT})\s*$")
    reference = _find(lines, r"Factuurnummer:\s*(\d+)")
    printed_date = _find(lines, rf"\bDatum:\s*({NL_DATE})", 1)
    subtotal = _find(lines, rf"^Subtotaal\s+€\s*({AMT})\s*$")
    vat_match = re.search(rf"^BTW \((\d+)% over € ({AMT})\)\s+€\s*({AMT})\s*$", text, re.MULTILINE)
    invoice_total = _find(lines, rf"^Totaalbedrag van deze factuur\s+€\s*({AMT})\s*$")
    if subtotal is None or vat_match is None or invoice_total is None:
        raise ExtractionError("Thuisbezorgd statement: the invoice part is missing or unreadable")
    _need(payout, "the payout", p)
    _need(outstanding, "the outstanding balance", p)

    # Fee lines sit between the intro and the subtotal; sales lines carry "t.w.v." and are skipped.
    begin = next((i for i, ln in enumerate(lines) if ln.startswith("De volgende diensten")), 0)
    stop = next((i for i, ln in enumerate(lines) if ln.startswith("Subtotaal")), len(lines))
    fee_lines = []
    for ln in lines[begin + 1:stop]:
        match = re.search(rf"€\s*({AMT})\s*$", ln)
        if match and "t.w.v." not in ln:
            fee_lines.append(_cents(match.group(1)))

    st = PlatformStatement(
        p, start, end, gross_sales_cents=total, fees_cents=_cents(subtotal),
        fee_vat_cents=_cents(vat_match.group(3)), payout_cents=_cents(payout),
        fee_vat_rate_percent=int(vat_match.group(1)), fee_vat_rate_printed=True,
        tips_cents=tips, cash_collected_cents=cash,
        adjustments_cents=sum(c for _, c in adjustments), adjustment_lines=adjustments,
        orders=int(order_count) if order_count else None, reference=reference,
        statement_date=_nl_date(statement_date) if statement_date else None,
        invoice_date=_nl_date(printed_date) if printed_date else None,
    )
    out = _cents(outstanding)
    settled_cents = _cents(settled) if settled else _cents(invoice_total)
    checks = [
        ("Cash + online payments vs the order total", cash + online - total),
        ("Online + tips + refunds vs the outstanding balance",
         online + tips + st.adjustments_cents - out),
        ("Outstanding balance - invoice vs the payout", out - settled_cents - st.payout_cents),
        ("Invoice subtotal + VAT vs the invoice total",
         st.fees_cents + st.fee_vat_cents - _cents(invoice_total)),
        ("The invoice total vs the amount netted", _cents(invoice_total) - settled_cents),
        ("VAT vs the printed rate", percent_of(_cents(vat_match.group(2)), st.fee_vat_rate_percent)
         - st.fee_vat_cents),
    ]
    if fee_lines:
        checks.append(("The invoice lines vs the subtotal", sum(fee_lines) - st.fees_cents))
    _reconcile(st, checks)
    return st


def parse_platform_text(text: str) -> PlatformStatement | None:
    """The statement in this text, or None when it is not a platform document."""
    flat = text.replace("\xa0", " ")
    if "Betaaloverzicht" in flat and "Uber Eats-servicekosten" in flat:
        statement = parse_uber(flat)
    elif "Uitstaand saldo online betalingen" in flat and "Verrekening van factuur" in flat:
        statement = parse_thuisbezorgd(flat)
    else:
        return None
    if REVERSE_RE.search(flat):
        statement.reverse_charge = True
        statement.warnings.append("The document mentions reverse-charged VAT; book it by hand.")
    return statement
