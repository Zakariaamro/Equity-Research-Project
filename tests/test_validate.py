"""Tests for edgar.validate (SPEC-004 R8)."""

from __future__ import annotations

import json

import pytest

from edgar import config, db, metrics, validate, xbrl
from tests.conftest import FIXTURES_DIR, backfill_fiscal_labels, insert_fixture_filings

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


def _ingest_and_compute_discrete(conn, tickers: list[str]) -> None:
    cik_by_ticker = {"AMZN": AMZN_CIK, "NVDA": NVDA_CIK, "MU": MU_CIK}
    ciks = [cik_by_ticker[t] for t in tickers]
    insert_fixture_filings(conn, ciks=ciks)
    for cik in ciks:
        xbrl.ingest_company(conn, FakeXbrlClient(FIXTURES_BY_CIK[cik]), cik)
        backfill_fiscal_labels(conn, cik, FIXTURES_BY_CIK[cik])
    metrics.compute_discrete_quarter_metrics(conn, tickers=tickers)


def test_discrete_quarter_sum_back_passes_on_real_nvda_data(conn):
    # NVDA's fixture has one complete fiscal year (FY2026) -- the only
    # combination this check can evaluate at all (category 12's own logic
    # skips incomplete fiscal years, not a violation on its own).
    _ingest_and_compute_discrete(conn, ["NVDA"])
    report = validate.run_validate(conn, tickers=["NVDA"])
    assert report.discrete_quarter_sum_violations == []


def test_discrete_quarter_sum_back_detects_a_stale_fy_figure(conn):
    # Simulates exactly the failure mode this check exists for: a
    # restatement lands in xbrl_facts (the FY figure changes) after
    # compute-metrics last ran, so the STORED discrete quarters (still the
    # old vintage) no longer sum to the CURRENT filed FY figure.
    _ingest_and_compute_discrete(conn, ["NVDA"])
    conn.execute(
        "UPDATE xbrl_facts SET value = value * 1.1 WHERE cik = ? AND concept = ? AND period_end = ? AND period_start = ?",
        (NVDA_CIK, "NetCashProvidedByUsedInOperatingActivities", "2026-01-25", "2025-01-27"),
    )
    conn.commit()

    report = validate.run_validate(conn, tickers=["NVDA"])

    violations = [v for v in report.discrete_quarter_sum_violations if v["canonical"] == "cfo"]
    assert violations
    assert violations[0]["fy_period_end"] == "2026-01-25"
    assert violations[0]["diff"] > config.DISCRETE_QUARTER_SUM_TOLERANCE_USD


def test_discrete_quarter_sum_back_exception_reported_not_hard_failed(conn, monkeypatch):
    _ingest_and_compute_discrete(conn, ["NVDA"])
    conn.execute(
        "UPDATE xbrl_facts SET value = value * 1.1 WHERE cik = ? AND concept = ? AND period_end = ? AND period_start = ?",
        (NVDA_CIK, "NetCashProvidedByUsedInOperatingActivities", "2026-01-25", "2025-01-27"),
    )
    conn.commit()
    monkeypatch.setattr(
        config,
        "DISCRETE_QUARTER_SUM_EXCEPTIONS",
        {
            (NVDA_CIK, "cfo", "2026-01-25"): config.DiscreteQuarterSumException(
                cik=NVDA_CIK, canonical="cfo", fy_period_end="2026-01-25", reason="test exception",
            )
        },
    )

    report = validate.run_validate(conn, tickers=["NVDA"])

    assert report.discrete_quarter_sum_violations == []
    exceptions = report.discrete_quarter_sum_exceptions
    assert any(f["fy_period_end"] == "2026-01-25" and f["reason"] == "test exception" for f in exceptions)


def test_discrete_quarter_sum_back_skips_an_incomplete_fiscal_year(conn):
    # MU's fixture has no FY2026 10-K yet -- Q4-discrete doesn't exist, so
    # there is nothing to sum-check for that fiscal year, and it must not
    # be reported as a violation.
    _ingest_and_compute_discrete(conn, ["MU"])
    report = validate.run_validate(conn, tickers=["MU"])
    assert report.discrete_quarter_sum_violations == []


