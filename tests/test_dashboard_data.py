"""Tests for dashboard.data (SPEC-008). A real (throwaway) SQLite database
per test -- no mocking of sqlite3 itself, since this module's whole job is
correct SQL against the real schema."""

from __future__ import annotations

import json

import pytest

from dashboard import data
from edgar import config, db

AMZN_CIK = "0001018724"


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "test.db"
    db.init_db(path)
    return path


def _insert_filing(db_path, accession_no, cik=AMZN_CIK, form_type="10-K", filing_date="2026-02-06",
                    period_end="2025-12-31", fiscal_year=2025, fiscal_period="FY"):
    conn = db.get_connection(db_path)
    conn.execute(
        "INSERT INTO filings (accession_no, cik, form_type, filing_date, period_end, fiscal_year, "
        "fiscal_period, discovered_at, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'sectioned') "
        "ON CONFLICT(accession_no) DO NOTHING",
        (accession_no, cik, form_type, filing_date, period_end, fiscal_year, fiscal_period, f"{filing_date}T00:00:00"),
    )
    conn.commit()
    conn.close()


def _insert_observation(db_path, accession_no, rule_name, subject, severity, statement, cik=AMZN_CIK,
                         period_end="2025-12-31", rule_version=None):
    rule_version = rule_version or config.RULE_REGISTRY[rule_name].version
    conn = db.get_connection(db_path)
    cur = conn.execute(
        "INSERT INTO observations (cik, accession_no, period_end, rule_name, rule_version, subject, "
        "severity, statement, refs_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, '[]', ?)",
        (cik, accession_no, period_end, rule_name, rule_version, subject, severity, statement, "2026-01-01T00:00:00"),
    )
    conn.commit()
    row_id = cur.lastrowid
    conn.close()
    return row_id


def _insert_finding(db_path, accession_no, category, severity, headline, detail="detail", quote="a" * 45):
    conn = db.get_connection(db_path)
    cur = conn.execute(
        "INSERT INTO sections (accession_no, category, short_name, source_file, position, text_hash) "
        "VALUES (?, 'Notes', ?, 'R1.htm', 1, ?)",
        (accession_no, f"Note {headline[:10]}", f"hash-{headline[:10]}"),
    )
    section_id = cur.lastrowid
    cur = conn.execute(
        "INSERT INTO analyses (section_id, prompt_name, prompt_version, model, input_hash, output_json, "
        "call_id, created_at) VALUES (?, 'section_analysis', 'v3', 'claude-sonnet-5', ?, '{}', NULL, ?)",
        (section_id, f"hash-{headline}", "2026-01-01T00:00:00"),
    )
    analysis_id = cur.lastrowid
    cur = conn.execute(
        "INSERT INTO findings (analysis_id, accession_no, category, severity, headline, detail, quote, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (analysis_id, accession_no, category, severity, headline, detail, quote, "2026-01-01T00:00:00"),
    )
    conn.commit()
    row_id = cur.lastrowid
    conn.close()
    return row_id


def _insert_metric(db_path, cik, name, period_start, period_end, value, formula="f", inputs_used=None, null_reason=None):
    conn = db.get_connection(db_path)
    inputs = dict(inputs_used or {})
    if value is None:
        inputs["_null_reason"] = null_reason
    conn.execute(
        "INSERT INTO metrics (cik, accession_no, period_start, period_end, name, value, formula, inputs_json, "
        "calc_version, computed_at) VALUES (?, NULL, ?, ?, ?, ?, ?, ?, ?, ?)",
        (cik, period_start, period_end, name, value, formula, json.dumps(inputs), config.CALC_VERSION, "2026-01-01T00:00:00"),
    )
    conn.commit()
    conn.close()


def _insert_xbrl_fact(db_path, cik, concept, period_end, value, period_start=None, accession_no="acc-fact"):
    conn = db.get_connection(db_path)
    conn.execute(
        "INSERT INTO xbrl_facts (cik, taxonomy, concept, unit, period_start, period_end, value, "
        "accession_no, filed_date) VALUES (?, 'us-gaap', ?, 'USD', ?, ?, ?, ?, ?) "
        "ON CONFLICT(cik, concept, unit, period_start, period_end, accession_no) DO UPDATE SET value = excluded.value",
        (cik, concept, period_start, period_end, value, accession_no, "2026-06-25"),
    )
    conn.commit()
    conn.close()


def _insert_brief(db_path, accession_no, cik, sentences):
    conn = db.get_connection(db_path)
    cur = conn.execute(
        "INSERT INTO briefs (accession_no, cik, prompt_name, prompt_version, verifier_version, model, "
        "input_hash, created_at) VALUES (?, ?, 'filing_brief', 'v1', 'v1', 'claude-sonnet-5', ?, ?)",
        (accession_no, cik, f"hash-{accession_no}", "2026-01-01T00:00:00"),
    )
    brief_id = cur.lastrowid
    for i, (sentence_type, text, refs) in enumerate(sentences):
        conn.execute(
            "INSERT INTO brief_sentences (brief_id, position, sentence_type, text, refs_json) VALUES (?, ?, ?, ?, ?)",
            (brief_id, i, sentence_type, text, json.dumps(refs)),
        )
    conn.commit()
    conn.close()
    return brief_id


# --- companies, filings ---


def test_get_companies_returns_expected_shape(db_path):
    companies = data.get_companies(db_path)
    tickers = {c["ticker"] for c in companies}
    assert {"AMZN", "NVDA", "MU"} <= tickers
    assert all("cik" in c and "name" in c for c in companies)


def test_get_anchor_filing_prefers_10k_10q_never_8k(db_path):
    _insert_filing(db_path, "acc-10q", form_type="10-Q", filing_date="2026-05-20")
    _insert_filing(db_path, "acc-8k", form_type="8-K", filing_date="2026-05-21")  # LATER than the 10-Q
    anchor = data.get_anchor_filing(AMZN_CIK, db_path)
    assert anchor["accession_no"] == "acc-10q"
    assert anchor["form_type"] == "10-Q"


def test_get_anchor_filing_includes_ticker_and_company_name(db_path):
    # SPEC-008 review D2 (found live): this query used to select only
    # `filings` columns, so every Overview header rendered "(MU)" with an
    # empty company name -- the caller patched `ticker` in by hand and
    # never patched `company_name` in at all.
    _insert_filing(db_path, "acc-10k", form_type="10-K", filing_date="2026-02-06")
    anchor = data.get_anchor_filing(AMZN_CIK, db_path)
    assert anchor["ticker"] == "AMZN"
    assert anchor["company_name"]  # non-empty -- a real seeded company name, not patched in by the caller


def test_get_more_recent_8k_found_when_same_day_or_later(db_path):
    _insert_filing(db_path, "acc-10q", form_type="10-Q", filing_date="2026-05-20")
    _insert_filing(db_path, "acc-8k", form_type="8-K", filing_date="2026-05-20")
    eightk = data.get_more_recent_8k(AMZN_CIK, "2026-05-20", db_path)
    assert eightk is not None
    assert eightk["accession_no"] == "acc-8k"


def test_get_more_recent_8k_none_when_only_older_8k_exists(db_path):
    _insert_filing(db_path, "acc-10q", form_type="10-Q", filing_date="2026-05-20")
    _insert_filing(db_path, "acc-8k-old", form_type="8-K", filing_date="2026-04-01")
    assert data.get_more_recent_8k(AMZN_CIK, "2026-05-20", db_path) is None


def test_get_all_filings_and_get_filing(db_path):
    _insert_filing(db_path, "acc1")
    all_filings = data.get_all_filings(db_path)
    assert any(f["accession_no"] == "acc1" for f in all_filings)
    single = data.get_filing("acc1", db_path)
    assert single["ticker"] == "AMZN"
    assert data.get_filing("does-not-exist", db_path) is None


# --- observations ---


def test_get_top_observations_filters_current_version_and_caps_per_rule(db_path):
    _insert_filing(db_path, "acc1")
    _insert_observation(db_path, "acc1", "section_appeared", "Note X", "high", "stale", rule_version="v1")
    _insert_observation(db_path, "acc1", "section_appeared", "Note X", "low", "current", rule_version="v4")
    for i in range(3):
        _insert_observation(db_path, "acc1", "section_wording_changed", f"Note {i}", "high", f"changed {i}")

    top = data.get_top_observations("acc1", db_path, max_total=8, max_per_rule=2)
    rule_names = [o["rule_name"] for o in top]
    assert rule_names.count("section_wording_changed") == 2  # capped
    assert rule_names.count("section_appeared") == 1  # only the current-version row survived
    assert all(o["rule_version"] == config.RULE_REGISTRY[o["rule_name"]].version for o in top)


def test_get_red_flag_findings(db_path):
    _insert_filing(db_path, "acc1")
    _insert_finding(db_path, "acc1", "red_flag", "medium", "the impairment")
    _insert_finding(db_path, "acc1", "litigation", "high", "a lawsuit")
    red_flags = data.get_red_flag_findings("acc1", db_path)
    assert len(red_flags) == 1
    assert red_flags[0]["category"] == "red_flag"


def test_get_red_flag_findings_empty_list_when_none(db_path):
    _insert_filing(db_path, "acc1")
    _insert_finding(db_path, "acc1", "litigation", "high", "a lawsuit")
    assert data.get_red_flag_findings("acc1", db_path) == []


def test_get_findings_for_filing_sorts_by_severity_then_category_then_id(db_path):
    # SPEC-008-batch-4 item 2 (approved 2026-08-16): reproduces the item's
    # own reported shape -- insertion order scattered Medium/Low/High
    # throughout, with ORDER BY id alone (this function's entire sort key
    # before this item) as the only ordering. Descending by severity,
    # then category, then id (the prior tie-break, still there).
    _insert_filing(db_path, "acc1")
    _insert_finding(db_path, "acc1", "note_item", "medium", "fog index note")
    _insert_finding(db_path, "acc1", "note_item", "low", "a low note")
    _insert_finding(db_path, "acc1", "concentration", "low", "customer concentration")
    _insert_finding(db_path, "acc1", "litigation", "high", "Indian tax dispute")
    _insert_finding(db_path, "acc1", "accounting_change", "medium", "revenue recognition change")
    _insert_finding(db_path, "acc1", "red_flag", "high", "$15.9B discrete tax expense")
    _insert_finding(db_path, "acc1", "concentration", "high", "Anthropic revaluation")
    findings = data.get_findings_for_filing("acc1", db_path)
    severities = [f["severity"] for f in findings]
    assert severities == ["high", "high", "high", "medium", "medium", "low", "low"]
    # Within "high", category is the tie-break: alphabetical (concentration
    # < litigation < red_flag), not insertion order.
    high_categories = [f["category"] for f in findings if f["severity"] == "high"]
    assert high_categories == ["concentration", "litigation", "red_flag"]


# --- metrics ---


def test_get_metric_series_separates_annual_from_quarterly_for_both_basis(db_path):
    _insert_metric(db_path, AMZN_CIK, "gross_margin", "2025-01-01", "2025-03-31", 0.50)  # ~90 days -> quarterly
    _insert_metric(db_path, AMZN_CIK, "gross_margin", "2025-01-01", "2025-12-31", 0.48)  # ~365 days -> annual
    quarterly = data.get_metric_series(AMZN_CIK, "gross_margin", "quarterly", db_path)
    annual = data.get_metric_series(AMZN_CIK, "gross_margin", "annual", db_path)
    assert len(quarterly) == 1 and quarterly[0]["value"] == 0.50
    assert len(annual) == 1 and annual[0]["value"] == 0.48


def test_get_metric_series_null_carries_reason_never_zero(db_path):
    _insert_metric(db_path, AMZN_CIK, "roic", "2026-03-01", "2026-05-28", None, null_reason="borrowings unresolved")
    series = data.get_metric_series(AMZN_CIK, "roic", "quarterly", db_path)
    assert len(series) == 1
    assert series[0]["value"] is None
    assert series[0]["value"] != 0
    assert series[0]["null_reason"] == "borrowings unresolved"


def test_get_latest_metric_returns_most_recent_period(db_path):
    _insert_metric(db_path, AMZN_CIK, "gross_margin", "2025-01-01", "2025-03-31", 0.50)
    _insert_metric(db_path, AMZN_CIK, "gross_margin", "2025-04-01", "2025-06-30", 0.52)
    latest = data.get_latest_metric(AMZN_CIK, "gross_margin", "quarterly", db_path)
    assert latest["period_end"] == "2025-06-30"
    assert latest["value"] == 0.52


def test_get_latest_metric_none_when_no_data(db_path):
    assert data.get_latest_metric(AMZN_CIK, "gross_margin", "quarterly", db_path) is None


