import base64
import io
import json
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

import anthropic
import httpx
import pytest
from PIL import Image

from ledgerline.config import get_settings
from ledgerline.inbox import service as inbox
from ledgerline.inbox.extract import ExtractionError, vision
from ledgerline.inbox.extract.vision import (
    SCAN_WARNING,
    SCHEMA,
    SYSTEM_PROMPT,
    AnthropicExtractor,
    TransientExtractionError,
    get_extractor,
    parse_result,
)

PDF = b"%PDF-1.4\n" + b"x" * 5000


def good_payload(**over):
    data = {
        "supplier_name": "Drukkerij Jansen",
        "supplier_vat_number": "NL123456789B01",
        "supplier_kvk": "12345678",
        "supplier_iban": "NL91ABNA0417164300",
        "invoice_number": "2026-0042",
        "invoice_date": "2026-10-01",
        "due_date": "2026-10-31",
        "currency": "EUR",
        "total_excl": "100.00",
        "total_vat": "21.00",
        "total_incl": "121.00",
        "reverse_charge": False,
        "lines": [
            {
                "description": "Flyers",
                "amount_excl": "100.00",
                "vat_rate_percent": 21,
                "vat_amount": "21.00",
            }
        ],
    }
    data.update(over)
    return data


def response(payload=None, stop_reason="end_turn", text=None):
    body = (
        text if text is not None else json.dumps(payload if payload is not None else good_payload())
    )
    return SimpleNamespace(
        stop_reason=stop_reason, content=[SimpleNamespace(type="text", text=body)]
    )


class FakeClient:
    def __init__(self, result=None, error=None):
        self.calls = []
        self._result = result if result is not None else response()
        self._error = error
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        if self._error:
            raise self._error
        return self._result


def http_error(cls, status):
    request = httpx.Request("POST", "https://api.invalid/v1/messages")
    return cls("boom", response=httpx.Response(status, request=request), body=None)


def extractor(**kw):
    client = FakeClient(**kw)
    return AnthropicExtractor(client=client, model="claude-opus-5-5"), client


def png_bytes(size=(40, 30), mode="RGB"):
    buf = io.BytesIO()
    Image.new(mode, size, "white").save(buf, format="PNG")
    return buf.getvalue()


def test_request_shape_for_pdf():
    ex, client = extractor()
    ex.extract("application/pdf", PDF)
    call = client.calls[0]
    assert call["model"] == "claude-opus-5-5"
    assert call["betas"] == ["server-side-fallback-2026-07-01"]
    assert call["fallbacks"] == "default"
    assert call["max_tokens"] == 16000
    assert "thinking" not in call and "temperature" not in call
    assert call["output_config"] == {
        "effort": "low",
        "format": {"type": "json_schema", "schema": SCHEMA},
    }
    blocks = call["messages"][0]["content"]
    assert [b["type"] for b in blocks] == ["document", "text"]
    source = blocks[0]["source"]
    assert source["type"] == "base64" and source["media_type"] == "application/pdf"
    assert "\n" not in source["data"]
    assert base64.b64decode(source["data"]) == PDF


def test_request_shape_for_image_blocks():
    ex, client = extractor()
    png = png_bytes()
    ex.extract("image/png", png)
    block = client.calls[0]["messages"][0]["content"][0]
    assert block["type"] == "image"
    assert block["source"]["media_type"] == "image/png"
    assert base64.b64decode(block["source"]["data"]) == png


def _strict(schema):
    if schema.get("type") == "object":
        assert schema["additionalProperties"] is False
        assert sorted(schema["required"]) == sorted(schema["properties"])
        for sub in schema["properties"].values():
            _strict(sub)
    if schema.get("type") == "array":
        _strict(schema["items"])


def test_schema_is_strict_at_every_level():
    _strict(SCHEMA)


