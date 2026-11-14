"""Format detection and the shared parse entry point."""
import hashlib

from ledgerline.banking import camt053, mt940
from ledgerline.banking.parsed import ParsedStatement, StatementError


def sniff(content: bytes) -> str | None:
    """camt053 or mt940 when the content is one of those, None for anything else (CSV)."""
    head = content[:2048].lstrip(b"\xef\xbb\xbf \t\r\n")
    if head.startswith(b"<"):
        return "camt053"
    if b":20:" in content[:4096] and b":60" in content:
        return "mt940"
    return None


def detect_format(content: bytes) -> str:
    found = sniff(content)
    if found is None:
        raise StatementError("unrecognised file: expected CAMT.053 XML or MT940")
    return found


def parse_statement(content: bytes) -> ParsedStatement:
    if detect_format(content) == "camt053":
        return camt053.parse(content)
    return mt940.parse(content)


def file_sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()
