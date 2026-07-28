"""Tests for edgar.analyze (SPEC-006). No network access anywhere here --
the LLM client is always a FakeRawClient (see test_llm.py) via edgar.llm.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from edgar import analyze, config, db, llm, section_store

AMZN_CIK = "0001018724"
NVDA_CIK = "0001045810"
FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _load_fixture_text(name: str) -> str:
    return (FIXTURES_DIR / name).read_text()


@pytest.fixture
def conn(tmp_path):
    db_path = tmp_path / "test.db"
    db.init_db(db_path)
    connection = db.get_connection(db_path)
    llm._reset_run_state()
    yield connection
    connection.close()


def _insert_filing(conn, accession_no, cik, form_type, filing_date, period_end, fiscal_year, fiscal_period):
    conn.execute(
        "INSERT INTO filings (accession_no, cik, form_type, filing_date, period_end, fiscal_year, fiscal_period, discovered_at, status) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'sectioned') ON CONFLICT(accession_no) DO NOTHING",
        (accession_no, cik, form_type, filing_date, period_end, fiscal_year, fiscal_period, f"{filing_date}T00:00:00"),
    )
    conn.commit()


def _insert_section(conn, accession_no, short_name, text, category=None):
    category = category or config.MENUCATEGORY_NOTES
    text_hash = section_store.write_section_text(text)
    cursor = conn.execute(
        "INSERT INTO sections (accession_no, category, short_name, source_file, position, text_hash) "
        "VALUES (?, ?, ?, 'R1.htm', 1, ?)",
        (accession_no, category, short_name, text_hash),
    )
    conn.commit()
    return cursor.lastrowid


VERBATIM_QUOTE_FROM_NOTE = (
    "The 2025 Tax Act increased our income tax provision, primarily due to a decrease "
    "in the foreign income deduction"
)

NOTE_TEXT = (
    "Income Taxes. In 2025, we recorded a net tax provision of $19.1 billion. The 2025 "
    "Tax Act increased our income tax provision, primarily due to a decrease in the "
    "foreign income deduction, and significantly decreased our cash taxes."
)


# --- R6: verbatim quote verification ---


def test_quote_verification_accepts_exact_match():
    quote = "The 2025 Tax Act increased our income tax provision, primarily due to a decrease"
    assert analyze.verify_quote(quote, NOTE_TEXT)


def test_quote_verification_accepts_whitespace_differences():
    # Extra/irregular whitespace in the "quote" but same words -- normalised
    # away, still a match.
    quote = "The  2025 Tax  Act increased   our income tax provision,\nprimarily due to a decrease"
    assert analyze.verify_quote(quote, NOTE_TEXT)


def test_quote_verification_rejects_fabricated_quote():
    quote = "This is a completely invented sentence that never appeared in the note at all"
    assert not analyze.verify_quote(quote, NOTE_TEXT)


def test_quote_verification_rejects_altered_wording():
    # Same idea, different words -- not just whitespace -- must fail.
    quote = "The 2025 Tax Act dramatically reduced our income tax provision overall"
    assert not analyze.verify_quote(quote, NOTE_TEXT)


def test_quote_verification_rejects_too_short_quote():
    # "the 2025" appears verbatim but is far under QUOTE_MIN_LENGTH.
    assert len("the 2025") < config.QUOTE_MIN_LENGTH
    assert not analyze.verify_quote("the 2025", NOTE_TEXT)


# --- R4: section selection ---


def test_select_candidate_sections_excludes_boilerplate(conn):
    _insert_filing(conn, "acc1", AMZN_CIK, "10-K", "2026-02-06", "2025-12-31", 2025, "FY")
    _insert_section(conn, "acc1", "Income Taxes", NOTE_TEXT)
    boilerplate = next(iter(config.BOILERPLATE_NOTE_NAMES))
    _insert_section(conn, "acc1", boilerplate, "Some boilerplate disclosure text here.")

    candidates = analyze.select_candidate_sections(conn, tickers=["AMZN"])
    names = {c["short_name"] for c in candidates}
    assert "Income Taxes" in names
    assert boilerplate not in names


def test_select_candidate_sections_excludes_before_analysis_start_date(conn):
    _insert_filing(conn, "acc_old", AMZN_CIK, "10-K", "2020-02-01", "2019-12-31", 2019, "FY")
    _insert_section(conn, "acc_old", "Income Taxes", NOTE_TEXT)
    candidates = analyze.select_candidate_sections(conn, tickers=["AMZN"])
    assert candidates == []


def test_select_candidate_sections_excludes_policies_category(conn):
    _insert_filing(conn, "acc1", AMZN_CIK, "10-K", "2026-02-06", "2025-12-31", 2025, "FY")
    _insert_section(conn, "acc1", "Significant Accounting Policies", NOTE_TEXT, category=config.MENUCATEGORY_POLICIES)
    candidates = analyze.select_candidate_sections(conn, tickers=["AMZN"])
    assert candidates == []


def test_sample_sections_deterministic():
    sections = [{"id": i} for i in range(20)]
    sample1 = analyze.sample_sections(sections, 5, seed=42)
    sample2 = analyze.sample_sections(sections, 5, seed=42)
    assert sample1 == sample2
    assert len(sample1) == 5


def test_sample_sections_different_seed_different_sample():
    sections = [{"id": i} for i in range(20)]
    sample1 = analyze.sample_sections(sections, 5, seed=1)
    sample2 = analyze.sample_sections(sections, 5, seed=2)
    assert sample1 != sample2


def test_format_fiscal_period():
    assert analyze.format_fiscal_period(2025, "FY") == "FY2025"
    assert analyze.format_fiscal_period(2026, "Q2") == "Q2 FY2026"
    assert analyze.format_fiscal_period(None, None) == "unknown fiscal period"


# --- R8: dry run makes no calls ---


def test_run_analysis_dry_run_makes_no_calls_and_estimates_cost(conn):
    _insert_filing(conn, "acc1", AMZN_CIK, "10-K", "2026-02-06", "2025-12-31", 2025, "FY")
    _insert_section(conn, "acc1", "Income Taxes", NOTE_TEXT)

    stats = analyze.run_analysis(conn, tickers=["AMZN"], execute=False)
    assert stats.dry_run is True
    assert stats.candidates == 1
    assert stats.processed == 1
    assert stats.estimated_cost_usd > 0
    assert conn.execute("SELECT COUNT(*) AS n FROM llm_calls").fetchone()["n"] == 0
    assert conn.execute("SELECT COUNT(*) AS n FROM analyses").fetchone()["n"] == 0


# --- full execute flow, fake client, no network ---


class FakeRawClient:
    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.calls = 0

    def messages_create(self, model, max_tokens, prompt):
        self.calls += 1
        return self._responses.pop(0), 1000, 200, "end_turn"


# Recorded fixture (Testing Requirements: "one with a fabricated quote") --
# one finding whose quote is a real verbatim substring of NOTE_TEXT below,
# one whose quote is invented. Exercised end to end here, against a real
# source note, so verify_quote's discard path is proven, not just asserted.
GOOD_RESPONSE_WITH_REAL_AND_FAKE_QUOTE = _load_fixture_text("llm_fabricated_quote_response.json")


def test_execute_writes_kept_findings_and_discards_fabricated_ones(conn):
    _insert_filing(conn, "acc1", AMZN_CIK, "10-K", "2026-02-06", "2025-12-31", 2025, "FY")
    _insert_section(conn, "acc1", "Income Taxes", NOTE_TEXT)
    fake = FakeRawClient([GOOD_RESPONSE_WITH_REAL_AND_FAKE_QUOTE])
    client = llm.LLMClient(raw_client=fake)

    stats = analyze.run_analysis(conn, tickers=["AMZN"], execute=True, client=client)
    assert stats.calls_made == 1
    assert stats.findings_returned == 2
    assert stats.findings_kept == 1
    assert stats.findings_discarded == 1

    findings = conn.execute("SELECT * FROM findings").fetchall()
    assert len(findings) == 1
    assert "Tax Act" in findings[0]["headline"]


def test_rerunning_unchanged_prompt_makes_zero_calls_and_writes_zero_rows(conn):
    _insert_filing(conn, "acc1", AMZN_CIK, "10-K", "2026-02-06", "2025-12-31", 2025, "FY")
    _insert_section(conn, "acc1", "Income Taxes", NOTE_TEXT)
    fake = FakeRawClient([GOOD_RESPONSE_WITH_REAL_AND_FAKE_QUOTE, GOOD_RESPONSE_WITH_REAL_AND_FAKE_QUOTE])
    client = llm.LLMClient(raw_client=fake)

    first = analyze.run_analysis(conn, tickers=["AMZN"], execute=True, client=client)
    assert first.calls_made == 1
    findings_after_first = conn.execute("SELECT COUNT(*) AS n FROM findings").fetchone()["n"]

    second = analyze.run_analysis(conn, tickers=["AMZN"], execute=True, client=client)
    assert second.calls_made == 0
    assert second.cache_hits == 1
    assert fake.calls == 1  # never called a second time
    findings_after_second = conn.execute("SELECT COUNT(*) AS n FROM findings").fetchone()["n"]
    assert findings_after_second == findings_after_first  # no duplicate rows


# --- SPEC-006A: budget guardrails ---


def _insert_many_sections(conn, n, accession_no="acc1"):
    _insert_filing(conn, accession_no, AMZN_CIK, "10-K", "2026-02-06", "2025-12-31", 2025, "FY")
    for i in range(n):
        _insert_section(conn, accession_no, f"Note {i}", NOTE_TEXT)


def test_per_run_cost_ceiling_stops_run(conn, monkeypatch):
    # Shrink LLM_MAX_OUTPUT_TOKENS so the pre-call cost ESTIMATE (which uses
    # it) is in the same ballpark as the fake client's fixed ACTUAL token
    # return (1000 in / 200 out) -- otherwise the estimate (driven by the
    # real 4096-token ceiling) swamps a few real calls' worth of spend and
    # the ceiling either refuses every call outright or none for a long time.
    monkeypatch.setattr(config, "LLM_MAX_OUTPUT_TOKENS", 200)
    _insert_many_sections(conn, 5)
    empty = _load_fixture_text("llm_empty_response.json")
    fake = FakeRawClient([empty] * 5)
    client = llm.LLMClient(raw_client=fake)

    real_call_cost = llm.compute_cost(config.LLM_MODEL, 1000, 200)
    ceiling = real_call_cost * 2.5  # room for ~2 real calls, not a 3rd

    stats = analyze.run_analysis(
        conn, tickers=["AMZN"], execute=True, client=client, max_run_cost_usd=ceiling,
    )

    assert 1 <= stats.calls_made < 5  # stopped before exhausting every candidate
    assert stats.stopped_reason is not None
    assert "per-run cost ceiling" in stats.stopped_reason
    assert stats.run_cost_usd <= ceiling + 1e-9
    assert stats.total_cost_usd == pytest.approx(stats.run_cost_usd)  # fresh ledger, this run is the whole total


def test_call_count_ceiling_stops_run(conn):
    _insert_many_sections(conn, 5)
    empty = _load_fixture_text("llm_empty_response.json")
    fake = FakeRawClient([empty] * 5)
    client = llm.LLMClient(raw_client=fake)

    stats = analyze.run_analysis(
        conn, tickers=["AMZN"], execute=True, client=client, max_calls_per_run=2,
    )

    assert stats.calls_made == 2
    assert stats.processed < stats.candidates
    assert stats.stopped_reason is not None
    assert "call-count ceiling" in stats.stopped_reason


def test_partial_run_reports_completed_work(conn):
    """AC2/AC5: a run stopped early by a guard still reports what it
    completed, and the work it did complete is really on disk -- not rolled
    back just because the run as a whole didn't finish."""
    _insert_many_sections(conn, 5)
    empty = _load_fixture_text("llm_empty_response.json")
    fake = FakeRawClient([empty] * 5)
    client = llm.LLMClient(raw_client=fake)

    stats = analyze.run_analysis(
        conn, tickers=["AMZN"], execute=True, client=client, max_calls_per_run=2,
    )

    assert stats.candidates == 5
    assert stats.processed == 3  # 2 real calls + the one that got refused
    assert stats.calls_made == 2
    assert stats.refused == 1
    assert stats.stopped_reason is not None
    analyses = conn.execute("SELECT COUNT(*) AS n FROM analyses").fetchone()["n"]
    assert analyses == 2  # the 2 completed calls' work is really persisted