def test_get_metric_evidence_disambiguates_shared_period_end(db_path):
    # Real documented case (decision log #19): an annual and an implicit
    # quarter can share the same period_end with different period_start.
    _insert_metric(db_path, AMZN_CIK, "revenue_yoy", "2025-01-01", "2025-12-31", 0.10, formula="annual formula")
    _insert_metric(db_path, AMZN_CIK, "revenue_yoy", "2025-10-01", "2025-12-31", 0.20, formula="quarterly formula")
    annual_evidence = data.get_metric_evidence(AMZN_CIK, "revenue_yoy", "2025-01-01", "2025-12-31", db_path)
    quarterly_evidence = data.get_metric_evidence(AMZN_CIK, "revenue_yoy", "2025-10-01", "2025-12-31", db_path)
    assert annual_evidence["formula"] == "annual formula"
    assert quarterly_evidence["formula"] == "quarterly formula"


# --- brief ---


def test_get_brief_sentences_resolves_sources_and_ranks_severity(db_path):
    _insert_filing(db_path, "acc1")
    obs_id = _insert_observation(db_path, "acc1", "metric_multi_year_extreme", "gross_margin", "high", "a 6-year high")
    finding_id = _insert_finding(db_path, "acc1", "litigation", "medium", "a lawsuit")
    _insert_brief(db_path, "acc1", AMZN_CIK, [
        ("restatement", "Gross margin hit a 6-year high.", [f"obs:{obs_id}"]),
        ("restatement", "A lawsuit was disclosed.", [f"finding:{finding_id}"]),
    ])
    sentences = data.get_brief_sentences("acc1", db_path)
    assert len(sentences) == 2
    assert sentences[0]["sources"][0]["kind"] == "observation"
    assert sentences[0]["max_source_severity"] == 0  # high
    assert sentences[1]["max_source_severity"] == 1  # medium


def test_get_brief_sentences_empty_list_when_no_brief(db_path):
    _insert_filing(db_path, "acc1")
    assert data.get_brief_sentences("acc1", db_path) == []


def test_get_filing_detail_brief_none_distinct_from_empty(db_path):
    _insert_filing(db_path, "acc1")
    detail = data.get_filing_detail("acc1", db_path)
    assert detail["brief"] is None  # no brief AT ALL, distinct from an empty one
    assert detail["observations"] == []
    assert detail["findings"] == []


def test_get_filing_detail_with_brief(db_path):
    _insert_filing(db_path, "acc1")
    _insert_brief(db_path, "acc1", AMZN_CIK, [("restatement", "Something true.", [])])
    detail = data.get_filing_detail("acc1", db_path)
    assert detail["brief"] is not None
    assert len(detail["brief"]["sentences"]) == 1


# --- cache invalidation ---


def test_missing_database_fails_with_clear_message(tmp_path):
    missing_path = tmp_path / "does_not_exist.db"
    with pytest.raises(FileNotFoundError, match=str(missing_path)):
        data.get_companies(missing_path)


def test_cache_invalidates_when_db_mtime_changes(db_path):
    companies_before = data.get_companies(db_path)
    assert len(companies_before) == 3
    # Simulate a real write between reads -- a new company inserted.
    conn = db.get_connection(db_path)
    conn.execute(
        "INSERT INTO companies (cik, ticker, name, fiscal_year_end) VALUES ('9999999999', 'TEST', 'Test Co', '1231')"
    )
    conn.commit()
    conn.close()
    import os
    import time

    # Force an unambiguously distinct mtime -- a bare os.utime(path, None) can
    # land within the filesystem's clock resolution of the original write and
    # not actually change anything measurable.
    bumped = time.time() + 5
    os.utime(db_path, (bumped, bumped))
    companies_after = data.get_companies(db_path)
    assert len(companies_after) == 4


# --- statement line values (SPEC-008 review D11) ---


def test_statement_line_values_picks_the_exact_duration_not_an_arbitrary_tie_break(db_path):
    # Found live: Micron's revenue at period_end=2026-05-28 has BOTH a
    # three-month row (period_start 2026-02-27, value 41,456M) and a
    # nine-month year-to-date cumulative row (period_start 2025-08-29,
    # value 78,959M) sharing the same period_end and filed_date -- a query
    # by period_end alone has no principled way to prefer one. The caller
    # must get back exactly the row matching the period_start it selected.
    _insert_xbrl_fact(
        db_path, AMZN_CIK, "RevenueFromContractWithCustomerExcludingAssessedTax", "2026-05-28",
        78_959_000_000, period_start="2025-08-29",
    )
    _insert_xbrl_fact(
        db_path, AMZN_CIK, "RevenueFromContractWithCustomerExcludingAssessedTax", "2026-05-28",
        41_456_000_000, period_start="2026-02-27", accession_no="acc-fact-2",
    )
    quarterly = data.get_statement_line_values(
        AMZN_CIK, "2026-02-27", "2026-05-28", (("revenue", "Revenue", None, None),), db_path
    )
    assert quarterly[0]["value"] == 41_456_000_000
    nine_month = data.get_statement_line_values(
        AMZN_CIK, "2025-08-29", "2026-05-28", (("revenue", "Revenue", None, None),), db_path
    )
    assert nine_month[0]["value"] == 78_959_000_000


def test_statement_line_values_instant_concepts_ignore_period_start(db_path):
    # A balance-sheet (instant) concept has no period_start at all -- must
    # still resolve by period_end regardless of what period_start the
    # caller's selected duration-based period happens to carry.
    _insert_xbrl_fact(db_path, AMZN_CIK, "CashAndCashEquivalentsAtCarryingValue", "2026-05-28", 24_995_000_000)
    rows = data.get_statement_line_values(
        AMZN_CIK, "2026-02-27", "2026-05-28", (("cash", "Cash and cash equivalents", None, None),), db_path
    )
    assert rows[0]["value"] == 24_995_000_000


def test_statement_line_values_renders_both_instant_and_duration_facts_for_one_period(db_path):
    # Reported live against AMZN's Mar 31 2026 period: an instant fact
    # (long-term debt) and two 89-day duration facts (cash from operations,
    # stock-based compensation) all genuinely tagged for the same
    # period_end, in the same statement pass. A regression that force-
    # filters period_start onto instant concepts too (which have no
    # period_start in xbrl_facts at all) would silently drop the instant
    # line specifically, while duration lines whose period_start happens to
    # match would still work -- exactly the asymmetric failure reported.
    _insert_xbrl_fact(db_path, AMZN_CIK, "LongTermDebtNoncurrent", "2026-03-31", 119_074_000_000)
    _insert_xbrl_fact(
        db_path, AMZN_CIK, "NetCashProvidedByUsedInOperatingActivities", "2026-03-31",
        26_032_000_000, period_start="2026-01-01",
    )
    _insert_xbrl_fact(
        db_path, AMZN_CIK, "ShareBasedCompensation", "2026-03-31",
        4_032_000_000, period_start="2026-01-01", accession_no="acc-fact-2",
    )
    balance_sheet = data.get_statement_line_values(
        AMZN_CIK, "2026-01-01", "2026-03-31", (("debt_noncurrent", "Long-term debt", None, None),), db_path
    )
    cash_flow = data.get_statement_line_values(
        AMZN_CIK, "2026-01-01", "2026-03-31",
        (("cfo", "Cash from operations", None, None), ("sbc", "Stock-based compensation", None, None)), db_path
    )
    assert balance_sheet[0]["value"] == 119_074_000_000
    assert cash_flow[0]["value"] == 26_032_000_000
    assert cash_flow[1]["value"] == 4_032_000_000


def test_statement_line_values_metric_registry_branch_also_matches_exact_period(db_path):
    _insert_metric(db_path, AMZN_CIK, "free_cash_flow", "2026-02-27", "2026-05-28", -1_234_000_000.0)
    rows = data.get_statement_line_values(
        AMZN_CIK, "2026-02-27", "2026-05-28", (("free_cash_flow", "Free cash flow", None, None),), db_path
    )
    assert rows[0]["value"] == -1_234_000_000.0
    # A different period_start for the same period_end must not match.
    rows_wrong_period = data.get_statement_line_values(
        AMZN_CIK, "2025-08-29", "2026-05-28", (("free_cash_flow", "Free cash flow", None, None),), db_path
    )
    assert rows_wrong_period[0]["value"] is None


def test_get_period_duration_class_matches_three_month_and_nine_month_bands():
    assert data.get_period_duration_class("2026-02-27", "2026-05-28") == "quarterly"
    assert data.get_period_duration_class("2025-08-29", "2026-05-28") == "three-quarter"


def test_cash_flow_period_uses_the_quarterly_period_when_it_has_data(db_path):
    _insert_xbrl_fact(
        db_path, AMZN_CIK, "NetCashProvidedByUsedInOperatingActivities", "2026-05-28",
        1_000_000_000, period_start="2026-02-27",
    )
    period_start, note = data.get_cash_flow_period(AMZN_CIK, "2026-02-27", "2026-05-28", db_path)
    assert period_start == "2026-02-27"
    assert note == ""


def test_cash_flow_period_falls_back_to_the_one_unambiguous_alternative(db_path):
    # Confirmed live against the real corpus: Micron's/NVIDIA's non-Q1
    # quarters routinely have no three-month cash-flow facts at all, only
    # the year-to-date cumulative for the same period_end.
    _insert_xbrl_fact(
        db_path, AMZN_CIK, "NetCashProvidedByUsedInOperatingActivities", "2026-05-28",
        3_000_000_000, period_start="2025-08-29",
    )
    period_start, note = data.get_cash_flow_period(AMZN_CIK, "2026-02-27", "2026-05-28", db_path)
    assert period_start == "2025-08-29"
    assert "year-to-date" in note


def test_cash_flow_period_refuses_to_guess_among_multiple_candidates(db_path):
    _insert_xbrl_fact(
        db_path, AMZN_CIK, "NetCashProvidedByUsedInOperatingActivities", "2026-05-28",
        3_000_000_000, period_start="2025-08-29",
    )
    _insert_xbrl_fact(
        db_path, AMZN_CIK, "NetCashProvidedByUsedInOperatingActivities", "2026-05-28",
        1_500_000_000, period_start="2025-11-28", accession_no="acc-fact-2",
    )
    period_start, note = data.get_cash_flow_period(AMZN_CIK, "2026-02-27", "2026-05-28", db_path)
    assert period_start == "2026-02-27"  # unchanged -- no principled way to prefer one candidate
    assert note == ""


# --- statement-line fallback (SPEC-008 review, PP&E follow-up) ---


def test_statement_line_uses_the_primary_label_when_the_primary_resolves(db_path):
    _insert_xbrl_fact(db_path, AMZN_CIK, "PropertyPlantAndEquipmentNet", "2026-03-31", 200_000_000_000)
    lines = (
        (
            "ppe_net", "Property, plant and equipment, net",
            "ppe_and_lease_net", "Property, plant and equipment and finance-lease ROU assets, net",
        ),
    )
    rows = data.get_statement_line_values(AMZN_CIK, "2026-01-01", "2026-03-31", lines, db_path)
    assert rows[0]["value"] == 200_000_000_000
    assert rows[0]["label"] == "Property, plant and equipment, net"


def test_statement_line_falls_back_and_relabels_when_the_primary_is_absent(db_path):
    # Confirmed live: Amazon tags
    # PropertyPlantAndEquipmentAndFinanceLeaseRightOfUseAssetAfterAccumulated
    # DepreciationAndAmortization (397.46B at 2026-03-31), never
    # PropertyPlantAndEquipmentNet. Showing the fallback value under the
    # PRIMARY label would pair a broader number with a narrower name --
    # the label itself must change to name what the number actually is.
    _insert_xbrl_fact(
        db_path, AMZN_CIK,
        "PropertyPlantAndEquipmentAndFinanceLeaseRightOfUseAssetAfterAccumulatedDepreciationAndAmortization",
        "2026-03-31", 397_460_000_000,
    )
    lines = (
        (
            "ppe_net", "Property, plant and equipment, net",
            "ppe_and_lease_net", "Property, plant and equipment and finance-lease ROU assets, net",
        ),
    )
    rows = data.get_statement_line_values(AMZN_CIK, "2026-01-01", "2026-03-31", lines, db_path)
    assert rows[0]["value"] == 397_460_000_000
    assert rows[0]["label"] == "Property, plant and equipment and finance-lease ROU assets, net"
    assert rows[0]["label"] != "Property, plant and equipment, net"


def test_statement_line_stays_not_tagged_when_neither_primary_nor_fallback_resolve(db_path):
    lines = (
        (
            "ppe_net", "Property, plant and equipment, net",
            "ppe_and_lease_net", "Property, plant and equipment and finance-lease ROU assets, net",
        ),
    )
    rows = data.get_statement_line_values(AMZN_CIK, "2026-01-01", "2026-03-31", lines, db_path)
    assert rows[0]["value"] is None
    assert rows[0]["label"] == "Property, plant and equipment, net"  # the primary label, not the fallback's


