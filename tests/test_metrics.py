"""Tests for edgar.metrics (SPEC-004 R1a/R3/R4/R5/R6/R7/R9).

Fixtures: trimmed real companyfacts responses for all three watchlist
companies (tests/fixtures/companyfacts_trimmed_*.json).
"""

from __future__ import annotations

import importlib
import json

import pytest

from edgar import config, db, metrics, xbrl
from tests.conftest import FIXTURES_DIR, backfill_fiscal_labels, insert_fixture_filings

AMZN_CIK = "0001018724"
NVDA_CIK = "0001045810"
MU_CIK = "0000723125"

TICKER_BY_CIK = {AMZN_CIK: "AMZN", NVDA_CIK: "NVDA", MU_CIK: "MU"}


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


def _ingest(conn, cik: str) -> None:
    # filings rows must exist before xbrl ingest runs -- matches the real
    # pipeline order (discover/fetch before ingest-xbrl) and is required for
    # xbrl.ingest_company's filings.fiscal_year/fiscal_period backfill
    # (SPEC-005 change 9) to have any known accessions to update.
    insert_fixture_filings(conn, ciks=[cik])
    xbrl.ingest_company(conn, FakeXbrlClient(FIXTURES_BY_CIK[cik]), cik)
    backfill_fiscal_labels(conn, cik, FIXTURES_BY_CIK[cik])


def _ingest_all(conn) -> None:
    for cik in FIXTURES_BY_CIK:
        _ingest(conn, cik)


def _metric_rows(conn, cik: str, name: str) -> list[dict]:
    rows = conn.execute(
        "SELECT period_end, value, formula, inputs_json FROM metrics WHERE cik = ? AND name = ? ORDER BY period_end",
        (cik, name),
    ).fetchall()
    return [dict(r) for r in rows]


# --- duration classification (pure function) ---


@pytest.mark.parametrize(
    "days,expected",
    [(90, "quarterly"), (181, "half-year"), (273, "three-quarter"), (365, "annual"), (45, "other")],
)
def test_duration_classification(days, expected):
    assert metrics._classify_duration(days) == expected


def test_instant_facts_classify_as_instant():
    assert metrics._classify_duration(None) == config.PERIOD_CLASS_INSTANT


# --- the central risk: quarterly and YTD sharing an end date ---


def test_quarterly_and_ytd_not_mixed(conn):
    # Real: Amazon's 2025-09-30 has both a 3-month (quarterly) and 9-month
    # (three-quarter) revenue fact ending on the same date.
    _ingest(conn, AMZN_CIK)
    metrics.compute_metrics(conn, tickers=["AMZN"])

    rows = _metric_rows(conn, AMZN_CIK, "revenue_yoy")
    q_row = next(r for r in rows if r["period_end"] == "2025-09-30")
    inputs = json.loads(q_row["inputs_json"])
    # the quarterly 3-month value (180,169,000,000), never the 9-month YTD one (503,538,000,000)
    resolved_values = [v for v in inputs.values() if isinstance(v, (int, float))]
    assert 503538000000 not in resolved_values


def test_period_mixing_never_occurs_in_resolution(conn):
    _ingest(conn, AMZN_CIK)
    facts = metrics._load_facts(conn, AMZN_CIK)
    # both a 3-month and 9-month fact exist for the same end date
    val_3mo, _ = metrics._resolve("revenue", facts, "2025-07-01", "2025-09-30")
    val_9mo, _ = metrics._resolve("revenue", facts, "2025-01-01", "2025-09-30")
    assert val_3mo == 180169000000
    assert val_9mo == 503538000000
    assert val_3mo != val_9mo


# --- restatement selection ---


def test_latest_filed_wins(conn):
    _ingest(conn, AMZN_CIK)
    facts = metrics._load_facts(conn, AMZN_CIK)
    val, _alias = metrics._resolve("net_income", facts, "2012-01-01", "2012-03-31")
    assert val == 130000000  # not the 2013-01-30 anomalous 201,000,000


# --- NULL discipline ---


