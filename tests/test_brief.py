"""Tests for edgar.brief (SPEC-007). No network access anywhere here -- the
LLM client is always a FakeRawClient (see test_llm.py's version; this module
has its own copy, matching the project's per-module-duplication convention).

Three demonstrations required before any real API call (2026-07-30 review):
  1. test_verifier_pass_rejects_fabricated_causal_claim
  2. test_aggregation_with_wrong_sum_dropped
  3. test_aggregation_with_mismatched_units_dropped
"""

from __future__ import annotations

import itertools
import json

import pytest

from edgar import brief, config, db, llm

AMZN_CIK = "0001018724"


class FakeRawClient:
    """Implements llm.RawLLMClient's Protocol with a canned response queue.
    Each queued response is a bare str (stop_reason defaults to "end_turn")
    or a (text, stop_reason) tuple."""

    def __init__(self, responses: list) -> None:
        self._responses = list(responses)
        self.calls: list[tuple[str, int, str]] = []

    def messages_create(self, model, max_tokens, prompt):
        self.calls.append((model, max_tokens, prompt))
        response = self._responses.pop(0)
        if isinstance(response, tuple):
            text, stop_reason = response
        else:
            text, stop_reason = response, "end_turn"
        return text, 1000, 200, stop_reason


@pytest.fixture
def conn(tmp_path):
    db_path = tmp_path / "test.db"
    db.init_db(db_path)
    connection = db.get_connection(db_path)
    llm._reset_run_state()
    yield connection
    connection.close()


def _insert_filing(conn, accession_no, cik=AMZN_CIK, form_type="10-K", filing_date="2026-02-06",
                    period_end="2025-12-31", fiscal_year=2025, fiscal_period="FY"):
    conn.execute(
        "INSERT INTO filings (accession_no, cik, form_type, filing_date, period_end, fiscal_year, "
        "fiscal_period, discovered_at, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'sectioned') "
        "ON CONFLICT(accession_no) DO NOTHING",
        (accession_no, cik, form_type, filing_date, period_end, fiscal_year, fiscal_period, f"{filing_date}T00:00:00"),
    )
    conn.commit()


_section_counter = itertools.count()


def _insert_observation(
    conn, accession_no, rule_name, subject, severity, statement, cik=AMZN_CIK,
    period_end="2025-12-31", rule_version=None,
):
    rule_version = rule_version or config.RULE_REGISTRY[rule_name].version
    cursor = conn.execute(
        "INSERT INTO observations (cik, accession_no, period_end, rule_name, rule_version, subject, "
        "severity, statement, refs_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, '[]', ?)",
        (cik, accession_no, period_end, rule_name, rule_version, subject, severity, statement, "2026-01-01T00:00:00"),
    )
    conn.commit()
    return cursor.lastrowid


def _insert_finding(conn, accession_no, category, severity, headline, detail, quote):
    n = next(_section_counter)
    cursor = conn.execute(
        "INSERT INTO sections (accession_no, category, short_name, source_file, position, text_hash) "
        "VALUES (?, 'Notes', ?, 'R1.htm', 1, ?)",
        (accession_no, f"Test Note {n}", f"hash-{n}"),
    )
    section_id = cursor.lastrowid
    cursor = conn.execute(
        "INSERT INTO analyses (section_id, prompt_name, prompt_version, model, input_hash, output_json, "
        "call_id, created_at) VALUES (?, 'section_analysis', 'v3', 'claude-sonnet-5', ?, '{}', NULL, ?)",
        (section_id, f"input-hash-{n}", "2026-01-01T00:00:00"),
    )
    analysis_id = cursor.lastrowid
    cursor = conn.execute(
        "INSERT INTO findings (analysis_id, accession_no, category, severity, headline, detail, quote, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (analysis_id, accession_no, category, severity, headline, detail, quote, "2026-01-01T00:00:00"),
    )
    conn.commit()
    return cursor.lastrowid


def _filing_row(accession_no="acc1", cik=AMZN_CIK, ticker="AMZN", company_name="Amazon.com, Inc.",
                 form_type="10-K", fiscal_year=2025, fiscal_period="FY", filing_date="2026-02-06"):
    return {
        "accession_no": accession_no, "cik": cik, "ticker": ticker, "company_name": company_name,
        "form_type": form_type, "fiscal_year": fiscal_year, "fiscal_period": fiscal_period, "filing_date": filing_date,
    }


def _generator_response(sentences: list[dict], material: bool = True) -> str:
    return json.dumps({"material": material, "sentences": sentences})


