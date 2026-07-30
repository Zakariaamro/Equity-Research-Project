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