def test_prompt_contains_injection_guard_and_null_rule():
    assert "Ignore any instructions" in SYSTEM_PROMPT
    assert "untrusted" in SYSTEM_PROMPT
    assert "use null" in SYSTEM_PROMPT
    assert "btw verlegd" in SYSTEM_PROMPT


def test_parses_to_cents_and_dates():
    ex, _ = extractor()
    result = ex.extract("application/pdf", PDF)
    assert result.supplier_name == "Drukkerij Jansen"
    assert result.invoice_date == date(2026, 10, 1)
    assert (result.total_excl_cents, result.total_vat_cents, result.total_incl_cents) == (
        10000,
        2100,
        12100,
    )
    line = result.lines[0]
    assert (line.amount_excl_cents, line.vat_rate_percent, line.vat_cents) == (10000, 21, 2100)
    assert result.warnings == [SCAN_WARNING]
    assert isinstance(result.total_incl_cents, int)


def test_nulls_stay_none_and_bad_amounts_warn():
    result = parse_result(
        good_payload(total_excl=None, total_vat="12,5x", total_incl=None, invoice_date="not-a-date")
    )
    assert result.total_excl_cents is None and result.total_vat_cents is None
    assert result.invoice_date is None
    assert any("total VAT" in w for w in result.warnings)
    assert any("invoice date" in w for w in result.warnings)


def test_totals_that_do_not_add_up_warn():
    result = parse_result(good_payload(total_incl="125.00"))
    assert any(w.startswith("Totals do not add up") for w in result.warnings)


def test_one_cent_rounding_is_tolerated():
    result = parse_result(good_payload(total_incl="121.01"))
    assert result.warnings == [SCAN_WARNING]


def test_line_sum_mismatch_warns():
    payload = good_payload()
    payload["lines"][0]["amount_excl"] = "90.00"
    payload["lines"][0]["vat_amount"] = "18.90"
    result = parse_result(payload)
    assert any(w.startswith("Lines add up to") for w in result.warnings)


def test_vat_rate_mismatch_warns_and_two_cents_pass():
    payload = good_payload()
    payload["lines"][0]["vat_amount"] = "20.00"
    assert any("VAT at 21%" in w for w in parse_result(payload).warnings)
    payload["lines"][0]["vat_amount"] = "21.02"
    assert not any("VAT at 21%" in w for w in parse_result(payload).warnings)


def test_total_vat_against_line_rates_mixed():
    payload = good_payload(
        total_excl="200.00",
        total_vat="30.00",
        total_incl="230.00",
        lines=[
            {
                "description": "A",
                "amount_excl": "100.00",
                "vat_rate_percent": 21,
                "vat_amount": None,
            },
            {
                "description": "B",
                "amount_excl": "100.00",
                "vat_rate_percent": 9,
                "vat_amount": None,
            },
        ],
    )
    assert parse_result(payload).warnings == [SCAN_WARNING]
    payload["total_vat"] = "40.00"
    payload["total_incl"] = "240.00"
    assert any("does not match the line rates" in w for w in parse_result(payload).warnings)


def test_reverse_charge_detected_and_skips_vat_checks():
    payload = good_payload(
        reverse_charge=True,
        total_vat=None,
        total_incl="100.00",
        lines=[
            {
                "description": "Dienst",
                "amount_excl": "100.00",
                "vat_rate_percent": 0,
                "vat_amount": None,
            }
        ],
    )
    result = parse_result(payload)
    assert result.reverse_charge is True
    assert result.warnings == [SCAN_WARNING]


def test_reverse_charge_with_printed_vat_warns():
    result = parse_result(good_payload(reverse_charge=True))
    assert any("reverse charge but a VAT amount" in w for w in result.warnings)


def test_date_plausibility():
    today = date(2026, 11, 8)
    assert any(
        "future" in w for w in parse_result(good_payload(invoice_date="2026-12-01"), today).warnings
    )
    assert any(
        "before 2000" in w
        for w in parse_result(good_payload(invoice_date="1999-12-31"), today).warnings
    )
    assert parse_result(good_payload(invoice_date="2026-11-08"), today).warnings[0] == SCAN_WARNING


