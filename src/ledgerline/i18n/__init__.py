"""Two-language interface text: a flat key catalogue per language, no external dependency.

The language comes from the `ledgerline_lang` cookie (set by the NL|EN switch), then the
Accept-Language header, then English. Catalogue messages are trusted text; parameters are
escaped when a template renders them. tests/i18n checks that both languages hold the same
keys and placeholders and that every key used in a template, route or script exists.
"""
import re
from collections.abc import Callable

from markupsafe import Markup

from ledgerline.i18n.en import MESSAGES as EN
from ledgerline.i18n.nl import MESSAGES as NL

COOKIE = "ledgerline_lang"
COOKIE_MAX_AGE = 365 * 24 * 3600
DEFAULT = "en"
CATALOGUES = {"en": EN, "nl": NL}
SUPPORTED = tuple(CATALOGUES)


def parse_accept_language(header: str) -> str | None:
    """The best supported language in an Accept-Language header, or None."""
    ranked = []
    for position, part in enumerate(header.split(",")):
        tag, _, params = part.strip().partition(";")
        match = re.search(r"q\s*=\s*([^;,\s]+)", params)
        try:
            quality = float(match.group(1)) if match else 1.0
        except ValueError:
            quality = 0.0
        base = tag.strip().split("-")[0].lower()
        if base in CATALOGUES and quality > 0:
            ranked.append((-quality, position, base))
    return min(ranked)[2] if ranked else None


def language(request) -> str:
    cached = getattr(request.state, "lang", None)
    if cached:
        return cached
    chosen = request.cookies.get(COOKIE)
    if chosen not in CATALOGUES:
        chosen = parse_accept_language(request.headers.get("accept-language", "")) or DEFAULT
    request.state.lang = chosen
    return chosen


def translate(lang: str, key: str, **params) -> str:
    """Plain text for flash messages and JSON. A key missing in `lang` falls back to English."""
    message = CATALOGUES.get(lang, EN).get(key) or EN.get(key) or key
    return message.format(**params) if params else message


def tr(request, key: str, **params) -> str:
    return translate(language(request), key, **params)


def template_translator(lang: str) -> Callable[..., Markup]:
    """`t()` for templates: the message is trusted, each parameter is escaped unless Markup."""
    def t(key: str, **params) -> Markup:
        message = CATALOGUES.get(lang, EN).get(key) or EN.get(key) or key
        # Catalogue text is trusted; Markup.format escapes every parameter that is not Markup.
        trusted = Markup(message)  # noqa: S704
        return trusted.format(**params) if params else trusted
    return t


def bundle(lang: str, prefix: str) -> dict[str, str]:
    """Every message under `prefix`, with the prefix removed, for scripts that cannot call t()."""
    merged = {**EN, **CATALOGUES.get(lang, {})}
    return {k[len(prefix):]: v for k, v in merged.items() if k.startswith(prefix)}
