"""Tests for edgar.validate (SPEC-004 R8)."""

from __future__ import annotations

import json

import pytest

from edgar import config, db, metrics, validate, xbrl
from tests.conftest import FIXTURES_DIR, insert_fixture_filings

AMZN_CIK = "0001018724"
NVDA_CIK = "0001045810"
MU_CIK = "0000723125"


class FakeXbrlClient:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def get_company_facts(self, cik: str) -> dict:
        return self.payload


def _load_fixture(name: str) -> dict:
    return json.loads((FIXTURES_DIR / name).read_text())


AMZN_FACTS = _load_fixture("companyfacts_trimmed_amzn.json")
NVDA_FACTS = _load_fixture("companyfacts_trimmed_nvda.json")
MU_FACTS = _load_fixture("companyfacts_trimmed_mu.json")
FIXTURES_BY_CIK = {AMZN_CIK: AMZN_FACTS, NVDA_CIK: NVDA_FACTS, MU_CIK: MU_FACTS}


@pytest.fixture
def conn(tmp_path):
    db_path = tmp_path / "test.db"
    db.init_db(db_path)
    connection = db.get_connection(db_path)
    yield connection
    connection.close()


def _ingest_and_compute(conn, tickers: list[str]) -> None:
    cik_by_ticker = {"AMZN": AMZN_CIK, "NVDA": NVDA_CIK, "MU": MU_CIK}
    ciks = [cik_by_ticker[t] for t in tickers]
    # filings rows must exist before xbrl ingest runs -- matches the real
    # pipeline order (discover/fetch before ingest-xbrl) and is required for
    # xbrl.ingest_company's filings.fiscal_year/fiscal_period backfill
    # (SPEC-005 change 9) to have any known accessions to update.
    insert_fixture_filings(conn, ciks=ciks)
    for cik in ciks:
        xbrl.ingest_company(conn, FakeXbrlClient(FIXTURES_BY_CIK[cik]), cik)
    metrics.compute_metrics(conn, tickers=tickers)


def test_validate_runs_clean_categories_on_real_data(conn):
    _ingest_and_compute(conn, ["AMZN", "NVDA", "MU"])

    report = validate.run_validate(conn, tickers=["AMZN", "NVDA", "MU"])

    # Real, correctly-ingested data should not trip these hard-failure categories.
    # (period-mixing is checked separately below -- real AMZN data genuinely has
    # a period_end shared across duration classes, which category 5 is supposed
    # to surface, not suppress.)
    assert report.dupont_violations == []
    assert report.gross_profit_violations == []
    assert report.debt_reconciliation_violations == []


def test_debt_reconciliation_passes_on_real_historical_micron_data(conn):
    _ingest_and_compute(conn, ["MU"])
    report = validate.run_validate(conn, tickers=["MU"])
    assert report.debt_reconciliation_violations == []


def test_debt_reconciliation_detects_corruption(conn):
    _ingest_and_compute(conn, ["MU"])
    # Corrupt one LongTermDebt value so it no longer matches Noncurrent+Current.
    conn.execute(
        "UPDATE xbrl_facts SET value = value * 2 WHERE concept = 'LongTermDebt' AND period_end = '2012-08-30'"
    )
    conn.commit()

    report = validate.run_validate(conn, tickers=["MU"])

    assert any(v["period_end"] == "2012-08-30" for v in report.debt_reconciliation_violations)


def test_debt_reconciliation_exception_reported_not_hard_failed(conn):
    # Real AMZN 2015-2016 ASU 2015-03 transition periods -- registered in
    # DEBT_RECONCILIATION_EXCEPTIONS (SPEC-004 R1b/AC-close). Must show up
    # informationally, with the reason, and not count as a hard failure.
    _ingest_and_compute(conn, ["AMZN"])
    report = validate.run_validate(conn, tickers=["AMZN"])
    assert report.debt_reconciliation_violations == []
    exception_periods = {f["period_end"] for f in report.debt_reconciliation_exceptions}
    assert {"2015-12-31", "2016-03-31", "2016-06-30", "2016-09-30"} <= exception_periods
    assert all("ASU 2015-03" in f["reason"] for f in report.debt_reconciliation_exceptions)


def test_debt_reconciliation_unregistered_period_still_hard_fails(conn):
    _ingest_and_compute(conn, ["MU"])
    conn.execute(
        "UPDATE xbrl_facts SET value = value * 2 WHERE concept = 'LongTermDebt' AND period_end = '2012-08-30'"
    )
    conn.commit()
    report = validate.run_validate(conn, tickers=["MU"])
    assert ("0000723125", "2012-08-30") not in config.DEBT_RECONCILIATION_EXCEPTIONS
    assert any(v["period_end"] == "2012-08-30" for v in report.debt_reconciliation_violations)