def test_scheduled_run_uses_lower_ceiling(conn, monkeypatch):
    monkeypatch.setattr(config, "LLM_MAX_OUTPUT_TOKENS", 200)
    monkeypatch.setattr(config, "LLM_SCHEDULED_RUN_MAX_COST_USD", 0.006)
    _insert_many_sections(conn, 5)
    empty = _load_fixture_text("llm_empty_response.json")
    fake = FakeRawClient([empty] * 5)
    client = llm.LLMClient(raw_client=fake)

    stats = analyze.run_analysis(
        conn, tickers=["AMZN"], execute=True, client=client,
        max_run_cost_usd=1000.0,  # a huge explicit override -- scheduled must clamp below it anyway
        scheduled=True,
    )

    assert stats.calls_made < 5
    assert stats.stopped_reason is not None
    assert stats.run_cost_usd <= config.LLM_SCHEDULED_RUN_MAX_COST_USD + 1e-9


def test_scheduled_run_refuses_sample():
    with pytest.raises(ValueError, match="scheduled runs must never use --sample"):
        analyze.run_analysis(None, tickers=["AMZN"], execute=True, sample=5, scheduled=True)


def test_cache_invalidation_reported_on_version_bump(conn):
    _insert_filing(conn, "acc1", AMZN_CIK, "10-K", "2026-02-06", "2025-12-31", 2025, "FY")
    section_id = _insert_section(conn, "acc1", "Income Taxes", NOTE_TEXT)

    # A prior analysis under an OLD prompt version -- config's CURRENT
    # version is config.SECTION_ANALYSIS_PROMPT_VERSION, deliberately not
    # "v1" here (this project's real current version is v3, but hardcoding
    # "not the current one" keeps this test correct even if that changes).
    old_version = "not-" + config.SECTION_ANALYSIS_PROMPT_VERSION
    conn.execute(
        "INSERT INTO analyses (section_id, prompt_name, prompt_version, model, input_hash, output_json, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, '2025-01-01T00:00:00')",
        (
            section_id, config.SECTION_ANALYSIS_PROMPT_NAME, old_version, config.LLM_MODEL,
            "some-old-input-hash-that-will-never-match-the-current-render", "{}",
        ),
    )
    conn.commit()

    stats = analyze.run_analysis(conn, tickers=["AMZN"], execute=False)

    assert stats.cache_hits_dry == 0  # no analysis under the CURRENT version/hash
    assert stats.new_calls_dry == 1
    assert stats.invalidated_by_version_bump == 1
    assert stats.invalidated_cost_usd > 0
    assert stats.version_bumped is True
    assert stats.prior_prompt_versions == (old_version,)


