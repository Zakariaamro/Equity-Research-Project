"""Tests for edgar.observations (SPEC-005)."""

from __future__ import annotations

import re

import pytest

from edgar import config, db, observations, readability, section_store

AMZN_CIK = "0001018724"
NVDA_CIK = "0001045810"
MU_CIK = "0000723125"


@pytest.fixture
def conn(tmp_path):
    db_path = tmp_path / "test.db"
    db.init_db(db_path)
    connection = db.get_connection(db_path)
    yield connection
    connection.close()


def _insert_filing(
    conn, accession_no, cik, form_type, period_end, fiscal_year=None, fiscal_period=None
):
    conn.execute(
        """
        INSERT INTO filings
            (accession_no, cik, form_type, filing_date, period_end, fiscal_year, fiscal_period,
             discovered_at, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'sectioned')
        ON CONFLICT(accession_no) DO UPDATE SET
            fiscal_year = excluded.fiscal_year, fiscal_period = excluded.fiscal_period
        """,
        (accession_no, cik, form_type, period_end, period_end, fiscal_year, fiscal_period, f"{period_end}T00:00:00"),
    )
    conn.commit()


def _insert_metric(conn, cik, period_start, period_end, name, value):
    conn.execute(
        """
        INSERT INTO metrics
            (cik, accession_no, period_start, period_end, name, value, formula, inputs_json,
             calc_version, computed_at)
        VALUES (?, NULL, ?, ?, ?, ?, 'test', '{}', ?, '2026-01-01T00:00:00')
        ON CONFLICT(cik, period_start, period_end, name, calc_version) DO UPDATE SET value = excluded.value
        """,
        (cik, period_start, period_end, name, value, config.CALC_VERSION),
    )
    conn.commit()


def _insert_section(conn, accession_no, category, short_name, text, position=1):
    text_hash = section_store.write_section_text(text)
    normalized_hash = section_store.hash_normalized_text(text)
    counts = readability.compute_counts(text)
    source_file = f"R{position}.htm"
    conn.execute(
        """
        INSERT INTO sections
            (accession_no, category, short_name, source_file, position, text_hash,
             normalized_text_hash, word_count, sentence_count, complex_word_count)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(accession_no, category, short_name, source_file) DO UPDATE SET
            text_hash = excluded.text_hash, normalized_text_hash = excluded.normalized_text_hash,
            word_count = excluded.word_count, sentence_count = excluded.sentence_count,
            complex_word_count = excluded.complex_word_count
        """,
        (
            accession_no, category, short_name, source_file, position, text_hash, normalized_hash,
            counts.word_count, counts.sentence_count, counts.complex_word_count,
        ),
    )
    conn.commit()
    return conn.execute(
        "SELECT id FROM sections WHERE accession_no = ? AND category = ? AND short_name = ? AND source_file = ?",
        (accession_no, category, short_name, source_file),
    ).fetchone()["id"]


# --- R3: no lookahead ---


def test_no_lookahead_raises_on_future_ref(conn):
    _insert_filing(conn, "acc2020", AMZN_CIK, "10-K", "2020-12-31", 2020, "FY")
    _insert_metric(conn, AMZN_CIK, "2020-01-01", "2020-12-31", "revenue", 100.0)
    _insert_metric(conn, AMZN_CIK, "2021-01-01", "2021-12-31", "revenue", 200.0)
    future_id = conn.execute(
        "SELECT id FROM metrics WHERE cik = ? AND period_end = '2021-12-31'", (AMZN_CIK,)
    ).fetchone()["id"]
    obs = observations.Observation(
        cik=AMZN_CIK, accession_no="acc2020", period_end="2020-12-31",
        rule_name="metric_multi_year_extreme", rule_version="v1", subject="revenue",
        severity="medium", statement="test", refs=[observations._ref("metrics", future_id)],
    )
    with pytest.raises(AssertionError):
        observations._assert_no_lookahead(conn, obs)


