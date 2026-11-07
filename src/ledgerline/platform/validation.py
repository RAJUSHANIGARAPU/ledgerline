"""Pure input checks shared by the contact and settings forms."""
import re
from datetime import date

from schwifty import IBAN
from schwifty.exceptions import SchwiftyException

_VAT_FORMATS: dict[str, str] = {
    "NL": r"\d{9}B\d{2}",
    "AT": r"U\d{8}",
    "BE": r"[01]\d{9}",
    "BG": r"\d{9,10}",
    "CY": r"\d{8}[A-Z]",
    "CZ": r"\d{8,10}",
    "DE": r"\d{9}",
    "DK": r"\d{8}",
    "EE": r"\d{9}",
    "EL": r"\d{9}",
    "ES": r"[A-Z0-9]\d{7}[A-Z0-9]",
    "FI": r"\d{8}",
    "FR": r"[A-Z0-9]{2}\d{9}",
    "HR": r"\d{11}",
    "HU": r"\d{8}",
    "IE": r"\d[A-Z0-9+*]\d{5}[A-Z]{1,2}",
    "IT": r"\d{11}",
    "LT": r"(\d{9}|\d{12})",
    "LU": r"\d{8}",
    "LV": r"\d{11}",
    "MT": r"\d{8}",
    "PL": r"\d{10}",
    "PT": r"\d{9}",
    "RO": r"\d{2,10}",
    "SE": r"\d{12}",
    "SI": r"\d{8}",
    "SK": r"\d{10}",
}
_GENERIC_VAT = re.compile(r"[A-Z]{2}[A-Z0-9]{2,12}")
_COUNTRY = re.compile(r"[A-Z]{2}")
_KVK = re.compile(r"\d{8}")
_EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
_PREFIX = re.compile(r"[A-Za-z0-9-]{0,10}")


def clean(value: str | None) -> str | None:
    value = (value or "").strip()
    return value or None


def normalise_vat(value: str) -> str:
    return re.sub(r"[\s.\-]", "", value).upper()


def check_vat(value: str | None) -> tuple[str | None, str | None]:
    """Return (normalised value, error). Format check only, not a VIES lookup."""
    if not clean(value):
        return None, None
    vat = normalise_vat(value or "")
    prefix, rest = vat[:2], vat[2:]
    pattern = _VAT_FORMATS.get(prefix)
    if pattern is not None:
        if re.fullmatch(pattern, rest):
            return vat, None
        hint = "NL + 9 digits + B + 2 digits" if prefix == "NL" else f"{prefix} format"
        return vat, f"VAT number does not match the expected format ({hint})."
    if _GENERIC_VAT.fullmatch(vat):
        return vat, None
    return vat, "VAT number must start with a two-letter country prefix."


def check_kvk(value: str | None) -> tuple[str | None, str | None]:
    kvk = clean(value)
    if kvk is None:
        return None, None
    kvk = re.sub(r"\s", "", kvk)
    if not _KVK.fullmatch(kvk):
        return kvk, "KvK number must be 8 digits."
    return kvk, None


def check_iban(value: str | None) -> tuple[str | None, str | None]:
    if not clean(value):
        return None, None
    try:
        return IBAN(value or "").compact, None
    except SchwiftyException:
        return re.sub(r"\s", "", value or "").upper(), "IBAN is not valid."


def check_country(value: str | None) -> tuple[str, str | None]:
    country = (clean(value) or "NL").upper()
    if not _COUNTRY.fullmatch(country):
        return country, "Country must be a two-letter ISO code."
    return country, None


def check_email(value: str | None) -> tuple[str | None, str | None]:
    email = clean(value)
    if email is not None and not _EMAIL.fullmatch(email):
        return email, "Email address is not valid."
    return email, None


def check_prefix(value: str | None) -> tuple[str, str | None]:
    prefix = (value or "").strip()
    if not _PREFIX.fullmatch(prefix):
        return prefix, "Invoice prefix may use letters, digits and dashes, up to 10 characters."
    return prefix, None


def check_password(password: str, confirm: str) -> str | None:
    if len(password) < 12:
        return "Password must be at least 12 characters."
    if password != confirm:
        return "The two passwords do not match."
    return None


def retention_until(last_activity: date | None, years: int = 7) -> date | None:
    """31 December, `years` after the year the data stops being current."""
    if last_activity is None:
        return None
    return date(last_activity.year + years, 12, 31)