def test_empty_findings_response_is_valid_end_to_end(conn):
    _insert_filing(conn, "acc1", AMZN_CIK, "10-K", "2026-02-06", "2025-12-31", 2025, "FY")
    _insert_section(conn, "acc1", "Income Taxes", NOTE_TEXT)
    fake = FakeRawClient([_load_fixture_text("llm_empty_response.json")])
    client = llm.LLMClient(raw_client=fake)

    stats = analyze.run_analysis(conn, tickers=["AMZN"], execute=True, client=client)
    assert stats.calls_made == 1
    assert stats.findings_returned == 0
    assert stats.findings_kept == 0
    assert conn.execute("SELECT COUNT(*) AS n FROM findings").fetchone()["n"] == 0


def test_real_prompt_file_sends_the_output_schema_to_the_model():
    """Regression, SPEC-006 v1->v2: the schema must appear BELOW the
    '## Template' marker, because everything above it is stripped by
    load_prompt_template and never reaches the model.

    v1 documented the schema only in the header and shipped a prompt asking
    the model to return "the JSON object described in this prompt's own
    instructions" -- instructions it had never been shown. Every other test in
    this file supplies its own inline template string, so none of them
    exercised the real file's header/template split, and the whole suite
    passed while the deployed prompt could not produce a parseable non-empty
    finding. This test reads the actual configured prompt file.
    """
    template = analyze.load_prompt_template(
        config.SECTION_ANALYSIS_PROMPT_NAME, config.SECTION_ANALYSIS_PROMPT_VERSION
    )
    for field in ("material", "findings", "category", "severity", "headline", "detail", "quote"):
        assert field in template, f"schema field {field!r} is never sent to the model"
    for category in config.FINDING_CATEGORIES:
        assert category in template, f"category {category!r} is never sent to the model"
    for severity in config.FINDING_SEVERITIES:
        assert severity in template, f"severity {severity!r} is never sent to the model"
    for token in ("%%NOTE_TEXT%%", "%%NOTE_NAME%%", "%%COMPANY%%"):
        assert token in template, f"interpolation token {token} missing from template"