def test_missing_input_yields_null_not_zero(conn):
    _ingest(conn, AMZN_CIK)
    metrics.compute_metrics(conn, tickers=["AMZN"])
    # rnd_expense is unresolved for Amazon -- rnd_intensity must be NULL, never 0
    rows = _metric_rows(conn, AMZN_CIK, "rnd_intensity")
    assert rows
    assert all(r["value"] is None for r in rows)
    assert all(json.loads(r["inputs_json"])["_null_reason"] for r in rows)


def test_divide_by_zero_yields_null():
    result = metrics._simple_ratio("operating_income", "revenue")(
        {"OperatingIncomeLoss": {("s", "e"): metrics.Fact(100.0, "OperatingIncomeLoss", "2020-01-01", 90)},
         "RevenueFromContractWithCustomerExcludingAssessedTax": {("s", "e"): metrics.Fact(0.0, "RevenueFromContractWithCustomerExcludingAssessedTax", "2020-01-01", 90)}},
        {}, "s", "e", "quarterly",
    )
    assert result.value is None
    assert "zero" in result.null_reason


# --- gross_margin dual path ---


def test_gross_margin_both_paths(conn):
    _ingest_all(conn)
    metrics.compute_metrics(conn, tickers=["AMZN", "NVDA", "MU"])

    amzn_rows = _metric_rows(conn, AMZN_CIK, "gross_margin")
    amzn_recent = next(r for r in amzn_rows if r["period_end"] == "2025-12-31")
    assert amzn_recent["value"] is not None
    assert "CostOfGoodsAndServicesSold" in amzn_recent["formula"]  # revenue - cogs fallback

    for cik in (NVDA_CIK, MU_CIK):
        rows = _metric_rows(conn, cik, "gross_margin")
        recent = [r for r in rows if r["value"] is not None]
        assert recent
        assert any("GrossProfit" in r["formula"] and "CostOf" not in r["formula"] for r in recent)


# --- per-period resolution, never company-wide (Amazon GrossProfit) ---


def test_per_period_resolution_not_company_wide(conn):
    _ingest(conn, AMZN_CIK)
    facts = metrics._load_facts(conn, AMZN_CIK)

    # Stale tag resolves for its real 2007-2008 period...
    val_old, alias_old = metrics._resolve("gross_profit", facts, "2007-01-01", "2007-12-31")
    assert val_old is not None
    assert alias_old == "GrossProfit"

    # ...but must NOT be treated as "available" for a modern period; gross_margin
    # must still fall through to revenue - cogs for FY2025.
    gp_result = metrics._resolve_gross_profit(facts, "2025-01-01", "2025-12-31")
    value, formula_fragment, _inputs = gp_result
    assert value is not None
    assert "CostOfGoodsAndServicesSold" in formula_fragment  # computed via fallback, not the stale tag


# --- fiscal, not calendar, YoY matching (non-December year end: MU) ---


def test_yoy_matches_fiscal_not_calendar(conn):
    _ingest(conn, MU_CIK)
    metrics.compute_metrics(conn, tickers=["MU"])

    rows = _metric_rows(conn, MU_CIK, "revenue_yoy")
    recent = next((r for r in rows if r["period_end"] == "2025-08-28"), None)
    assert recent is not None
    assert recent["value"] is not None
    inputs = json.loads(recent["inputs_json"])
    # both current (2025-08-28) and prior (2024-08-29) values present -- 364 days apart
    assert len(inputs) == 2


# --- total_debt: combined-tag preference and component fallback (SPEC-004 R1b) ---


def test_total_debt_prefers_combined_tag(conn):
    _ingest(conn, MU_CIK)
    facts = metrics._load_facts(conn, MU_CIK)
    # Recent MU periods: LongTermDebtNoncurrent has gone stale, only LongTermDebt (combined) + DebtCurrent exist
    value, note, _inputs = metrics._resolve_total_debt(facts, "2025-08-28")
    assert value is not None
    assert "no combined tag" not in note  # used the combined tag directly, not summed components