def test_compute_observations_never_stores_a_lookahead_reference(conn):
    for i, (start, end, value) in enumerate(
        [
            ("2015-01-01", "2015-12-31", 1.0), ("2016-01-01", "2016-12-31", 1.1),
            ("2017-01-01", "2017-12-31", 1.2), ("2018-01-01", "2018-12-31", 1.3),
            ("2019-01-01", "2019-12-31", 5.0),
        ]
    ):
        _insert_filing(conn, f"acc{i}", AMZN_CIK, "10-K", end, 2015 + i, "FY")
        _insert_metric(conn, AMZN_CIK, start, end, "roe", value)
    written = observations.compute_observations(conn, tickers=["AMZN"], rule_names=["metric_multi_year_extreme"])
    assert written  # the 5.0 should register as a new high
    for row in conn.execute("SELECT period_end, refs_json FROM observations"):
        import json
        for ref in json.loads(row["refs_json"]):
            table = "metrics" if ref["table"] == "metrics" else "sections"
            r = conn.execute(f"SELECT period_end FROM {table} WHERE id = ?", (ref["id"],)).fetchone() if table == "metrics" else None
            if r is not None:
                assert r["period_end"] <= row["period_end"]


# --- metric_multi_year_extreme minimum history ---


def test_extreme_requires_minimum_history(conn):
    # Only 3 prior annual periods (min_prior for annual is 4) -- must not fire
    # even though the 4th value is a genuine extreme.
    values = [1.0, 1.0, 1.0, 99.0]
    for i, v in enumerate(values):
        year = 2020 + i
        _insert_filing(conn, f"acc{year}", AMZN_CIK, "10-K", f"{year}-12-31", year, "FY")
        _insert_metric(conn, AMZN_CIK, f"{year}-01-01", f"{year}-12-31", "roe", v)
    obs = observations._rule_metric_multi_year_extreme(conn, AMZN_CIK)
    assert obs == []

    # A 5th period (now 4 prior periods exist) with another extreme value fires.
    _insert_filing(conn, "acc2024", AMZN_CIK, "10-K", "2024-12-31", 2024, "FY")
    _insert_metric(conn, AMZN_CIK, "2024-01-01", "2024-12-31", "roe", 200.0)
    obs = observations._rule_metric_multi_year_extreme(conn, AMZN_CIK)
    assert any(o.period_end == "2024-12-31" and o.subject == "roe" for o in obs)


def test_extreme_skips_compounding_level_metrics():
    non_informative = {"ebitda", "nopat", "invested_capital", "free_cash_flow", "net_debt"}
    assert non_informative <= set(config.METRIC_REGISTRY)
    for name in non_informative:
        assert config.METRIC_REGISTRY[name].extreme_informative is False
    # Spot-check a ratio metric stays informative.
    assert config.METRIC_REGISTRY["gross_margin"].extreme_informative is True


def test_extreme_never_fires_on_a_compounding_level_metric_even_with_a_genuine_record(conn):
    # ebitda: a textbook "growing company sets a new record" sequence -- must
    # never produce an observation, regardless of how extreme the value is.
    values = [10.0, 11.0, 12.0, 13.0, 1000.0]
    for i, v in enumerate(values):
        year = 2020 + i
        _insert_filing(conn, f"acce{year}", AMZN_CIK, "10-K", f"{year}-12-31", year, "FY")
        _insert_metric(conn, AMZN_CIK, f"{year}-01-01", f"{year}-12-31", "ebitda", v)
    obs = observations._rule_metric_multi_year_extreme(conn, AMZN_CIK)
    assert obs == []


# --- metric_sigma_move ---


def test_sigma_handles_zero_variance(conn):
    for i in range(9):
        end = f"2020-{(i % 4) + 1:02d}-28"
        start = f"2019-{(i % 4) + 1:02d}-01"
        _insert_filing(conn, f"accq{i}", AMZN_CIK, "10-Q", end, 2020, f"Q{(i % 4) + 1}")
        _insert_metric(conn, AMZN_CIK, start, end, "current_ratio", 1.5)  # constant -> stdev 0
    obs = observations._rule_metric_sigma_move(conn, AMZN_CIK)
    assert obs == []


_QUARTERLY_DATES = [
    ("2018-04-01", "2018-06-30"), ("2018-07-01", "2018-09-30"), ("2018-10-01", "2018-12-31"),
    ("2019-01-01", "2019-03-31"), ("2019-04-01", "2019-06-30"), ("2019-07-01", "2019-09-30"),
    ("2019-10-01", "2019-12-31"), ("2020-01-01", "2020-03-31"), ("2020-04-01", "2020-06-30"),
]