# --- numeric support (measured, not enforced) ---

NUMERIC_SOURCE = (
    "Revenue from one customer was 16% (primarily included in the CNBU segment) of total "
    "revenue for the first nine months of 2025. Excluded obligations consisted of $1,160 "
    "million of finance lease obligations over a weighted-average period of 15 years."
)


def test_numeric_support_accepts_numbers_present_in_source():
    result = analyze.check_numeric_support(
        "Customer concentration reached 16% of revenue",
        "The note discloses 16% for the first nine months of 2025.",
        "",  # no quote -- both numbers should land in note-only, not unsupported
        NUMERIC_SOURCE,
    )
    assert result.unsupported == []
    assert result.checked == result.supported == 2  # 16 and 2025, deduplicated
    assert result.supported_in_note_only == 2
    assert result.supported_in_quote == 0


def test_numeric_support_credits_quote_over_note_only():
    """A number present in the finding's OWN quote is the strongest tier --
    must be counted as supported_in_quote, not supported_in_note_only, even
    though the same number also appears in the wider note."""
    result = analyze.check_numeric_support(
        "Customer concentration reached 16% of revenue",
        "Detail text with nothing further.",
        "Revenue from one customer was 16% of total revenue for the period.",
        NUMERIC_SOURCE,
    )
    assert result.checked == 1
    assert result.supported_in_quote == 1
    assert result.supported_in_note_only == 0
    assert result.unsupported == []