def _verifier_response(verdicts: list[dict]) -> str:
    return json.dumps({"verifications": verdicts})


# =====================================================================
# Demonstration 1: a fabricated causal claim rejected by the VERIFIER pass
# (R4's lexical checks cannot catch implied causation with no connective at
# all -- "Inventory rose. Margins fell." -- this is exactly what R5 exists
# to catch, independently of the type checks.)
# =====================================================================


def test_verifier_pass_rejects_fabricated_causal_claim(conn):
    _insert_filing(conn, "acc1")
    _insert_observation(conn, "acc1", "metric_multi_year_extreme", "gross_margin", "high",
                        "gross_margin of 0.50 is the lowest in 6 years.")
    _insert_observation(conn, "acc1", "section_length_change", "Inventory", "medium",
                        "The Inventory note is 40% longer than a year ago.")

    generator_template = brief.load_prompt_template(config.BRIEF_GENERATOR_PROMPT_NAME, config.BRIEF_GENERATOR_PROMPT_VERSION)
    verifier_template = brief.load_prompt_template(config.BRIEF_VERIFIER_PROMPT_NAME, config.BRIEF_VERIFIER_VERSION)

    # This sentence is a valid JUXTAPOSITION under R4: two references, no
    # causal connective in the text. But juxtaposing "grew" and "fell" this
    # way strongly implies causation without ever saying "because" -- R4's
    # lexical check has no way to catch that. The verifier does.
    sentence = {
        "type": "juxtaposition",
        "text": "The Inventory note grew substantially longer as gross margin fell to a 6-year low.",
        "refs": ["obs:2", "obs:1"],
    }
    gen_response = _generator_response([sentence])
    ver_response = _verifier_response([
        {"position": 0, "verdict": "unsupported", "unsupported_claim": "implies the inventory note's growth caused the margin decline; neither source states a cause"}
    ])

    fake = FakeRawClient([gen_response, ver_response])
    client = llm.LLMClient(raw_client=fake)

    outcome = brief.generate_brief(conn, _filing_row(), generator_template, verifier_template, client=client)

    assert outcome.status == "ok"
    assert outcome.sentences == []  # the only sentence was dropped
    assert outcome.generator_dropped == 0  # it PASSED R4 -- this is the point
    assert outcome.verifier_dropped == 1  # the verifier is what caught it
    assert len(fake.calls) == 2  # generator, then verifier -- exactly one of each

    stored = conn.execute("SELECT COUNT(*) AS n FROM brief_sentences WHERE brief_id = ?", (outcome.brief_id,)).fetchone()
    assert stored["n"] == 0  # never persisted


# =====================================================================
# Demonstration 2: an aggregation with an arithmetically WRONG sum, rejected
# by the type check (R4, in code -- no LLM call involved in the check itself)
# =====================================================================


def test_aggregation_with_wrong_sum_dropped():
    supplied_index = {
        "finding:1": {"kind": "finding", "row": {}, "text": "A $525 million verdict was awarded against the company."},
        "finding:2": {"kind": "finding", "row": {}, "text": "The company paid a $746 million fine."},
    }
    # 525 + 746 = 1271 (~$1.27B), NOT $2 billion.
    sentence = {
        "type": "aggregation",
        "text": "Combined litigation costs reached $2 billion this period.",
        "refs": ["finding:1", "finding:2"],
    }
    kept, reason = brief.verify_sentence(sentence, supplied_index)
    assert kept is False
    assert "does not verify" in reason


def test_aggregation_with_correct_sum_kept():
    supplied_index = {
        "finding:1": {"kind": "finding", "row": {}, "text": "A $525 million verdict was awarded against the company."},
        "finding:2": {"kind": "finding", "row": {}, "text": "The company paid a $746 million fine."},
    }
    sentence = {
        "type": "aggregation",
        "text": "Combined litigation costs reached $1271 million this period.",
        "refs": ["finding:1", "finding:2"],
    }
    kept, reason = brief.verify_sentence(sentence, supplied_index)
    assert kept is True, reason


# =====================================================================
# Demonstration 3: an aggregation summing a EURO amount and a DOLLAR amount,
# rejected by the unit check (SPEC-007 v2.1's fix) -- the real case found in
# pre-implementation review: 525 + 746 = 1271 is correct ARITHMETIC while
# conflating two different currencies.
# =====================================================================