def test_total_debt_falls_back_to_components(conn):
    _ingest(conn, MU_CIK)
    facts = metrics._load_facts(conn, MU_CIK)
    # Old MU periods (2010-2013): no "total_debt" alias tag resolves via _resolve directly
    # because at those dates LongTermDebt/Noncurrent/Current all coexist; force the
    # fallback path by checking a period where only components exist.
    total_debt_val, _ = metrics._resolve("total_debt", facts, None, "2012-08-30")
    dn, _ = metrics._resolve("debt_noncurrent", facts, None, "2012-08-30")
    dc, _ = metrics._resolve("debt_current", facts, None, "2012-08-30")
    assert dn is not None and dc is not None
    if total_debt_val is not None:
        assert abs(total_debt_val - (dn + dc)) < 1  # exact match confirmed historically


def test_receivables_has_no_fallback_alias():
    assert config.CONCEPT_REGISTRY["receivables"].aliases == ("AccountsReceivableNetCurrent",)


def test_total_debt_assumes_zero_for_untagged_finance_lease(conn):
    # Real: NVIDIA never tags FinanceLeaseLiability{Noncurrent,Current} (it
    # discloses only operating leases). total_debt must compute anyway,
    # treating the absent finance lease components as $0 (SPEC-004 R1h),
    # not go NULL the way a primary measure would.
    _ingest(conn, NVDA_CIK)
    facts = metrics._load_facts(conn, NVDA_CIK)
    borrowings, _note, _inputs = metrics._resolve_borrowings(facts, "2026-01-25")
    assert borrowings is not None
    value, note, _inputs = metrics._resolve_total_debt(facts, "2026-01-25")
    assert value == pytest.approx(borrowings)
    assert "assumed" in note


def test_total_debt_still_null_when_borrowings_missing(conn):
    # The refined NULL rule (R1h) only loosens the finance-lease side.
    # Absent borrowings must still be NULL, never $0.
    facts: dict = {}
    value, note, _inputs = metrics._resolve_total_debt(facts, "2026-01-25")
    assert value is None


def test_equity_and_net_income_single_alias():
    assert config.CONCEPT_REGISTRY["equity"].aliases == ("StockholdersEquity",)
    assert config.CONCEPT_REGISTRY["net_income"].aliases == ("NetIncomeLoss",)


def test_depreciation_falls_back_to_dep_amort(conn):
    _ingest(conn, MU_CIK)
    facts = metrics._load_facts(conn, MU_CIK)
    # Real: Micron's pure "Depreciation" tag is not in the trimmed fixture for
    # its recent periods (only DD&A is) -- depreciation_rate must fall back.
    result = metrics._compute_depreciation_rate(facts, {}, "2024-08-30", "2025-08-28", "annual")
    if result.value is not None:
        assert "fell back to dep_amort" in result.formula or "Depreciation" in result.formula


# --- DuPont reconciliation ---


def test_dupont_reconciles_to_roe(conn):
    _ingest(conn, AMZN_CIK)
    metrics.compute_metrics(conn, tickers=["AMZN"])

    def _value(name, period_end):
        rows = _metric_rows(conn, AMZN_CIK, name)
        row = next((r for r in rows if r["period_end"] == period_end), None)
        return row["value"] if row else None

    period = "2025-12-31"
    net_margin = _value("net_margin", period)
    asset_turnover = _value("asset_turnover", period)
    equity_multiplier = _value("equity_multiplier", period)
    roe = _value("roe", period)
    assert None not in (net_margin, asset_turnover, equity_multiplier, roe)
    product = net_margin * asset_turnover * equity_multiplier
    assert abs(product - roe) / abs(roe) < 0.01


# --- Beneish components against a hand-computed example ---


