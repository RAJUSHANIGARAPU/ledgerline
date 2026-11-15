import re
from pathlib import Path

import pytest
from markupsafe import Markup

from ledgerline import i18n
from ledgerline.i18n import CATALOGUES, EN, NL, parse_accept_language
from ledgerline.models import Role

SRC = Path(__file__).resolve().parents[2] / "src" / "ledgerline"
PLACEHOLDER = re.compile(r"\{(\w+)\}")
TEMPLATE_KEY = re.compile(r"""\bt\(\s*'([\w.]+)'""")
PYTHON_KEY = re.compile(r'(?:\btr\(\w+,\s*|\bsay\()"([\w.]+)"')
JS_KEY = re.compile(r"""\b(?:say|plural)\(\s*[`"]([\w.]+)""")
DYNAMIC_PREFIXES = ("role.",)


def _sources(pattern, *globs):
    for glob in globs:
        for path in sorted(SRC.glob(glob)):
            yield path, pattern.findall(path.read_text())


def test_both_languages_define_the_same_keys():
    assert set(EN) == set(NL), (set(EN) ^ set(NL))


@pytest.mark.parametrize("key", sorted(EN))
def test_placeholders_match_between_languages(key):
    assert set(PLACEHOLDER.findall(EN[key])) == set(PLACEHOLDER.findall(NL[key])), key


@pytest.mark.parametrize("lang", sorted(CATALOGUES))
def test_no_empty_or_markup_bearing_messages(lang):
    for key, message in CATALOGUES[lang].items():
        assert message.strip(), key
        assert not re.search(r'[<>&"]', message), f"{lang}:{key} contains markup characters"


def test_dutch_is_not_a_copy_of_english():
    same = {k for k in EN if EN[k] == NL[k]}
    allowed = {"nav.menu", "foot.account", "foot.privacy", "role.accountant", "auth.totp.code"}
    assert same <= allowed, same - allowed


def test_every_key_used_in_templates_code_and_scripts_exists():
    used = set()
    for _, keys in _sources(TEMPLATE_KEY, "templates/**/*.html"):
        used.update(keys)
    for _, keys in _sources(PYTHON_KEY, "**/*.py"):
        used.update(keys)
    for _, keys in _sources(JS_KEY, "static/js/capture.js"):
        for key in keys:
            full = f"js.capture.{key}"
            if key.endswith("."):  # err.<code>, covered by the queue code test
                continue
            plural = f"{full}_one" in EN and f"{full}_other" in EN
            used.update({f"{full}_one", f"{full}_other"} if plural else {full})
    missing = {k for k in used if k not in EN and not k.startswith(DYNAMIC_PREFIXES)}
    assert not missing, missing
    assert len(used) > 40


def test_every_error_code_the_queue_stores_has_a_message():
    queue = (SRC / "static/js/capture-queue.js").read_text()
    codes = set(re.findall(r'code: "(\w+)"', queue))
    codes |= set(re.findall(r'\d{3}: "(\w+)"', queue))
    assert len(codes) >= 7, codes
    assert {c for c in codes if f"js.capture.err.{c}" not in EN} == set()


def test_every_role_has_a_label_in_both_languages():
    for role in Role:
        assert f"role.{role.value}" in EN and f"role.{role.value}" in NL


def test_every_inbox_error_code_shown_on_capture_is_mapped():
    from ledgerline.mobile.routes import FRIENDLY

    assert set(FRIENDLY.values()) <= set(EN)


def test_bundle_strips_the_prefix_and_falls_back_to_english():
    nl = i18n.bundle("nl", "js.capture.")
    assert nl["retry"] == NL["js.capture.retry"]
    assert set(nl) == {k[len("js.capture."):] for k in EN if k.startswith("js.capture.")}


@pytest.mark.parametrize("header,expected", [
    ("nl", "nl"), ("nl-NL,nl;q=0.9,en;q=0.8", "nl"), ("en-US,en;q=0.9,nl;q=0.8", "en"),
    ("de,nl;q=0.5", "nl"), ("fr,de", None), ("", None), ("*", None), ("nl;q=0,en", "en"),
    ("nl;q=abc,en;q=0.5", "en"),
])
def test_accept_language_parsing(header, expected):
    assert parse_accept_language(header) == expected


def test_template_translator_escapes_parameters_but_not_markup():
    t = i18n.template_translator("en")
    escaped = str(t("captured.duplicate", name="<b>x</b>"))
    assert escaped == "&lt;b&gt;x&lt;/b&gt; is already in your inbox."
    assert str(t("captured.duplicate", name=Markup("<b>x</b>"))).startswith("<b>x</b>")


def test_unknown_key_returns_the_key_instead_of_failing():
    assert i18n.translate("nl", "no.such.key") == "no.such.key"