def test_aggregation_with_mismatched_units_dropped():
    supplied_index = {
        "finding:1": {"kind": "finding", "row": {}, "text": "A $525 million verdict was awarded against the company."},
        "finding:2": {"kind": "finding", "row": {}, "text": "The company paid a €746 million fine."},
    }
    sentence = {
        "type": "aggregation",
        # 525 + 746 = 1271 -- correct raw arithmetic, WRONG because one
        # addend is dollars and the other is euros.
        "text": "Combined litigation costs reached $1271 million this period.",
        "refs": ["finding:1", "finding:2"],
    }
    kept, reason = brief.verify_sentence(sentence, supplied_index)
    assert kept is False
    assert "does not verify" in reason


def test_aggregation_percent_units_still_sum_correctly():
    """Sanity check that the unit-aware fix doesn't break the ordinary
    same-unit case -- percentages summing against each other, matching the
    real customer-concentration pattern from SPEC-006."""
    supplied_index = {
        "finding:1": {
            "kind": "finding", "row": {},
            "text": "Three direct customers accounted for 27%, 18% and 12% of accounts receivable.",
        },
    }
    sentence = {
        "type": "aggregation",
        "text": "Three customers account for 57% combined of accounts receivable.",
        "refs": ["finding:1"],
    }
    kept, reason = brief.verify_sentence(sentence, supplied_index)
    assert kept is True, reason


# --- R2: selection ---


def test_observation_selection_filters_to_current_rule_version(conn):
    _insert_filing(conn, "acc1")
    # A stale v1 row with a HIGHER severity than the current version assigns
    # -- section_appeared/disappeared were "high" under v1, "low" under v4.
    _insert_observation(conn, "acc1", "section_appeared", "Some Note", "high", "stale high", rule_version="v1")
    _insert_observation(conn, "acc1", "section_appeared", "Some Note", "low", "current low", rule_version="v4")

    selected = brief.select_observations(conn, "acc1")
    assert len(selected) == 1
    assert selected[0]["rule_version"] == config.RULE_REGISTRY["section_appeared"].version
    assert selected[0]["severity"] == "low"


def test_observation_selection_caps_two_per_slot(conn):
    _insert_filing(conn, "acc1")
    # Section-subject rule: 2 per rule_name, unchanged behaviour.
    for i in range(4):
        _insert_observation(conn, "acc1", "section_wording_changed", f"Note {i}", "high", f"note {i} changed")
    selected = brief.select_observations(conn, "acc1")
    assert len(selected) == 2

    # Metric-subject rule: 2 per (rule_name, metric_category), not 2 per
    # rule_name alone -- margins and returns are DIFFERENT categories.
    conn.execute("DELETE FROM observations")
    conn.commit()
    _insert_observation(conn, "acc1", "metric_multi_year_extreme", "gross_margin", "high", "margins 1")  # margins
    _insert_observation(conn, "acc1", "metric_multi_year_extreme", "operating_margin", "high", "margins 2")  # margins
    _insert_observation(conn, "acc1", "metric_multi_year_extreme", "asset_turnover", "high", "returns 1")  # returns
    _insert_observation(conn, "acc1", "metric_multi_year_extreme", "equity_multiplier", "high", "returns 2")  # returns
    selected = brief.select_observations(conn, "acc1")
    assert len(selected) == 4  # 2 margins + 2 returns -- NOT capped at 2 total
    categories = {config.METRIC_REGISTRY[o["subject"]].category for o in selected}
    assert categories == {"margins", "returns"}


def test_observation_selection_rule_ceiling_of_four(conn):
    _insert_filing(conn, "acc1")
    # 4 distinct metric categories, 2 metrics each -- 8 available, but the
    # rule ceiling caps metric_multi_year_extreme at 4 total regardless.
    metrics_by_category = {
        "margins": ["gross_margin", "operating_margin"],
        "returns": ["asset_turnover", "equity_multiplier"],
        "working_capital": ["days_receivables", "days_payables"],
        "capital_cash": ["capex_to_revenue", "capex_to_depreciation"],
    }
    for metrics in metrics_by_category.values():
        for m in metrics:
            _insert_observation(conn, "acc1", "metric_multi_year_extreme", m, "high", f"{m} extreme")

    selected = brief.select_observations(conn, "acc1")
    assert len(selected) == config.BRIEF_OBSERVATION_RULE_CEILING == 4
    categories = {config.METRIC_REGISTRY[o["subject"]].category for o in selected}
    assert len(categories) == 2  # only 2 of the 4 categories fit under the ceiling (2 slots each)