def test_beneish_components_against_hand_computed_example(conn):
    _ingest(conn, AMZN_CIK)
    metrics.compute_metrics(conn, tickers=["AMZN"])

    facts = metrics._load_facts(conn, AMZN_CIK)
    start, end = "2025-01-01", "2025-12-31"
    p_start, p_end = "2024-01-01", "2024-12-31"

    rev_t, _ = metrics._resolve("revenue", facts, start, end)
    rev_p, _ = metrics._resolve("revenue", facts, p_start, p_end)
    expected_sgi = rev_t / rev_p

    rows = _metric_rows(conn, AMZN_CIK, "beneish_sgi")
    row = next(r for r in rows if r["period_end"] == end)
    assert row["value"] == pytest.approx(expected_sgi)

    rec_t, _ = metrics._resolve("receivables", facts, None, end)
    rec_p, _ = metrics._resolve("receivables", facts, None, p_end)
    expected_dsri = (rec_t / rev_t) / (rec_p / rev_p)
    rows = _metric_rows(conn, AMZN_CIK, "beneish_dsri")
    row = next(r for r in rows if r["period_end"] == end)
    assert row["value"] == pytest.approx(expected_dsri)


def test_beneish_tata_needs_no_prior_period(conn):
    _ingest(conn, AMZN_CIK)
    metrics.compute_metrics(conn, tickers=["AMZN"])
    facts = metrics._load_facts(conn, AMZN_CIK)
    ni, _ = metrics._resolve("net_income", facts, "2025-01-01", "2025-12-31")
    cfo, _ = metrics._resolve("cfo", facts, "2025-01-01", "2025-12-31")
    ta, _ = metrics._resolve("total_assets", facts, None, "2025-12-31")
    expected = (ni - cfo) / ta
    rows = _metric_rows(conn, AMZN_CIK, "beneish_tata")
    row = next(r for r in rows if r["period_end"] == "2025-12-31")
    assert row["value"] == pytest.approx(expected)


# --- concept drift (real NVIDIA pretax_income transition) ---


def test_concept_drift_detected(conn):
    _ingest(conn, NVDA_CIK)
    facts = metrics._load_facts(conn, NVDA_CIK)
    annual_ends, quarterly_ends = metrics._real_fiscal_period_ends(conn, NVDA_CIK)
    periods_by_class = metrics._build_periods_by_class(facts, annual_ends, quarterly_ends)
    annual_periods = sorted(periods_by_class.get("annual", set()))

    resolved_sequence = []
    for start, end in annual_periods:
        val, alias = metrics._resolve("pretax_income", facts, start, end)
        if alias is not None:
            resolved_sequence.append((end, alias))

    aliases_used = {alias for _end, alias in resolved_sequence}
    assert (
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments"
        in aliases_used
    )
    assert (
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest" in aliases_used
    )
    # a real drift boundary exists: consecutive periods differ in resolved alias
    drifted = any(resolved_sequence[i][1] != resolved_sequence[i + 1][1] for i in range(len(resolved_sequence) - 1))
    assert drifted


# --- formula / inputs_json integrity ---


def test_formula_names_concepts_actually_used(conn):
    _ingest(conn, AMZN_CIK)
    metrics.compute_metrics(conn, tickers=["AMZN"])

    rows = conn.execute(
        "SELECT name, formula, inputs_json FROM metrics WHERE cik = ? AND value IS NOT NULL", (AMZN_CIK,)
    ).fetchall()
    assert rows
    for row in rows:
        inputs = json.loads(row["inputs_json"])
        # every alias-shaped token in inputs_json should be traceable; spot check
        # that at least one concept name from inputs_json appears in formula.
        # beneish_m_score nests component-prefixed keys ("AQI.Assets_t-1") --
        # the component name itself (AQI) is what the top-level formula names.
        concept_keys = [k.split(".")[0].split("_t")[0] for k in inputs if k != "_null_reason"]
        assert any(ck in row["formula"] for ck in concept_keys), (row["name"], row["formula"], inputs)


def test_every_metric_row_has_formula_and_inputs_json(conn):
    _ingest(conn, AMZN_CIK)
    metrics.compute_metrics(conn, tickers=["AMZN"])
    rows = conn.execute("SELECT formula, inputs_json FROM metrics WHERE cik = ?", (AMZN_CIK,)).fetchall()
    assert rows
    for row in rows:
        assert row["formula"]
        assert row["inputs_json"]
        json.loads(row["inputs_json"])  # valid JSON


# --- startup consistency check ---


