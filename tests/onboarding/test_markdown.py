from ledgerline.onboarding.markdown import render_markdown, safe_url


def html_of(text: str) -> str:
    return render_markdown(text)[0]


def test_headings_lists_tables_and_toc():
    out, toc = render_markdown(
        "# T\n## Part one\ntext **bold** and `code<x>`\n\n- a\n  - b\n- c\n\n"
        "| h1 | h2 |\n|---|---|\n| 1 | 2 |\n")
    assert '<h2 id="part-one">Part one</h2>' in out
    assert "<strong>bold</strong>" in out and "<code>code&lt;x&gt;</code>" in out
    assert "<ul><li>a<ul><li>b</li></ul></li><li>c</li></ul>" in out
    assert "<th>h1</th>" in out and "<td>2</td>" in out
    assert toc == [(2, "Part one", "part-one")]


def test_script_tags_are_escaped():
    out = html_of("hello <script>alert(1)</script>\n\n## <img src=x onerror=alert(1)>")
    assert "<script" not in out and "<img" not in out
    assert "&lt;script&gt;" in out


def test_dangerous_links_are_neutralised():
    for url in ("javascript:alert(1)", "JaVaScRiPt:alert(1)", "data:text/html,x",
                "//evil.example/x", "java\tscript:alert(1)", "vbscript:x"):
        out = html_of(f"[click]({url})")
        assert "<a " not in out and "href" not in out, url


def test_safe_links_are_kept_and_external_ones_hardened():
    assert '<a href="/sales?a=1&amp;b=2">x</a>' in html_of("[x](/sales?a=1&b=2)")
    out = html_of("[x](https://example.nl/p)")
    assert 'href="https://example.nl/p"' in out and 'rel="noopener noreferrer"' in out
    assert safe_url("#anchor") == "#anchor"
    assert safe_url("DEPLOY.md") is None


def test_quote_in_link_label_cannot_break_attributes():
    out = html_of('[a" onmouseover="x](/ok)')
    assert 'onmouseover="' not in out


def test_comments_are_dropped_and_code_fences_kept_literal():
    out = html_of("a <!-- verify --> b\n\n```\n<b>x</b>\n```")
    assert "verify" not in out
    assert "<pre><code>&lt;b&gt;x&lt;/b&gt;</code></pre>" in out