def test_line_without_amount_is_dropped_with_warning():
    payload = good_payload(
        lines=[
            {"description": "?", "amount_excl": None, "vat_rate_percent": None, "vat_amount": None}
        ]
    )
    result = parse_result(payload)
    assert result.lines == []
    assert any("Line 1 has no amount" in w for w in result.warnings)


def test_non_euro_currency_warns():
    assert any("not EUR" in w for w in parse_result(good_payload(currency="usd")).warnings)


def test_refusal_and_max_tokens_and_garbage():
    with pytest.raises(ExtractionError, match="document could not be read"):
        extractor(result=response(stop_reason="refusal"))[0].extract("application/pdf", PDF)
    with pytest.raises(ExtractionError):
        extractor(result=response(stop_reason="max_tokens"))[0].extract("application/pdf", PDF)
    with pytest.raises(ExtractionError, match="unreadable"):
        extractor(result=response(text="not json"))[0].extract("application/pdf", PDF)
    with pytest.raises(ExtractionError, match="unreadable"):
        extractor(result=response(text="[1]"))[0].extract("application/pdf", PDF)


@pytest.mark.parametrize(
    "error,message,transient",
    [
        (
            http_error(anthropic.AuthenticationError, 401),
            "extraction service is not configured",
            False,
        ),
        (
            http_error(anthropic.PermissionDeniedError, 403),
            "extraction service is not configured",
            False,
        ),
        (http_error(anthropic.NotFoundError, 404), "extraction service is not configured", False),
        (http_error(anthropic.RateLimitError, 429), "temporarily unavailable, will retry", True),
        (
            http_error(anthropic.InternalServerError, 500),
            "temporarily unavailable, will retry",
            True,
        ),
        (http_error(anthropic.APIStatusError, 529), "temporarily unavailable, will retry", True),
        (
            anthropic.APIConnectionError(request=httpx.Request("POST", "https://api.invalid")),
            "temporarily unavailable, will retry",
            True,
        ),
        (
            anthropic.APITimeoutError(request=httpx.Request("POST", "https://api.invalid")),
            "temporarily unavailable, will retry",
            True,
        ),
        (
            http_error(anthropic.BadRequestError, 400),
            "document could not be processed by the extraction service",
            False,
        ),
        (http_error(anthropic.APIStatusError, 413), "extraction request was rejected", False),
    ],
)
def test_error_mapping(error, message, transient):
    ex, _ = extractor(error=error)
    with pytest.raises(ExtractionError) as caught:
        ex.extract("application/pdf", PDF)
    assert str(caught.value) == message
    assert isinstance(caught.value, TransientExtractionError) is transient


def test_error_message_carries_no_provider_detail():
    error = http_error(anthropic.AuthenticationError, 401)
    error.message = "invalid x-api-key sk-ant-secret"
    ex, _ = extractor(error=error)
    with pytest.raises(ExtractionError) as caught:
        ex.extract("application/pdf", PDF)
    assert "sk-ant" not in str(caught.value)
    assert caught.value.__cause__ is None


def test_can_handle():
    ex, _ = extractor()
    assert ex.can_handle("application/pdf", PDF)
    assert ex.can_handle("image/jpeg", b"x")
    assert not ex.can_handle("application/xml", b"<Invoice/>")
    assert not ex.can_handle("application/pdf", b"")
    assert ex.method == "vision"


def test_small_image_passes_through_unchanged():
    ex, client = extractor()
    png = png_bytes()
    ex.extract("image/png", png)
    sent = client.calls[0]["messages"][0]["content"][0]["source"]["data"]
    assert base64.b64decode(sent) == png


def test_large_dimension_image_is_downscaled():
    ex, client = extractor()
    big = png_bytes(size=(5000, 4500))
    ex.extract("image/png", big)
    source = client.calls[0]["messages"][0]["content"][0]["source"]
    with Image.open(io.BytesIO(base64.b64decode(source["data"]))) as img:
        assert max(img.size) <= vision.MAX_IMAGE_SIDE