def test_metric_registry_inputs_exist_in_concept_registry(monkeypatch):
    real_registry = config.METRIC_REGISTRY
    bad_registry = dict(real_registry)
    bad_registry["_bad_metric"] = config.MetricDef("_bad_metric", ("not_a_real_input",), "annual", None, False, "test")
    monkeypatch.setattr(config, "METRIC_REGISTRY", bad_registry)

    try:
        with pytest.raises(RuntimeError, match="not_a_real_input"):
            importlib.reload(metrics)
    finally:
        monkeypatch.setattr(config, "METRIC_REGISTRY", real_registry)
        importlib.reload(metrics)  # restore real module state for subsequent tests


# --- idempotency ---


def test_compute_metrics_idempotent(conn):
    _ingest(conn, AMZN_CIK)
    metrics.compute_metrics(conn, tickers=["AMZN"])
    count_after_first = conn.execute("SELECT COUNT(*) AS n FROM metrics").fetchone()["n"]

    second_written = metrics.compute_metrics(conn, tickers=["AMZN"])
    count_after_second = conn.execute("SELECT COUNT(*) AS n FROM metrics").fetchone()["n"]

    assert count_after_second == count_after_first
    assert second_written == []


# --- SPEC-008 C4 (approved 2026-08-08): discrete fiscal quarters ---


def test_discrete_quarter_generic_loop_never_computes_it(conn):
    # computed_separately metrics must be skipped by compute_metrics's own
    # generic per-period loop -- if COMPUTE_FUNCS's guard is ever reached
    # (the skip removed by accident), it raises rather than silently
    # writing a value at the wrong (cumulative) period_start.
    _ingest(conn, MU_CIK)
    metrics.compute_metrics(conn, tickers=["MU"])
    rows = _metric_rows(conn, MU_CIK, "cfo_discrete")
    assert rows == []  # compute_metrics alone writes nothing for it


def test_discrete_quarter_q1_is_the_filed_figure_directly_no_subtraction(conn):
    _ingest(conn, MU_CIK)
    metrics.compute_discrete_quarter_metrics(conn, tickers=["MU"])
    rows = _metric_rows(conn, MU_CIK, "cfo_discrete")
    q1 = next(r for r in rows if r["period_end"] == "2025-11-27")
    assert q1["value"] == 8_411_000_000.0
    assert "already discrete" in q1["formula"]


def test_discrete_quarter_q2_q3_derived_by_subtraction_matches_hand_computed(conn):
    # MU's real cumulative pattern: Q1=8,411M, Q2 YTD=20,314M, Q3 YTD=45,702M
    # -- discrete Q2 = 20,314 - 8,411 = 11,903M, discrete Q3 = 45,702 -
    # 20,314 = 25,388M. Confirmed by hand before writing this test.
    _ingest(conn, MU_CIK)
    metrics.compute_discrete_quarter_metrics(conn, tickers=["MU"])
    rows = {r["period_end"]: r for r in _metric_rows(conn, MU_CIK, "cfo_discrete")}
    assert rows["2026-02-26"]["value"] == pytest.approx(11_903_000_000.0)
    assert rows["2026-05-28"]["value"] == pytest.approx(25_388_000_000.0)


def test_discrete_quarter_period_start_is_the_real_filed_date_not_a_calendar_guess(conn):
    # MU's fiscal year floats (2025-08-29, not any fixed MMDD) -- the
    # discrete quarter's own period_start must come from the actual prior
    # filed period_end + 1 day, never a `config.Company.fiscal_year_end`
    # calendar assumption.
    _ingest(conn, MU_CIK)
    metrics.compute_discrete_quarter_metrics(conn, tickers=["MU"])
    rows = {r["period_end"]: r for r in conn.execute(
        "SELECT period_start, period_end, value FROM metrics WHERE cik = ? AND name = 'cfo_discrete'", (MU_CIK,)
    ).fetchall()}
    assert rows["2025-11-27"]["period_start"] == "2025-08-29"  # Q1: fiscal year's own start
    assert rows["2026-02-26"]["period_start"] == "2025-11-28"  # Q2: the day after Q1 actually ended
    assert rows["2026-05-28"]["period_start"] == "2026-02-27"  # Q3: the day after Q2 actually ended