def test_sigma_move_fires_on_real_outlier(conn):
    values = [1.50, 1.51, 1.49, 1.52, 1.48, 1.53, 1.47, 1.54, 50.0]
    for i, (start, end) in enumerate(_QUARTERLY_DATES):
        _insert_filing(conn, f"accs{i}", AMZN_CIK, "10-Q", end, 2018 + i // 4, f"Q{(i % 4) + 1}")
        _insert_metric(conn, AMZN_CIK, start, end, "current_ratio", values[i])
    obs = observations._rule_metric_sigma_move(conn, AMZN_CIK)
    assert len(obs) == 1
    assert "50" in obs[0].statement


# --- metric_divergence: NULL prior is not zero, and transition-only firing ---


def test_null_prior_period_does_not_fire_yoy_decline_divergence(conn):
    _insert_filing(conn, "accA", MU_CIK, "10-K", "2020-08-30", 2020, "FY")
    _insert_metric(conn, MU_CIK, "2019-09-01", "2020-08-30", "depreciation_rate", None)
    _insert_filing(conn, "accB", MU_CIK, "10-K", "2021-09-02", 2021, "FY")
    _insert_metric(conn, MU_CIK, "2020-08-31", "2021-09-02", "depreciation_rate", 0.05)
    obs = observations._rule_metric_divergence(conn, MU_CIK)
    assert obs == []  # prior fiscal year had no value -- must not be treated as a decline from 0


_EIGHT_QUARTERLY_DATES = [
    ("2020-01-01", "2020-03-31"), ("2020-04-01", "2020-06-30"), ("2020-07-01", "2020-09-30"),
    ("2020-10-01", "2020-12-31"), ("2021-01-01", "2021-03-31"), ("2021-04-01", "2021-06-30"),
    ("2021-07-01", "2021-09-30"), ("2021-10-01", "2021-12-31"),
]


def test_persistent_condition_does_not_fire_every_period(conn):
    # capex_to_depreciation-style: stays above the divergence threshold for
    # many consecutive quarters. Must fire ONCE, at the transition, not on
    # every period the condition holds.
    values = [1.0, 1.0, 2.0, 2.0, 2.0, 2.0, 0.5, 2.0]
    for i, (start, end) in enumerate(_EIGHT_QUARTERLY_DATES):
        _insert_filing(conn, f"accp{i}", AMZN_CIK, "10-Q", end, 2020 + i // 4, f"Q{(i % 4) + 1}")
        _insert_metric(conn, AMZN_CIK, start, end, "capex_to_depreciation", values[i])
    obs = observations._rule_metric_divergence(conn, AMZN_CIK)
    # crosses above 1.5 at index 2 (fires), stays above through index 5 (no
    # more fires), drops below at index 6, crosses back above at index 7 (fires again)
    fired_periods = [o.period_end for o in obs]
    assert len(fired_periods) == 2


def test_threshold_cross_fires_only_on_transition(conn):
    values = [0.6, 0.6, 0.4, 0.3, 0.3, 0.6]  # crosses below 0.5 once, back above once
    dates = _EIGHT_QUARTERLY_DATES[:6]
    for i, (start, end) in enumerate(dates):
        _insert_filing(conn, f"acct{i}", AMZN_CIK, "10-Q", end, 2020 + i // 4, f"Q{(i % 4) + 1}")
        _insert_metric(conn, AMZN_CIK, start, end, "fcf_conversion", values[i])
    obs = observations._rule_metric_threshold_cross(conn, AMZN_CIK)
    # only the below-0.5 threshold applies to fcf_conversion -- one crossing-in event
    assert len(obs) == 1
    assert obs[0].period_end == dates[2][1]


# --- metric_stopped_computing: fiscal-matched, not structural gaps ---


def test_stopped_computing_fires_when_prior_fiscal_period_had_a_value(conn):
    _insert_filing(conn, "accS1", AMZN_CIK, "10-Q", "2020-03-31", 2020, "Q1")
    _insert_metric(conn, AMZN_CIK, "2020-01-01", "2020-03-31", "revenue_qoq", 0.05)
    _insert_filing(conn, "accS2", AMZN_CIK, "10-Q", "2021-03-31", 2021, "Q1")
    _insert_metric(conn, AMZN_CIK, "2021-01-01", "2021-03-31", "revenue_qoq", None)
    obs = observations._rule_metric_stopped_computing(conn, AMZN_CIK)
    assert len(obs) == 1
    assert obs[0].period_end == "2021-03-31"


def test_stopped_computing_does_not_fire_for_structural_gap(conn):
    # Both this Q1 and last year's Q1 are NULL -- a structural gap (revenue_qoq
    # has no directly-tagged Q4 to compare Q1 against), not a "stopped" event.
    _insert_filing(conn, "accG1", AMZN_CIK, "10-Q", "2020-03-31", 2020, "Q1")
    _insert_metric(conn, AMZN_CIK, "2020-01-01", "2020-03-31", "revenue_qoq", None)
    _insert_filing(conn, "accG2", AMZN_CIK, "10-Q", "2021-03-31", 2021, "Q1")
    _insert_metric(conn, AMZN_CIK, "2021-01-01", "2021-03-31", "revenue_qoq", None)
    obs = observations._rule_metric_stopped_computing(conn, AMZN_CIK)
    assert obs == []


# --- section_wording_changed: measured similarity threshold, both categories ---


POLICY_TEXT_A = (
    "v3.20.0.1\nSignificant Accounting Policies 12 Months Ended\nDec. 31, 2023\n"
    "Basis of Presentation Basis of PresentationThe consolidated financial statements "
    "include the accounts of the Company and its subsidiaries. Revenue is recognized "
    "when control transfers to the customer. Inventory is valued at the lower of cost "
    "and net realizable value using the first-in, first-out method."
)
POLICY_TEXT_A_TRIVIAL_EDIT = POLICY_TEXT_A.replace("v3.20.0.1", "v3.21.2").replace(
    "Dec. 31, 2023", "Dec. 31, 2024"
)
POLICY_TEXT_A_REWRITE = (
    "v3.21.2\nSignificant Accounting Policies 12 Months Ended\nDec. 31, 2024\n"
    "Basis of Presentation Basis of PresentationDuring the year the Company adopted a "
    "new accounting standard for derivative instruments and restated prior-period "
    "amounts. Effective January 1, 2024, the Company also changed its policy for "
    "capitalizing internal-use software costs, which it now expenses as incurred. "
    "Management believes these changes better reflect the economics of the business "
    "and provide more decision-useful information to investors evaluating performance."
)


def test_section_wording_changed_skips_trivial_edit_in_both_categories(conn):
    for category in (config.MENUCATEGORY_POLICIES, config.MENUCATEGORY_NOTES):
        _insert_filing(conn, f"accW1-{category}", AMZN_CIK, "10-K", "2023-12-31", 2023, "FY")
        _insert_section(conn, f"accW1-{category}", category, "Significant Accounting Policies", POLICY_TEXT_A)
        _insert_filing(conn, f"accW2-{category}", AMZN_CIK, "10-K", "2024-12-31", 2024, "FY")
        _insert_section(
            conn, f"accW2-{category}", category, "Significant Accounting Policies", POLICY_TEXT_A_TRIVIAL_EDIT
        )
    obs = observations._rule_section_wording_changed(conn, AMZN_CIK)
    assert obs == []


def test_section_wording_changed_fires_on_substantial_rewrite_in_both_categories(conn):
    for category in (config.MENUCATEGORY_POLICIES, config.MENUCATEGORY_NOTES):
        _insert_filing(conn, f"accR1-{category}", AMZN_CIK, "10-K", "2023-12-31", 2023, "FY")
        _insert_section(conn, f"accR1-{category}", category, "Significant Accounting Policies", POLICY_TEXT_A)
        _insert_filing(conn, f"accR2-{category}", AMZN_CIK, "10-K", "2024-12-31", 2024, "FY")
        _insert_section(
            conn, f"accR2-{category}", category, "Significant Accounting Policies", POLICY_TEXT_A_REWRITE
        )
    obs = observations._rule_section_wording_changed(conn, AMZN_CIK)
    fired_categories = {o.statement for o in obs}
    assert len(obs) == 2  # both categories fire
    assert all("similar to a year ago" in s for s in fired_categories)


# --- section_appeared / section_disappeared: renames and fluctuating notes ---


def test_note_rename_via_alias_is_not_appeared_or_disappeared(conn, monkeypatch):
    monkeypatch.setitem(config.NOTE_NAME_ALIASES, "Old Note Name", "New Note Name")
    _insert_filing(conn, "accN1", MU_CIK, "10-K", "2020-08-30", 2020, "FY")
    _insert_section(conn, "accN1", config.MENUCATEGORY_NOTES, "Old Note Name", "Some note text about leases and equipment.")
    _insert_filing(conn, "accN2", MU_CIK, "10-K", "2021-09-02", 2021, "FY")
    _insert_section(conn, "accN2", config.MENUCATEGORY_NOTES, "New Note Name", "Some note text about leases and equipment updated.")
    obs = observations._rule_section_appeared_disappeared(conn, MU_CIK)
    assert obs == []


def test_genuinely_new_note_fires_appeared(conn):
    _insert_filing(conn, "accX1", MU_CIK, "10-K", "2020-08-30", 2020, "FY")
    _insert_section(conn, "accX1", config.MENUCATEGORY_NOTES, "Leases", "Lease text.")
    _insert_filing(conn, "accX2", MU_CIK, "10-K", "2021-09-02", 2021, "FY")
    _insert_section(conn, "accX2", config.MENUCATEGORY_NOTES, "Leases", "Lease text updated.")
    _insert_section(conn, "accX2", config.MENUCATEGORY_NOTES, "Derivative Instruments", "New derivative policy text.")
    obs = observations._rule_section_appeared_disappeared(conn, MU_CIK)
    assert any(o.rule_name == "section_appeared" and o.subject == "Derivative Instruments" for o in obs)


def test_fluctuating_note_excluded_from_appeared_disappeared(conn):
    fluctuating = next(iter(config.FLUCTUATING_NOTE_NAMES))
    _insert_filing(conn, "accF1", MU_CIK, "10-Q", "2020-03-01", 2020, "Q2")
    _insert_section(conn, "accF1", config.MENUCATEGORY_NOTES, fluctuating, "Pending standard text.")
    _insert_filing(conn, "accF2", MU_CIK, "10-Q", "2021-03-04", 2021, "Q2")
    _insert_section(conn, "accF2", config.MENUCATEGORY_NOTES, "Some Other Note", "Unrelated content.")
    obs = observations._rule_section_appeared_disappeared(conn, MU_CIK)
    assert all(o.subject != fluctuating for o in obs)


# --- determinism, idempotency, refs resolve, statement quality ---


def _seed_mixed_corpus(conn):
    for i, (start, end, fy, value) in enumerate(
        [
            ("2019-01-01", "2019-12-31", 2019, 0.20), ("2020-01-01", "2020-12-31", 2020, 0.21),
            ("2021-01-01", "2021-12-31", 2021, 0.19), ("2022-01-01", "2022-12-31", 2022, 0.30),
        ]
    ):
        _insert_filing(conn, f"accM{i}", AMZN_CIK, "10-K", end, fy, "FY")
        _insert_metric(conn, AMZN_CIK, start, end, "gross_margin", value)
    _insert_filing(conn, "accSec1", AMZN_CIK, "10-K", "2021-12-31", 2021, "FY")
    _insert_section(conn, "accSec1", config.MENUCATEGORY_NOTES, "Leases", "Lease disclosure text about equipment.")
    _insert_filing(conn, "accSec2", AMZN_CIK, "10-K", "2022-12-31", 2022, "FY")
    _insert_section(
        conn, "accSec2", config.MENUCATEGORY_NOTES, "Leases",
        "Lease disclosure text about equipment and buildings, substantially expanded this year "
        "with new sale-leaseback and impairment considerations discussed at length below.",
    )


def test_statements_are_deterministic(conn):
    _seed_mixed_corpus(conn)
    first = sorted((o.rule_name, o.subject, o.period_end, o.statement) for o in observations._observations_for_company(conn, AMZN_CIK, None))
    second = sorted((o.rule_name, o.subject, o.period_end, o.statement) for o in observations._observations_for_company(conn, AMZN_CIK, None))
    assert first == second


def test_statements_contain_figures_and_no_causal_language(conn):
    _seed_mixed_corpus(conn)
    causal_words = ("because", "due to", "caused", "resulted in", "led to", "as a result")
    obs = observations._observations_for_company(conn, AMZN_CIK, None)
    assert obs
    for o in obs:
        assert re.search(r"\d", o.statement), f"no figure in: {o.statement!r}"
        lowered = o.statement.lower()
        for word in causal_words:
            assert word not in lowered, f"causal language in: {o.statement!r}"


def test_refs_resolve(conn):
    _seed_mixed_corpus(conn)
    written = observations.compute_observations(conn, tickers=["AMZN"])
    assert written
    import json
    for row in conn.execute("SELECT refs_json FROM observations"):
        for ref in json.loads(row["refs_json"]):
            table = ref["table"]
            resolved = conn.execute(f"SELECT id FROM {table} WHERE id = ?", (ref["id"],)).fetchone()
            assert resolved is not None


def test_observations_idempotent(conn):
    _seed_mixed_corpus(conn)
    first = observations.compute_observations(conn, tickers=["AMZN"])
    assert first
    second = observations.compute_observations(conn, tickers=["AMZN"])
    assert second == []
