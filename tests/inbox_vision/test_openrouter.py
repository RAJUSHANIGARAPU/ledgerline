import io
import json

import httpx
import pytest
from PIL import Image

from ledgerline.config import get_settings
from ledgerline.inbox.extract import ExtractionError
from ledgerline.inbox.extract import openrouter as orr
from ledgerline.inbox.extract.vision import SCHEMA, TransientExtractionError

from .test_restaurant_invoices import FIXTURES
from .test_vision import good_payload


def _png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (40, 40), "white").save(buf, format="PNG")
    return buf.getvalue()


PNG = _png()
MODEL = "vendor/vision-model:free"


def completion(content, finish="stop"):
    return {"choices": [{"finish_reason": finish, "message": {"content": content}}]}


def extractor(handler):
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return orr.OpenRouterExtractor(MODEL, "test-key", client)


def serving(body, status=200):
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(status, json=body)

    return handler, seen


def good_body():
    payload = good_payload(credit_note=False, paid_at_delivery=False, vat_summary=[])
    for line in payload["lines"]:
        line["is_deposit"] = False
    return completion(json.dumps(payload))


def test_happy_path_request_and_result():
    handler, seen = serving(good_body())
    invoice = extractor(handler).extract("image/png", PNG)
    assert invoice.invoice_number == "2026-0042" and invoice.total_incl_cents == 12100
    assert not any("expected format" in w for w in invoice.warnings)
    request = seen[0]
    body = json.loads(request.content)
    assert str(request.url) == orr.ENDPOINT
    assert request.headers["authorization"] == "Bearer test-key"
    assert body["model"] == MODEL
    assert body["provider"] == {"data_collection": "deny"}
    assert body["response_format"]["json_schema"]["schema"] == SCHEMA
    parts = body["messages"][1]["content"]
    assert parts[0]["image_url"]["url"].startswith("data:image/png;base64,")


def test_restaurant_fixture_through_openrouter_keeps_validation():
    data = json.loads((FIXTURES / "arithmetic_mismatch.json").read_text())
    handler, _ = serving(completion(json.dumps(data)))
    invoice = extractor(handler).extract("image/png", PNG)
    assert any("Totals do not add up" in w for w in invoice.warnings)


def test_json_in_code_fence_is_accepted():
    handler, _ = serving(completion("```json\n" + json.dumps(good_payload()) + "\n```"))
    assert extractor(handler).extract("image/png", PNG).invoice_number == "2026-0042"


@pytest.mark.parametrize("status", [429, 500, 503])
def test_rate_limit_and_server_errors_are_transient(status):
    handler, _ = serving({"error": {"message": "slow down"}}, status)
    with pytest.raises(TransientExtractionError):
        extractor(handler).extract("image/png", PNG)


def test_connection_error_is_transient():
    def handler(request):
        raise httpx.ConnectError("down")

    with pytest.raises(TransientExtractionError):
        extractor(handler).extract("image/png", PNG)


@pytest.mark.parametrize("status", [401, 402, 403, 404, 400])
def test_rejections_are_extraction_errors_without_provider_detail(status):
    handler, _ = serving({"error": {"message": "secret-detail"}}, status)
    with pytest.raises(ExtractionError) as info:
        extractor(handler).extract("image/png", PNG)
    assert "secret-detail" not in str(info.value)
    assert not isinstance(info.value, TransientExtractionError)


@pytest.mark.parametrize(
    "body",
    [
        completion("not json at all"),
        completion("[1, 2]"),
        completion(None),
        {"choices": []},
        {"unexpected": True},
        completion("{}", finish="length"),
    ],
)
def test_malformed_output_is_an_extraction_error(body):
    handler, _ = serving(body)
    with pytest.raises(ExtractionError):
        extractor(handler).extract("image/png", PNG)


def test_non_json_response_body_is_an_extraction_error():
    def handler(request):
        return httpx.Response(200, text="<html>gateway</html>")

    with pytest.raises(ExtractionError):
        extractor(handler).extract("image/png", PNG)


def test_schema_violating_output_is_flagged_not_trusted():
    handler, _ = serving(completion(json.dumps({"invoice_number": "A1", "lines": "oops"})))
    invoice = extractor(handler).extract("image/png", PNG)
    text = " ".join(invoice.warnings)
    assert "did not follow the expected format" in text and "lines is not a list" in text
    assert invoice.invoice_number == "A1"


def test_wrong_typed_line_items_become_an_error():
    handler, _ = serving(completion(json.dumps(good_payload(lines=["x"]))))
    with pytest.raises(ExtractionError):
        extractor(handler).extract("image/png", PNG)


def test_only_images_are_handled():
    ex = extractor(lambda r: httpx.Response(200))
    assert ex.can_handle("image/jpeg", b"x") and not ex.can_handle("application/pdf", b"x")


def test_get_extractor_gating(session, admin, monkeypatch):
    monkeypatch.setattr(get_settings(), "extraction_provider", "openrouter")
    monkeypatch.setattr(get_settings(), "extraction_model", MODEL)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    assert orr.get_extractor(session, admin.id) is None
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    assert isinstance(orr.get_extractor(session, admin.id), orr.OpenRouterExtractor)
    monkeypatch.setattr(get_settings(), "extraction_model", "claude-opus-5-5")
    assert orr.get_extractor(session, admin.id) is None
    monkeypatch.setattr(get_settings(), "extraction_model", MODEL)
    monkeypatch.setattr(get_settings(), "extraction_provider", "anthropic")
    assert orr.get_extractor(session, admin.id) is None