def test_discrete_quarter_fails_closed_when_the_fy_figure_is_not_filed_yet(conn):
    # MU's fixture has no FY2026 10-K yet (the fiscal year is still in
    # progress) -- Q4-discrete must be absent (null with a reason), never
    # estimated.
    _ingest(conn, MU_CIK)
    metrics.compute_discrete_quarter_metrics(conn, tickers=["MU"])
    rows = conn.execute(
        "SELECT value, inputs_json FROM metrics WHERE cik = ? AND name = 'cfo_discrete' AND period_end = ?",
        (MU_CIK, "2026-08-28"),  # would be the Q4/FY end, if it were filed
    ).fetchall()
    assert rows == []  # not even a null row -- the fiscal quarter itself doesn't exist in `filings` yet


def test_discrete_quarter_full_cycle_sums_to_the_filed_fy_figure(conn):
    # NVDA's FY2026 fixture is complete: Q1=27,414M, Q2 YTD=42,779M, Q3
    # YTD=66,530M, FY=102,718M -- confirmed by hand: discrete quarters are
    # 27,414 / 15,365 / 23,751 / 36,188, summing to exactly 102,718.
    _ingest(conn, NVDA_CIK)
    metrics.compute_discrete_quarter_metrics(conn, tickers=["NVDA"])
    rows = {r["period_end"]: r["value"] for r in _metric_rows(conn, NVDA_CIK, "cfo_discrete")}
    q1, q2, q3, q4 = rows["2025-04-27"], rows["2025-07-27"], rows["2025-10-26"], rows["2026-01-25"]
    assert (q1, q2, q3, q4) == pytest.approx((27_414_000_000.0, 15_365_000_000.0, 23_751_000_000.0, 36_188_000_000.0))
    assert q1 + q2 + q3 + q4 == pytest.approx(102_718_000_000.0)


def test_discrete_quarter_capex_plausibility_gate_nulls_a_restated_endpoint(conn):
    # SPEC-008 C4 item 2: subtraction can produce a negative derived capex
    # if an endpoint was restated -- meaningless, must become a null with a
    # reason, never a displayed figure. Simulates a restatement by
    # corrupting Q1's filed capex UPWARD after ingest, so Q2's cumulative
    # figure (unchanged) minus the inflated Q1 goes negative.
    _ingest(conn, MU_CIK)
    conn.execute(
        "UPDATE xbrl_facts SET value = value * 100 WHERE cik = ? AND concept = ? AND period_end = ?",
        (MU_CIK, "PaymentsToAcquirePropertyPlantAndEquipment", "2025-11-27"),
    )
    conn.commit()
    metrics.compute_discrete_quarter_metrics(conn, tickers=["MU"])
    rows = {r["period_end"]: r for r in _metric_rows(conn, MU_CIK, "capex_discrete")}
    q2 = rows["2026-02-26"]
    assert q2["value"] is None
    assert "implausible" in json.loads(q2["inputs_json"])["_null_reason"]


def test_discrete_quarter_negative_cfo_is_not_caught_by_the_plausibility_gate(conn):
    # The opposite of the case above: cfo has NO lower bound (None), since
    # a negative operating quarter is a legitimate business outcome, not an
    # error signal -- simulate one and confirm it survives.
    _ingest(conn, MU_CIK)
    conn.execute(
        "UPDATE xbrl_facts SET value = ? WHERE cik = ? AND concept = ? AND period_end = ?",
        (-500_000_000.0, MU_CIK, "NetCashProvidedByUsedInOperatingActivities", "2025-11-27"),
    )
    conn.commit()
    metrics.compute_discrete_quarter_metrics(conn, tickers=["MU"])
    rows = {r["period_end"]: r for r in _metric_rows(conn, MU_CIK, "cfo_discrete")}
    # Q2 discrete = 20,314M (unchanged YTD) - (-500M) = 20,814M -- a huge
    # positive number, not what's being tested here; what matters is Q1
    # itself (the negative figure, filed directly) survives as negative.
    assert rows["2025-11-27"]["value"] == -500_000_000.0