def test_finding_selection_caps_two_per_category(conn):
    _insert_filing(conn, "acc1")
    for i in range(4):
        _insert_finding(conn, "acc1", "litigation", "high", f"litigation matter {i}", "detail", "quote text long enough to pass min length easily")
    _insert_finding(conn, "acc1", "red_flag", "medium", "the only red flag", "detail", "quote text long enough to pass min length easily")

    selected = brief.select_findings(conn, "acc1")
    categories = [f["category"] for f in selected]
    assert categories.count("litigation") == 2
    assert "red_flag" in categories  # not crowded out


def test_selection_tie_break_is_severity_then_id(conn):
    _insert_filing(conn, "acc1")
    id_a = _insert_observation(conn, "acc1", "section_wording_changed", "Note A", "high", "a")
    id_b = _insert_observation(conn, "acc1", "section_disappeared", "Note B", "high", "b")
    selected_first = brief.select_observations(conn, "acc1")
    selected_second = brief.select_observations(conn, "acc1")
    assert [o["id"] for o in selected_first] == [o["id"] for o in selected_second]
    assert [o["id"] for o in selected_first] == sorted([id_a, id_b])


def test_observations_and_findings_are_bounded_and_deterministic(conn):
    _insert_filing(conn, "acc1")
    assert brief.select_observations(conn, "acc1") == []
    assert brief.select_findings(conn, "acc1") == []


# --- R4: universal checks ---


def test_sentence_without_refs_is_dropped():
    kept, reason = brief.verify_sentence({"type": "restatement", "text": "Something happened.", "refs": []}, {})
    assert kept is False
    assert "reference" in reason.lower()


def test_reference_to_unsupplied_item_is_dropped():
    supplied_index = {"obs:1": {"kind": "obs", "row": {}, "text": "real observation"}}
    sentence = {"type": "restatement", "text": "Something happened.", "refs": ["obs:999"]}
    kept, reason = brief.verify_sentence(sentence, supplied_index)
    assert kept is False
    assert "zero resolving" in reason


def test_predictive_language_dropped_any_type():
    supplied_index = {
        "obs:1": {"kind": "obs", "row": {}, "text": "a"},
        "obs:2": {"kind": "obs", "row": {}, "text": "b"},
    }
    sentence = {"type": "juxtaposition", "text": "This suggests further weakness ahead.", "refs": ["obs:1", "obs:2"]}
    kept, reason = brief.verify_sentence(sentence, supplied_index)
    assert kept is False
    assert "predictive" in reason


def test_unrecognised_type_dropped():
    sentence = {"type": "speculation", "text": "Something.", "refs": ["obs:1"]}
    kept, reason = brief.verify_sentence(sentence, {"obs:1": {"kind": "obs", "row": {}, "text": "x"}})
    assert kept is False
    assert "unrecognised type" in reason


# --- R4: per-type checks ---


def test_restatement_requires_exactly_one_reference():
    supplied_index = {
        "obs:1": {"kind": "obs", "row": {}, "text": "a"},
        "obs:2": {"kind": "obs", "row": {}, "text": "b"},
    }
    sentence = {"type": "restatement", "text": "Something happened.", "refs": ["obs:1", "obs:2"]}
    kept, reason = brief.verify_sentence(sentence, supplied_index)
    assert kept is False
    assert "exactly one" in reason


def test_juxtaposition_with_causal_connective_dropped():
    supplied_index = {
        "obs:1": {"kind": "obs", "row": {}, "text": "a"},
        "obs:2": {"kind": "obs", "row": {}, "text": "b"},
    }
    sentence = {"type": "juxtaposition", "text": "Margins fell due to rising costs.", "refs": ["obs:1", "obs:2"]}
    kept, reason = brief.verify_sentence(sentence, supplied_index)
    assert kept is False
    assert "causal" in reason


def test_grouping_requires_two_references_and_no_unsourced_number():
    supplied_index = {
        "finding:1": {"kind": "finding", "row": {}, "text": "matter one, no numbers here"},
        "finding:2": {"kind": "finding", "row": {}, "text": "matter two, also no numbers"},
    }
    ok_sentence = {"type": "grouping", "text": "Two matters share a common theme.", "refs": ["finding:1", "finding:2"]}
    kept, reason = brief.verify_sentence(ok_sentence, supplied_index)
    assert kept is True, reason

    bad_sentence = {"type": "grouping", "text": "Two matters total $999 million.", "refs": ["finding:1", "finding:2"]}
    kept, reason = brief.verify_sentence(bad_sentence, supplied_index)
    assert kept is False
    assert "not present" in reason