def test_statement_line_without_a_fallback_is_unaffected(db_path):
    rows = data.get_statement_line_values(
        AMZN_CIK, "2026-01-01", "2026-03-31", (("cash", "Cash and cash equivalents", None, None),), db_path
    )
    assert rows[0]["value"] is None
    assert rows[0]["label"] == "Cash and cash equivalents"


# --- structurally-absent concepts (SPEC-008 review, debt-line follow-up) ---


def _establish_analyzed_window(db_path, cik, earliest_period_end="2020-12-31"):
    # concept_never_tagged scopes its check to period_end >= the earliest
    # `metrics` period_end for this company -- needs at least one metrics
    # row to establish that window, same as the real corpus always has.
    _insert_metric(db_path, cik, "gross_margin", "2020-01-01", earliest_period_end, 0.5)


def test_concept_never_tagged_true_when_no_row_exists_within_the_analyzed_window(db_path):
    _establish_analyzed_window(db_path, AMZN_CIK)
    assert data.concept_never_tagged(AMZN_CIK, "debt_noncurrent", db_path) is True


def test_concept_never_tagged_false_when_a_row_exists_within_the_window(db_path):
    _establish_analyzed_window(db_path, AMZN_CIK)
    _insert_xbrl_fact(db_path, AMZN_CIK, "LongTermDebtNoncurrent", "2026-03-31", 119_074_000_000)
    assert data.concept_never_tagged(AMZN_CIK, "debt_noncurrent", db_path) is False


def test_concept_never_tagged_ignores_facts_from_before_the_analyzed_window(db_path):
    # Found live: Micron DID tag LongTermDebtNoncurrent in 2012-2013, years
    # before this project's analyzed window begins -- a naive "any row,
    # ever" check was a false negative, since it has never once been
    # tagged in any period this dashboard actually shows.
    _establish_analyzed_window(db_path, AMZN_CIK, earliest_period_end="2020-12-31")
    _insert_xbrl_fact(db_path, AMZN_CIK, "LongTermDebtNoncurrent", "2013-05-30", 3_267_000_000)
    assert data.concept_never_tagged(AMZN_CIK, "debt_noncurrent", db_path) is True


def test_concept_never_tagged_false_for_a_company_with_no_metrics_rows_at_all(db_path):
    # No established analyzed window -- not "structurally absent", just
    # not this function's concern (docstring's stated behavior).
    assert data.concept_never_tagged(AMZN_CIK, "debt_noncurrent", db_path) is False


def test_null_reason_distinguishes_structural_absence_from_a_period_gap(db_path):
    _establish_analyzed_window(db_path, AMZN_CIK)

    # No debt_noncurrent row for this company at all -- structural absence,
    # with the pointer to the written diagnosis.
    reason = data.get_statement_line_null_reason(AMZN_CIK, "debt_noncurrent", "AMZN", db_path)
    assert "has not tagged this concept in any filing on record" in reason
    assert "debt-tag diagnosis" in reason

    # A concept with no written diagnosis still gets the honest structural
    # phrasing, just without inventing a pointer that doesn't exist.
    reason_no_pointer = data.get_statement_line_null_reason(AMZN_CIK, "cash", "AMZN", db_path)
    assert "has not tagged this concept in any filing on record" in reason_no_pointer
    assert "diagnosis" not in reason_no_pointer

    # A row EXISTS for some other period within the window -- a genuine
    # period-specific gap, not a structural absence, gets the original
    # phrasing.
    _insert_xbrl_fact(db_path, AMZN_CIK, "LongTermDebtNoncurrent", "2025-03-31", 100_000_000_000)
    reason_period_gap = data.get_statement_line_null_reason(AMZN_CIK, "debt_noncurrent", "AMZN", db_path)
    assert reason_period_gap == "not tagged for this period"


# --- C4: the multi-period statement table ---


def test_get_statement_periods_orders_oldest_to_newest_and_filters_by_basis(db_path):
    _insert_metric(db_path, AMZN_CIK, "gross_margin", "2025-01-01", "2025-03-31", 0.5)
    _insert_metric(db_path, AMZN_CIK, "gross_margin", "2025-04-01", "2025-06-30", 0.5)
    _insert_metric(db_path, AMZN_CIK, "gross_margin", "2024-01-01", "2024-12-31", 0.5)  # annual
    quarterly = data.get_statement_periods(AMZN_CIK, "quarterly", db_path)
    assert [p["period_end"] for p in quarterly] == ["2025-03-31", "2025-06-30"]  # oldest first
    annual = data.get_statement_periods(AMZN_CIK, "annual", db_path)
    assert [p["period_end"] for p in annual] == ["2024-12-31"]


def test_get_statement_periods_carries_fiscal_year_and_period_from_filings(db_path):
    # SPEC-008-batch-3 item 5 (approved 2026-08-14): the render layer's
    # column headers need fiscal_year/fiscal_period straight from
    # `filings` -- never computed from the date -- to lead with FY2025/
    # Q3 FY26 instead of a bare calendar date.
    _insert_filing(db_path, "acc-q1-2025", form_type="10-Q", period_end="2025-03-31", fiscal_year=2025, fiscal_period="Q1")
    _insert_metric(db_path, AMZN_CIK, "gross_margin", "2025-01-01", "2025-03-31", 0.5)
    quarterly = data.get_statement_periods(AMZN_CIK, "quarterly", db_path)
    period = next(p for p in quarterly if p["period_end"] == "2025-03-31")
    assert period["fiscal_year"] == 2025
    assert period["fiscal_period"] == "Q1"


def test_get_statement_periods_fails_closed_when_no_filing_carries_a_fiscal_label(db_path):
    _insert_metric(db_path, AMZN_CIK, "gross_margin", "2025-01-01", "2025-03-31", 0.5)
    # No filings row at all for this period.
    quarterly = data.get_statement_periods(AMZN_CIK, "quarterly", db_path)
    period = next(p for p in quarterly if p["period_end"] == "2025-03-31")
    assert period["fiscal_year"] is None
    assert period["fiscal_period"] is None


def test_growth_pct_computes_period_over_period_change():
    assert data._growth_pct(100.0, 110.0) == pytest.approx(0.10)
    assert data._growth_pct(100.0, 90.0) == pytest.approx(-0.10)


def test_growth_pct_returns_none_for_zero_prior():
    assert data._growth_pct(0.0, 50.0) is None


# --- SPEC-008-batch-1 item 2 (D14, approved 2026-08-08): n/m classification ---


def test_classify_growth_flags_zero_base():
    growth_pct, reason = data._classify_growth(0.0, 50.0, typical_magnitude=1000.0)
    assert growth_pct is None  # division by zero -- no number to attach, same as before
    assert reason == "zero_base"


def test_classify_growth_flags_sign_crossing():
    growth_pct, reason = data._classify_growth(72.0, -113.0, typical_magnitude=1000.0)
    assert growth_pct is not None  # mathematically defined
    assert reason == "sign_crossing"


def test_classify_growth_does_not_flag_a_clean_move_to_exactly_zero():
    # value == 0 is a clean -100%, meaningful -- not the same as the sign
    # flipping, which is what makes a percentage change undefined in
    # direction.
    growth_pct, reason = data._classify_growth(100.0, 0.0, typical_magnitude=1000.0)
    assert growth_pct == pytest.approx(-1.0)
    assert reason is None


def test_classify_growth_flags_near_zero_base_reproducing_the_real_micron_example():
    # MU's real free cash flow: $72M base, typical (median) magnitude
    # $786M -- ratio 0.092, which the review's own example (+4097.2%) is
    # built from. A 5% threshold would miss this; the chosen 10% catches it.
    growth_pct, reason = data._classify_growth(72_000_000.0, 3_022_000_000.0, typical_magnitude=786_000_000.0)
    assert growth_pct == pytest.approx(40.97222222222222)
    assert reason == "near_zero_base"


def test_classify_growth_does_not_flag_a_base_comfortably_above_the_threshold():
    growth_pct, reason = data._classify_growth(200_000_000.0, 220_000_000.0, typical_magnitude=786_000_000.0)
    assert reason is None


def test_classify_growth_treats_missing_typical_magnitude_as_never_near_zero():
    # A single-value row (nothing to compute a median from) can still hit
    # zero-base/sign-crossing, but never the near-zero-base check, which
    # needs a real reference point.
    growth_pct, reason = data._classify_growth(1.0, 100.0, typical_magnitude=None)
    assert reason is None


def test_cash_flow_table_renders_not_meaningful_flag_for_a_near_zero_base(db_path):
    # Reproduces the review's own cited case end to end, through the real
    # table-building path (not just the classifier in isolation): a run of
    # capex_discrete values shaped so one quarter has a tiny base relative
    # to its own history. (Originally free_cash_flow -- moved to capex,
    # still in this statement, when free_cash_flow moved to the key-
    # metrics tab in SPEC-008-batch-2 item 1/2.)
    _insert_metric(db_path, AMZN_CIK, "gross_margin", "2025-01-01", "2025-03-31", 0.5)
    _insert_metric(db_path, AMZN_CIK, "gross_margin", "2025-04-01", "2025-06-30", 0.5)
    _insert_metric(db_path, AMZN_CIK, "gross_margin", "2025-07-01", "2025-09-30", 0.5)
    _insert_metric(db_path, AMZN_CIK, "capex_discrete", "2025-01-01", "2025-03-31", 800_000_000)
    _insert_metric(db_path, AMZN_CIK, "capex_discrete", "2025-04-01", "2025-06-30", 72_000_000)
    _insert_metric(db_path, AMZN_CIK, "capex_discrete", "2025-07-01", "2025-09-30", 3_022_000_000)
    periods = data.get_statement_periods(AMZN_CIK, "quarterly", db_path)
    rows = data.get_cash_flow_table(AMZN_CIK, periods, "AMZN", db_path)
    capex_row = next(r for r in rows if r["canonical"] == "capex")
    q3_cell = next(c for c in capex_row["cells"] if c["period_end"] == "2025-09-30")
    assert q3_cell["growth_not_meaningful"] == "near_zero_base"


# --- SPEC-008-batch-1 item 1 (D13, approved 2026-08-09): year-over-year growth ---


def test_year_ago_period_end_matches_same_fiscal_quarter_one_year_earlier():
    fiscal_labels = {
        "2025-03-31": (2025, "Q1"), "2025-06-30": (2025, "Q2"),
        "2026-03-31": (2026, "Q1"), "2026-06-30": (2026, "Q2"),
    }
    assert data._year_ago_period_end("2026-06-30", fiscal_labels) == "2025-06-30"
    assert data._year_ago_period_end("2026-03-31", fiscal_labels) == "2025-03-31"


def test_year_ago_period_end_fails_closed_when_the_prior_year_quarter_is_absent():
    fiscal_labels = {"2026-06-30": (2026, "Q2")}  # no 2025 Q2 at all
    assert data._year_ago_period_end("2026-06-30", fiscal_labels) is None


def test_year_ago_period_end_fails_closed_for_an_unlabelled_period():
    assert data._year_ago_period_end("2026-06-30", {}) is None


def test_year_ago_period_end_never_matches_a_different_fiscal_quarter():
    # A same-numbered fiscal_year that's the WRONG quarter must not match --
    # this is a same-QUARTER, year-ago lookup, not a same-year lookup.
    fiscal_labels = {"2026-06-30": (2026, "Q2"), "2025-03-31": (2025, "Q1")}
    assert data._year_ago_period_end("2026-06-30", fiscal_labels) is None


def test_fiscal_labels_reads_from_filings_table(db_path):
    _insert_filing(db_path, "acc-q2-2026", form_type="10-Q", period_end="2026-06-30", fiscal_year=2026, fiscal_period="Q2")
    _insert_filing(db_path, "acc-q2-2025", form_type="10-Q", period_end="2025-06-30", fiscal_year=2025, fiscal_period="Q2")
    labels = data._fiscal_labels(AMZN_CIK, db_path)
    assert labels["2026-06-30"] == (2026, "Q2")
    assert labels["2025-06-30"] == (2025, "Q2")