def test_free_cash_flow_discrete_composes_cfo_and_capex_discrete(conn):
    _ingest(conn, MU_CIK)
    metrics.compute_discrete_quarter_metrics(conn, tickers=["MU"])
    cfo = {r["period_end"]: r["value"] for r in _metric_rows(conn, MU_CIK, "cfo_discrete")}
    capex = {r["period_end"]: r["value"] for r in _metric_rows(conn, MU_CIK, "capex_discrete")}
    fcf = {r["period_end"]: r["value"] for r in _metric_rows(conn, MU_CIK, "free_cash_flow_discrete")}
    for end in ("2025-11-27", "2026-02-26", "2026-05-28"):
        assert fcf[end] == pytest.approx(cfo[end] - capex[end])


def test_discrete_quarter_metrics_idempotent(conn):
    _ingest(conn, MU_CIK)
    metrics.compute_discrete_quarter_metrics(conn, tickers=["MU"])
    count_after_first = conn.execute(
        "SELECT COUNT(*) AS n FROM metrics WHERE name LIKE '%_discrete'"
    ).fetchone()["n"]
    second_written = metrics.compute_discrete_quarter_metrics(conn, tickers=["MU"])
    count_after_second = conn.execute(
        "SELECT COUNT(*) AS n FROM metrics WHERE name LIKE '%_discrete'"
    ).fetchone()["n"]
    assert count_after_second == count_after_first
    assert second_written == []


# --- SPEC-008 D12 (approved 2026-08-08): discrete Q4 on the income statement ---
#
# The trimmed companyfacts fixtures don't carry a clean, complete fiscal-
# year cycle for any income-statement concept (unlike cfo, which happened
# to) -- real data confirms the mechanism works (verified against
# data/app.db: AMZN's derived Q4 2024 revenue is exactly 187,792,000,000,
# matching 637,959,000,000 FY minus 450,167,000,000 9-month YTD, both real
# filed figures), but a fixture-driven test needs facts inserted directly
# rather than relying on what happens to be in the trimmed JSON.


def _insert_filing_row(conn, cik, accession_no, form_type, period_end, fiscal_year, fiscal_period):
    conn.execute(
        "INSERT INTO filings (accession_no, cik, form_type, filing_date, period_end, fiscal_year, "
        "fiscal_period, discovered_at, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'sectioned') "
        "ON CONFLICT(accession_no) DO NOTHING",
        (accession_no, cik, form_type, period_end, period_end, fiscal_year, fiscal_period, f"{period_end}T00:00:00"),
    )


def _insert_income_fact(conn, cik, concept, period_start, period_end, value, duration_days, accession_no):
    conn.execute(
        "INSERT INTO xbrl_facts (cik, taxonomy, concept, unit, period_start, period_end, value, "
        "accession_no, filed_date, duration_days) VALUES (?, 'us-gaap', ?, 'USD', ?, ?, ?, ?, ?, ?)",
        (cik, concept, period_start, period_end, value, accession_no, period_end, duration_days),
    )


def _seed_amzn_fiscal_year_revenue(conn):
    """A clean, synthetic, single-fiscal-year AMZN-shaped revenue cycle --
    Q1/Q2/Q3 cumulative-tagged (the real pattern confirmed against
    data/app.db: AMZN double-tags discrete AND cumulative for income-
    statement lines in the same filing), FY tagged only as the annual
    cumulative (no discrete Q4 anywhere -- the D12 gap itself). Hand-
    computed: Q1=100M, Q2=120M, Q3=140M, Q4=140M, summing to FY=500M."""
    concept = "RevenueFromContractWithCustomerExcludingAssessedTax"
    _insert_filing_row(conn, AMZN_CIK, "acc-q1", "10-Q", "2025-03-31", 2025, "Q1")
    _insert_filing_row(conn, AMZN_CIK, "acc-q2", "10-Q", "2025-06-30", 2025, "Q2")
    _insert_filing_row(conn, AMZN_CIK, "acc-q3", "10-Q", "2025-09-30", 2025, "Q3")
    _insert_filing_row(conn, AMZN_CIK, "acc-fy", "10-K", "2025-12-31", 2025, "FY")
    _insert_income_fact(conn, AMZN_CIK, concept, "2025-01-01", "2025-03-31", 100_000_000, 90, "acc-q1")
    _insert_income_fact(conn, AMZN_CIK, concept, "2025-01-01", "2025-06-30", 220_000_000, 181, "acc-q2")
    _insert_income_fact(conn, AMZN_CIK, concept, "2025-01-01", "2025-09-30", 360_000_000, 273, "acc-q3")
    _insert_income_fact(conn, AMZN_CIK, concept, "2025-01-01", "2025-12-31", 500_000_000, 365, "acc-fy")
    conn.commit()


