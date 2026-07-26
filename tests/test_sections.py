"""Tests for edgar.sections (SPEC-002 R2/R3/R5).

Fixture: amzn_10k_filing_summary.xml, the real (uncompressed) FilingSummary.xml
for AMZN 10-K 0001018724-26-000004.
Source: gzipped copy already archived at
data/raw/0001018724/0001018724-26-000004/FilingSummary.xml.gz (fetched
2026-07-25 as part of SPEC-001's live discover run).
"""

from __future__ import annotations

import gzip
import hashlib

import pytest

from edgar import db, section_store, sections
from edgar.edgar_client import EdgarNotFoundError
from tests.conftest import FIXTURES_DIR

ACCESSION = "0001018724-26-000004"


class FakeSectionsClient:
    def __init__(self, r_file_contents=None, missing=None) -> None:
        self.r_file_contents = r_file_contents or {}
        self.missing = missing or set()
        self.requested: list[str] = []

    def get_archive_file(self, cik: str, accession_no: str, filename: str) -> bytes:
        self.requested.append(filename)
        if filename in self.missing:
            raise EdgarNotFoundError(f"{filename} missing")
        if filename in self.r_file_contents:
            return self.r_file_contents[filename]
        # Generic but valid content so every other selected report extracts fine.
        return f"<html><body><table><tr><td>{filename}</td><td>1,000</td></tr></table></body></html>".encode()


@pytest.fixture
def conn(tmp_path):
    db_path = tmp_path / "test.db"
    db.init_db(db_path)
    connection = db.get_connection(db_path)
    yield connection
    connection.close()


@pytest.fixture
def archive_dir(tmp_path):
    d = tmp_path / "raw" / "0001018724" / ACCESSION
    d.mkdir(parents=True)
    xml_bytes = (FIXTURES_DIR / "amzn_10k_filing_summary.xml").read_bytes()
    (d / "FilingSummary.xml.gz").write_bytes(gzip.compress(xml_bytes))
    return d


def insert_10k(conn, raw_path, accession_no=ACCESSION, status="fetched"):
    conn.execute(
        """
        INSERT INTO filings (
            accession_no, cik, form_type, filing_date, period_end, items,
            primary_doc, raw_path, discovered_at, status
        ) VALUES (?, '0001018724', '10-K', '2026-02-06', '2025-12-31', NULL,
                  'amzn-20251231.htm', ?, '2026-02-06T00:00:00', ?)
        """,
        (accession_no, str(raw_path), status),
    )
    conn.commit()


def test_selects_only_content_categories(conn, archive_dir):
    insert_10k(conn, archive_dir)
    status = sections.extract_filing(conn, FakeSectionsClient(), ACCESSION)

    assert status == "sectioned"
    categories = {
        row["category"] for row in conn.execute("SELECT DISTINCT category FROM sections")
    }
    assert categories and categories <= {"Statements", "Notes", "Policies"}
    assert "Cover" not in categories
    assert "Tables" not in categories
    assert "Details" not in categories


def test_note_shortnames_extracted(conn, archive_dir):
    insert_10k(conn, archive_dir)
    sections.extract_filing(conn, FakeSectionsClient(), ACCESSION)

    names = {
        row["short_name"]
        for row in conn.execute("SELECT short_name FROM sections WHERE category = 'Notes'")
    }
    for expected in ["Income Taxes", "Segment Information", "Leases", "Debt"]:
        assert expected in names


def test_statement_reports_all_present(conn, archive_dir):
    insert_10k(conn, archive_dir)
    sections.extract_filing(conn, FakeSectionsClient(), ACCESSION)

    names = {
        row["short_name"]
        for row in conn.execute("SELECT short_name FROM sections WHERE category = 'Statements'")
    }
    for expected in [
        "Consolidated Statements of Operations",
        "Consolidated Balance Sheets",
        "Consolidated Statements of Cash Flows",
        "Consolidated Statements of Comprehensive Income",
        "Consolidated Statements of Stockholders' Equity",
    ]:
        assert expected in names


def test_income_taxes_text_has_intact_figures_and_no_markup(conn, archive_dir):
    insert_10k(conn, archive_dir)
    r18 = (FIXTURES_DIR / "amzn_r18_income_taxes.htm").read_bytes()
    client = FakeSectionsClient(r_file_contents={"R18.htm": r18})
    sections.extract_filing(conn, client, ACCESSION)

    row = conn.execute(
        "SELECT text_hash FROM sections WHERE short_name = 'Income Taxes'"
    ).fetchone()
    assert row is not None
    text = section_store.read_section_text(row["text_hash"])
    assert "<" not in text
    assert "$7.1 billion" in text


def test_apostrophe_shortname_roundtrip(conn, archive_dir):
    insert_10k(conn, archive_dir)
    sections.extract_filing(conn, FakeSectionsClient(), ACCESSION)

    row = conn.execute(
        "SELECT short_name FROM sections WHERE category = 'Notes' AND short_name LIKE '%Stockholders%'"
    ).fetchone()
    assert row is not None
    assert row["short_name"] == "Stockholders' Equity"