def test_income_statement_table_computes_yoy_growth_against_the_same_fiscal_quarter(db_path):
    _insert_filing(db_path, "acc-q2-2025", form_type="10-Q", period_end="2025-06-30", fiscal_year=2025, fiscal_period="Q2")
    _insert_filing(db_path, "acc-q3-2025", form_type="10-Q", period_end="2025-09-30", fiscal_year=2025, fiscal_period="Q3")
    _insert_filing(db_path, "acc-q2-2026", form_type="10-Q", period_end="2026-06-30", fiscal_year=2026, fiscal_period="Q2")
    _insert_metric(db_path, AMZN_CIK, "gross_margin", "2025-04-01", "2025-06-30", 0.5)
    _insert_metric(db_path, AMZN_CIK, "gross_margin", "2025-07-01", "2025-09-30", 0.5)
    _insert_metric(db_path, AMZN_CIK, "gross_margin", "2026-04-01", "2026-06-30", 0.5)
    _insert_xbrl_fact(
        db_path, AMZN_CIK, "RevenueFromContractWithCustomerExcludingAssessedTax", "2025-06-30",
        100_000_000, period_start="2025-04-01",
    )
    # The SEQUENTIAL prior quarter (Q3 2025) is deliberately a very
    # different number from the YEAR-AGO quarter (Q2 2025) -- if YoY ever
    # silently fell back to comparing against the sequential prior cell
    # instead of the true fiscal-year-ago one, this would catch it.
    _insert_xbrl_fact(
        db_path, AMZN_CIK, "RevenueFromContractWithCustomerExcludingAssessedTax", "2025-09-30",
        999_000_000, period_start="2025-07-01", accession_no="acc-fact-q3",
    )
    _insert_xbrl_fact(
        db_path, AMZN_CIK, "RevenueFromContractWithCustomerExcludingAssessedTax", "2026-06-30",
        150_000_000, period_start="2026-04-01", accession_no="acc-fact-q2-2026",
    )
    periods = data.get_statement_periods(AMZN_CIK, "quarterly", db_path)
    rows = data.get_income_statement_table(AMZN_CIK, periods, "AMZN", db_path)
    revenue_row = next(r for r in rows if r["canonical"] == "revenue")
    q2_2026_cell = next(c for c in revenue_row["cells"] if c["period_end"] == "2026-06-30")
    assert q2_2026_cell["yoy_growth_pct"] == pytest.approx(0.5)  # (150M - 100M) / 100M, vs Q2 2025


def test_income_statement_table_yoy_fails_closed_when_year_ago_quarter_missing(db_path):
    _insert_filing(db_path, "acc-q2-2026", form_type="10-Q", period_end="2026-06-30", fiscal_year=2026, fiscal_period="Q2")
    _insert_metric(db_path, AMZN_CIK, "gross_margin", "2026-04-01", "2026-06-30", 0.5)
    _insert_xbrl_fact(
        db_path, AMZN_CIK, "RevenueFromContractWithCustomerExcludingAssessedTax", "2026-06-30",
        150_000_000, period_start="2026-04-01",
    )
    periods = data.get_statement_periods(AMZN_CIK, "quarterly", db_path)
    rows = data.get_income_statement_table(AMZN_CIK, periods, "AMZN", db_path)
    revenue_row = next(r for r in rows if r["canonical"] == "revenue")
    q2_2026_cell = next(c for c in revenue_row["cells"] if c["period_end"] == "2026-06-30")
    assert q2_2026_cell["yoy_growth_pct"] is None


def test_income_statement_table_yoy_fails_closed_when_year_ago_value_itself_missing(db_path):
    # The year-ago fiscal quarter EXISTS (a filing was made) but its own
    # revenue value was never resolved -- still None, never a comparison
    # against a blank.
    _insert_filing(db_path, "acc-q2-2025", form_type="10-Q", period_end="2025-06-30", fiscal_year=2025, fiscal_period="Q2")
    _insert_filing(db_path, "acc-q2-2026", form_type="10-Q", period_end="2026-06-30", fiscal_year=2026, fiscal_period="Q2")
    _insert_metric(db_path, AMZN_CIK, "gross_margin", "2025-04-01", "2025-06-30", 0.5)
    _insert_metric(db_path, AMZN_CIK, "gross_margin", "2026-04-01", "2026-06-30", 0.5)
    _insert_xbrl_fact(
        db_path, AMZN_CIK, "RevenueFromContractWithCustomerExcludingAssessedTax", "2026-06-30",
        150_000_000, period_start="2026-04-01",
    )
    periods = data.get_statement_periods(AMZN_CIK, "quarterly", db_path)
    rows = data.get_income_statement_table(AMZN_CIK, periods, "AMZN", db_path)
    revenue_row = next(r for r in rows if r["canonical"] == "revenue")
    q2_2026_cell = next(c for c in revenue_row["cells"] if c["period_end"] == "2026-06-30")
    assert q2_2026_cell["yoy_growth_pct"] is None


def test_yoy_growth_gets_the_same_n_slash_m_treatment_as_sequential_growth(db_path):
    _insert_filing(db_path, "acc-q2-2025", form_type="10-Q", period_end="2025-06-30", fiscal_year=2025, fiscal_period="Q2")
    _insert_filing(db_path, "acc-q3-2025", form_type="10-Q", period_end="2025-09-30", fiscal_year=2025, fiscal_period="Q3")
    _insert_filing(db_path, "acc-q2-2026", form_type="10-Q", period_end="2026-06-30", fiscal_year=2026, fiscal_period="Q2")
    _insert_metric(db_path, AMZN_CIK, "gross_margin", "2025-04-01", "2025-06-30", 0.5)
    _insert_metric(db_path, AMZN_CIK, "gross_margin", "2025-07-01", "2025-09-30", 0.5)
    _insert_metric(db_path, AMZN_CIK, "gross_margin", "2026-04-01", "2026-06-30", 0.5)
    _insert_metric(db_path, AMZN_CIK, "capex_discrete", "2025-04-01", "2025-06-30", 800_000_000)
    _insert_metric(db_path, AMZN_CIK, "capex_discrete", "2025-07-01", "2025-09-30", 700_000_000)
    _insert_metric(db_path, AMZN_CIK, "capex_discrete", "2026-04-01", "2026-06-30", 72_000_000)
    periods = data.get_statement_periods(AMZN_CIK, "quarterly", db_path)
    rows = data.get_cash_flow_table(AMZN_CIK, periods, "AMZN", db_path)
    capex_row = next(r for r in rows if r["canonical"] == "capex")
    q2_2026_cell = next(c for c in capex_row["cells"] if c["period_end"] == "2026-06-30")
    # YoY base is 800M, comfortably above 10% of this row's own median --
    # NOT flagged. Confirms n/m is computed independently for YoY, not
    # just copied from the sequential flag (whose own base, 700M, is also
    # not near-zero here -- both should be clean, this isolates that YoY
    # uses ITS OWN base, the year-ago cell, not the sequential prior one).
    assert q2_2026_cell["yoy_growth_not_meaningful"] is None
    assert q2_2026_cell["yoy_growth_pct"] == pytest.approx((72_000_000 - 800_000_000) / 800_000_000)


def test_annual_basis_yoy_equals_sequential_by_construction(db_path):
    # D13's own constraint: on the annual basis, YoY and sequential are
    # numerically identical (a fiscal year's "same period, one year
    # earlier" IS its immediately preceding annual cell) -- not special-
    # cased in the data layer, just a natural consequence of the fiscal-
    # label lookup. Which one to SHOW is a display decision (out of scope
    # for this batch); this confirms the data layer doesn't need to care.
    _insert_filing(db_path, "acc-fy2024", form_type="10-K", period_end="2024-12-31", fiscal_year=2024, fiscal_period="FY")
    _insert_filing(db_path, "acc-fy2025", form_type="10-K", period_end="2025-12-31", fiscal_year=2025, fiscal_period="FY")
    _insert_metric(db_path, AMZN_CIK, "gross_margin", "2024-01-01", "2024-12-31", 0.5)
    _insert_metric(db_path, AMZN_CIK, "gross_margin", "2025-01-01", "2025-12-31", 0.5)
    _insert_xbrl_fact(
        db_path, AMZN_CIK, "RevenueFromContractWithCustomerExcludingAssessedTax", "2024-12-31",
        600_000_000, period_start="2024-01-01",
    )
    _insert_xbrl_fact(
        db_path, AMZN_CIK, "RevenueFromContractWithCustomerExcludingAssessedTax", "2025-12-31",
        700_000_000, period_start="2025-01-01", accession_no="acc-fact-2",
    )
    periods = data.get_statement_periods(AMZN_CIK, "annual", db_path)
    rows = data.get_income_statement_table(AMZN_CIK, periods, "AMZN", db_path)
    revenue_row = next(r for r in rows if r["canonical"] == "revenue")
    fy2025_cell = next(c for c in revenue_row["cells"] if c["period_end"] == "2025-12-31")
    assert fy2025_cell["yoy_growth_pct"] == pytest.approx(fy2025_cell["growth_pct"])


# --- SPEC-008-batch-1 item 4 (approved 2026-08-09): gross profit derived from components ---


def test_gross_profit_derived_from_revenue_minus_cogs_when_not_filed(db_path):
    _insert_metric(db_path, AMZN_CIK, "gross_margin", "2025-01-01", "2025-03-31", 0.5)
    _insert_xbrl_fact(
        db_path, AMZN_CIK, "RevenueFromContractWithCustomerExcludingAssessedTax", "2025-03-31",
        1_000_000_000, period_start="2025-01-01",
    )
    _insert_xbrl_fact(
        db_path, AMZN_CIK, "CostOfGoodsAndServicesSold", "2025-03-31",
        600_000_000, period_start="2025-01-01",
    )
    # No GrossProfit fact filed at all for this period.
    periods = data.get_statement_periods(AMZN_CIK, "quarterly", db_path)
    rows = data.get_income_statement_table(AMZN_CIK, periods, "AMZN", db_path)
    gp_row = next(r for r in rows if r["canonical"] == "gross_profit")
    cell = gp_row["cells"][0]
    assert cell["value"] == 400_000_000.0
    assert cell["is_derived_quarter"] is True
    assert "blank_cause" not in cell


def test_gross_profit_prefers_the_filed_figure_over_the_derived_one(db_path):
    _insert_metric(db_path, AMZN_CIK, "gross_margin", "2025-01-01", "2025-03-31", 0.5)
    _insert_xbrl_fact(
        db_path, AMZN_CIK, "RevenueFromContractWithCustomerExcludingAssessedTax", "2025-03-31",
        1_000_000_000, period_start="2025-01-01",
    )
    _insert_xbrl_fact(
        db_path, AMZN_CIK, "CostOfGoodsAndServicesSold", "2025-03-31",
        600_000_000, period_start="2025-01-01",
    )
    _insert_xbrl_fact(db_path, AMZN_CIK, "GrossProfit", "2025-03-31", 450_000_000, period_start="2025-01-01")
    periods = data.get_statement_periods(AMZN_CIK, "quarterly", db_path)
    rows = data.get_income_statement_table(AMZN_CIK, periods, "AMZN", db_path)
    gp_row = next(r for r in rows if r["canonical"] == "gross_profit")
    cell = gp_row["cells"][0]
    assert cell["value"] == 450_000_000.0  # the FILED figure, not 400M implied by revenue-cogs
    assert cell.get("is_derived_quarter") is not True


def test_gross_profit_stays_blank_when_revenue_or_cogs_also_missing(db_path):
    _insert_metric(db_path, AMZN_CIK, "gross_margin", "2025-01-01", "2025-03-31", 0.5)
    _insert_xbrl_fact(
        db_path, AMZN_CIK, "RevenueFromContractWithCustomerExcludingAssessedTax", "2025-03-31",
        1_000_000_000, period_start="2025-01-01",
    )
    # No cogs at all -- can't derive.
    periods = data.get_statement_periods(AMZN_CIK, "quarterly", db_path)
    rows = data.get_income_statement_table(AMZN_CIK, periods, "AMZN", db_path)
    gp_row = next(r for r in rows if r["canonical"] == "gross_profit")
    cell = gp_row["cells"][0]
    assert cell["value"] is None
    assert cell["blank_cause"] == "gap"