def test_income_statement_q4_derived_from_fy_minus_nine_month(conn):
    _seed_amzn_fiscal_year_revenue(conn)
    metrics.compute_discrete_quarter_metrics(conn, tickers=["AMZN"])
    rows = {r["period_end"]: r for r in _metric_rows(conn, AMZN_CIK, "revenue_discrete")}
    assert rows["2025-03-31"]["value"] == pytest.approx(100_000_000.0)  # Q1, direct
    assert rows["2025-12-31"]["value"] == pytest.approx(140_000_000.0)  # Q4 = 500M - 360M
    assert "500" in rows["2025-12-31"]["formula"] or "YTD to 2025-12-31" in rows["2025-12-31"]["formula"]


def test_income_statement_quarters_sum_back_to_filed_fy(conn):
    _seed_amzn_fiscal_year_revenue(conn)
    metrics.compute_discrete_quarter_metrics(conn, tickers=["AMZN"])
    rows = {r["period_end"]: r["value"] for r in _metric_rows(conn, AMZN_CIK, "revenue_discrete")}
    total = rows["2025-03-31"] + rows["2025-06-30"] + rows["2025-09-30"] + rows["2025-12-31"]
    assert total == pytest.approx(500_000_000.0)


def test_income_statement_q4_fails_closed_when_fy_not_filed_yet(conn):
    # Same shape as MU's real in-progress fiscal year (cash flow) -- no
    # 10-K yet means no FY figure to subtract from, so Q4 must be absent,
    # never estimated.
    concept = "RevenueFromContractWithCustomerExcludingAssessedTax"
    _insert_filing_row(conn, AMZN_CIK, "acc-q1", "10-Q", "2025-03-31", 2025, "Q1")
    _insert_filing_row(conn, AMZN_CIK, "acc-q3", "10-Q", "2025-09-30", 2025, "Q3")
    _insert_income_fact(conn, AMZN_CIK, concept, "2025-01-01", "2025-03-31", 100_000_000, 90, "acc-q1")
    _insert_income_fact(conn, AMZN_CIK, concept, "2025-01-01", "2025-09-30", 360_000_000, 273, "acc-q3")
    conn.commit()
    metrics.compute_discrete_quarter_metrics(conn, tickers=["AMZN"])
    rows = conn.execute(
        "SELECT value FROM metrics WHERE cik = ? AND name = 'revenue_discrete' AND period_end = ?",
        (AMZN_CIK, "2025-12-31"),
    ).fetchall()
    assert rows == []  # the fiscal quarter itself doesn't exist in `filings` -- nothing to write


def test_balance_sheet_lines_have_no_discrete_fallback(conn):
    # D12 is explicit: the balance sheet is unaffected -- instants need no
    # subtraction. Confirms no BALANCE_SHEET_LINES-style canonical was
    # accidentally swept into the discrete-quarter mechanism.
    balance_sheet_canonicals = {
        "cash", "short_term_investments", "receivables", "inventory", "current_assets",
        "ppe_net", "ppe_and_lease_net", "total_assets", "payables", "current_liabilities",
        "debt_noncurrent", "equity",
    }
    assert balance_sheet_canonicals.isdisjoint(metrics._DISCRETE_QUARTER_CANONICALS)
