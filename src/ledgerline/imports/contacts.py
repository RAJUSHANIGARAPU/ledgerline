"""Contacts CSV import: parse, validate, find duplicates, preview, then create."""
import csv
import io
import re
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from ledgerline import audit
from ledgerline.imports import csvio
from ledgerline.models import Administration, Contact, ContactKind
from ledgerline.platform.contacts import parse_form

COLUMNS = ("name", "kind", "email", "address", "postcode", "city", "country",
           "vat_number", "kvk", "iban")
ALIASES = {
    "name": ("naam", "company", "bedrijfsnaam"),
    "kind": ("type", "soort", "relatietype"),
    "email": ("e_mail", "emailadres"),
    "address": ("address_line", "adres", "straat"),
    "postcode": ("zipcode", "zip", "postal_code"),
    "city": ("plaats", "woonplaats", "stad"),
    "country": ("land",),
    "vat_number": ("vat", "btw", "btw_nummer", "btw_id", "btw_nr"),
    "kvk": ("kvk_number", "kvk_nummer", "coc"),
    "iban": ("rekeningnummer",),
}
KIND_WORDS = {
    "customer": "customer", "klant": "customer", "debiteur": "customer",
    "supplier": "supplier", "leverancier": "supplier", "crediteur": "supplier",
    "both": "both", "beide": "both",
}
TEMPLATE_ROWS = [
    ["Voorbeeld Klant BV", "customer", "administratie@voorbeeld-klant.example",
     "Voorbeeldstraat 12", "1011 AB", "Amsterdam", "NL", "NL123456782B01", "12345678",
     "NL91ABNA0417164300"],
    ["Voorbeeld Leverancier", "supplier", "info@voorbeeld-leverancier.example", "Proefweg 3",
     "3511 CD", "Utrecht", "NL", "", "", ""],
]


@dataclass
class RowPlan:
    row: int
    data: dict
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    action: str = "create"  # create | skip | error
    reason: str = ""


@dataclass
class ContactPlan:
    rows: list[RowPlan]
    delimiter: str
    warnings: list[str]

    def count(self, action: str) -> int:
        return sum(1 for r in self.rows if r.action == action)


def template_csv(delimiter: str = ";") -> bytes:
    out = io.StringIO()
    writer = csv.writer(out, delimiter=delimiter, lineterminator="\r\n")
    writer.writerow(COLUMNS)
    writer.writerows(TEMPLATE_ROWS)
    return b"\xef\xbb\xbf" + out.getvalue().encode("utf-8")


def _name_key(name: str | None) -> str:
    return re.sub(r"\s+", " ", (name or "").strip()).casefold()


def _postcode_key(postcode: str | None) -> str:
    return re.sub(r"\s", "", postcode or "").upper()


class _Index:
    """Existing and already-planned contacts by the three duplicate rules."""

    def __init__(self) -> None:
        self.vat: dict[str, str] = {}
        self.kvk: dict[str, str] = {}
        self.name_postcode: dict[tuple[str, str], str] = {}

    def add(self, name: str, vat: str | None, kvk: str | None, postcode: str | None,
            label: str) -> None:
        if vat:
            self.vat.setdefault(vat, label)
        if kvk:
            self.kvk.setdefault(kvk, label)
        self.name_postcode.setdefault((_name_key(name), _postcode_key(postcode)), label)

    def find(self, data: dict) -> str | None:
        if data.get("vat_number") and data["vat_number"] in self.vat:
            return f"same VAT number as {self.vat[data['vat_number']]}"
        if data.get("kvk_number") and data["kvk_number"] in self.kvk:
            return f"same KvK number as {self.kvk[data['kvk_number']]}"
        hit = self.name_postcode.get((_name_key(data["name"]), _postcode_key(data["postcode"])))
        if hit:
            return f"same name and postcode as {hit}"
        return None


def _existing_index(session: Session, admin_id: int) -> _Index:
    index = _Index()
    for c in session.scalars(select(Contact).where(
            Contact.administration_id == admin_id, Contact.anonymised_at.is_(None))):
        index.add(c.name, c.vat_number, c.kvk_number, c.postcode, f"existing contact {c.name}")
    return index


def plan_contacts(session: Session, admin: Administration, content: bytes) -> ContactPlan:
    text, warnings = csvio.decode(content)
    table = csvio.read_table(text, ALIASES, required=("name",))
    index = _existing_index(session, admin.id)
    rows: list[RowPlan] = []
    for number, raw in table.rows:
        plan = _plan_row(number, raw, table.row_errors.get(number))
        if plan.action != "error":
            hit = index.find(plan.data)
            if hit:
                plan.action, plan.reason = "skip", f"duplicate: {hit}"
            else:
                index.add(plan.data["name"], plan.data.get("vat_number"),
                          plan.data.get("kvk_number"), plan.data.get("postcode"),
                          f"row {number} ({plan.data['name']})")
        rows.append(plan)
    return ContactPlan(rows, table.delimiter, [*warnings, *table.warnings])


def _plan_row(number: int, raw: dict[str, str], structural: str | None) -> RowPlan:
    kind_raw = raw.get("kind", "").strip().lower()
    warnings: list[str] = []
    if not kind_raw:
        kind = ContactKind.CUSTOMER.value
        warnings.append("kind is empty; imported as customer")
    else:
        kind = KIND_WORDS.get(kind_raw, kind_raw)
    form = {
        "name": raw.get("name", ""), "kind": kind, "email": raw.get("email", ""),
        "address_line": raw.get("address", ""), "postcode": raw.get("postcode", ""),
        "city": raw.get("city", ""), "country": raw.get("country", ""),
        "vat_number": raw.get("vat_number", ""), "kvk_number": raw.get("kvk", ""),
        "iban": raw.get("iban", ""),
    }
    data, errors = parse_form(form)
    messages = [f"{field_name}: {msg}" for field_name, msg in errors.items()]
    if structural:
        messages.append(structural)
    plan = RowPlan(number, data, messages, warnings)
    if messages:
        plan.action, plan.reason = "error", "; ".join(messages)
        return plan
    if kind in (ContactKind.CUSTOMER, ContactKind.BOTH) and not (
            data.get("address_line") and data.get("city")):
        plan.warnings.append("address or city is empty; an invoice cannot be issued to this "
                             "customer until both are filled in")
    return plan


def apply_plan(session: Session, admin: Administration, plan: ContactPlan, user_id: int | None,
               filename: str | None = None) -> int:
    created = 0
    for row in plan.rows:
        if row.action != "create":
            continue
        contact = Contact(administration_id=admin.id, **row.data)
        session.add(contact)
        session.flush()
        audit.record(session, "contact.create", user_id=user_id, administration_id=admin.id,
                     entity_type="contact", entity_id=contact.id,
                     details={"via": "import", "row": row.row})
        created += 1
    audit.record(session, "contact.import", user_id=user_id, administration_id=admin.id,
                 entity_type="contact", details={
                     "file": (filename or "")[:255], "created": created,
                     "skipped": plan.count("skip"), "errors": plan.count("error")})
    return created