def _seed_amzn_fiscal_year_revenue(conn):
    """SPEC-008 D12 (approved 2026-08-08): a clean, synthetic, single-
    fiscal-year AMZN-shaped revenue cycle -- the trimmed companyfacts
    fixtures don't carry a complete cycle for any income-statement
    concept, so this inserts facts directly (mirrors test_metrics.py's own
    version of this helper). Q1=100M, Q2 YTD=220M, Q3 YTD=360M, FY=500M --
    hand-computed discrete quarters: 100/120/140/140, summing to 500."""
    concept = "RevenueFromContractWithCustomerExcludingAssessedTax"
    for accession_no, form_type, period_end, fiscal_period in (
        ("acc-q1", "10-Q", "2025-03-31", "Q1"),
        ("acc-q2", "10-Q", "2025-06-30", "Q2"),
        ("acc-q3", "10-Q", "2025-09-30", "Q3"),
        ("acc-fy", "10-K", "2025-12-31", "FY"),
    ):
        conn.execute(
            "INSERT INTO filings (accession_no, cik, form_type, filing_date, period_end, fiscal_year, "
            "fiscal_period, discovered_at, status) VALUES (?, ?, ?, ?, ?, 2025, ?, ?, 'sectioned')",
            (accession_no, AMZN_CIK, form_type, period_end, period_end, fiscal_period, f"{period_end}T00:00:00"),
        )
    for period_start, period_end, value, duration_days, accession_no in (
        ("2025-01-01", "2025-03-31", 100_000_000, 90, "acc-q1"),
        ("2025-01-01", "2025-06-30", 220_000_000, 181, "acc-q2"),
        ("2025-01-01", "2025-09-30", 360_000_000, 273, "acc-q3"),
        ("2025-01-01", "2025-12-31", 500_000_000, 365, "acc-fy"),
    ):
        conn.execute(
            "INSERT INTO xbrl_facts (cik, taxonomy, concept, unit, period_start, period_end, value, "
            "accession_no, filed_date, duration_days) VALUES (?, 'us-gaap', ?, 'USD', ?, ?, ?, ?, ?, ?)",
            (AMZN_CIK, concept, period_start, period_end, value, accession_no, period_end, duration_days),
        )
    conn.commit()


def test_discrete_quarter_sum_back_covers_income_statement_lines_too(conn):
    # D12: the same sum-back mechanism built for cash flow (_DISCRETE_
    # QUARTER_CANONICALS is now shared, not cash-flow-specific) -- passes
    # clean on correct data, and catches a corrupted FY figure for an
    # income-statement line exactly as it already does for cfo.
    _seed_amzn_fiscal_year_revenue(conn)
    metrics.compute_discrete_quarter_metrics(conn, tickers=["AMZN"])
    report = validate.run_validate(conn, tickers=["AMZN"])
    assert report.discrete_quarter_sum_violations == []

    conn.execute(
        "UPDATE xbrl_facts SET value = 999000000 WHERE cik = ? AND period_start = '2025-01-01' "
        "AND period_end = '2025-12-31'",
        (AMZN_CIK,),
    )
    conn.commit()

    report = validate.run_validate(conn, tickers=["AMZN"])
    violations = [v for v in report.discrete_quarter_sum_violations if v["canonical"] == "revenue"]
    assert violations
    assert violations[0]["fy_period_end"] == "2025-12-31"


# --- SPEC-008-batch-2 cash-reconciliation follow-up (approved 2026-08-13): category 34 ---