def test_gross_profit_derived_cells_get_growth_and_yoy_like_any_other_cell(db_path):
    _insert_filing(db_path, "acc-q1-2025", form_type="10-Q", period_end="2025-03-31", fiscal_year=2025, fiscal_period="Q1")
    _insert_filing(db_path, "acc-q1-2026", form_type="10-Q", period_end="2026-03-31", fiscal_year=2026, fiscal_period="Q1")
    _insert_metric(db_path, AMZN_CIK, "gross_margin", "2025-01-01", "2025-03-31", 0.5)
    _insert_metric(db_path, AMZN_CIK, "gross_margin", "2026-01-01", "2026-03-31", 0.5)
    _insert_xbrl_fact(
        db_path, AMZN_CIK, "RevenueFromContractWithCustomerExcludingAssessedTax", "2025-03-31",
        1_000_000_000, period_start="2025-01-01",
    )
    _insert_xbrl_fact(
        db_path, AMZN_CIK, "CostOfGoodsAndServicesSold", "2025-03-31", 600_000_000, period_start="2025-01-01",
    )
    _insert_xbrl_fact(
        db_path, AMZN_CIK, "RevenueFromContractWithCustomerExcludingAssessedTax", "2026-03-31",
        1_200_000_000, period_start="2026-01-01", accession_no="acc-fact-rev-2",
    )
    _insert_xbrl_fact(
        db_path, AMZN_CIK, "CostOfGoodsAndServicesSold", "2026-03-31", 600_000_000, period_start="2026-01-01",
        accession_no="acc-fact-cogs-2",
    )
    periods = data.get_statement_periods(AMZN_CIK, "quarterly", db_path)
    rows = data.get_income_statement_table(AMZN_CIK, periods, "AMZN", db_path)
    gp_row = next(r for r in rows if r["canonical"] == "gross_profit")
    fy2026_cell = next(c for c in gp_row["cells"] if c["period_end"] == "2026-03-31")
    # 2025: 400M derived. 2026: 600M derived. YoY = (600-400)/400 = 0.5.
    assert fy2026_cell["yoy_growth_pct"] == pytest.approx(0.5)


def test_income_statement_table_computes_growth_between_adjacent_periods(db_path):
    _insert_metric(db_path, AMZN_CIK, "gross_margin", "2025-01-01", "2025-03-31", 0.5)
    _insert_metric(db_path, AMZN_CIK, "gross_margin", "2025-04-01", "2025-06-30", 0.5)
    _insert_xbrl_fact(
        db_path, AMZN_CIK, "RevenueFromContractWithCustomerExcludingAssessedTax", "2025-03-31",
        100_000_000, period_start="2025-01-01",
    )
    _insert_xbrl_fact(
        db_path, AMZN_CIK, "RevenueFromContractWithCustomerExcludingAssessedTax", "2025-06-30",
        150_000_000, period_start="2025-04-01", accession_no="acc-fact-2",
    )
    periods = data.get_statement_periods(AMZN_CIK, "quarterly", db_path)
    rows = data.get_income_statement_table(AMZN_CIK, periods, "AMZN", db_path)
    revenue_row = next(r for r in rows if r["canonical"] == "revenue")
    assert revenue_row["cells"][0]["growth_pct"] is None  # no prior cell to compare against
    assert revenue_row["cells"][1]["growth_pct"] == pytest.approx(0.5)


def test_income_statement_table_derives_q4_when_only_the_fy_figure_is_filed(db_path):
    # SPEC-008 D12 (approved 2026-08-08): the income statement gets the
    # SAME merge-fallback treatment cash flow already has -- Q4 has no
    # discrete filed fact anywhere (only the FY 10-K's annual figure), so
    # revenue_discrete (metrics.compute_discrete_quarter_metrics's own
    # output, not exercised here) supplies the derived quarter.
    _insert_metric(db_path, AMZN_CIK, "gross_margin", "2025-07-01", "2025-09-30", 0.5)
    _insert_metric(db_path, AMZN_CIK, "gross_margin", "2025-10-01", "2025-12-31", 0.5)
    _insert_xbrl_fact(
        db_path, AMZN_CIK, "RevenueFromContractWithCustomerExcludingAssessedTax", "2025-09-30",
        360_000_000, period_start="2025-07-01",
    )
    # No direct Q4 fact at all -- only the derived discrete metric exists,
    # as metrics.py would have already computed and stored it.
    _insert_metric(db_path, AMZN_CIK, "revenue_discrete", "2025-10-01", "2025-12-31", 140_000_000)
    periods = data.get_statement_periods(AMZN_CIK, "quarterly", db_path)
    rows = data.get_income_statement_table(AMZN_CIK, periods, "AMZN", db_path)
    revenue_row = next(r for r in rows if r["canonical"] == "revenue")
    q4_cell = next(c for c in revenue_row["cells"] if c["period_end"] == "2025-12-31")
    assert q4_cell["value"] == 140_000_000
    assert q4_cell["is_derived_quarter"] is True


def test_income_statement_table_q4_blank_when_no_discrete_metric_computed_yet(db_path):
    # Same setup, but revenue_discrete hasn't been computed -- a genuine
    # blank (gap), never a silently wrong or estimated figure.
    _insert_metric(db_path, AMZN_CIK, "gross_margin", "2025-07-01", "2025-09-30", 0.5)
    _insert_metric(db_path, AMZN_CIK, "gross_margin", "2025-10-01", "2025-12-31", 0.5)
    _insert_xbrl_fact(
        db_path, AMZN_CIK, "RevenueFromContractWithCustomerExcludingAssessedTax", "2025-09-30",
        360_000_000, period_start="2025-07-01",
    )
    periods = data.get_statement_periods(AMZN_CIK, "quarterly", db_path)
    rows = data.get_income_statement_table(AMZN_CIK, periods, "AMZN", db_path)
    revenue_row = next(r for r in rows if r["canonical"] == "revenue")
    q4_cell = next(c for c in revenue_row["cells"] if c["period_end"] == "2025-12-31")
    assert q4_cell["value"] is None
    assert q4_cell["blank_cause"] == "gap"


def test_balance_sheet_table_never_marks_a_cell_derived(db_path):
    # D12: BALANCE_SHEET_LINES declares no fallback_canonical for any line
    # other than PP&E's genuinely-different-concept split (never "derived"
    # -- a different FILED concept, not a subtraction), so nothing here
    # inherits the income-statement/cash-flow Q4=FY-9M treatment. Item 7
    # (approved 2026-08-11) adds a SEPARATE derivation path -- total_
    # liabilities = total_assets - equity, arithmetic across LINES within
    # one period, not across time -- but this fixture supplies neither
    # input, so total_liabilities stays blank too; see
    # test_total_liabilities_derived_from_assets_minus_equity below for
    # that path exercised directly.
    _insert_metric(db_path, AMZN_CIK, "gross_margin", "2025-01-01", "2025-03-31", 0.5)
    _insert_xbrl_fact(db_path, AMZN_CIK, "CashAndCashEquivalentsAtCarryingValue", "2025-03-31", 1_000_000)
    periods = data.get_statement_periods(AMZN_CIK, "quarterly", db_path)
    rows = data.get_balance_sheet_table(AMZN_CIK, periods, "AMZN", db_path)
    for row in rows:
        for cell in row["cells"]:
            assert "is_derived_quarter" not in cell


# --- SPEC-008-batch-1 item 5 (approved 2026-08-09): three-section cash flow statement ---


def test_cash_flow_lines_includes_all_three_new_sections():
    canonicals = {line[0] for line in data.CASH_FLOW_LINES}
    operating = {"receivables_change", "inventory_change", "payables_change", "deferred_tax", "other_noncash"}
    investing = {"acquisitions", "investment_purchases", "investment_maturities"}
    financing = {"buybacks", "dividends_paid", "debt_issued", "debt_repaid", "finance_lease_principal_paid"}
    reconciliation = {"fx_effect_on_cash", "net_change_in_cash"}
    assert operating <= canonicals
    assert investing <= canonicals
    assert financing <= canonicals
    assert reconciliation <= canonicals


def test_cash_flow_lines_new_lines_all_declare_the_merge_fallback():
    # Same discipline as every existing cash-flow line (SPEC-008 C4 item 3):
    # fallback_label == label, one row, filed-or-derived -- never a second
    # resolution pattern invented for the new lines.
    new_canonicals = {
        "receivables_change", "inventory_change", "payables_change", "deferred_tax", "other_noncash",
        "acquisitions", "investment_purchases", "investment_maturities",
        "buybacks", "dividends_paid", "debt_issued", "debt_repaid", "finance_lease_principal_paid",
        "fx_effect_on_cash", "net_change_in_cash",
    }
    for canonical, label, fallback_canonical, fallback_label in data.CASH_FLOW_LINES:
        if canonical not in new_canonicals:
            continue
        assert fallback_canonical == f"{canonical}_discrete"
        assert fallback_label == label


def test_cash_flow_table_resolves_a_new_line_filed_directly(db_path):
    _insert_metric(db_path, AMZN_CIK, "gross_margin", "2025-01-01", "2025-03-31", 0.5)
    _insert_xbrl_fact(
        db_path, AMZN_CIK, "PaymentsForRepurchaseOfCommonStock", "2025-03-31", 250_000_000, period_start="2025-01-01",
    )
    periods = data.get_statement_periods(AMZN_CIK, "quarterly", db_path)
    rows = data.get_cash_flow_table(AMZN_CIK, periods, "AMZN", db_path)
    buybacks_row = next(r for r in rows if r["canonical"] == "buybacks")
    assert buybacks_row["label"] == "Share repurchases"
    assert buybacks_row["cells"][0]["value"] == 250_000_000.0
    assert buybacks_row["cells"][0].get("is_derived_quarter") is not True


def test_cash_flow_table_derives_a_new_line_when_only_discrete_metric_exists(db_path):
    _insert_metric(db_path, AMZN_CIK, "gross_margin", "2025-01-01", "2025-03-31", 0.5)
    _insert_metric(db_path, AMZN_CIK, "buybacks_discrete", "2025-01-01", "2025-03-31", 60_000_000)
    periods = data.get_statement_periods(AMZN_CIK, "quarterly", db_path)
    rows = data.get_cash_flow_table(AMZN_CIK, periods, "AMZN", db_path)
    buybacks_row = next(r for r in rows if r["canonical"] == "buybacks")
    assert buybacks_row["cells"][0]["value"] == 60_000_000.0
    assert buybacks_row["cells"][0]["is_derived_quarter"] is True


def test_cash_flow_table_blank_when_not_tagged_discretely_and_no_discrete_metric_computed(db_path):
    # Period 1: cfo tagged directly at the true quarterly duration. Period
    # 2: cfo only tagged year-to-date, and no cfo_discrete metric row
    # exists yet (metrics.compute_discrete_quarter_metrics hasn't run in
    # this fixture) -- SPEC-008 C4 (approved 2026-08-08): the table no
    # longer substitutes the wrong-duration filed figure the way it used
    # to (the old is_duration_fallback/† mechanism); a genuine blank
    # instead, never a mismatched-duration number.
    _insert_metric(db_path, AMZN_CIK, "gross_margin", "2025-01-01", "2025-03-31", 0.5)
    _insert_metric(db_path, AMZN_CIK, "gross_margin", "2025-04-01", "2025-06-30", 0.5)
    _insert_xbrl_fact(
        db_path, AMZN_CIK, "NetCashProvidedByUsedInOperatingActivities", "2025-03-31",
        10_000_000, period_start="2025-01-01",
    )
    _insert_xbrl_fact(
        db_path, AMZN_CIK, "NetCashProvidedByUsedInOperatingActivities", "2025-06-30",
        25_000_000, period_start="2025-01-01", accession_no="acc-fact-2",  # YTD, not quarterly, for this period_end
    )
    periods = data.get_statement_periods(AMZN_CIK, "quarterly", db_path)
    rows = data.get_cash_flow_table(AMZN_CIK, periods, "AMZN", db_path)
    cfo_row = next(r for r in rows if r["canonical"] == "cfo")
    assert cfo_row["cells"][1]["value"] is None
    assert cfo_row["cells"][1]["blank_cause"] == "gap"
    assert cfo_row["cells"][1]["growth_pct"] is None


def test_cash_flow_table_uses_the_derived_discrete_metric_when_it_exists(db_path):
    # Same setup as above, but cfo_discrete (metrics.
    # compute_discrete_quarter_metrics's own output) has already been
    # computed and stored at the true quarterly period -- the table shows
    # it, marks the cell derived, and growth% now populates since both
    # cells are genuinely the same (three-month) duration.
    _insert_metric(db_path, AMZN_CIK, "gross_margin", "2025-01-01", "2025-03-31", 0.5)
    _insert_metric(db_path, AMZN_CIK, "gross_margin", "2025-04-01", "2025-06-30", 0.5)
    _insert_xbrl_fact(
        db_path, AMZN_CIK, "NetCashProvidedByUsedInOperatingActivities", "2025-03-31",
        10_000_000, period_start="2025-01-01",
    )
    _insert_metric(db_path, AMZN_CIK, "cfo_discrete", "2025-04-01", "2025-06-30", 15_000_000)
    periods = data.get_statement_periods(AMZN_CIK, "quarterly", db_path)
    rows = data.get_cash_flow_table(AMZN_CIK, periods, "AMZN", db_path)
    cfo_row = next(r for r in rows if r["canonical"] == "cfo")
    assert cfo_row["cells"][1]["value"] == 15_000_000
    assert cfo_row["cells"][1]["is_derived_quarter"] is True
    assert cfo_row["cells"][1]["growth_pct"] == pytest.approx(0.5)


