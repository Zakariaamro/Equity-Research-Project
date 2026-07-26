"""Shared HTML-to-clean-text conversion.

Used by sections.py (SPEC-002) for R-files and, per the spec, will also be
used by SPEC-003 for MD&A / Risk Factors / Exhibit 99.1 extraction.

The overriding rule: numbers, currency symbols, parentheses, and footnote
markers must survive byte-for-byte. These feed an analyst tool -- a
silently altered figure is the worst possible bug class here. To guarantee
that, text is reconstructed by walking the parse tree and concatenating
NavigableStrings exactly as found (no separators inserted between inline
elements), so digits split across adjacent tags (common in XBRL-tagged
HTML) are never pulled apart by a synthesized space. Whitespace collapsing
happens only as a final normalisation pass over already-concatenated text,
never between characters that were adjacent in the source.
"""

from __future__ import annotations

import re

from bs4 import BeautifulSoup, Comment, NavigableString, Tag

_BLOCK_TAGS = {
    "p", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6",
    "ul", "ol", "section", "article", "header", "footer",
}
_SKIP_TAGS = {"script", "style", "head", "title", "meta"}

# Typographic characters that should normalise to their plain equivalents
# without touching digits themselves.
_CHAR_REPLACEMENTS = {
    "\xa0": " ",  # non-breaking space
    " ": " ",  # thin space
    "​": "",  # zero-width space
    "‘": "'", "’": "'",  # curly single quotes
    "“": '"', "”": '"',  # curly double quotes
    "–": "-", "—": "-",  # en/em dash
}


_SGML_ENVELOPE_OPEN_RE = {
    bytes: re.compile(rb"\A\s*<DOCUMENT>.*?<TEXT>\s*", re.IGNORECASE | re.DOTALL),
    str: re.compile(r"\A\s*<DOCUMENT>.*?<TEXT>\s*", re.IGNORECASE | re.DOTALL),
}
_SGML_ENVELOPE_CLOSE_RE = {
    bytes: re.compile(rb"\s*</TEXT>\s*</DOCUMENT>\s*\Z", re.IGNORECASE),
    str: re.compile(r"\s*</TEXT>\s*</DOCUMENT>\s*\Z", re.IGNORECASE),
}


def _strip_sgml_envelope(html: str | bytes) -> str | bytes:
    """Strip the SGML <DOCUMENT>...<TEXT>...</TEXT></DOCUMENT> wrapper that
    R-files are served in when fetched directly (e.g. .../R18.htm) -- confirmed
    live 2026-07-26, see ARCHITECTURE.md §3.7. Reproducible: HTTP 200,
    content-type text/html, identical on repeat fetch.

    Explicit and required, rather than relying on BeautifulSoup's leniency
    to find <body> despite the wrapper -- that worked by accident and the
    failure mode of a wrong accident is silent mis-parsing, which is what
    this whole module exists to prevent.

    A no-op (returns input unchanged) when the envelope isn't present, so
    plain HTML documents pass through untouched.
    """
    open_re = _SGML_ENVELOPE_OPEN_RE[type(html)]
    close_re = _SGML_ENVELOPE_CLOSE_RE[type(html)]

    open_match = open_re.match(html)
    if not open_match:
        return html

    inner = html[open_match.end():]
    close_match = close_re.search(inner)
    if close_match:
        inner = inner[: close_match.start()]
    return inner


def _render_table(table: Tag) -> str:
    """Render a table as one line per row: cell texts joined, in order."""
    lines: list[str] = []
    for row in table.find_all("tr"):
        cells = row.find_all(["td", "th"])
        cell_texts = [_normalise_inline(cell.get_text()) for cell in cells]
        if not any(cell_texts):
            continue  # spacer rows are common in SEC-rendered tables
        lines.append("  ".join(cell_texts))
    return "\n".join(lines)


def _normalise_inline(text: str) -> str:
    for old, new in _CHAR_REPLACEMENTS.items():
        text = text.replace(old, new)
    # Any whitespace, including newlines incidental to pretty-printed source
    # HTML, collapses per normal HTML rendering semantics -- only our own
    # explicit block/table/br markers (inserted elsewhere) are meaningful
    # line breaks.
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _walk(node, out: list[str]) -> None:
    if isinstance(node, Comment):
        return
    if isinstance(node, NavigableString):
        # Collapse incidental source whitespace (including newlines from
        # pretty-printed HTML) to a single space, per HTML semantics -- this
        # must not be read as a paragraph break. Real paragraph breaks are
        # inserted explicitly below, at block/table/br boundaries.
        out.append(re.sub(r"\s+", " ", str(node)))
        return
    if not isinstance(node, Tag):
        return
    if node.name in _SKIP_TAGS:
        return
    if node.name == "br":
        out.append("\n")
        return
    if node.name == "table":
        out.append("\n")
        out.append(_render_table(node))
        out.append("\n")
        return

    for child in node.children:
        _walk(child, out)

    if node.name in _BLOCK_TAGS:
        out.append("\n")


def html_to_text(html: str | bytes) -> str:
    """Convert R-file (or similar SEC-rendered) HTML into clean plain text.

    - Table rows become one line each: cell values joined with two spaces.
    - Paragraph/block boundaries become line breaks.
    - Scripts, styles, and all markup are stripped.
    - Numbers, currency symbols, parenthesised negatives, and footnote
      markers are preserved exactly as they appear in the source.
    """
    html = _strip_sgml_envelope(html)
    soup = BeautifulSoup(html, "html.parser")
    body = soup.body
    if body is None:
        raise ValueError(
            "html_to_text: no <body> element found after parsing -- refusing to "
            "silently mis-parse. If this is a bare HTML fragment with no <body> "
            "wrapper, wrap it before calling html_to_text."
        )

    pieces: list[str] = []
    for child in body.children:
        _walk(child, pieces)

    text = "".join(pieces)
    for old, new in _CHAR_REPLACEMENTS.items():
        text = text.replace(old, new)

    # Normalise whitespace without touching paragraph structure:
    # collapse horizontal whitespace, then collapse blank-line runs.
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.split("\n")]
    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip("\n")
