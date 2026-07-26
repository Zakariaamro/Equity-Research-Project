"""Tests for edgar.html_text.html_to_text (SPEC-002 R4).

Fixtures:
- amzn_r18_income_taxes.htm: real R-file for AMZN 10-K 0001018724-26-000004's
  "Income Taxes" note. Note this is served wrapped in an SGML
  <DOCUMENT><TYPE>XML...<TEXT><html>...</html></TEXT></DOCUMENT> envelope --
  confirmed live 2026-07-26, see ARCHITECTURE.md §3.7.
  Source: https://www.sec.gov/Archives/edgar/data/1018724/000101872426000004/R18.htm
- amzn_r4_statements_operations.htm: real R-file for the same filing's
  "Consolidated Statements of Operations".
  Source: https://www.sec.gov/Archives/edgar/data/1018724/000101872426000004/R4.htm
"""

from __future__ import annotations

import pytest

from edgar.html_text import _strip_sgml_envelope, html_to_text
from tests.conftest import FIXTURES_DIR


def test_html_to_text_strips_markup():
    html = """
    <html><body>
    <style>.x { color: red; }</style>
    <script>alert('x');</script>
    <p>Net <b>income</b> was <span class="hl">strong</span>.</p>
    </body></html>
    """
    text = html_to_text(html)
    assert "<" not in text
    assert ">" not in text
    assert "color: red" not in text
    assert "alert" not in text
    assert "Net income was strong." in text


def test_html_to_text_preserves_numbers():
    html = """
    <html><body>
    <p>Revenue was <span>$</span><span>1,234,567</span> thousand,
    down <span>(56.78)</span>% with a footnote<a href="#fn1">(1)</a>.</p>
    </body></html>
    """
    text = html_to_text(html)
    assert "$1,234,567" in text
    assert "(56.78)" in text
    assert "footnote(1)" in text


def test_html_to_text_table_rows_stay_on_one_line():
    html = """
    <html><body>
    <table>
      <tr><td>Net sales</td><td>$</td><td>637,959</td></tr>
      <tr><td></td><td></td><td></td></tr>
      <tr><td>Cost of sales</td><td>$</td><td>(325,978)</td></tr>
    </table>
    </body></html>
    """
    text = html_to_text(html)
    lines = [line for line in text.split("\n") if line.strip()]
    assert any("Net sales" in line and "637,959" in line for line in lines)
    assert any("Cost of sales" in line and "(325,978)" in line for line in lines)
    # Spacer row produced no blank/near-empty line between them.
    net_sales_idx = next(i for i, l in enumerate(lines) if "Net sales" in l)
    cost_idx = next(i for i, l in enumerate(lines) if "Cost of sales" in l)
    assert cost_idx == net_sales_idx + 1


def test_html_to_text_normalises_non_breaking_space_without_corrupting_digits():
    html = "<html><body><p>Total:" + "\xa0" + "$1,234" + "\xa0" + "million</p></body></html>"
    text = html_to_text(html)
    assert "\xa0" not in text
    assert "$1,234" in text
    assert "million" in text


def test_html_to_text_incidental_source_newline_does_not_split_a_number():
    # Pretty-printed source HTML sometimes has a literal newline inside a
    # single text node; that must collapse to a space, not a line break,
    # per normal HTML whitespace semantics.
    html = "<html><body><p>Net income\nwas\n$1,234</p></body></html>"
    text = html_to_text(html)
    assert "\n" not in text.strip()
    assert "$1,234" in text


def test_html_to_text_raises_when_no_body_found():
    with pytest.raises(ValueError):
        html_to_text("<table><tr><td>fragment, no html/body wrapper</td></tr></table>")


def test_strip_sgml_envelope_removes_wrapper_and_preserves_html():
    wrapped = (
        b"<DOCUMENT>\n<TYPE>XML\n<SEQUENCE>31\n<FILENAME>R18.htm\n"
        b"<DESCRIPTION>IDEA: XBRL DOCUMENT\n<TEXT>\n"
        b"<html><body><p>content</p></body></html>\n"
        b"</TEXT>\n</DOCUMENT>\n"
    )
    stripped = _strip_sgml_envelope(wrapped)
    assert stripped == b"<html><body><p>content</p></body></html>"


def test_strip_sgml_envelope_is_a_noop_for_plain_html():
    plain = b"<html><body><p>content</p></body></html>"
    assert _strip_sgml_envelope(plain) == plain


def test_html_to_text_real_sgml_wrapped_rfile_has_no_envelope_leakage():
    """The real fixture is SGML-wrapped as served by SEC. None of the
    envelope's own header text (DOCUMENT/TYPE/SEQUENCE/FILENAME/DESCRIPTION
    labels) should leak into the extracted text."""
    raw = (FIXTURES_DIR / "amzn_r18_income_taxes.htm").read_bytes()
    assert raw.lstrip().startswith(b"<DOCUMENT>")  # confirms the fixture is wrapped

    text = html_to_text(raw)

    assert "<DOCUMENT>" not in text
    assert "IDEA: XBRL DOCUMENT" not in text
    assert "Income Taxes" in text
    assert "$7.1 billion" in text


def test_html_to_text_real_income_taxes_note_has_intact_figures():
    html = (FIXTURES_DIR / "amzn_r18_income_taxes.htm").read_bytes()
    text = html_to_text(html)

    assert "<" not in text
    assert "Income Taxes" in text
    # Real figures from the note, must survive with digits/separators intact.
    assert "$7.1 billion" in text
    assert "$19.1 billion" in text


def test_html_to_text_real_statement_table_rows_readable():
    html = (FIXTURES_DIR / "amzn_r4_statements_operations.htm").read_bytes()
    text = html_to_text(html)

    assert "<" not in text
    assert "Net sales" in text or "Total net sales" in text
    # At least one line reads as a label followed by numeric values, not a
    # run-on paragraph merging every row together.
    lines = [line for line in text.split("\n") if line.strip()]
    assert any(any(ch.isdigit() for ch in line) for line in lines)
    assert len(lines) > 3