def test_gross_profit_crosscheck_detects_corruption(conn):
    _ingest_and_compute(conn, ["NVDA"])
    conn.execute("UPDATE xbrl_facts SET value = value * 3 WHERE concept = 'GrossProfit'")
    conn.commit()

    report = validate.run_validate(conn, tickers=["NVDA"])

    assert report.gross_profit_violations


def test_range_violations_detected(conn):
    _ingest_and_compute(conn, ["AMZN"])
    conn.execute("UPDATE metrics SET value = 99.0 WHERE name = 'current_ratio'")
    conn.commit()

    report = validate.run_validate(conn, tickers=["AMZN"])

    assert any(v["metric"] == "current_ratio" for v in report.range_violations)


def test_range_exceptions_reported_not_hard_failed(conn):
    # NVDA's real Q2 FY2023 inventory write-down and MU's real fiscal Q2 2024
    # discrete tax benefit -- both registered in RANGE_EXCEPTIONS. Must show
    # up informationally, with their reasons, and not count as hard failures.
    _ingest_and_compute(conn, ["NVDA", "MU"])
    report = validate.run_validate(conn, tickers=["NVDA", "MU"])
    assert report.range_violations == []
    metrics_found = {(f["metric"], f["ticker"]) for f in report.range_exceptions}
    assert ("incremental_gross_margin", "NVDA") in metrics_found
    assert ("effective_tax_rate", "MU") in metrics_found
    assert all(f["reason"] for f in report.range_exceptions)


def test_range_exceptions_unregistered_violation_still_hard_fails(conn):
    _ingest_and_compute(conn, ["AMZN"])
    conn.execute("UPDATE metrics SET value = 99.0 WHERE name = 'current_ratio'")
    conn.commit()
    report = validate.run_validate(conn, tickers=["AMZN"])
    assert not any(key[0] == "current_ratio" for key in config.RANGE_EXCEPTIONS)
    assert any(v["metric"] == "current_ratio" for v in report.range_violations)


def test_period_mixing_check_reads_zero_on_real_benign_collision(conn):
    # Real: Amazon has a 365-day and a 90-day duration fact both ending
    # 2025-06-30 (the implicit-Q4 case, SPEC-004 R3a). Redefined category 5
    # must NOT flag this -- no metric row's inputs actually mixed periods,
    # since the engine keys on the full (period_start, period_end) pair.
    _ingest_and_compute(conn, ["AMZN"])
    report = validate.run_validate(conn, tickers=["AMZN"])
    assert report.period_mixing_violations == []


def test_period_mixing_check_catches_leaked_period(conn):
    _ingest_and_compute(conn, ["AMZN"])
    # Corrupt one metric row's inputs_json so a value doesn't correspond to any
    # real fact at that row's own period -- simulates a period-leak bug.
    row = conn.execute(
        "SELECT id, inputs_json FROM metrics WHERE cik = ? AND name = 'operating_margin' AND value IS NOT NULL LIMIT 1",
        (AMZN_CIK,),
    ).fetchone()
    assert row is not None
    corrupted = json.loads(row["inputs_json"])
    key = next(iter(corrupted))
    corrupted[key] = corrupted[key] * 1.2345 + 999  # no real fact has this value
    conn.execute("UPDATE metrics SET inputs_json = ? WHERE id = ?", (json.dumps(corrupted), row["id"]))
    conn.commit()

    report = validate.run_validate(conn, tickers=["AMZN"])

    assert any(v["metric"] == "operating_margin" for v in report.period_mixing_violations)


def test_concept_drift_reported(conn):
    _ingest_and_compute(conn, ["NVDA"])
    report = validate.run_validate(conn, tickers=["NVDA"])
    assert any(e["canonical"] == "pretax_income" for e in report.concept_drift)


def test_coverage_reported(conn):
    _ingest_and_compute(conn, ["AMZN"])
    report = validate.run_validate(conn, tickers=["AMZN"])
    assert report.coverage
    assert all("computed" in c and "total" in c for c in report.coverage)


def test_unresolved_concepts_reported(conn):
    _ingest_and_compute(conn, ["AMZN"])
    report = validate.run_validate(conn, tickers=["AMZN"])
    assert any(u["canonical"] == "rnd_expense" for u in report.unresolved_concepts)


def test_hard_failure_count_reflects_categories_1_through_6(conn):
    _ingest_and_compute(conn, ["AMZN"])
    conn.execute("UPDATE metrics SET value = 99.0 WHERE name = 'current_ratio'")
    conn.commit()

    report = validate.run_validate(conn, tickers=["AMZN"])

    assert report.hard_failure_count >= 1
    assert report.hard_failure_count == (
        len(report.range_violations)
        + len(report.dupont_violations)
        + len(report.gross_profit_violations)
        + len(report.debt_reconciliation_violations)
        + len(report.period_mixing_violations)
        + len(report.alias_agreement_violations)
        + len(report.observation_lookahead_violations)
        + len(report.observation_determinism_violations)
        + len(report.observation_orphan_refs)
    )


