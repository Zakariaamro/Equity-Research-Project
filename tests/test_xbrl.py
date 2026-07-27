"""Tests for edgar.xbrl (SPEC-004 R2).

Fixtures: trimmed real companyfacts responses (tests/fixtures/companyfacts_trimmed_*.json).
"""

from __future__ import annotations

import json

import pytest

from edgar import config, db, xbrl
from edgar.edgar_client import EdgarNotFoundError
from tests.conftest import FIXTURES_DIR

AMZN_CIK = "0001018724"


class FakeXbrlClient:
    def __init__(self, payload: dict | None = None, missing: bool = False) -> None:
        self.payload = payload
        self.missing = missing
        self.calls = 0

    def get_company_facts(self, cik: str) -> dict:
        self.calls += 1
        if self.missing:
            raise EdgarNotFoundError(f"no facts for {cik!r}")
        return self.payload


@pytest.fixture
def conn(tmp_path):
    db_path = tmp_path / "test.db"
    db.init_db(db_path)
    connection = db.get_connection(db_path)
    yield connection
    connection.close()


def _load_fixture(name: str) -> dict:
    return json.loads((FIXTURES_DIR / name).read_text())


AMZN_FACTS = _load_fixture("companyfacts_trimmed_amzn.json")


def test_only_configured_concepts_ingested(conn):
    xbrl.ingest_company(conn, FakeXbrlClient(AMZN_FACTS), AMZN_CIK)

    concepts = {row["concept"] for row in conn.execute("SELECT DISTINCT concept FROM xbrl_facts").fetchall()}
    assert concepts
    assert concepts <= config.ALL_CONFIGURED_CONCEPTS


def test_only_declared_units_stored(conn):
    xbrl.ingest_company(conn, FakeXbrlClient(AMZN_FACTS), AMZN_CIK)

    rows = conn.execute("SELECT concept, unit FROM xbrl_facts").fetchall()
    for row in rows:
        canonical = next(
            name for name, ci in config.CONCEPT_REGISTRY.items() if row["concept"] in ci.aliases
        )
        assert row["unit"] == config.CONCEPT_REGISTRY[canonical].unit


def test_duration_facts_have_duration_days_instant_facts_null(conn):
    xbrl.ingest_company(conn, FakeXbrlClient(AMZN_FACTS), AMZN_CIK)

    for row in conn.execute("SELECT concept, period_start, duration_days FROM xbrl_facts").fetchall():
        if row["period_start"] is None:
            assert row["duration_days"] is None
        else:
            assert row["duration_days"] is not None


def test_unresolved_concept_reported_for_amazon(conn):
    # Amazon does not disclose R&D as its own XBRL line -- confirmed absent live.
    result = xbrl.ingest_company(conn, FakeXbrlClient(AMZN_FACTS), AMZN_CIK)

    assert "rnd_expense" in result["unresolved"]


def test_gross_profit_stale_tag_still_ingested(conn):
    # Real: Amazon's GrossProfit tag only appears in FY2007-2008 filings.
    result = xbrl.ingest_company(conn, FakeXbrlClient(AMZN_FACTS), AMZN_CIK)

    assert "GrossProfit" in result["written_by_concept"]
    assert result["written_by_concept"]["GrossProfit"] > 0


def test_ingest_idempotent(conn):
    client = FakeXbrlClient(AMZN_FACTS)
    first = xbrl.ingest_company(conn, client, AMZN_CIK)
    count_after_first = conn.execute("SELECT COUNT(*) AS n FROM xbrl_facts").fetchone()["n"]

    second = xbrl.ingest_company(conn, client, AMZN_CIK)
    count_after_second = conn.execute("SELECT COUNT(*) AS n FROM xbrl_facts").fetchone()["n"]

    assert count_after_second == count_after_first
    assert sum(second["written_by_concept"].values()) == 0
    assert sum(first["written_by_concept"].values()) > 0


def test_accession_no_null_when_not_tracked_in_filings(conn):
    # None of the fixture's accessions are in `filings` (empty table) -- every
    # row's accession_no must be NULL, never violate the FK, never be dropped.
    xbrl.ingest_company(conn, FakeXbrlClient(AMZN_FACTS), AMZN_CIK)

    rows = conn.execute("SELECT accession_no FROM xbrl_facts").fetchall()
    assert rows
    assert all(row["accession_no"] is None for row in rows)


def test_accession_no_populated_when_tracked_in_filings(conn):
    # Pick a real accn from the fixture and pre-register it in `filings`.
    revenue_entries = AMZN_FACTS["facts"]["us-gaap"]["OperatingIncomeLoss"]["units"]["USD"]
    known_accn = revenue_entries[0]["accn"]
    conn.execute(
        """
        INSERT INTO companies (cik, ticker, name, fiscal_year_end) VALUES ('0001018724', 'AMZN', 'x', '1231')
        ON CONFLICT(cik) DO NOTHING
        """
    )
    conn.execute(
        """
        INSERT INTO filings (accession_no, cik, form_type, filing_date, discovered_at, status)
        VALUES (?, '0001018724', '10-K', '2020-01-01', '2020-01-01T00:00:00', 'fetched')
        """,
        (known_accn,),
    )
    conn.commit()

    xbrl.ingest_company(conn, FakeXbrlClient(AMZN_FACTS), AMZN_CIK)

    row = conn.execute(
        "SELECT accession_no FROM xbrl_facts WHERE concept = 'OperatingIncomeLoss' AND accession_no IS NOT NULL"
    ).fetchone()
    assert row is not None
    assert row["accession_no"] == known_accn


def test_companyfacts_404_raises_typed_error(conn):
    with pytest.raises(xbrl.XbrlIngestError):
        xbrl.ingest_company(conn, FakeXbrlClient(missing=True), AMZN_CIK)


def test_restated_period_all_values_retained(conn):
    # Real restated period: NetIncomeLoss for 2012-01-01/2012-03-31 has 4 filed
    # values (130M, 201M anomaly, 130M corrected, 130M) -- all must be kept.
    xbrl.ingest_company(conn, FakeXbrlClient(AMZN_FACTS), AMZN_CIK)

    rows = conn.execute(
        "SELECT value, filed_date FROM xbrl_facts WHERE concept = 'NetIncomeLoss' "
        "AND period_start = '2012-01-01' AND period_end = '2012-03-31'"
    ).fetchall()
    assert len(rows) == 4
    latest = max(rows, key=lambda r: r["filed_date"])
    assert latest["value"] == 130000000