def test_cash_flow_table_computes_growth_between_two_true_quarterly_cells(db_path):
    _insert_metric(db_path, AMZN_CIK, "gross_margin", "2025-01-01", "2025-03-31", 0.5)
    _insert_metric(db_path, AMZN_CIK, "gross_margin", "2025-04-01", "2025-06-30", 0.5)
    _insert_xbrl_fact(
        db_path, AMZN_CIK, "NetCashProvidedByUsedInOperatingActivities", "2025-03-31",
        10_000_000, period_start="2025-01-01",
    )
    _insert_xbrl_fact(
        db_path, AMZN_CIK, "NetCashProvidedByUsedInOperatingActivities", "2025-06-30",
        15_000_000, period_start="2025-04-01", accession_no="acc-fact-2",  # its OWN quarterly period_start
    )
    periods = data.get_statement_periods(AMZN_CIK, "quarterly", db_path)
    rows = data.get_cash_flow_table(AMZN_CIK, periods, "AMZN", db_path)
    cfo_row = next(r for r in rows if r["canonical"] == "cfo")
    assert cfo_row["cells"][1]["is_derived_quarter"] is False
    assert cfo_row["cells"][1]["growth_pct"] == pytest.approx(0.5)


def test_balance_sheet_table_splits_ppe_row_when_fallback_used_in_at_least_one_column(db_path):
    # MU's real pattern: pure ppe_net early, only the combined ROU-inclusive
    # tag from some point on.
    _insert_metric(db_path, AMZN_CIK, "gross_margin", "2020-01-01", "2020-03-31", 0.5)
    _insert_metric(db_path, AMZN_CIK, "gross_margin", "2021-01-01", "2021-03-31", 0.5)
    _insert_xbrl_fact(db_path, AMZN_CIK, "PropertyPlantAndEquipmentNet", "2020-03-31", 50_000_000_000)
    _insert_xbrl_fact(
        db_path, AMZN_CIK,
        "PropertyPlantAndEquipmentAndFinanceLeaseRightOfUseAssetAfterAccumulatedDepreciationAndAmortization",
        "2021-03-31", 80_000_000_000,
    )
    periods = data.get_statement_periods(AMZN_CIK, "quarterly", db_path)
    rows = data.get_balance_sheet_table(AMZN_CIK, periods, "AMZN", db_path)
    ppe_rows = [r for r in rows if r["canonical"] == "ppe_net"]
    assert len(ppe_rows) == 2
    assert ppe_rows[0]["label"] == "PP&E, net"
    assert ppe_rows[0]["cells"][0]["value"] == 50_000_000_000
    assert ppe_rows[0]["cells"][1]["value"] is None
    assert ppe_rows[1]["label"] == "PP&E & finance-lease ROU assets, net"
    assert ppe_rows[1]["cells"][0]["value"] is None
    assert ppe_rows[1]["cells"][1]["value"] == 80_000_000_000


def test_balance_sheet_table_single_row_when_fallback_never_used(db_path):
    _insert_metric(db_path, AMZN_CIK, "gross_margin", "2020-01-01", "2020-03-31", 0.5)
    _insert_xbrl_fact(db_path, AMZN_CIK, "PropertyPlantAndEquipmentNet", "2020-03-31", 50_000_000_000)
    periods = data.get_statement_periods(AMZN_CIK, "quarterly", db_path)
    rows = data.get_balance_sheet_table(AMZN_CIK, periods, "AMZN", db_path)
    ppe_rows = [r for r in rows if r["canonical"] == "ppe_net"]
    assert len(ppe_rows) == 1
    assert ppe_rows[0]["label"] == "PP&E, net"


def test_balance_sheet_table_omits_the_primary_row_when_only_the_fallback_is_ever_used(db_path):
    # AMZN's real pattern, generalised: if the primary canonical NEVER
    # resolves anywhere in the displayed range, showing its row anyway
    # would be a permanently empty "Property, plant and equipment, net"
    # row on every table -- SPEC-008 C4 constraint 3.
    _insert_metric(db_path, AMZN_CIK, "gross_margin", "2020-01-01", "2020-03-31", 0.5)
    _insert_metric(db_path, AMZN_CIK, "gross_margin", "2021-01-01", "2021-03-31", 0.5)
    _insert_xbrl_fact(
        db_path, AMZN_CIK,
        "PropertyPlantAndEquipmentAndFinanceLeaseRightOfUseAssetAfterAccumulatedDepreciationAndAmortization",
        "2020-03-31", 70_000_000_000,
    )
    _insert_xbrl_fact(
        db_path, AMZN_CIK,
        "PropertyPlantAndEquipmentAndFinanceLeaseRightOfUseAssetAfterAccumulatedDepreciationAndAmortization",
        "2021-03-31", 80_000_000_000, accession_no="acc-fact-2",
    )
    periods = data.get_statement_periods(AMZN_CIK, "quarterly", db_path)
    rows = data.get_balance_sheet_table(AMZN_CIK, periods, "AMZN", db_path)
    ppe_rows = [r for r in rows if r["canonical"] == "ppe_net"]
    assert len(ppe_rows) == 1
    assert ppe_rows[0]["label"] == "PP&E & finance-lease ROU assets, net"


def test_cash_flow_lines_follow_up_shorthand_labels():
    # SPEC-008-batch-4 follow-up item 1 (approved 2026-08-18): "Acquisitions,
    # net" and "Maturities/sales of investments" -- the latter reused
    # verbatim from config.py's own investment_maturities_discrete
    # MetricDef.display_name (SPEC-006), not invented a second time here.
    by_canonical = {line[0]: line for line in data.CASH_FLOW_LINES}
    assert by_canonical["acquisitions"][1] == "Acquisitions, net"
    assert by_canonical["acquisitions"][3] == "Acquisitions, net"
    assert by_canonical["investment_maturities"][1] == "Maturities/sales of investments"
    assert by_canonical["investment_maturities"][3] == "Maturities/sales of investments"
    assert (
        by_canonical["investment_maturities"][1]
        == config.METRIC_REGISTRY["investment_maturities_discrete"].display_name.removesuffix(" (discrete quarter)")
    )


def test_statement_table_row_with_no_data_at_all_still_shows_a_single_blank_row(db_path):
    _insert_metric(db_path, AMZN_CIK, "gross_margin", "2020-01-01", "2020-03-31", 0.5)
    periods = data.get_statement_periods(AMZN_CIK, "quarterly", db_path)
    rows = data.get_balance_sheet_table(AMZN_CIK, periods, "AMZN", db_path)
    ppe_rows = [r for r in rows if r["canonical"] == "ppe_net"]
    assert len(ppe_rows) == 1
    assert ppe_rows[0]["cells"][0]["value"] is None


# --- SPEC-008 C4 constraint 4: a blank cell states which of three things it means ---


def test_blank_cell_cause_is_split_when_the_paired_row_carries_this_period(db_path):
    # The exact fixture from test_balance_sheet_table_splits_ppe_row_...:
    # MU's real pattern, pure ppe_net early, only the combined ROU-inclusive
    # tag from some point on -- each row's blank half is the OTHER row's
    # populated half, not a genuine gap.
    _insert_metric(db_path, AMZN_CIK, "gross_margin", "2020-01-01", "2020-03-31", 0.5)
    _insert_metric(db_path, AMZN_CIK, "gross_margin", "2021-01-01", "2021-03-31", 0.5)
    _insert_xbrl_fact(db_path, AMZN_CIK, "PropertyPlantAndEquipmentNet", "2020-03-31", 50_000_000_000)
    _insert_xbrl_fact(
        db_path, AMZN_CIK,
        "PropertyPlantAndEquipmentAndFinanceLeaseRightOfUseAssetAfterAccumulatedDepreciationAndAmortization",
        "2021-03-31", 80_000_000_000,
    )
    periods = data.get_statement_periods(AMZN_CIK, "quarterly", db_path)
    rows = data.get_balance_sheet_table(AMZN_CIK, periods, "AMZN", db_path)
    ppe_rows = [r for r in rows if r["canonical"] == "ppe_net"]
    primary_row = ppe_rows[0]
    fallback_row = ppe_rows[1]
    assert primary_row["cells"][1]["blank_cause"] == "split"
    assert "finance-lease ROU assets" in primary_row["cells"][1]["blank_reason"]
    assert fallback_row["cells"][0]["blank_cause"] == "split"
    assert "PP&E, net" in fallback_row["cells"][0]["blank_reason"]


def test_blank_cell_cause_is_gap_when_the_concept_is_tagged_elsewhere_but_not_this_period(db_path):
    # A genuine period-specific gap, not a split-row pairing (no fallback
    # concept ever used here) and not structural absence (the concept IS
    # tagged, just in the other period).
    _insert_metric(db_path, AMZN_CIK, "gross_margin", "2020-01-01", "2020-03-31", 0.5)
    _insert_metric(db_path, AMZN_CIK, "gross_margin", "2021-01-01", "2021-03-31", 0.5)
    _insert_xbrl_fact(db_path, AMZN_CIK, "PropertyPlantAndEquipmentNet", "2021-03-31", 50_000_000_000)
    periods = data.get_statement_periods(AMZN_CIK, "quarterly", db_path)
    rows = data.get_balance_sheet_table(AMZN_CIK, periods, "AMZN", db_path)
    ppe_row = next(r for r in rows if r["canonical"] == "ppe_net")
    assert ppe_row["cells"][0]["value"] is None
    assert ppe_row["cells"][0]["blank_cause"] == "gap"
    assert ppe_row["cells"][0]["blank_reason"] == "not tagged for this period"
    assert ppe_row["cells"][1]["value"] == 50_000_000_000


# --- SPEC-008-batch-1 render-batch follow-up (approved 2026-08-11) ---
# Found live, browser-checked against batch-1's shipped data layer:
# (1) EPS/shares built but never wired into a display list -- see
#     dashboard/pages/financials.py and dashboard/components.py instead,
#     this module's own rows were already correct.
# (2) free_cash_flow moved to the very end of CASH_FLOW_LINES by item 5's
#     rewrite -- a regression on the most-read line on the page. (Batch-2
#     item 1 supersedes this again -- free_cash_flow leaves the cash flow
#     statement entirely, see the batch-2 block below.)
# (3) no closing figure for the investing or financing sections.


def test_cash_flow_lines_includes_the_two_new_section_subtotals():
    canonicals = {line[0] for line in data.CASH_FLOW_LINES}
    assert "net_cash_investing" in canonicals
    assert "net_cash_financing" in canonicals


# --- SPEC-008-batch-2 item 1 (approved 2026-08-13): traditional statement order ---


def test_cash_flow_lines_free_cash_flow_removed_it_now_lives_on_key_metrics(db_path):
    canonicals = {line[0] for line in data.CASH_FLOW_LINES}
    assert "free_cash_flow" not in canonicals


def test_cash_flow_lines_operating_section_ends_with_its_own_subtotal():
    # Traditional order: net_income opens the operating section, cfo (its
    # subtotal) closes it -- the reverse of SPEC-008 C4's original layout.
    canonicals = [line[0] for line in data.CASH_FLOW_LINES]
    assert canonicals[0] == "net_income"
    operating_inputs_last = canonicals.index("payables_change")
    cfo_idx = canonicals.index("cfo")
    investing_first = canonicals.index("capex")
    assert operating_inputs_last < cfo_idx < investing_first


def test_cash_flow_lines_investing_and_financing_sections_end_with_their_own_subtotal():
    canonicals = [line[0] for line in data.CASH_FLOW_LINES]
    assert canonicals.index("investment_maturities") < canonicals.index("net_cash_investing") < canonicals.index("buybacks")
    assert (
        canonicals.index("finance_lease_principal_paid") < canonicals.index("net_cash_financing")
        < canonicals.index("fx_effect_on_cash")
    )


def test_cash_flow_lines_ends_with_the_reconciliation_including_beginning_and_ending_cash():
    canonicals = [line[0] for line in data.CASH_FLOW_LINES]
    assert canonicals[-4:] == [
        "fx_effect_on_cash", "net_change_in_cash", "cash_beginning", "cash_and_restricted_cash",
    ]


def test_cash_flow_table_resolves_net_cash_investing_filed_directly(db_path):
    _insert_metric(db_path, AMZN_CIK, "gross_margin", "2025-01-01", "2025-03-31", 0.5)
    _insert_xbrl_fact(
        db_path, AMZN_CIK, "NetCashProvidedByUsedInInvestingActivities", "2025-03-31",
        -12_000_000_000, period_start="2025-01-01",
    )
    periods = data.get_statement_periods(AMZN_CIK, "quarterly", db_path)
    rows = data.get_cash_flow_table(AMZN_CIK, periods, "AMZN", db_path)
    row = next(r for r in rows if r["canonical"] == "net_cash_investing")
    assert row["label"] == "Net cash used in investing"  # SPEC-008-batch-4 item 1: shortened
    assert row["cells"][0]["value"] == -12_000_000_000.0
    assert row["cells"][0].get("is_derived_quarter") is not True


