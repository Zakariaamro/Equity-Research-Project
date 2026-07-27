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
        "10-Q": ["2025-04-27", "2025-07-27", "2025-10-26", "2026-04-26"],
    },
    "0000723125": {  # MU
        "10-K": ["2025-08-28", "2024-08-29"],
        "10-Q": ["2025-11-27", "2026-02-26", "2026-05-28"],
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