def _seed_amzn_cash_reconciliation_cycle(conn, ending_value=180_000_000):
    """A clean, synthetic beginning/net-change/ending cash triple -- the
    trimmed AMZN fixture doesn't carry cash_and_restricted_cash or
    net_change_in_cash at all. Beginning (2025-03-31) = 100M, Q2 2025 net
    change = 80M, ending (2025-06-30) defaults to 180M -- reconciles
    exactly unless the caller overrides `ending_value` to break it."""
    conn.execute(
        "INSERT INTO xbrl_facts (cik, taxonomy, concept, unit, period_start, period_end, value, "
        "accession_no, filed_date) VALUES (?, 'us-gaap', ?, 'USD', NULL, ?, ?, 'acc-beg', ?)",
        (AMZN_CIK, "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents", "2025-03-31",
         100_000_000, "2025-03-31"),
    )
    conn.execute(
        "INSERT INTO xbrl_facts (cik, taxonomy, concept, unit, period_start, period_end, value, "
        "accession_no, filed_date) VALUES (?, 'us-gaap', ?, 'USD', NULL, ?, ?, 'acc-end', ?)",
        (AMZN_CIK, "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents", "2025-06-30",
         ending_value, "2025-06-30"),
    )
    conn.execute(
        "INSERT INTO xbrl_facts (cik, taxonomy, concept, unit, period_start, period_end, value, "
        "accession_no, filed_date) VALUES (?, 'us-gaap', ?, 'USD', ?, ?, ?, 'acc-chg', ?)",
        (
            AMZN_CIK,
            "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalentsPeriodIncreaseDecreaseIncludingExchangeRateEffect",
            "2025-04-01", "2025-06-30", 80_000_000, "2025-06-30",
        ),
    )
    conn.commit()


def test_cash_reconciliation_passes_on_clean_synthetic_data(conn):
    _seed_amzn_cash_reconciliation_cycle(conn)  # 100M + 80M = 180M, ties exactly
    report = validate.run_validate(conn, tickers=["AMZN"])
    assert report.cash_reconciliation_violations == []


def test_cash_reconciliation_detects_a_mismatch(conn):
    _seed_amzn_cash_reconciliation_cycle(conn, ending_value=999_000_000)  # 100M + 80M != 999M
    report = validate.run_validate(conn, tickers=["AMZN"])
    violations = [v for v in report.cash_reconciliation_violations if v["period_end"] == "2025-06-30"]
    assert violations
    assert violations[0]["diff"] == pytest.approx(819_000_000)


def test_cash_reconciliation_does_not_double_count_the_fx_effect(conn):
    # net_change_in_cash's own concept name ends "...IncludingExchangeRate
    # Effect" -- confirmed against the real corpus before this rule was
    # written: adding fx_effect_on_cash on top double-counts it. A clean
    # reconciliation must stay clean even when an fx_effect_on_cash fact
    # exists alongside it for the same period.
    _seed_amzn_cash_reconciliation_cycle(conn)
    conn.execute(
        "INSERT INTO xbrl_facts (cik, taxonomy, concept, unit, period_start, period_end, value, "
        "accession_no, filed_date) VALUES (?, 'us-gaap', ?, 'USD', ?, ?, ?, 'acc-fx', ?)",
        (
            AMZN_CIK, "EffectOfExchangeRateOnCashAndCashEquivalents", "2025-04-01", "2025-06-30",
            5_000_000, "2025-06-30",
        ),
    )
    conn.commit()
    report = validate.run_validate(conn, tickers=["AMZN"])
    assert report.cash_reconciliation_violations == []


def test_cash_reconciliation_exception_reported_not_hard_failed(conn, monkeypatch):
    _seed_amzn_cash_reconciliation_cycle(conn, ending_value=999_000_000)
    monkeypatch.setattr(
        config,
        "CASH_RECONCILIATION_EXCEPTIONS",
        {
            (AMZN_CIK, "2025-06-30"): config.CashReconciliationException(
                cik=AMZN_CIK, period_end="2025-06-30", reason="test exception",
            )
        },
    )
    report = validate.run_validate(conn, tickers=["AMZN"])
    assert report.cash_reconciliation_violations == []
    exceptions = report.cash_reconciliation_exceptions
    assert any(f["period_end"] == "2025-06-30" and f["reason"] == "test exception" for f in exceptions)


def test_cash_reconciliation_unregistered_period_still_hard_fails(conn):
    _seed_amzn_cash_reconciliation_cycle(conn, ending_value=999_000_000)
    assert (AMZN_CIK, "2025-06-30") not in config.CASH_RECONCILIATION_EXCEPTIONS
    report = validate.run_validate(conn, tickers=["AMZN"])
    assert any(v["period_end"] == "2025-06-30" for v in report.cash_reconciliation_violations)