def test_text_hash_is_stable_across_whitespace_only_html_changes(conn, archive_dir):
    insert_10k(conn, archive_dir)
    r18 = (FIXTURES_DIR / "amzn_r18_income_taxes.htm").read_bytes()
    client = FakeSectionsClient(r_file_contents={"R18.htm": r18})
    sections.extract_filing(conn, client, ACCESSION)

    row = conn.execute(
        "SELECT text_hash FROM sections WHERE short_name = 'Income Taxes'"
    ).fetchone()
    text = section_store.read_section_text(row["text_hash"])
    assert row["text_hash"] == hashlib.sha256(text.encode("utf-8")).hexdigest()

    # Re-extract from HTML with every existing newline doubled -- more
    # incidental whitespace, same content. (Padding before literal "<td"
    # tags is NOT a safe perturbation here: some cells contain nested
    # tables, and get_text() flattens nested cells with no separator by
    # design, so whitespace injected before a *nested* <td> would leak
    # into that outer cell's text -- a real content change, not noise.)
    padded = r18.replace(b"\n", b"\n\n")
    client2 = FakeSectionsClient(r_file_contents={"R18.htm": padded})
    sections.extract_filing(conn, client2, ACCESSION)

    row2 = conn.execute(
        "SELECT text_hash FROM sections WHERE short_name = 'Income Taxes'"
    ).fetchone()
    assert row2["text_hash"] == row["text_hash"]


def test_extract_is_idempotent(conn, archive_dir):
    insert_10k(conn, archive_dir)
    client = FakeSectionsClient()

    first = sections.extract_pending(conn, client, accession=ACCESSION)
    assert first == [{"accession_no": ACCESSION, "status": "sectioned"}]
    count_after_first = conn.execute("SELECT COUNT(*) AS n FROM sections").fetchone()["n"]
    assert count_after_first > 0
    requested_before = len(client.requested)

    second = sections.extract_pending(conn, client, accession=ACCESSION)

    assert second == []  # nothing left in status='fetched'
    assert len(client.requested) == requested_before  # no new R-file fetches
    count_after_second = conn.execute("SELECT COUNT(*) AS n FROM sections").fetchone()["n"]
    assert count_after_second == count_after_first


def test_extract_force_reprocesses_sectioned_filing(conn, archive_dir):
    insert_10k(conn, archive_dir)
    client = FakeSectionsClient()
    sections.extract_pending(conn, client, accession=ACCESSION)

    results = sections.extract_pending(conn, client, accession=ACCESSION, force=True)

    assert results == [{"accession_no": ACCESSION, "status": "sectioned"}]


def test_filing_summary_missing_skips_without_failing(conn, tmp_path):
    empty_dir = tmp_path / "raw" / "0001018724" / ACCESSION
    empty_dir.mkdir(parents=True)
    insert_10k(conn, empty_dir)

    status = sections.extract_filing(conn, FakeSectionsClient(), ACCESSION)

    assert status == "skipped"
    row = conn.execute(
        "SELECT status FROM filings WHERE accession_no = ?", (ACCESSION,)
    ).fetchone()
    assert row["status"] == "fetched"  # unchanged, retryable


def test_rfile_404_leaves_status_unchanged_and_keeps_other_sections(conn, archive_dir):
    insert_10k(conn, archive_dir)
    client = FakeSectionsClient(missing={"R18.htm"})  # Income Taxes

    status = sections.extract_filing(conn, client, ACCESSION)

    assert status == "failed"
    row = conn.execute(
        "SELECT status FROM filings WHERE accession_no = ?", (ACCESSION,)
    ).fetchone()
    assert row["status"] == "fetched"  # not advanced to 'sectioned'

    missing_row = conn.execute(
        "SELECT * FROM sections WHERE short_name = 'Income Taxes'"
    ).fetchone()
    assert missing_row is None
    other_row = conn.execute(
        "SELECT * FROM sections WHERE short_name = 'Segment Information'"
    ).fetchone()
    assert other_row is not None  # other sections still written


def test_empty_extracted_text_writes_no_row(conn, archive_dir):
    insert_10k(conn, archive_dir)
    client = FakeSectionsClient(r_file_contents={"R3.htm": b"<html><body></body></html>"})

    sections.extract_filing(conn, client, ACCESSION)

    row = conn.execute(
        "SELECT * FROM sections WHERE short_name = 'Consolidated Statements of Cash Flows'"
    ).fetchone()
    assert row is None


def test_extract_writes_hash_only(conn, archive_dir):
    insert_10k(conn, archive_dir)
    sections.extract_filing(conn, FakeSectionsClient(), ACCESSION)

    columns = {row["name"] for row in conn.execute("PRAGMA table_info(sections)")}
    assert "text" not in columns

    rows = conn.execute("SELECT text_hash FROM sections").fetchall()
    assert rows
    for row in rows:
        assert row["text_hash"]
        assert section_store.read_section_text(row["text_hash"])  # readable, no error


def test_write_section_skips_db_write_when_hash_unchanged(conn, archive_dir):
    insert_10k(conn, archive_dir)
    report = sections.ReportRef(
        short_name="Income Taxes", category="Notes", html_file_name="R18.htm", position=1
    )
    text = "Some income taxes text, unchanged across re-extraction."

    sections._write_section(conn, ACCESSION, report, text)
    conn.commit()

    changes_before = conn.total_changes
    sections._write_section(conn, ACCESSION, report, text)  # identical text again
    changes_after = conn.total_changes

    assert changes_after == changes_before  # no INSERT/UPDATE executed at all


def test_8k_is_skipped_entirely(conn, tmp_path):
    d = tmp_path / "raw" / "0001018724" / "0001018724-26-000002"
    d.mkdir(parents=True)
    conn.execute(
        """
        INSERT INTO filings (
            accession_no, cik, form_type, filing_date, period_end, items,
            primary_doc, raw_path, discovered_at, status
        ) VALUES ('0001018724-26-000002', '0001018724', '8-K', '2026-02-05', '2026-02-05',
                  '2.02,9.01', 'amzn-20260205.htm', ?, '2026-02-05T00:00:00', 'fetched')
        """,
        (str(d),),
    )
    conn.commit()

    status = sections.extract_filing(conn, FakeSectionsClient(), "0001018724-26-000002")

    assert status == "skipped"
    count = conn.execute("SELECT COUNT(*) AS n FROM sections").fetchone()["n"]
    assert count == 0
