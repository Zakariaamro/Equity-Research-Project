from __future__ import annotations

import pytest

from edgar import db, monitor
from edgar.edgar_client import EdgarNotFoundError
from tests.conftest import FIXTURES_DIR, load_fixture


class FakeClient:
    """Stands in for EdgarClient in monitor tests. No network involved."""

    def __init__(self, submissions: dict, index_html_by_accession: dict | None = None) -> None:
        self._submissions = submissions
        self._index_html = index_html_by_accession or {}
        self.submissions_calls = 0
        self.archive_calls: list[tuple[str, str, str]] = []

    def get_submissions(self, cik: str) -> dict:
        self.submissions_calls += 1
        return self._submissions

    def get_archive_file(self, cik: str, accession_no: str, filename: str) -> bytes:
        self.archive_calls.append((cik, accession_no, filename))
        if accession_no in self._index_html:
            return self._index_html[accession_no]
        raise EdgarNotFoundError(f"no index page for {accession_no}")

    def get_filing_index(self, cik: str, accession_no: str):
        raise NotImplementedError("not needed for monitor tests")


@pytest.fixture
def conn(tmp_path):
    db_path = tmp_path / "test.db"
    db.init_db(db_path)
    connection = db.get_connection(db_path)
    yield connection
    connection.close()


@pytest.fixture
def synthetic_client():
    submissions = load_fixture("synthetic_submissions.json")
    index_html = {
        "0001018724-25-000999": (FIXTURES_DIR / "8k_index_with_202.html").read_bytes(),
        "0001018724-25-000997": (FIXTURES_DIR / "8k_index_no_items.html").read_bytes(),
    }
    return FakeClient(submissions, index_html)


def test_monitor_filters_form_types(conn, synthetic_client):
    discovered = monitor.find_new_filings(conn, synthetic_client, tickers=["AMZN"])
    accessions = {f.accession_no for f in discovered}
    forms = {f.form_type for f in discovered}

    assert "4" not in forms
    assert "S-8" not in forms
    assert "0000000000-26-000001" not in accessions  # the Form 4
    assert "0001018724-25-000996" not in accessions  # the S-8


def test_monitor_filters_8k_items(conn, synthetic_client):
    discovered = monitor.find_new_filings(conn, synthetic_client, tickers=["AMZN"])
    accessions = {f.accession_no for f in discovered}

    assert "0001018724-26-000002" in accessions  # '2.02,9.01' directly in submissions
    assert "0001018724-25-000998" not in accessions  # '5.02' only -> excluded


def test_monitor_8k_items_whitespace_tolerant(conn, synthetic_client):
    discovered = monitor.find_new_filings(conn, synthetic_client, tickers=["AMZN"])
    by_accession = {f.accession_no: f for f in discovered}

    # submissions items is "9.01, 2.02" (space after the comma) -- must still match.
    assert "0001018724-25-000995" in by_accession
    assert "2.02" in by_accession["0001018724-25-000995"].items.split(",")


def test_monitor_8k_falls_back_to_index(conn, synthetic_client):
    discovered = monitor.find_new_filings(conn, synthetic_client, tickers=["AMZN"])
    accessions = {f.accession_no for f in discovered}

    # items empty in submissions, but the index page has Item 2.02 -> included
    assert "0001018724-25-000999" in accessions
    assert (
        "0001018724",
        "0001018724-25-000999",
        "0001018724-25-000999-index.html",
    ) in synthetic_client.archive_calls

    # items empty in submissions AND nothing in the index page -> excluded
    assert "0001018724-25-000997" not in accessions


def test_monitor_is_idempotent(conn, synthetic_client):
    first = monitor.find_new_filings(conn, synthetic_client, tickers=["AMZN"])
    second = monitor.find_new_filings(conn, synthetic_client, tickers=["AMZN"])

    assert len(first) > 0
    assert second == []
    row_count = conn.execute("SELECT COUNT(*) AS n FROM filings").fetchone()["n"]
    assert row_count == len(first)


def test_monitor_dry_run_writes_nothing(conn, synthetic_client):
    discovered = monitor.find_new_filings(
        conn, synthetic_client, tickers=["AMZN"], dry_run=True
    )
    assert len(discovered) > 0
    row_count = conn.execute("SELECT COUNT(*) AS n FROM filings").fetchone()["n"]
    assert row_count == 0


def test_monitor_respects_limit(conn, synthetic_client):
    discovered = monitor.find_new_filings(conn, synthetic_client, tickers=["AMZN"], limit=1)
    assert len(discovered) == 1
    row_count = conn.execute("SELECT COUNT(*) AS n FROM filings").fetchone()["n"]
    assert row_count == 1


def test_monitor_accession_already_known_is_skipped_silently(conn, synthetic_client):
    monitor.find_new_filings(conn, synthetic_client, tickers=["AMZN"])
    submissions_calls_before = synthetic_client.submissions_calls
    second = monitor.find_new_filings(conn, synthetic_client, tickers=["AMZN"])
    assert second == []
    assert synthetic_client.submissions_calls == submissions_calls_before + 1  # still polled SEC


# ---- Real fixture sanity check (ties directly to SPEC-001 acceptance criteria) ----


@pytest.fixture
def real_amzn_client():
    return FakeClient(load_fixture("amzn_submissions.json"))


def test_monitor_real_amzn_fixture_matches_acceptance_targets(conn, real_amzn_client):
    discovered = monitor.find_new_filings(conn, real_amzn_client, tickers=["AMZN"])
    by_accession = {f.accession_no: f for f in discovered}

    assert by_accession["0001018724-26-000004"].form_type == "10-K"
    assert by_accession["0001018724-26-000004"].filing_date == "2026-02-06"
    assert by_accession["0001018724-26-000014"].form_type == "10-Q"
    assert by_accession["0001018724-26-000014"].filing_date == "2026-04-30"

    forms = {f.form_type for f in discovered}
    assert forms <= {"10-K", "10-Q", "8-K"}

    for filing in discovered:
        if filing.form_type == "8-K":
            assert "2.02" in filing.items.split(",")