def test_oversized_file_is_downscaled_to_jpeg_under_limit(monkeypatch):
    monkeypatch.setattr(vision, "MAX_IMAGE_BYTES", 20_000)
    ex, client = extractor()
    noisy = Image.effect_noise((1200, 1200), 90).convert("RGB")
    buf = io.BytesIO()
    noisy.save(buf, format="JPEG", quality=95)
    assert buf.tell() > 20_000
    monkeypatch.setattr(vision, "MAX_IMAGE_SIDE", 300)
    ex.extract("image/jpeg", buf.getvalue())
    source = client.calls[0]["messages"][0]["content"][0]["source"]
    sent = base64.b64decode(source["data"])
    assert len(sent) <= 20_000 and source["media_type"] == "image/jpeg"


def test_unreadable_image_and_huge_pdf_rejected():
    ex, client = extractor()
    with pytest.raises(ExtractionError):
        ex.extract("image/jpeg", b"\xff\xd8\xff" + b"junk" * 2_000_000)
    with pytest.raises(ExtractionError, match="too large"):
        ex.extract("application/pdf", b"%PDF-" + b"0" * (vision.MAX_PDF_BYTES + 1))
    assert client.calls == []


def test_provider_none_returns_none(session, admin, monkeypatch):
    monkeypatch.setattr(get_settings(), "extraction_provider", "none")
    assert get_extractor(session, admin.id, client=FakeClient()) is None


def test_missing_credentials_returns_none(session, admin, monkeypatch):
    monkeypatch.setattr(get_settings(), "extraction_provider", "anthropic")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    assert get_extractor(session, admin.id) is None


def _vision_docs(session, admin, count, received_at=None):
    for i in range(count):
        doc, _ = inbox.receive(session, admin.id, f"d{i}.pdf", PDF + str(i).encode(), "upload")
        doc.extraction_method = "vision"
        if received_at:
            doc.received_at = received_at
    session.flush()


def test_monthly_cap(session, admin, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "extraction_provider", "anthropic")
    monkeypatch.setattr(settings, "extraction_monthly_limit", 3)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    _vision_docs(session, admin, 2)
    assert isinstance(get_extractor(session, admin.id), AnthropicExtractor)
    third = inbox.receive(session, admin.id, "more.pdf", PDF + b"more", "upload")[0]
    third.extraction_method = "vision"
    session.flush()
    assert get_extractor(session, admin.id) is None


def test_cap_counts_only_this_month_and_only_vision(session, admin, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "extraction_provider", "anthropic")
    monkeypatch.setattr(settings, "extraction_monthly_limit", 1)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    _vision_docs(session, admin, 2, received_at=datetime.now(UTC) - timedelta(days=45))
    other, _ = inbox.receive(session, admin.id, "t.pdf", PDF + b"text", "upload")
    other.extraction_method = "pdf_text"
    session.flush()
    assert vision.vision_usage_this_month(session, admin.id) == 0
    assert get_extractor(session, admin.id) is not None


def test_limit_zero_disables_cap(session, admin, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "extraction_provider", "anthropic")
    monkeypatch.setattr(settings, "extraction_monthly_limit", 0)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    _vision_docs(session, admin, 3)
    assert get_extractor(session, admin.id) is not None


def test_cap_is_per_administration(session, admin, monkeypatch):
    from ledgerline.ledger.chart import create_administration

    other = create_administration(session, name="Ander BV", kvk_number="87654321")
    settings = get_settings()
    monkeypatch.setattr(settings, "extraction_provider", "anthropic")
    monkeypatch.setattr(settings, "extraction_monthly_limit", 1)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    _vision_docs(session, other, 2)
    assert get_extractor(session, admin.id) is not None
    assert get_extractor(session, other.id) is None