def test_numeric_support_flags_a_derived_number_absent_from_source():
    """$1.16 billion is the same quantity as the note's '$1,160 million', but
    the model rewrote the scale -- so the literal '1.16' is not in the source.
    Exactly the ambiguous case NUMERIC_SUPPORT_ENFORCE exists to leave alone:
    flagged, counted, and deliberately not discarded."""
    result = analyze.check_numeric_support(
        "Company disclosed $1.16 billion of excluded lease obligations",
        "The obligations run over 15 years.",
        "",
        NUMERIC_SOURCE,
    )
    assert "1.16" in result.unsupported
    assert result.supported == result.checked - len(result.unsupported)


def test_numeric_support_normalises_thousands_separators():
    result = analyze.check_numeric_support(
        "Obligations of $1160 million disclosed", "Over 15 years.", "", NUMERIC_SOURCE
    )
    assert result.unsupported == []  # '1160' matches the source's '1,160'


def test_numeric_support_does_not_match_a_number_inside_a_longer_number():
    """'116' must not count as supported merely because '1,160' contains it --
    a substring match would make the whole metric meaningless."""
    result = analyze.check_numeric_support(
        "A finding about 116 things", "Detail about 116.", "", NUMERIC_SOURCE
    )
    assert result.unsupported == ["116"]


def test_numeric_support_tolerates_redundant_decimal_tail():
    result = analyze.check_numeric_support(
        "Concentration of 16.0% of revenue", "Nothing further.", "", NUMERIC_SOURCE
    )
    assert result.unsupported == []


# --- 2026-07-28: ordinal-word normalisation (real false positive found live) ---
# A finding wrote "Q4 FY2026" (digit form) while the source spelled out "the
# fourth quarter of fiscal year 2026" -- extract_numeric_tokens only ever
# produces digit-form tokens, so the gap was entirely on the source side.


def test_numeric_support_recognises_spelled_out_ordinal_in_source():
    source = "We expect to commence new leases in the fourth quarter of fiscal year 2026."
    result = analyze.check_numeric_support(
        "New leases expected to commence in Q4 FY2026", "Detail text.", "", source
    )
    assert result.unsupported == []
    assert result.supported_in_note_only == 2  # "4" (fourth) and "2026"


def test_numeric_support_ordinal_normalisation_never_touches_quote_verification():
    # The whole point of scoping this to numeric-support only: verify_quote
    # must stay strictly verbatim, never treat "fourth" and "4" as equal.
    source = "We expect to commence new leases in the fourth quarter of fiscal year 2026."
    assert not analyze.verify_quote("We expect to commence new leases in Q4 FY2026", source)


# --- 2026-07-28: derived-sum verification ---
# Absent-from-source numbers get one more check: are they a correct subset-sum
# of the quote's OWN numbers? A real customer-concentration case, live: three
# customers disclosed at 27%, 18%, 12% in the quote; the model's headline
# summed them to "57% combined" -- correct arithmetic, not in the source
# verbatim. The DANGEROUS case this exists to catch is the same shape but
# wrong: a headline claiming "58%" for that same quote must stay unsupported,
# since the sum genuinely doesn't check out.