def test_sourced_causal_requires_causal_source():
    no_causal_source = {"obs:1": {"kind": "obs", "row": {}, "text": "gross_margin is the highest in 6 years."}}
    sentence = {"type": "sourced_causal", "text": "Margin rose because of cost discipline.", "refs": ["obs:1"]}
    kept, reason = brief.verify_sentence(sentence, no_causal_source)
    assert kept is False
    assert "sourced_causal requires" in reason

    causal_source = {"obs:1": {"kind": "obs", "row": {}, "text": "Tax provision increased due to a change in estimate."}}
    sentence2 = {"type": "sourced_causal", "text": "The tax provision increased due to a change in estimate.", "refs": ["obs:1"]}
    kept2, reason2 = brief.verify_sentence(sentence2, causal_source)
    assert kept2 is True, reason2


# --- generator/verifier schema validation ---


def test_validate_generator_schema_rejects_missing_sentence_keys():
    with pytest.raises(llm.InvalidResponseError):
        brief.validate_generator_schema({"material": True, "sentences": [{"type": "restatement"}]})


def test_validate_verifier_schema_rejects_malformed_entries():
    with pytest.raises(llm.InvalidResponseError):
        brief.validate_verifier_schema({"verifications": [{"verdict": "supported"}]})  # missing position


# --- caching, empty input, idempotency ---


def test_cache_prevents_second_call(conn):
    _insert_filing(conn, "acc1")
    _insert_observation(conn, "acc1", "metric_multi_year_extreme", "gross_margin", "high", "gross_margin is the highest in 6 years.")

    generator_template = brief.load_prompt_template(config.BRIEF_GENERATOR_PROMPT_NAME, config.BRIEF_GENERATOR_PROMPT_VERSION)
    verifier_template = brief.load_prompt_template(config.BRIEF_VERIFIER_PROMPT_NAME, config.BRIEF_VERIFIER_VERSION)

    sentence = {"type": "restatement", "text": "Gross margin reached a 6-year high.", "refs": ["obs:1"]}
    gen_response = _generator_response([sentence])
    ver_response = _verifier_response([{"position": 0, "verdict": "supported"}])
    fake = FakeRawClient([gen_response, ver_response])
    client = llm.LLMClient(raw_client=fake)

    first = brief.generate_brief(conn, _filing_row(), generator_template, verifier_template, client=client)
    assert first.status == "ok"
    assert len(first.sentences) == 1
    assert len(fake.calls) == 2

    second = brief.generate_brief(conn, _filing_row(), generator_template, verifier_template, client=client)
    assert second.status == "cached"
    assert len(fake.calls) == 2  # no new calls
    # "cached" returns stored DB rows (sentence_type/refs_json), not the
    # freshly-generated dict shape (type/refs) -- compare the meaningful
    # fields, not raw dict equality.
    assert len(second.sentences) == 1
    assert second.sentences[0]["sentence_type"] == first.sentences[0]["type"]
    assert second.sentences[0]["text"] == first.sentences[0]["text"]


def test_briefs_idempotent(conn):
    """Re-running makes zero calls and writes zero rows for an already-cached filing."""
    _insert_filing(conn, "acc1")
    _insert_observation(conn, "acc1", "metric_multi_year_extreme", "gross_margin", "high", "gross_margin is the highest in 6 years.")
    generator_template = brief.load_prompt_template(config.BRIEF_GENERATOR_PROMPT_NAME, config.BRIEF_GENERATOR_PROMPT_VERSION)
    verifier_template = brief.load_prompt_template(config.BRIEF_VERIFIER_PROMPT_NAME, config.BRIEF_VERIFIER_VERSION)
    sentence = {"type": "restatement", "text": "Gross margin reached a 6-year high.", "refs": ["obs:1"]}
    fake = FakeRawClient([_generator_response([sentence]), _verifier_response([{"position": 0, "verdict": "supported"}])])
    client = llm.LLMClient(raw_client=fake)
    brief.generate_brief(conn, _filing_row(), generator_template, verifier_template, client=client)

    before_briefs = conn.execute("SELECT COUNT(*) AS n FROM briefs").fetchone()["n"]
    before_sentences = conn.execute("SELECT COUNT(*) AS n FROM brief_sentences").fetchone()["n"]

    outcome = brief.generate_brief(conn, _filing_row(), generator_template, verifier_template, client=client)
    assert outcome.status == "cached"

    assert conn.execute("SELECT COUNT(*) AS n FROM briefs").fetchone()["n"] == before_briefs
    assert conn.execute("SELECT COUNT(*) AS n FROM brief_sentences").fetchone()["n"] == before_sentences


