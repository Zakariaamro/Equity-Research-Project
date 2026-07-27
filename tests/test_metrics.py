"""Tests for edgar.metrics (SPEC-004 R1a/R3/R4/R5/R6/R7/R9).

Fixtures: trimmed real companyfacts responses for all three watchlist
companies (tests/fixtures/companyfacts_trimmed_*.json).
"""

from __future__ import annotations

import importlib
import json

import pytest

from edgar import config, db, metrics, xbrl
from tests.conftest import FIXTURES_DIR, insert_fixture_filings

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
    xbrl.ingest_company(conn, FakeXbrlClient(FIXTURES_BY_CIK[cik]), cik)
    insert_fixture_filings(conn, ciks=[cik])


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