def test_cash_reconciliation_real_amzn_2018_restatement_is_a_registered_exception(conn):
    # The actual finding this rule was built from (SPEC-008-batch-2 cash-
    # reconciliation follow-up): AMZN restated the 2018-03-31 instant from
    # $17,616M to $23,507M without a corresponding restatement of the
    # duration facts touching it. Confirms the REAL, committed exception
    # registry (not a monkeypatched one) covers it.
    assert ("0001018724", "2018-03-31") in config.CASH_RECONCILIATION_EXCEPTIONS
    assert ("0001018724", "2018-06-30") in config.CASH_RECONCILIATION_EXCEPTIONS
    assert ("0001018724", "2019-03-31") in config.CASH_RECONCILIATION_EXCEPTIONS


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


# --- SPEC-006 R10: LLM infrastructure checks ---

from edgar import analyze, llm  # noqa: E402 (module-level, but grouped with this section)

GOOD_RESPONSE = json.dumps({
    "material": True,
    "findings": [{
        "category": "accounting_change", "severity": "medium", "headline": "Tax Act increased provision",
        "detail": "detail text", "quote": "The 2025 Tax Act increased our income tax provision",
    }],
})

NOTE_TEXT = "Income Taxes. The 2025 Tax Act increased our income tax provision, primarily due to a decrease."


class _FakeRawClient:
    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)

    def messages_create(self, model, max_tokens, prompt):
        return self._responses.pop(0), 1000, 200, "end_turn"


def _insert_notes_section(conn, accession_no="acc1", cik=AMZN_CIK, short_name="Income Taxes", text=NOTE_TEXT) -> int:
    from edgar import section_store

    conn.execute(
        "INSERT INTO filings (accession_no, cik, form_type, filing_date, period_end, discovered_at, status) "
        "VALUES (?, ?, '10-K', '2026-02-06', '2025-12-31', '2026-02-06T00:00:00', 'sectioned') "
        "ON CONFLICT(accession_no) DO NOTHING",
        (accession_no, cik),
    )
    text_hash = section_store.write_section_text(text)
    cursor = conn.execute(
        "INSERT INTO sections (accession_no, category, short_name, source_file, position, text_hash) "
        "VALUES (?, 'Notes', ?, 'R1.htm', 1, ?)",
        (accession_no, short_name, text_hash),
    )
    conn.commit()
    return cursor.lastrowid


@pytest.fixture(autouse=True)
def _reset_llm_run_state():
    llm._reset_run_state()


def test_llm_pricing_staleness_flags_old_verification_date(conn, monkeypatch):
    stale = config.ModelPricing(
        input_per_mtok=1.0, output_per_mtok=1.0, source_url="https://example.com", verified_date="2020-01-01",
    )
    monkeypatch.setitem(config.LLM_PRICING, "claude-test-stale", stale)
    report = validate.run_validate(conn, tickers=["AMZN"])
    stale_models = {f["model"] for f in report.llm_pricing_staleness}
    assert "claude-test-stale" in stale_models


def test_llm_pricing_staleness_clean_for_current_table(conn):
    report = validate.run_validate(conn, tickers=["AMZN"])
    assert report.llm_pricing_staleness == []  # real LLM_PRICING was verified 2026-07-27


def test_llm_ledger_reconciliation_clean_by_default(conn):
    llm.record_result(conn, "claude-sonnet-5", input_tokens=1000, output_tokens=200, status="ok")
    report = validate.run_validate(conn, tickers=["AMZN"])
    assert report.llm_ledger_mismatches == []


def test_llm_budget_headroom_reports_spent_and_remaining(conn):
    llm.record_result(conn, "claude-sonnet-5", input_tokens=1000, output_tokens=200, status="ok")
    report = validate.run_validate(conn, tickers=["AMZN"])
    expected_spent = llm.total_spent(conn)
    assert report.llm_budget["spent"] == pytest.approx(expected_spent)
    assert report.llm_budget["budget"] == config.LLM_BUDGET_USD
    assert report.llm_budget["over_budget"] is False


def test_llm_budget_headroom_flags_over_budget(conn, monkeypatch):
    monkeypatch.setattr(config, "LLM_BUDGET_USD", 0.0001)
    llm.record_result(conn, "claude-sonnet-5", input_tokens=1_000_000, output_tokens=1_000_000, status="ok")
    report = validate.run_validate(conn, tickers=["AMZN"])
    assert report.llm_budget["over_budget"] is True
    assert report.hard_failure_count > 0