def test_cash_flow_table_resolves_net_cash_financing_via_discrete_fallback(db_path):
    _insert_metric(db_path, AMZN_CIK, "gross_margin", "2025-01-01", "2025-03-31", 0.5)
    _insert_metric(db_path, AMZN_CIK, "net_cash_financing_discrete", "2025-01-01", "2025-03-31", -3_000_000_000)
    periods = data.get_statement_periods(AMZN_CIK, "quarterly", db_path)
    rows = data.get_cash_flow_table(AMZN_CIK, periods, "AMZN", db_path)
    row = next(r for r in rows if r["canonical"] == "net_cash_financing")
    assert row["label"] == "Net cash from financing activities"  # SPEC-008-batch-4 item 1: shortened
    assert row["cells"][0]["value"] == -3_000_000_000.0
    assert row["cells"][0]["is_derived_quarter"] is True


def test_cash_flow_table_cfo_now_carries_the_operating_subtotal_label(db_path):
    _insert_metric(db_path, AMZN_CIK, "gross_margin", "2025-01-01", "2025-03-31", 0.5)
    _insert_xbrl_fact(
        db_path, AMZN_CIK, "NetCashProvidedByUsedInOperatingActivities", "2025-03-31",
        10_000_000, period_start="2025-01-01",
    )
    periods = data.get_statement_periods(AMZN_CIK, "quarterly", db_path)
    rows = data.get_cash_flow_table(AMZN_CIK, periods, "AMZN", db_path)
    cfo_row = next(r for r in rows if r["canonical"] == "cfo")
    assert cfo_row["label"] == "Net cash from operating activities"  # SPEC-008-batch-4 item 1: shortened


def test_cash_flow_table_net_income_resolves_via_the_income_statements_own_canonical(db_path):
    _insert_metric(db_path, AMZN_CIK, "gross_margin", "2025-01-01", "2025-03-31", 0.5)
    _insert_xbrl_fact(db_path, AMZN_CIK, "NetIncomeLoss", "2025-03-31", 7_000_000, period_start="2025-01-01")
    periods = data.get_statement_periods(AMZN_CIK, "quarterly", db_path)
    rows = data.get_cash_flow_table(AMZN_CIK, periods, "AMZN", db_path)
    row = next(r for r in rows if r["canonical"] == "net_income")
    assert row["label"] == "Net income"
    assert row["cells"][0]["value"] == 7_000_000.0


def test_cash_flow_table_cash_at_end_of_period_uses_the_broad_restricted_cash_concept(db_path):
    # SPEC-008-batch-2 cash-reconciliation follow-up (approved 2026-08-13,
    # found live): this must NOT be the balance sheet's narrower `cash`
    # (CashAndCashEquivalentsAtCarryingValue) -- that was the original
    # bug. The cash flow statement's own reconciliation uses the broader
    # post-ASU-2016-18 concept, same one net_change_in_cash already uses.
    _insert_metric(db_path, AMZN_CIK, "gross_margin", "2025-01-01", "2025-03-31", 0.5)
    _insert_xbrl_fact(
        db_path, AMZN_CIK, "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents", "2025-03-31", 9_000_000,
    )
    # A DIFFERENT value under the narrow balance-sheet concept, at the
    # SAME date -- proves the row reads the broad concept, not this one.
    _insert_xbrl_fact(db_path, AMZN_CIK, "CashAndCashEquivalentsAtCarryingValue", "2025-03-31", 7_500_000)
    periods = data.get_statement_periods(AMZN_CIK, "quarterly", db_path)
    rows = data.get_cash_flow_table(AMZN_CIK, periods, "AMZN", db_path)
    ending_rows = [r for r in rows if r["canonical"] == "cash_and_restricted_cash"]
    assert len(ending_rows) == 1
    assert ending_rows[0]["label"] == "Cash at end of period"
    assert ending_rows[0]["cells"][0]["value"] == 9_000_000.0
    assert ending_rows[0]["cells"][0].get("is_derived_quarter") is not True


def test_cash_flow_table_cash_at_beginning_of_period_resolves_one_day_before_period_start(db_path):
    # Confirmed against the real corpus: a duration fact's period_start is
    # one calendar day after the prior instant's own date -- Q2 2025
    # (start=2025-04-01) reads the cash_and_restricted_cash instant filed
    # at 2025-03-31.
    _insert_metric(db_path, AMZN_CIK, "gross_margin", "2025-04-01", "2025-06-30", 0.5)
    _insert_xbrl_fact(
        db_path, AMZN_CIK, "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents", "2025-03-31", 6_000_000,
    )
    periods = data.get_statement_periods(AMZN_CIK, "quarterly", db_path)
    rows = data.get_cash_flow_table(AMZN_CIK, periods, "AMZN", db_path)
    row = next(r for r in rows if r["canonical"] == "cash_beginning")
    assert row["label"] == "Cash at beginning of period"
    assert row["cells"][0]["value"] == 6_000_000.0
    # A real filed number at an adjacent instant, not an arithmetic
    # derivation -- must NOT carry the derived-quarter marker.
    assert row["cells"][0].get("is_derived_quarter") is not True


def test_cash_flow_table_cash_at_beginning_of_period_stays_blank_when_the_prior_instant_is_missing(db_path):
    _insert_metric(db_path, AMZN_CIK, "gross_margin", "2025-04-01", "2025-06-30", 0.5)
    # No cash_and_restricted_cash fact at all -- neither at 2025-03-31 nor
    # anywhere else.
    periods = data.get_statement_periods(AMZN_CIK, "quarterly", db_path)
    rows = data.get_cash_flow_table(AMZN_CIK, periods, "AMZN", db_path)
    row = next(r for r in rows if r["canonical"] == "cash_beginning")
    assert row["cells"][0]["value"] is None
    assert row["cells"][0]["blank_cause"] == "gap"


def test_cash_flow_table_cash_beginning_and_ending_get_growth_and_yoy_like_any_other_cell(db_path):
    _insert_filing(db_path, "acc-q1-2025", form_type="10-Q", period_end="2025-03-31", fiscal_year=2025, fiscal_period="Q1")
    _insert_filing(db_path, "acc-q2-2025", form_type="10-Q", period_end="2025-06-30", fiscal_year=2025, fiscal_period="Q2")
    _insert_metric(db_path, AMZN_CIK, "gross_margin", "2025-01-01", "2025-03-31", 0.5)
    _insert_metric(db_path, AMZN_CIK, "gross_margin", "2025-04-01", "2025-06-30", 0.5)
    _insert_xbrl_fact(
        db_path, AMZN_CIK, "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents", "2025-03-31", 100_000_000,
    )
    _insert_xbrl_fact(
        db_path, AMZN_CIK, "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents", "2025-06-30", 150_000_000,
        accession_no="acc-fact-2",
    )
    periods = data.get_statement_periods(AMZN_CIK, "quarterly", db_path)
    rows = data.get_cash_flow_table(AMZN_CIK, periods, "AMZN", db_path)
    ending_row = next(r for r in rows if r["canonical"] == "cash_and_restricted_cash")
    assert ending_row["cells"][1]["growth_pct"] == pytest.approx(0.5)


def test_cash_flow_table_beginning_plus_net_change_equals_ending_on_real_shaped_data(db_path):
    # SPEC-008-batch-2 cash-reconciliation follow-up: the whole point of
    # switching concepts -- confirms the three reconciliation rows
    # actually agree with each other now, reproducing AMZN's real Q1 2026
    # shape (90,106 -> 80,927 via a net change including the FX effect,
    # not a separate addition -- see the validate rule for the corpus-wide
    # version of this same check).
    _insert_metric(db_path, AMZN_CIK, "gross_margin", "2025-01-01", "2025-03-31", 0.5)
    _insert_xbrl_fact(
        db_path, AMZN_CIK, "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents", "2024-12-31",
        90_106_000_000,
    )
    _insert_xbrl_fact(
        db_path, AMZN_CIK, "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents", "2025-03-31",
        80_927_000_000, accession_no="acc-fact-2",
    )
    _insert_xbrl_fact(
        db_path, AMZN_CIK,
        "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalentsPeriodIncreaseDecreaseIncludingExchangeRateEffect",
        "2025-03-31", -9_179_000_000, period_start="2025-01-01",
    )
    periods = data.get_statement_periods(AMZN_CIK, "quarterly", db_path)
    rows = data.get_cash_flow_table(AMZN_CIK, periods, "AMZN", db_path)
    beginning = next(r for r in rows if r["canonical"] == "cash_beginning")["cells"][0]["value"]
    net_change = next(r for r in rows if r["canonical"] == "net_change_in_cash")["cells"][0]["value"]
    ending = next(r for r in rows if r["canonical"] == "cash_and_restricted_cash")["cells"][0]["value"]
    assert beginning + net_change == pytest.approx(ending)


# --- SPEC-008-batch-1 item 6 (approved 2026-08-09): EPS and share counts ---
# (KEY_METRICS_LINES widened by SPEC-008-batch-2 item 2 -- these tests scope
# their assertions to the four per-share canonicals specifically, not every
# row in the table, since the table now also carries free_cash_flow/fcff/
# fcfe/fcff_tax_rate rows that legitimately DO use the discrete-quarter
# merge mechanism the per-share lines never do.)

_PER_SHARE_CANONICALS = {"eps_basic", "eps_diluted", "basic_shares", "diluted_shares"}


def test_eps_and_shares_table_resolves_all_four_lines(db_path):
    _insert_metric(db_path, AMZN_CIK, "gross_margin", "2025-01-01", "2025-03-31", 0.5)
    _insert_xbrl_fact(db_path, AMZN_CIK, "EarningsPerShareBasic", "2025-03-31", 1.61, period_start="2025-01-01")
    _insert_xbrl_fact(db_path, AMZN_CIK, "EarningsPerShareDiluted", "2025-03-31", 1.59, period_start="2025-01-01")
    _insert_xbrl_fact(
        db_path, AMZN_CIK, "WeightedAverageNumberOfSharesOutstandingBasic", "2025-03-31",
        10_600_000_000, period_start="2025-01-01",
    )
    _insert_xbrl_fact(
        db_path, AMZN_CIK, "WeightedAverageNumberOfDilutedSharesOutstanding", "2025-03-31",
        10_700_000_000, period_start="2025-01-01",
    )
    periods = data.get_statement_periods(AMZN_CIK, "quarterly", db_path)
    rows = data.get_key_metrics_table(AMZN_CIK, periods, "AMZN", db_path)
    values = {r["canonical"]: r["cells"][0]["value"] for r in rows if r["canonical"] in _PER_SHARE_CANONICALS}
    assert values == {
        "eps_basic": 1.61, "eps_diluted": 1.59,
        "basic_shares": 10_600_000_000.0, "diluted_shares": 10_700_000_000.0,
    }


def test_eps_and_shares_table_never_derives_q4_no_fallback_at_all(db_path):
    # The core finding this item is built on: EPS/share counts are
    # WEIGHTED AVERAGES, not summable flows, so the Q4 = FY - 9M mechanism
    # every other statement uses is mathematically invalid here. Confirms
    # a missing Q4 stays a genuine gap, never a subtraction result -- no
    # PER-SHARE cell in this table can ever carry is_derived_quarter (the
    # cash-flow rows added by batch-2 item 2 are a different story, and
    # legitimately do).
    _insert_metric(db_path, AMZN_CIK, "gross_margin", "2025-01-01", "2025-12-31", 0.5)
    _insert_xbrl_fact(
        db_path, AMZN_CIK, "EarningsPerShareDiluted", "2025-12-31", 7.17, period_start="2025-01-01",
    )
    periods = data.get_statement_periods(AMZN_CIK, "annual", db_path)
    rows = data.get_key_metrics_table(AMZN_CIK, periods, "AMZN", db_path)
    eps_row = next(r for r in rows if r["canonical"] == "eps_diluted")
    fy_cell = next(c for c in eps_row["cells"] if c["period_end"] == "2025-12-31")
    # The FY cumulative figure IS filed directly (7.17) -- shown as-is
    # (this is the real filed annual EPS, correct on the annual basis);
    # what's asserted here is that no cell anywhere carries a derived
    # marker, since this line never attempts subtraction at all.
    assert fy_cell["value"] == 7.17
    for row in rows:
        if row["canonical"] not in _PER_SHARE_CANONICALS:
            continue
        for cell in row["cells"]:
            assert "is_derived_quarter" not in cell


