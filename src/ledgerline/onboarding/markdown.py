"""A small, safe Markdown renderer for the user guide.

Supports headings, paragraphs, nested lists, block quotes, fenced code, tables, rules and the
inline forms bold, italic, code and links. All text is HTML-escaped; only http(s), mailto,
same-site paths and #anchors survive as links.
"""
import html
import re
from urllib.parse import urlsplit

_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_HEADING = re.compile(r"^(#{1,4})\s+(.+?)\s*#*\s*$")
_RULE = re.compile(r"^\s*(-{3,}|\*{3,})\s*$")
_ITEM = re.compile(r"^(\s*)([-*]|\d+[.)])\s+(.*)$")
_TABLE_SEP = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")
_CODE = re.compile(r"`([^`\n]+)`")
_LINK = re.compile(r"\[([^\]\n]+)\]\(([^)\s]+)\)")
_BOLD = re.compile(r"\*\*(.+?)\*\*")
_ITALIC = re.compile(r"(?<![\w*])\*([^*\n]+)\*(?![\w*])")
_ALLOWED_SCHEMES = {"http", "https", "mailto"}


def safe_url(url: str) -> str | None:
    if not url or any(ord(c) < 33 or ord(c) == 127 for c in url):
        return None
    if url.startswith("//"):
        return None
    if url.startswith(("/", "#")):
        return url
    scheme = urlsplit(url).scheme.lower()
    return url if scheme in _ALLOWED_SCHEMES else None


def _emphasis(text: str) -> str:
    return _ITALIC.sub(r"<em>\1</em>", _BOLD.sub(r"<strong>\1</strong>", text))


def inline(text: str) -> str:
    stash: list[str] = []

    def keep(fragment: str) -> str:
        stash.append(fragment)
        return f"\x00{len(stash) - 1}\x00"

    text = text.replace("\x00", "")
    text = _CODE.sub(lambda m: keep(f"<code>{html.escape(m.group(1))}</code>"), text)
    text = html.escape(text, quote=True)

    def link(m: re.Match) -> str:
        label, target = m.group(1), html.unescape(m.group(2))
        url = safe_url(target)
        if url is None:
            return _emphasis(label)
        external = not url.startswith(("/", "#"))
        extra = ' target="_blank" rel="noopener noreferrer"' if external else ""
        return keep(f'<a href="{html.escape(url, quote=True)}"{extra}>{_emphasis(label)}</a>')

    text = _emphasis(_LINK.sub(link, text))
    return re.sub(r"\x00(\d+)\x00", lambda m: stash[int(m.group(1))], text)


def _slug(text: str, used: set[str]) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", re.sub(r"[`*]", "", text).lower()).strip("-") or "section"
    slug, n = base, 2
    while slug in used:
        slug, n = f"{base}-{n}", n + 1
    used.add(slug)
    return slug


def _cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _list(lines: list[str], i: int) -> tuple[str, int]:
    first = _ITEM.match(lines[i])
    base = len(first.group(1).expandtabs(4))
    tag = "ol" if first.group(2)[0].isdigit() else "ul"
    out = [f"<{tag}>"]
    while i < len(lines):
        m = _ITEM.match(lines[i])
        if not m or len(m.group(1).expandtabs(4)) < base:
            break
        if len(m.group(1).expandtabs(4)) > base:
            nested, i = _list(lines, i)
            out[-1] = out[-1][: -len("</li>")] + nested + "</li>"
            continue
        parts = [m.group(3)]
        i += 1
        while i < len(lines) and lines[i].strip() and not _ITEM.match(lines[i]) \
                and lines[i].startswith(" "):
            parts.append(lines[i].strip())
            i += 1
        out.append(f"<li>{inline(' '.join(parts))}</li>")
    out.append(f"</{tag}>")
    return "".join(out), i


def render_markdown(source: str) -> tuple[str, list[tuple[int, str, str]]]:
    """Return (html, toc) where toc is a list of (level, text, anchor) for h2 and h3."""
    lines = _COMMENT.sub("", source.replace("\r\n", "\n")).split("\n")
    out: list[str] = []
    toc: list[tuple[int, str, str]] = []
    used: set[str] = set()
    para: list[str] = []

    def flush() -> None:
        if para:
            out.append(f"<p>{inline(' '.join(para))}</p>")
            para.clear()

    i = 0
    while i < len(lines):
        line = lines[i]
        if line.strip().startswith("```"):
            flush()
            i += 1
            code = []
            while i < len(lines) and not lines[i].strip().startswith("```"):
                code.append(lines[i])
                i += 1
            i += 1
            out.append(f"<pre><code>{html.escape(chr(10).join(code))}</code></pre>")
            continue
        if not line.strip():
            flush()
            i += 1
            continue
        heading = _HEADING.match(line)
        if heading:
            flush()
            level, text = len(heading.group(1)), heading.group(2)
            anchor = _slug(text, used)
            out.append(f'<h{level} id="{anchor}">{inline(text)}</h{level}>')
            if level in (2, 3):
                toc.append((level, re.sub(r"[`*]", "", text), anchor))
            i += 1
            continue
        if _RULE.match(line):
            flush()
            out.append("<hr>")
            i += 1
            continue
        if line.lstrip().startswith(">"):
            flush()
            quote = []
            while i < len(lines) and lines[i].lstrip().startswith(">"):
                quote.append(lines[i].lstrip()[1:].strip())
                i += 1
            out.append(f"<blockquote><p>{inline(' '.join(quote))}</p></blockquote>")
            continue
        if line.lstrip().startswith("|") and i + 1 < len(lines) and _TABLE_SEP.match(lines[i + 1]):
            flush()
            head = "".join(f"<th>{inline(c)}</th>" for c in _cells(line))
            rows = []
            i += 2
            while i < len(lines) and lines[i].lstrip().startswith("|"):
                rows.append("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in _cells(lines[i]))
                            + "</tr>")
                i += 1
            out.append('<div class="table-wrap"><table class="table"><thead><tr>' + head
                       + "</tr></thead><tbody>" + "".join(rows) + "</tbody></table></div>")
            continue
        if _ITEM.match(line):
            flush()
            block, i = _list(lines, i)
            out.append(block)
            continue
        para.append(line.strip())
        i += 1
    flush()
    return "\n".join(out), toc
