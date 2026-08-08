from __future__ import annotations

import json
from pathlib import Path

import pytest

from edgar import config

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def sec_user_agent_env(monkeypatch):
    """SEC_USER_AGENT is set by default; tests for the unset case delete it explicitly."""
    monkeypatch.setenv("SEC_USER_AGENT", "Test Agent test@example.com")


@pytest.fixture(autouse=True)
def sections_dir(tmp_path, monkeypatch):
    """Isolate every test from the real data/sections/ content store (SPEC-003)."""
    d = tmp_path / "sections"
    monkeypatch.setattr(config, "SECTIONS_DIR", d)
    return d


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES_DIR / name).read_text())


# SPEC-004 R3a: compute_metrics restricts annual/quarterly periods to real
# filings.period_end values. These are the real period ends the trimmed
# companyfacts fixtures actually carry duration facts for -- see how each
# fixture was built (tests/fixtures/companyfacts_trimmed_*.json).
FIXTURE_FISCAL_PERIOD_ENDS: dict[str, dict[str, list[str]]] = {
    "0001018724": {  # AMZN
        "10-K": ["2025-12-31", "2024-12-31"],
        "10-Q": ["2025-06-30", "2025-09-30", "2026-03-31"],
    },
    "0001045810": {  # NVDA
        "10-K": ["2019-01-27", "2020-01-26", "2021-01-31", "2022-01-30", "2025-01-26", "2026-01-25"],
        # 2021-08-01/2022-07-31: real Q2 FY2023 gaming-GPU inventory write-down
        # (RANGE_EXCEPTIONS incremental_gross_margin case).
        "10-Q": ["2025-04-27", "2025-07-27", "2025-10-26", "2026-04-26", "2021-08-01", "2022-07-31"],
    },
    "0000723125": {  # MU
        "10-K": ["2025-08-28", "2024-08-29"],
        # 2024-02-29: real fiscal Q2 2024 discrete tax benefit
        # (RANGE_EXCEPTIONS effective_tax_rate case).
        "10-Q": ["2025-11-27", "2026-02-26", "2026-05-28", "2024-02-29"],
    },
}


def insert_fixture_filings(conn, ciks: list[str] | None = None) -> None:
    """Insert filings rows for FIXTURE_FISCAL_PERIOD_ENDS so compute_metrics has a
    real fiscal-period-end set to restrict against (SPEC-004 R3a)."""
    for cik, forms in FIXTURE_FISCAL_PERIOD_ENDS.items():
        if ciks is not None and cik not in ciks:
            continue
        for form_type, ends in forms.items():
            for i, end in enumerate(ends):
                accession_no = f"{cik}-fixture-{form_type}-{i}"
                conn.execute(
                    """
                    INSERT INTO filings (accession_no, cik, form_type, filing_date, period_end, discovered_at, status)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(accession_no) DO NOTHING
                    """,
                    (accession_no, cik, form_type, end, end, f"{end}T00:00:00", "sectioned"),
                )
    conn.commit()


def backfill_fiscal_labels(conn, cik: str, facts_json: dict) -> None:
    """Populate filings.fiscal_year/fiscal_period for a fixture-inserted
    company, matching (form_type, period_end) against the fy/fp embedded in
    the company's own companyfacts fixture.

    `insert_fixture_filings`'s synthetic accession numbers
    ("<cik>-fixture-10-Q-0", ...) never match the real `accn` values inside
    the trimmed companyfacts JSON, so `xbrl.ingest_company`'s own accn-keyed
    backfill (SPEC-005 change 9, `xbrl._extract_fiscal_labels`) silently
    backfills nothing for fixture-based tests -- found live while writing
    SPEC-008 C4's discrete-quarter tests, which are the first tests to
    actually depend on `filings.fiscal_year`/`fiscal_period` being
    populated rather than just present as columns. Existing tests never
    asserted on these two columns, so backfilling them for the first time
    is additive, not a behavior change for anything already passing."""
    # A period_end can appear tagged with more than one (fy, fp) across
    # different real accessions -- the filing that actually covers that
    # period, plus a LATER filing re-showing it as a prior-year comparative
    # (confirmed live: NVDA's 2025-04-27 appears as fy=2026 in its own 10-Q
    # and fy=2027 in the following year's 10-Q). Production code
    # (xbrl._extract_fiscal_labels) never faces this ambiguity -- each real
    # accession gets its own `filings` row there. This fixture-only helper
    # collapses to one row per (form_type, period_end) instead, so it has
    # to pick one: the SMALLEST fy, which is always the filing's own
    # original label, never a later comparative re-mention (a later
    # accession revisiting an older period always carries a LARGER fy).
    taxonomy_facts = facts_json.get("facts", {}).get(config.COMPANYFACTS_TAXONOMY, {})
    labels: dict[tuple[str, str], tuple[int, str]] = {}
    for concept_data in taxonomy_facts.values():
        for entries in concept_data.get("units", {}).values():
            for entry in entries:
                form, end = entry.get("form"), entry.get("end")
                fy, fp = entry.get("fy"), entry.get("fp")
                if form not in (config.TENK_FORM_TYPE, config.TENQ_FORM_TYPE) or not end or fy is None or fp is None:
                    continue
                existing = labels.get((form, end))
                if existing is None or fy < existing[0]:
                    labels[(form, end)] = (fy, fp)
    for (form, end), (fy, fp) in labels.items():
        conn.execute(
            "UPDATE filings SET fiscal_year = ?, fiscal_period = ? WHERE cik = ? AND form_type = ? AND period_end = ?",
            (fy, fp, cik, form, end),
        )
    conn.commit()