def test_eps_and_shares_table_computes_growth_like_any_other_statement(db_path):
    _insert_filing(db_path, "acc-q1-2025", form_type="10-Q", period_end="2025-03-31", fiscal_year=2025, fiscal_period="Q1")
    _insert_filing(db_path, "acc-q2-2025", form_type="10-Q", period_end="2025-06-30", fiscal_year=2025, fiscal_period="Q2")
    _insert_metric(db_path, AMZN_CIK, "gross_margin", "2025-01-01", "2025-03-31", 0.5)
    _insert_metric(db_path, AMZN_CIK, "gross_margin", "2025-04-01", "2025-06-30", 0.5)
    _insert_xbrl_fact(db_path, AMZN_CIK, "EarningsPerShareDiluted", "2025-03-31", 1.00, period_start="2025-01-01")
    _insert_xbrl_fact(
        db_path, AMZN_CIK, "EarningsPerShareDiluted", "2025-06-30", 1.50, period_start="2025-04-01",
        accession_no="acc-fact-2",
    )
    periods = data.get_statement_periods(AMZN_CIK, "quarterly", db_path)
    rows = data.get_key_metrics_table(AMZN_CIK, periods, "AMZN", db_path)
    eps_row = next(r for r in rows if r["canonical"] == "eps_diluted")
    assert eps_row["cells"][1]["growth_pct"] == pytest.approx(0.5)


# --- SPEC-008-batch-2 item 2 (approved 2026-08-13): FCF/FCFF/FCFE on the key metrics tab ---


def test_key_metrics_lines_widened_with_cash_flow_and_rate_lines():
    canonicals = {line[0] for line in data.KEY_METRICS_LINES}
    for expected in ("free_cash_flow", "fcff", "fcfe", "fcff_tax_rate"):
        assert expected in canonicals


def test_key_metrics_table_resolves_fcfe_filed_directly(db_path):
    _insert_metric(db_path, AMZN_CIK, "gross_margin", "2025-01-01", "2025-03-31", 0.5)
    _insert_metric(db_path, AMZN_CIK, "fcfe", "2025-01-01", "2025-03-31", 900_000_000)
    periods = data.get_statement_periods(AMZN_CIK, "quarterly", db_path)
    rows = data.get_key_metrics_table(AMZN_CIK, periods, "AMZN", db_path)
    row = next(r for r in rows if r["canonical"] == "fcfe")
    assert row["label"] == "Free cash flow to equity"
    assert row["cells"][0]["value"] == 900_000_000.0
    assert row["cells"][0].get("rate_assumption") is not True  # only fcff carries this marker


def test_key_metrics_table_marks_every_populated_fcff_cell_as_a_rate_assumption(db_path):
    # Requirement 2: the marker must appear on an ANNUAL, directly-filed-
    # duration FCFF figure too, not only on discrete-derived cells -- it
    # is a fact about the constructed tax rate, independent of whether the
    # cfo/capex/interest inputs themselves were filed or subtracted.
    _insert_filing(db_path, "acc-fy2025", form_type="10-K", period_end="2025-12-31", fiscal_year=2025, fiscal_period="FY")
    _insert_metric(db_path, AMZN_CIK, "gross_margin", "2025-01-01", "2025-12-31", 0.5)
    _insert_metric(db_path, AMZN_CIK, "fcff", "2025-01-01", "2025-12-31", 1_200_000_000)
    periods = data.get_statement_periods(AMZN_CIK, "annual", db_path)
    rows = data.get_key_metrics_table(AMZN_CIK, periods, "AMZN", db_path)
    fcff_row = next(r for r in rows if r["canonical"] == "fcff")
    cell = fcff_row["cells"][0]
    assert cell["value"] == 1_200_000_000.0
    assert cell.get("is_derived_quarter") is not True  # a filed-duration annual figure, not a subtraction
    assert cell["rate_assumption"] is True  # but STILL rests on the constructed rate


def test_key_metrics_table_never_marks_a_blank_fcff_cell_a_rate_assumption(db_path):
    _insert_metric(db_path, AMZN_CIK, "gross_margin", "2025-01-01", "2025-03-31", 0.5)
    # No fcff or fcff_discrete metric at all -- a genuine gap.
    periods = data.get_statement_periods(AMZN_CIK, "quarterly", db_path)
    rows = data.get_key_metrics_table(AMZN_CIK, periods, "AMZN", db_path)
    fcff_row = next(r for r in rows if r["canonical"] == "fcff")
    assert fcff_row["cells"][0]["value"] is None
    assert fcff_row["cells"][0]["rate_assumption"] is False


def test_key_metrics_table_labels_the_tax_rate_row_by_basis(db_path):
    _insert_filing(db_path, "acc-fy2025", form_type="10-K", period_end="2025-12-31", fiscal_year=2025, fiscal_period="FY")
    _insert_metric(db_path, AMZN_CIK, "gross_margin", "2025-01-01", "2025-12-31", 0.5)
    _insert_metric(db_path, AMZN_CIK, "fcff_tax_rate", "2025-01-01", "2025-12-31", 0.21)
    annual_periods = data.get_statement_periods(AMZN_CIK, "annual", db_path)
    annual_rows = data.get_key_metrics_table(AMZN_CIK, annual_periods, "AMZN", db_path)
    annual_rate_row = next(r for r in annual_rows if r["canonical"] == "fcff_tax_rate")
    assert "this year's own rate" in annual_rate_row["label"]

    _insert_metric(db_path, AMZN_CIK, "gross_margin", "2025-01-01", "2025-03-31", 0.5)
    _insert_metric(db_path, AMZN_CIK, "fcff_tax_rate_discrete", "2025-01-01", "2025-03-31", 0.19)
    quarterly_periods = data.get_statement_periods(AMZN_CIK, "quarterly", db_path)
    quarterly_rows = data.get_key_metrics_table(AMZN_CIK, quarterly_periods, "AMZN", db_path)
    quarterly_rate_row = next(r for r in quarterly_rows if r["canonical"] == "fcff_tax_rate")
    assert "trailing twelve months" in quarterly_rate_row["label"]


# --- SPEC-008-batch-1 item 7 (approved 2026-08-11): balance sheet completeness ---


def test_balance_sheet_lines_includes_the_new_item_7_lines():
    canonicals = {line[0] for line in data.BALANCE_SHEET_LINES}
    for expected in (
        "goodwill", "intangibles", "retained_earnings", "debt_current",
        "operating_lease_liabilities", "total_liabilities",
    ):
        assert expected in canonicals


def test_balance_sheet_table_resolves_goodwill_intangibles_and_retained_earnings_filed_directly(db_path):
    _insert_metric(db_path, AMZN_CIK, "gross_margin", "2025-01-01", "2025-03-31", 0.5)
    _insert_xbrl_fact(db_path, AMZN_CIK, "Goodwill", "2025-03-31", 23_000_000_000, period_start=None)
    _insert_xbrl_fact(
        db_path, AMZN_CIK, "IntangibleAssetsNetExcludingGoodwill", "2025-03-31", 900_000_000, period_start=None,
    )
    _insert_xbrl_fact(
        db_path, AMZN_CIK, "RetainedEarningsAccumulatedDeficit", "2025-03-31", 300_000_000_000, period_start=None,
    )
    periods = data.get_statement_periods(AMZN_CIK, "quarterly", db_path)
    rows = data.get_balance_sheet_table(AMZN_CIK, periods, "AMZN", db_path)
    values = {r["canonical"]: r["cells"][0]["value"] for r in rows}
    assert values["goodwill"] == 23_000_000_000.0
    assert values["intangibles"] == 900_000_000.0
    assert values["retained_earnings"] == 300_000_000_000.0


def test_total_liabilities_derived_from_assets_minus_equity_when_not_filed(db_path):
    # AMZN never files a standalone `Liabilities` total (only NVDA and MU
    # do) -- confirmed against the real companyfacts corpus before this
    # was written. Checked identity: computed == filed Liabilities in
    # 16/16 recent NVDA and MU quarters, zero mismatches.
    _insert_metric(db_path, AMZN_CIK, "gross_margin", "2025-01-01", "2025-03-31", 0.5)
    _insert_xbrl_fact(db_path, AMZN_CIK, "Assets", "2025-03-31", 1_000_000_000, period_start=None)
    _insert_xbrl_fact(db_path, AMZN_CIK, "StockholdersEquity", "2025-03-31", 400_000_000, period_start=None)
    # No Liabilities fact filed at all for this period.
    periods = data.get_statement_periods(AMZN_CIK, "quarterly", db_path)
    rows = data.get_balance_sheet_table(AMZN_CIK, periods, "AMZN", db_path)
    liab_row = next(r for r in rows if r["canonical"] == "total_liabilities")
    cell = liab_row["cells"][0]
    assert cell["value"] == 600_000_000.0
    assert cell["is_derived_quarter"] is True
    assert "blank_cause" not in cell


def test_total_liabilities_prefers_the_filed_figure_over_the_derived_one(db_path):
    _insert_metric(db_path, AMZN_CIK, "gross_margin", "2025-01-01", "2025-03-31", 0.5)
    _insert_xbrl_fact(db_path, AMZN_CIK, "Assets", "2025-03-31", 1_000_000_000, period_start=None)
    _insert_xbrl_fact(db_path, AMZN_CIK, "StockholdersEquity", "2025-03-31", 400_000_000, period_start=None)
    _insert_xbrl_fact(db_path, AMZN_CIK, "Liabilities", "2025-03-31", 590_000_000, period_start=None)
    periods = data.get_statement_periods(AMZN_CIK, "quarterly", db_path)
    rows = data.get_balance_sheet_table(AMZN_CIK, periods, "AMZN", db_path)
    liab_row = next(r for r in rows if r["canonical"] == "total_liabilities")
    cell = liab_row["cells"][0]
    assert cell["value"] == 590_000_000.0  # the FILED figure, not 600M implied by assets-equity
    assert cell.get("is_derived_quarter") is not True


def test_total_liabilities_stays_blank_when_assets_or_equity_also_missing(db_path):
    _insert_metric(db_path, AMZN_CIK, "gross_margin", "2025-01-01", "2025-03-31", 0.5)
    _insert_xbrl_fact(db_path, AMZN_CIK, "Assets", "2025-03-31", 1_000_000_000, period_start=None)
    # No equity at all -- can't derive.
    periods = data.get_statement_periods(AMZN_CIK, "quarterly", db_path)
    rows = data.get_balance_sheet_table(AMZN_CIK, periods, "AMZN", db_path)
    liab_row = next(r for r in rows if r["canonical"] == "total_liabilities")
    cell = liab_row["cells"][0]
    assert cell["value"] is None
    assert cell["blank_cause"] == "gap"


def test_total_liabilities_derived_cells_get_growth_and_yoy_like_any_other_cell(db_path):
    _insert_filing(db_path, "acc-q1-2025", form_type="10-Q", period_end="2025-03-31", fiscal_year=2025, fiscal_period="Q1")
    _insert_filing(db_path, "acc-q1-2026", form_type="10-Q", period_end="2026-03-31", fiscal_year=2026, fiscal_period="Q1")
    _insert_metric(db_path, AMZN_CIK, "gross_margin", "2025-01-01", "2025-03-31", 0.5)
    _insert_metric(db_path, AMZN_CIK, "gross_margin", "2026-01-01", "2026-03-31", 0.5)
    _insert_xbrl_fact(db_path, AMZN_CIK, "Assets", "2025-03-31", 1_000_000_000, period_start=None)
    _insert_xbrl_fact(db_path, AMZN_CIK, "StockholdersEquity", "2025-03-31", 400_000_000, period_start=None)
    _insert_xbrl_fact(
        db_path, AMZN_CIK, "Assets", "2026-03-31", 1_200_000_000, period_start=None, accession_no="acc-fact-assets-2",
    )
    _insert_xbrl_fact(
        db_path, AMZN_CIK, "StockholdersEquity", "2026-03-31", 400_000_000, period_start=None,
        accession_no="acc-fact-equity-2",
    )
    periods = data.get_statement_periods(AMZN_CIK, "quarterly", db_path)
    rows = data.get_balance_sheet_table(AMZN_CIK, periods, "AMZN", db_path)
    liab_row = next(r for r in rows if r["canonical"] == "total_liabilities")
    fy2026_cell = next(c for c in liab_row["cells"] if c["period_end"] == "2026-03-31")
    # 2025: 600M derived. 2026: 800M derived. YoY = (800-600)/600 = 1/3.
    assert fy2026_cell["yoy_growth_pct"] == pytest.approx(1 / 3)