def test_alias_agreement_clean_after_registry_splits(conn):
    # sbc, dep_amort, equity, net_income, interest_expense all previously
    # disagreed live (dep_amort/depreciation, equity/equity_including_nci,
    # net_income/net_income_including_nci, interest_expense/interest_expense_debt,
    # sbc/AllocatedShareBasedCompensationExpense). Each was split into its own
    # canonical input; none of those should produce hard failures anymore.
    _ingest_and_compute(conn, ["AMZN", "NVDA", "MU"])
    report = validate.run_validate(conn, tickers=["AMZN", "NVDA", "MU"])
    assert report.alias_agreement_violations == []


def test_alias_agreement_exception_reported_not_hard_failed(conn):
    # capex's real AMZN 2016-12-31 disagreement is registered as an accepted
    # exception (SPEC-004 R1g) -- must show up informationally, with its
    # reason, and must NOT count toward hard_failure_count.
    _ingest_and_compute(conn, ["AMZN"])
    report = validate.run_validate(conn, tickers=["AMZN"])
    assert not any(v["canonical"] == "capex" for v in report.alias_agreement_violations)
    exception_findings = [f for f in report.alias_agreement_exceptions if f["canonical"] == "capex"]
    assert exception_findings
    assert "hand-verified" in exception_findings[0]["reason"] or "AC9" in exception_findings[0]["reason"]


def test_alias_agreement_unregistered_canonical_still_hard_fails(conn):
    _ingest_and_compute(conn, ["NVDA"])
    conn.execute(
        "UPDATE xbrl_facts SET value = value * 2 "
        "WHERE concept = 'IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest'"
    )
    conn.commit()
    report = validate.run_validate(conn, tickers=["NVDA"])
    assert "pretax_income" not in config.ALIAS_AGREEMENT_EXCEPTIONS
    assert any(v["canonical"] == "pretax_income" for v in report.alias_agreement_violations)
    assert report.hard_failure_count >= 1


def test_unverified_yoy_drift_not_flagged_for_verified_nvda_transition(conn):
    # NVIDIA's pretax_income transition IS co-tagged (5 overlapping periods,
    # identical values) -- must not be flagged as unverified.
    _ingest_and_compute(conn, ["NVDA"])
    report = validate.run_validate(conn, tickers=["NVDA"])
    assert not any(f["canonical"] == "pretax_income" for f in report.unverified_yoy_drift)


def test_total_debt_zero_assumption_reported_for_nvda(conn):
    # NVIDIA never tags FinanceLeaseLiability{Noncurrent,Current} -- confirms
    # category 11 surfaces this at the portfolio level.
    _ingest_and_compute(conn, ["NVDA"])
    report = validate.run_validate(conn, tickers=["NVDA"])
    nvda_findings = [f for f in report.finance_lease_zero_assumptions if f["ticker"] == "NVDA"]
    assert nvda_findings
    assert nvda_findings[0]["periods_affected"] > 0


def test_nvda_roic_and_net_debt_now_compute(conn):
    # SPEC-004 R1h: total_debt assumes $0 for NVDA's absent finance lease
    # components rather than going permanently NULL.
    _ingest_and_compute(conn, ["NVDA"])
    rows = conn.execute(
        "SELECT name, value FROM metrics WHERE cik = ? AND name IN ('net_debt', 'roic') AND value IS NOT NULL",
        (NVDA_CIK,),
    ).fetchall()
    names_with_values = {row["name"] for row in rows}
    assert "net_debt" in names_with_values
    assert "roic" in names_with_values


def test_format_report_is_readable_text(conn):
    _ingest_and_compute(conn, ["AMZN"])
    report = validate.run_validate(conn, tickers=["AMZN"])
    text = validate.format_report(report)
    assert "Range violations" in text
    assert "Range exceptions" in text
    assert "DuPont reconciliation" in text
    assert "Debt reconciliation" in text
    assert "Debt reconciliation exceptions" in text
    assert "Alias agreement" in text
    assert "Unverified YoY drift" in text
    assert "Finance lease zero-assumption" in text
    assert "Database size" in text


def test_db_size_reports_real_file_size_and_never_hard_fails(conn):
    # `conn` is a real on-disk tmp_path database (not :memory:), so this
    # exercises the real PRAGMA database_list lookup, not a mock.
    _ingest_and_compute(conn, ["AMZN"])
    report = validate.run_validate(conn, tickers=["AMZN"])
    assert report.db_size["size_bytes"] is not None
    assert report.db_size["size_bytes"] > 0
    assert report.db_size["soft_ceiling_bytes"] == config.DB_SIZE_SOFT_CEILING_BYTES
    assert report.db_size["over_soft_ceiling"] is False  # a tiny test db is nowhere near 15 MB
    assert report.db_size["measured_marginal_bytes_per_filing"] > 0
    assert report.hard_failure_count == 0  # db_size never contributes to hard failures
