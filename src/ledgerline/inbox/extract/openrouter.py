"""Optional extraction through OpenRouter's OpenAI-compatible chat completions endpoint.

Off unless LEDGERLINE_EXTRACTION_PROVIDER=openrouter. Same field schema, prompt and
deterministic checks as the Anthropic reader; models behind OpenRouter vary a lot, so the
output is never trusted. See docs/EXTRACTION.md.
"""

import json
import logging
import os
import re

import httpx
from sqlalchemy.orm import Session

from ledgerline.config import get_settings
from ledgerline.inbox.extract import ExtractedInvoice, ExtractionError
from ledgerline.inbox.extract.vision import (
    IMAGE_TYPES,
    METHOD,
    NOT_CONFIGURED_MESSAGE,
    SCHEMA,
    SYSTEM_PROMPT,
    USER_PROMPT,
    TransientExtractionError,
    _b64,
    _prepare_image,
    parse_result,
    vision_usage_this_month,
)

log = logging.getLogger(__name__)

ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"
MAX_TOKENS = 8000
TIMEOUT = 90.0
PROVIDER_PREFERENCES = {"data_collection": "deny"}
FORMAT_WARNING = "The model did not follow the expected format"
_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$")
_REQUIRED_BOOL = ("reverse_charge", "credit_note", "paid_at_delivery")


def shape_problems(data: dict) -> list[str]:
    """Where the model output departs from SCHEMA; the schema is advisory on some endpoints."""
    missing = [key for key in SCHEMA["required"] if key not in data]
    problems = [f"missing {', '.join(missing)}"] if missing else []
    for key in ("lines", "vat_summary"):
        if key in data and not isinstance(data[key], list):
            problems.append(f"{key} is not a list")
    for key in _REQUIRED_BOOL:
        if key in data and not isinstance(data[key], bool):
            problems.append(f"{key} is not true or false")
    return problems


class OpenRouterExtractor:
    method = METHOD

    def __init__(self, model: str, api_key: str, client: httpx.Client | None = None):
        self.model = model
        self.api_key = api_key
        self.client = client or httpx.Client(timeout=TIMEOUT)

    def can_handle(self, content_type: str, content: bytes) -> bool:
        return content_type in IMAGE_TYPES and bool(content)

    def _body(self, content_type: str, content: bytes) -> dict:
        content_type, content = _prepare_image(content_type, content)
        image = f"data:{content_type};base64,{_b64(content)}"
        instruction = (
            f"{USER_PROMPT}\nReply with one JSON object only, matching this JSON schema:\n"
            f"{json.dumps(SCHEMA)}"
        )
        return {
            "model": self.model,
            "max_tokens": MAX_TOKENS,
            "temperature": 0,
            "provider": PROVIDER_PREFERENCES,
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "invoice", "strict": True, "schema": SCHEMA},
            },
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": image}},
                        {"type": "text", "text": instruction},
                    ],
                },
            ],
        }

    def extract(self, content_type: str, content: bytes) -> ExtractedInvoice:
        body = self._body(content_type, content)
        try:
            response = self.client.post(
                ENDPOINT, json=body, headers={"Authorization": f"Bearer {self.api_key}"}
            )
        except httpx.HTTPError:
            raise TransientExtractionError("temporarily unavailable, will retry") from None
        status = response.status_code
        if status == 429 or status >= 500:
            raise TransientExtractionError("temporarily unavailable, will retry")
        if status in (401, 402, 403):
            log.error("extraction provider rejected the credentials or credit (%s)", status)
            raise ExtractionError(NOT_CONFIGURED_MESSAGE)
        if status == 404:
            log.error("no endpoint for the model under the privacy preference (%s)", status)
            raise ExtractionError("no provider is available for this model under the privacy rule")
        if status >= 400:
            log.warning("extraction request failed (%s)", status)
            raise ExtractionError("extraction request was rejected")
        return self._parse(response)

    def _parse(self, response: httpx.Response) -> ExtractedInvoice:
        try:
            choice = response.json()["choices"][0]
            text = choice["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError):
            raise ExtractionError("extraction returned an unreadable result") from None
        if choice.get("finish_reason") == "length":
            raise ExtractionError("document is too long to extract")
        try:
            data = json.loads(_FENCE.sub("", text.strip()))
        except (ValueError, AttributeError):
            raise ExtractionError("extraction returned an unreadable result") from None
        if not isinstance(data, dict):
            raise ExtractionError("extraction returned an unreadable result")
        problems = shape_problems(data)
        for key in ("lines", "vat_summary"):
            if not isinstance(data.get(key, []), list):
                data[key] = []
        try:
            invoice = parse_result(data)
        except (TypeError, ValueError, AttributeError):
            raise ExtractionError("extraction returned an unreadable result") from None
        if problems:
            invoice.warnings.append(f"{FORMAT_WARNING} ({'; '.join(problems)}); check all fields")
        return invoice


def has_credentials() -> bool:
    return bool(os.environ.get("OPENROUTER_API_KEY"))


def get_extractor(
    session: Session, administration_id: int, *, client: httpx.Client | None = None
) -> OpenRouterExtractor | None:
    """The OpenRouter reader, or None when disabled, unconfigured or over the monthly cap."""
    settings = get_settings()
    if settings.extraction_provider != "openrouter":
        return None
    if not has_credentials():
        log.warning("extraction_provider is openrouter but OPENROUTER_API_KEY is not set")
        return None
    if "/" not in settings.extraction_model:
        log.warning("extraction_model must be an OpenRouter model id such as vendor/model-name")
        return None
    limit = settings.extraction_monthly_limit
    if limit > 0:
        used = vision_usage_this_month(session, administration_id)
        if used >= limit:
            log.warning("monthly vision extraction limit reached (%s/%s)", used, limit)
            return None
    return OpenRouterExtractor(settings.extraction_model, os.environ["OPENROUTER_API_KEY"], client)
