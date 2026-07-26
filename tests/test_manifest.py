"""Tests for manifest construction in fetch.py (SPEC-002 R1).

Fixtures:
- nvda_8k_index.html: real `-index.html` page for NVDA 8-K
  0001045810-26-000019 (fetched 2026-07-26). This is the exact filing that
  produced the SPEC-001 false-positive Exhibit 99.1 warning -- its earnings
  release is named q4fy26pr.htm, not anything matching an "ex99" pattern.
  Source: https://www.sec.gov/Archives/edgar/data/1045810/000104581026000019/0001045810-26-000019-index.html
- amzn_10k_index.html: real `-index.html` page for AMZN 10-K
  0001018724-26-000004, used to test the merge against a form type with
  more exhibit variety (EX-21.1, EX-23.1, EX-101.SCH, ...).
  Source: https://www.sec.gov/Archives/edgar/data/1018724/000101872426000004/0001018724-26-000004-index.html
- mu_8k_index_ex99_typo.html: real `-index.html` page for MU 8-K
  0000723125-19-000172, whose Exhibit 99.1 row is declared with SEC's own
  literal typo "EX-99..1" (double dot) -- confirmed live 2026-07-26. A
  genuine source data-quality issue, not a parsing bug.
  Source: https://www.sec.gov/Archives/edgar/data/723125/000072312519000172/0000723125-19-000172-index.html
"""

from __future__ import annotations

from edgar.fetch import _has_exhibit_991, _normalise_doc_type, _parse_document_format_table
from tests.conftest import FIXTURES_DIR


def test_manifest_identifies_exhibit_991_by_type_not_filename():
    """The exact NVDA case: q4fy26pr.htm matches no filename regex for
    Exhibit 99.1, but the SEC-declared type resolves it correctly."""
    html = (FIXTURES_DIR / "nvda_8k_index.html").read_bytes()

    types = _parse_document_format_table(html)

    assert types["q4fy26pr.htm"][0] == "EX-99.1"
    assert types["q4fy26cfocommentary.htm"][0] == "EX-99.2"
    # Sanity: filename really doesn't look like an exhibit 99.1 name.
    assert "99" not in "q4fy26pr.htm".replace(".htm", "")


def test_manifest_type_table_excludes_viewer_support_files():
    """FilingSummary.xml, R*.htm, and the XBRL taxonomy linkbase files (which
    live in the page's separate 'Data Files' table, not 'Document Format
    Files') never appear here -- confirmed against real AMZN 10-K data
    (ARCHITECTURE §3.6). Only the 'Document Format Files' table is parsed."""
    html = (FIXTURES_DIR / "amzn_10k_index.html").read_bytes()

    types = _parse_document_format_table(html)

    assert "FilingSummary.xml" not in types
    assert "R1.htm" not in types
    assert "MetaLinks.json" not in types
    assert "amzn-20251231.xsd" not in types  # lives in the "Data Files" table
    # But real filed documents are present, with real types.
    assert types["amzn-20251231.htm"][0] == "10-K"
    assert types["amzn-20251231xex211.htm"][0] == "EX-21.1"


def test_manifest_type_table_missing_returns_empty():
    html = b"<html><body><p>no table here</p></body></html>"
    assert _parse_document_format_table(html) == {}


def test_normalise_doc_type_collapses_repeated_dots():
    assert _normalise_doc_type("EX-99..1") == "EX-99.1"
    assert _normalise_doc_type("  ex-99.1  ") == "EX-99.1"
    assert _normalise_doc_type("EX-99.1") == "EX-99.1"  # already canonical -- no-op


def test_has_exhibit_991_tolerates_real_mu_typo():
    html = (FIXTURES_DIR / "mu_8k_index_ex99_typo.html").read_bytes()
    types = _parse_document_format_table(html)

    assert types["a2020q1exhibit991-pres.htm"][0] == "EX-99..1"  # raw, unnormalised
    assert _has_exhibit_991(types, "0000723125-19-000172") is True


def test_has_exhibit_991_warns_when_normalisation_changes_the_string(caplog):
    types = {"a2020q1exhibit991-pres.htm": ("EX-99..1", "2020 Q1 EXHIBIT 99.1 PRESS RELEASE")}

    with caplog.at_level("WARNING"):
        result = _has_exhibit_991(types, "0000723125-19-000172")

    assert result is True
    assert any("Normalising" in m and "EX-99..1" in m for m in caplog.messages)


def test_has_exhibit_991_no_warning_when_type_already_canonical(caplog):
    types = {"amzn-20251231xex991.htm": ("EX-99.1", "EX-99.1")}

    with caplog.at_level("WARNING"):
        result = _has_exhibit_991(types, "0001018724-26-000002")

    assert result is True
    assert not any("Normalising" in m for m in caplog.messages)


def test_manifest_type_table_ignores_data_files_table():
    """The page has a second `tableFile`-class table ('Data Files') that
    must not be mistaken for the Document Format Files table."""
    html = b"""
    <html><body>
    <table class="tableFile" summary="Data Files">
      <tr><th>Seq</th><th>Description</th><th>Document</th><th>Type</th><th>Size</th></tr>
      <tr><td>1</td><td>DECOY</td><td><a href="x">decoy.xml</a></td><td>EX-99.1</td><td>1</td></tr>
    </table>
    </body></html>
    """
    assert _parse_document_format_table(html) == {}