_CONCENTRATION_QUOTE = "Three direct customers accounted for 27%, 18% and 12% of our accounts receivable balance."


def test_numeric_support_verifies_correct_derived_sum():
    result = analyze.check_numeric_support(
        "Three customers account for 57% combined of accounts receivable",
        "Detail text.",
        _CONCENTRATION_QUOTE,
        NUMERIC_SOURCE,
    )
    assert "57" in result.derived_verified
    assert "57" not in result.unsupported
    assert result.derived_verified_count == 1


def test_numeric_support_rejects_incorrect_looking_sum():
    """The dangerous case: a sum that LOOKS plausible (same shape as the real
    one above) but is arithmetically wrong must stay unsupported, never get
    waved through as derived-verified just because it resembles a sum."""
    result = analyze.check_numeric_support(
        "Three customers account for 58% combined of accounts receivable",
        "Detail text.",
        _CONCENTRATION_QUOTE,
        NUMERIC_SOURCE,
    )
    assert "58" not in result.derived_verified
    assert "58" in result.unsupported


def test_numeric_support_derived_sum_requires_at_least_two_addends():
    # A single quote number equal to the token is already handled by
    # supported_in_quote -- derived-sum must not redundantly "verify" it via
    # a size-1 "sum", which would just be membership wearing a different name.
    result = analyze.check_numeric_support(
        "Concentration reached 27%", "Detail text.", _CONCENTRATION_QUOTE, NUMERIC_SOURCE
    )
    assert result.supported_in_quote == 1
    assert result.derived_verified == []


def test_unsupported_number_is_counted_but_finding_is_kept(conn):
    """The whole point of the metric while NUMERIC_SUPPORT_ENFORCE is False:
    the finding survives, the number is counted against the rate."""
    assert config.NUMERIC_SUPPORT_ENFORCE is False
    _insert_filing(conn, "acc1", AMZN_CIK, "10-K", "2026-02-06", "2025-12-31", 2025, "FY")
    _insert_section(conn, "acc1", "Income Taxes", NOTE_TEXT)
    response = json.dumps(
        {
            "material": True,
            "findings": [
                {
                    "category": "accounting_change",
                    "severity": "medium",
                    "headline": "Tax Act raised the provision by $999,999 million",
                    "detail": "A figure of $999,999 million appears nowhere in the note.",
                    "quote": VERBATIM_QUOTE_FROM_NOTE,
                }
            ],
        }
    )
    client = llm.LLMClient(raw_client=FakeRawClient([response]))

    stats = analyze.run_analysis(conn, tickers=["AMZN"], execute=True, client=client)
    assert stats.findings_kept == 1
    assert stats.findings_discarded == 0
    assert stats.findings_with_unsupported_numbers == 1
    assert stats.numeric_tokens_checked > stats.numeric_tokens_supported
    assert stats.numeric_support_rate is not None and stats.numeric_support_rate < 1.0
    assert conn.execute("SELECT COUNT(*) AS n FROM findings").fetchone()["n"] == 1


def test_numeric_support_rate_is_none_when_nothing_checked(conn):
    _insert_filing(conn, "acc1", AMZN_CIK, "10-K", "2026-02-06", "2025-12-31", 2025, "FY")
    _insert_section(conn, "acc1", "Income Taxes", NOTE_TEXT)
    client = llm.LLMClient(raw_client=FakeRawClient([_load_fixture_text("llm_empty_response.json")]))
    stats = analyze.run_analysis(conn, tickers=["AMZN"], execute=True, client=client)
    assert stats.numeric_tokens_checked == 0
    assert stats.numeric_support_rate is None


def test_real_prompt_file_defines_every_category_below_the_marker():
    """v3: the category vocabulary is only useful if the model is told what
    the words mean, and (per the v1 lesson) that has to be below the marker."""
    template = analyze.load_prompt_template(
        config.SECTION_ANALYSIS_PROMPT_NAME, config.SECTION_ANALYSIS_PROMPT_VERSION
    )
    body = template[template.index("The six") :] if "The six" in template else template
    for category in config.FINDING_CATEGORIES:
        assert f'"{category}" —' in body or f'"{category}" -' in body, f"{category} has no definition"