def test_llm_orphan_findings_detects_dangling_analysis_id(conn):
    # An orphan can never arise through normal code (FKs are enforced, PRAGMA
    # foreign_keys=ON in db.py) -- this check exists as a belt-and-suspenders
    # guard against corruption from outside the app (a bug, a manual edit, a
    # partial migration), so the test has to disable enforcement to construct
    # the scenario it defends against.
    _insert_notes_section(conn)
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute(
        "INSERT INTO findings (analysis_id, accession_no, category, severity, headline, detail, quote, created_at) "
        "VALUES (999, 'acc1', 'note_item', 'low', 'h', 'd', 'q', '2026-01-01T00:00:00')"
    )
    conn.commit()
    conn.execute("PRAGMA foreign_keys = ON")
    report = validate.run_validate(conn, tickers=["AMZN"])
    assert len(report.llm_orphan_findings) == 1
    assert report.hard_failure_count > 0


def test_llm_quote_integrity_passes_for_verified_finding(conn):
    section_id = _insert_notes_section(conn)
    client = llm.LLMClient(raw_client=_FakeRawClient([GOOD_RESPONSE]))
    outcome = llm.get_or_create_analysis(
        conn, section_id, "section_analysis", "v1",
        rendered_prompt="rendered prompt referencing: " + NOTE_TEXT, client=client,
    )
    assert outcome.status == "ok"
    conn.execute(
        "INSERT INTO findings (analysis_id, accession_no, category, severity, headline, detail, quote, created_at) "
        "VALUES (?, 'acc1', 'accounting_change', 'medium', 'h', 'd', ?, '2026-01-01T00:00:00')",
        (outcome.analysis_id, "The 2025 Tax Act increased our income tax provision"),
    )
    conn.commit()
    report = validate.run_validate(conn, tickers=["AMZN"])
    assert report.llm_quote_integrity_violations == []


def test_llm_quote_integrity_detects_fabricated_quote(conn):
    section_id = _insert_notes_section(conn)
    client = llm.LLMClient(raw_client=_FakeRawClient([GOOD_RESPONSE]))
    outcome = llm.get_or_create_analysis(
        conn, section_id, "section_analysis", "v1",
        rendered_prompt="rendered prompt referencing: " + NOTE_TEXT, client=client,
    )
    conn.execute(
        "INSERT INTO findings (analysis_id, accession_no, category, severity, headline, detail, quote, created_at) "
        "VALUES (?, 'acc1', 'accounting_change', 'medium', 'h', 'd', ?, '2026-01-01T00:00:00')",
        (outcome.analysis_id, "this quote never appeared anywhere in the note text at all"),
    )
    conn.commit()
    report = validate.run_validate(conn, tickers=["AMZN"])
    assert len(report.llm_quote_integrity_violations) == 1
    assert report.hard_failure_count > 0


def test_llm_discard_rate_reports_per_prompt_version(conn):
    section_id = _insert_notes_section(conn)
    client = llm.LLMClient(raw_client=_FakeRawClient([GOOD_RESPONSE]))
    template = analyze.load_prompt_template(config.SECTION_ANALYSIS_PROMPT_NAME, config.SECTION_ANALYSIS_PROMPT_VERSION)
    row = analyze.select_candidate_sections(conn, tickers=["AMZN"])[0]
    result = analyze.analyze_one_section(conn, row, template, client=client)
    assert result.status == "ok"
    assert result.findings_returned == 1
    assert result.findings_kept == 1  # the fixture's quote is a real substring of NOTE_TEXT

    report = validate.run_validate(conn, tickers=["AMZN"])
    by_version = {d["prompt_version"]: d for d in report.llm_discard_rate}
    assert by_version[config.SECTION_ANALYSIS_PROMPT_VERSION]["returned"] == 1
    assert by_version[config.SECTION_ANALYSIS_PROMPT_VERSION]["kept"] == 1
    assert by_version[config.SECTION_ANALYSIS_PROMPT_VERSION]["discard_rate"] == 0.0