def test_no_observations_or_findings_makes_no_call(conn):
    _insert_filing(conn, "acc1")
    generator_template = brief.load_prompt_template(config.BRIEF_GENERATOR_PROMPT_NAME, config.BRIEF_GENERATOR_PROMPT_VERSION)
    verifier_template = brief.load_prompt_template(config.BRIEF_VERIFIER_PROMPT_NAME, config.BRIEF_VERIFIER_VERSION)
    fake = FakeRawClient([])  # would raise IndexError if called at all
    client = llm.LLMClient(raw_client=fake)

    outcome = brief.generate_brief(conn, _filing_row(), generator_template, verifier_template, client=client)
    assert outcome.status == "empty"
    assert len(fake.calls) == 0
    assert conn.execute("SELECT COUNT(*) AS n FROM llm_calls").fetchone()["n"] == 0


def test_material_false_is_valid_empty_brief(conn):
    _insert_filing(conn, "acc1")
    _insert_observation(conn, "acc1", "metric_multi_year_extreme", "gross_margin", "low", "gross_margin is unremarkable this period.")
    generator_template = brief.load_prompt_template(config.BRIEF_GENERATOR_PROMPT_NAME, config.BRIEF_GENERATOR_PROMPT_VERSION)
    verifier_template = brief.load_prompt_template(config.BRIEF_VERIFIER_PROMPT_NAME, config.BRIEF_VERIFIER_VERSION)
    fake = FakeRawClient([_generator_response([], material=False)])
    client = llm.LLMClient(raw_client=fake)

    outcome = brief.generate_brief(conn, _filing_row(), generator_template, verifier_template, client=client)
    assert outcome.status == "ok"
    assert outcome.sentences == []
    assert len(fake.calls) == 1  # no sentences survived R4 -> no verifier call needed


# --- R5: verifier fail-closed ---


def test_verifier_unparseable_output_fails_closed(conn):
    _insert_filing(conn, "acc1")
    _insert_observation(conn, "acc1", "metric_multi_year_extreme", "gross_margin", "high", "gross_margin is the highest in 6 years.")
    generator_template = brief.load_prompt_template(config.BRIEF_GENERATOR_PROMPT_NAME, config.BRIEF_GENERATOR_PROMPT_VERSION)
    verifier_template = brief.load_prompt_template(config.BRIEF_VERIFIER_PROMPT_NAME, config.BRIEF_VERIFIER_VERSION)
    sentence = {"type": "restatement", "text": "Gross margin reached a 6-year high.", "refs": ["obs:1"]}
    # Verifier returns garbage on both the initial attempt and its retry.
    fake = FakeRawClient([_generator_response([sentence]), "not valid json {{{", "still not valid json"])
    client = llm.LLMClient(raw_client=fake)

    outcome = brief.generate_brief(conn, _filing_row(), generator_template, verifier_template, client=client)
    assert outcome.status == "ok"
    assert outcome.sentences == []  # failed closed -- nothing survives an unparseable verifier response
    assert outcome.verifier_dropped == 1


def test_cross_filing_reference_rejected_by_validate(conn):
    _insert_filing(conn, "acc1")
    _insert_filing(conn, "acc2")
    obs_id = _insert_observation(conn, "acc2", "metric_multi_year_extreme", "gross_margin", "high", "belongs to acc2")
    conn.execute(
        "INSERT INTO briefs (accession_no, cik, prompt_name, prompt_version, verifier_version, model, input_hash, created_at) "
        "VALUES ('acc1', ?, 'filing_brief', 'v1', 'v1', 'claude-sonnet-5', 'hash1', '2026-01-01T00:00:00')",
        (AMZN_CIK,),
    )
    brief_id = conn.execute("SELECT id FROM briefs WHERE accession_no='acc1'").fetchone()["id"]
    conn.execute(
        "INSERT INTO brief_sentences (brief_id, position, sentence_type, text, refs_json) VALUES (?, 0, 'restatement', 'x', ?)",
        (brief_id, json.dumps([f"obs:{obs_id}"])),
    )
    conn.commit()

    from edgar import validate
    report = validate.run_validate(conn)
    assert len(report.brief_cross_filing_refs) == 1
    assert report.brief_cross_filing_refs[0]["source_accession_no"] == "acc2"
