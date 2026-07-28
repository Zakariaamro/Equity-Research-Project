"""Tests for edgar.llm (SPEC-006).

R1: "Build this first. Do not write the API client until this exists and is
tested." The first section of this file covers the ledger and budget cap
ONLY -- no network, no Anthropic client, because at the point that section
was written, none existed yet. Everything below "R2: caching, cost
estimation, client" tests the API client via a fake RawLLMClient (per
llm.RawLLMClient's Protocol) -- no real network access anywhere in this file
(SPEC-006 constraint: "No network in unit tests").
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from edgar import config, db, llm

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _load_fixture_text(name: str) -> str:
    """Raw text (as the API would return it), not parsed -- these fixtures
    are recorded RESPONSES, so tests exercise the same json.loads() path
    production code does."""
    return (FIXTURES_DIR / name).read_text()


class FakeRawClient:
    """Implements llm.RawLLMClient's Protocol with a canned response queue --
    no network, no `anthropic` import.

    Each queued response is either a bare `str` (stop_reason defaults to
    "end_turn", the common case) or a `(text, stop_reason)` tuple, for tests
    that need to simulate a specific stop_reason -- most importantly
    "max_tokens" (2026-07-27 live-error-analysis fix: truncation is read
    from this field now, so tests exercising it must be able to set it)."""

    def __init__(self, responses: list[str | tuple[str, str]]) -> None:
        self._responses = list(responses)
        self.calls: list[tuple[str, int, str]] = []

    def messages_create(self, model: str, max_tokens: int, prompt: str) -> tuple[str, int, int, str | None]:
        self.calls.append((model, max_tokens, prompt))
        response = self._responses.pop(0)
        if isinstance(response, tuple):
            text, stop_reason = response
        else:
            text, stop_reason = response, "end_turn"
        return text, 1000, 200, stop_reason


def _insert_section(conn, accession_no="acc1", cik="0001018724", short_name="Income Taxes") -> int:
    conn.execute(
        "INSERT INTO filings (accession_no, cik, form_type, filing_date, period_end, discovered_at, status) "
        "VALUES (?, ?, '10-K', '2026-02-06', '2025-12-31', '2026-02-06T00:00:00', 'sectioned') "
        "ON CONFLICT(accession_no) DO NOTHING",
        (accession_no, cik),
    )
    cursor = conn.execute(
        "INSERT INTO sections (accession_no, category, short_name, source_file, position, text_hash) "
        "VALUES (?, 'Notes', ?, 'R1.htm', 1, 'deadbeef')",
        (accession_no, short_name),
    )
    conn.commit()
    return cursor.lastrowid


@pytest.fixture
def conn(tmp_path):
    db_path = tmp_path / "test.db"
    db.init_db(db_path)
    connection = db.get_connection(db_path)
    llm._reset_run_state()
    yield connection
    connection.close()


def test_compute_cost_matches_pricing_table(conn):
    # claude-sonnet-5: $2/$10 per MTok (introductory rate, in effect through
    # 2026-08-31 -- SPEC-006A pricing fix).
    cost = llm.compute_cost("claude-sonnet-5", input_tokens=1_000_000, output_tokens=1_000_000)
    assert cost == pytest.approx(2.00 + 10.00)


def test_compute_cost_unpriced_model_raises(conn):
    with pytest.raises(ValueError, match="No LLM_PRICING entry"):
        llm.compute_cost("claude-nonexistent-model", 100, 100)


def test_total_spent_zero_on_empty_ledger(conn):
    assert llm.total_spent(conn) == 0.0


def test_record_result_appends_row_and_updates_total(conn):
    call_id = llm.record_result(
        conn, "claude-sonnet-5", input_tokens=1000, output_tokens=200, status="ok",
        prompt_name="section_analysis", prompt_version="v1",
    )
    assert call_id is not None
    expected_cost = 1000 / 1e6 * 2.00 + 200 / 1e6 * 10.00
    assert llm.total_spent(conn) == pytest.approx(expected_cost)

    row = conn.execute("SELECT * FROM llm_calls WHERE id = ?", (call_id,)).fetchone()
    assert row["status"] == "ok"
    assert row["model"] == "claude-sonnet-5"
    assert row["cost_usd"] == pytest.approx(expected_cost)


def test_record_result_rejects_invalid_status(conn):
    with pytest.raises(ValueError, match="status must be"):
        llm.record_result(conn, "claude-sonnet-5", 100, 100, status="refused")


# --- R1 AC2: the cap is proven ---


def test_budget_cap_refuses_call(conn):
    # Record enough real spend to sit just under the budget, then attempt a
    # call whose estimated cost would push it over.
    input_rate = config.LLM_PRICING["claude-sonnet-5"].input_per_mtok
    big_input_tokens = int(config.LLM_BUDGET_USD * 0.999 / input_rate * 1_000_000)
    llm.record_result(conn, "claude-sonnet-5", input_tokens=big_input_tokens, output_tokens=0, status="ok")
    spent_before = llm.total_spent(conn)
    assert spent_before < config.LLM_BUDGET_USD

    with pytest.raises(llm.BudgetExceededError):
        llm.ensure_budget_available(
            conn, "claude-sonnet-5", estimated_input_tokens=1_000_000, estimated_output_tokens=1_000_000,
            prompt_name="section_analysis", prompt_version="v1",
        )
    # Spend must not have changed -- refused calls cost nothing.
    assert llm.total_spent(conn) == pytest.approx(spent_before)


def test_ledger_records_refusals(conn):
    # Force a refusal by setting recorded spend right at the budget.
    input_rate = config.LLM_PRICING["claude-sonnet-5"].input_per_mtok
    llm.record_result(
        conn, "claude-sonnet-5",
        input_tokens=int(config.LLM_BUDGET_USD / input_rate * 1_000_000), output_tokens=0, status="ok",
    )
    with pytest.raises(llm.BudgetExceededError):
        llm.ensure_budget_available(conn, "claude-sonnet-5", 1000, 1000)

    refused = conn.execute("SELECT * FROM llm_calls WHERE status = 'refused'").fetchall()
    assert len(refused) == 1
    assert refused[0]["cost_usd"] == 0.0
    assert refused[0]["note"] is not None and "budget" in refused[0]["note"]


def test_ensure_budget_available_allows_call_under_cap(conn):
    # Should not raise, and should not write any row (only actual calls or
    # refusals write rows -- a successful budget check is silent).
    llm.ensure_budget_available(conn, "claude-sonnet-5", estimated_input_tokens=1000, estimated_output_tokens=200)
    assert conn.execute("SELECT COUNT(*) AS n FROM llm_calls").fetchone()["n"] == 0


def test_warn_fraction_logged_once_per_run(conn, caplog):
    import logging
    caplog.set_level(logging.WARNING, logger="edgar.llm")
    # Spend to just past the 75% warn fraction.
    input_rate = config.LLM_PRICING["claude-sonnet-5"].input_per_mtok
    warn_threshold_tokens = int(config.LLM_BUDGET_USD * config.LLM_WARN_FRACTION * 1.01 / input_rate * 1_000_000)
    llm.record_result(conn, "claude-sonnet-5", input_tokens=warn_threshold_tokens, output_tokens=0, status="ok")

    llm.ensure_budget_available(conn, "claude-sonnet-5", 100, 100)
    llm.ensure_budget_available(conn, "claude-sonnet-5", 100, 100)
    warnings = [r for r in caplog.records if "approaching the cap" in r.message]
    assert len(warnings) == 1  # only once, not once per call


def test_spend_summary_reports_total_and_breakdown(conn):
    llm.record_result(
        conn, "claude-sonnet-5", input_tokens=1000, output_tokens=200, status="ok",
        prompt_name="section_analysis", prompt_version="v1",
    )
    llm.record_result(
        conn, "claude-sonnet-5", input_tokens=500, output_tokens=0, status="error",
        prompt_name="section_analysis", prompt_version="v1",
    )
    summary = llm.spend_summary(conn)
    assert summary["total_spent"] == pytest.approx(llm.total_spent(conn))
    assert summary["budget"] == config.LLM_BUDGET_USD
    assert summary["remaining"] == pytest.approx(config.LLM_BUDGET_USD - summary["total_spent"])
    assert len(summary["breakdown"]) == 2  # one 'ok' group, one 'error' group


# --- R2: caching, cost estimation, client (fake RawLLMClient -- no network) ---

# Recorded response fixtures (Testing Requirements: "one good, one with a
# fabricated quote, and one with no findings"). GOOD_RESPONSE and
# EMPTY_RESPONSE below use the simple good/empty fixtures; the fabricated-
# quote fixture is exercised end to end in test_analyze.py, where there is a
# real source note to verify the quote against.
GOOD_RESPONSE = _load_fixture_text("llm_good_response.json")
EMPTY_RESPONSE = _load_fixture_text("llm_empty_response.json")


def test_estimate_tokens_uses_chars_per_token():
    text = "a" * 400
    assert llm.estimate_tokens(text) == round(400 / config.CHARS_PER_TOKEN_ESTIMATE)


def test_estimate_cost_returns_tokens_and_cost():
    result = llm.estimate_cost("some prompt text", model="claude-sonnet-5", estimated_output_tokens=300)
    assert result["output_tokens"] == 300
    assert result["input_tokens"] == llm.estimate_tokens("some prompt text")
    assert result["cost_usd"] == pytest.approx(llm.compute_cost("claude-sonnet-5", result["input_tokens"], 300))


def test_compute_input_hash_changes_with_interpolated_content():
    # Same template-shaped text, different interpolated note text -> must
    # NOT collide (R2: hash covers the fully rendered prompt, not the template).
    h1 = llm.compute_input_hash("Analyze this note: Income Taxes disclosure A", "claude-sonnet-5", "v1")
    h2 = llm.compute_input_hash("Analyze this note: Income Taxes disclosure B", "claude-sonnet-5", "v1")
    assert h1 != h2


def test_validate_output_schema_accepts_empty_findings():
    llm.validate_output_schema(json.loads(EMPTY_RESPONSE))  # must not raise


def test_validate_output_schema_rejects_bad_category():
    with pytest.raises(llm.InvalidResponseError):
        llm.validate_output_schema({"material": True, "findings": [{
            "category": "not_a_real_category", "severity": "low", "headline": "x", "detail": "y", "quote": "z",
        }]})


def test_missing_api_key_fails_fast(monkeypatch):
    monkeypatch.delenv("EQUITY_RESEARCH_ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="EQUITY_RESEARCH_ANTHROPIC_API_KEY"):
        llm.LLMClient()  # no raw_client override -> tries to build the real one -> needs the key


# --- SPEC-006A L9: environment canary ---


def test_env_canary_warns_when_anthropic_api_key_set(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-not-this-projects-key")
    warning = llm.check_environment_canary()
    assert warning is not None
    assert "ANTHROPIC_API_KEY" in warning
    assert "EQUITY_RESEARCH_ANTHROPIC_API_KEY" in warning  # names what this project reads instead


def test_env_canary_silent_when_unset(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert llm.check_environment_canary() is None


def test_env_canary_silent_when_only_project_key_set(monkeypatch):
    # Setting the project's OWN key must not trip the canary -- it exists to
    # catch the generic SDK variable, not the project's correctly-scoped one.
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("EQUITY_RESEARCH_ANTHROPIC_API_KEY", "sk-ant-this-projects-key")
    assert llm.check_environment_canary() is None


def test_cache_prevents_second_call(conn):
    section_id = _insert_section(conn)
    fake = FakeRawClient([GOOD_RESPONSE, GOOD_RESPONSE])  # 2nd would only be consumed on a real 2nd call
    client = llm.LLMClient(model="claude-sonnet-5", raw_client=fake)

    first = llm.get_or_create_analysis(
        conn, section_id, "section_analysis", "v1", "rendered prompt text", client=client,
    )
    assert first.status == "ok"
    assert len(fake.calls) == 1

    second = llm.get_or_create_analysis(
        conn, section_id, "section_analysis", "v1", "rendered prompt text", client=client,
    )
    assert second.status == "cached"
    assert len(fake.calls) == 1  # no second call made
    assert second.output == first.output


def test_invalid_json_retried_once_then_error(conn):
    section_id = _insert_section(conn)
    fake = FakeRawClient(["not valid json {{{", "still not valid json"])
    client = llm.LLMClient(raw_client=fake)

    outcome = llm.get_or_create_analysis(conn, section_id, "section_analysis", "v1", "prompt text", client=client)
    assert outcome.status == "error"
    assert len(fake.calls) == 2  # one attempt + one retry, per config.LLM_JSON_PARSE_MAX_RETRIES

    # 2026-07-28 live-error-analysis fix: each of the 2 REAL (billed) attempts
    # gets its own ledger row -- never folded into a single row for whichever
    # attempt the loop happened to end on.
    error_rows = conn.execute("SELECT * FROM llm_calls WHERE status = 'error'").fetchall()
    assert len(error_rows) == 2


# --- 2026-07-28: the ledger's core invariant, as a property test ---
#
# Found live, against the real API, in this project's first execution of the
# retry path: a truncated-then-successfully-retried section wrote only ONE
# `llm_calls` row (the retry's), silently dropping the wasted first attempt's
# real, billed cost. This is the test that should have caught it when the
# retry path was first written -- every REAL API invocation must produce
# exactly one ledger row, full stop, regardless of which attempt in a
# section's sequence it is or how the section ultimately resolves. Every
# `FakeRawClient.calls` entry IS a simulated real, billed invocation; the
# invariant is `len(fake.calls) == (llm_calls row count after the call)`.

_TRUNCATION_TEST_RESPONSE_SCENARIOS = {
    "single_ok": ["GOOD"],
    "generic_retry_then_ok": [("BAD", "end_turn"), "GOOD"],
    "generic_retry_exhausted": [("BAD", "end_turn"), ("BAD", "end_turn")],
    "truncation_retry_then_ok": [("BAD", "max_tokens"), "GOOD"],
    "truncation_retry_exhausted": [("BAD", "max_tokens"), ("BAD", "max_tokens")],
}


@pytest.mark.parametrize("scenario", sorted(_TRUNCATION_TEST_RESPONSE_SCENARIOS))
def test_every_real_attempt_produces_exactly_one_ledger_row(conn, scenario):
    section_id = _insert_section(conn)
    responses = [
        (GOOD_RESPONSE if item == "GOOD" else TRUNCATED_RESPONSE) if isinstance(item, str)
        else (GOOD_RESPONSE if item[0] == "GOOD" else TRUNCATED_RESPONSE, item[1])
        for item in _TRUNCATION_TEST_RESPONSE_SCENARIOS[scenario]
    ]
    fake = FakeRawClient(responses)
    client = llm.LLMClient(raw_client=fake)

    llm.get_or_create_analysis(conn, section_id, "section_analysis", "v1", "prompt text", client=client)

    row_count = conn.execute("SELECT COUNT(*) AS n FROM llm_calls").fetchone()["n"]
    assert row_count == len(fake.calls), (
        f"{scenario}: {len(fake.calls)} real API invocation(s) but {row_count} ledger row(s) -- "
        "every real invocation must bill its own row"
    )


# --- 2026-07-27 live-error-analysis fix: stop_reason-driven truncation ---

TRUNCATED_RESPONSE = _load_fixture_text("llm_truncated_response.json")


def test_truncation_read_from_stop_reason_not_inferred_from_parse_failure(conn):
    # A response that FAILS to parse (unterminated string, like the fixture)
    # but reports stop_reason="end_turn" (the model just emitted bad JSON on
    # its own, nothing to do with the output cap) must be treated as a plain
    # parse error, retried at the SAME cap -- never assumed to be truncation
    # just because the text happens to look cut off.
    section_id = _insert_section(conn)
    fake = FakeRawClient([(TRUNCATED_RESPONSE, "end_turn"), (TRUNCATED_RESPONSE, "end_turn")])
    client = llm.LLMClient(raw_client=fake)

    outcome = llm.get_or_create_analysis(conn, section_id, "section_analysis", "v1", "prompt text", client=client)
    assert outcome.status == "error"  # not "truncated"
    assert len(fake.calls) == 2  # the ordinary generic-parse retry, both at the same cap
    assert fake.calls[0][1] == fake.calls[1][1] == config.LLM_MAX_OUTPUT_TOKENS


def test_truncated_call_retried_once_at_higher_cap_then_succeeds(conn):
    section_id = _insert_section(conn)
    fake = FakeRawClient([(TRUNCATED_RESPONSE, "max_tokens"), (GOOD_RESPONSE, "end_turn")])
    client = llm.LLMClient(raw_client=fake)

    outcome = llm.get_or_create_analysis(conn, section_id, "section_analysis", "v1", "prompt text", client=client)
    assert outcome.status == "ok"
    # One truncated attempt at the normal cap, one retry at the raised cap --
    # never a second attempt at the SAME cap (that would just truncate
    # again), and never more than one retry.
    assert len(fake.calls) == 2
    assert fake.calls[0][1] == config.LLM_MAX_OUTPUT_TOKENS
    assert fake.calls[1][1] == config.LLM_TRUNCATION_RETRY_OUTPUT_TOKENS

    # 2026-07-28 live-error-analysis fix: the wasted, truncated FIRST attempt
    # was a real, billed API call too (input tokens + a full output cap of
    # "thinking" that never produced text) -- it gets its own ledger row,
    # never silently folded away just because the retry succeeded.
    ok_rows = conn.execute("SELECT * FROM llm_calls WHERE status = 'ok'").fetchall()
    assert len(ok_rows) == 1
    error_rows = conn.execute("SELECT * FROM llm_calls WHERE status = 'error'").fetchall()
    assert len(error_rows) == 1
    assert "truncated" in error_rows[0]["note"] and "max_tokens" in error_rows[0]["note"]


def test_truncated_call_still_truncated_after_retry_is_recorded_distinctly(conn):
    section_id = _insert_section(conn)
    fake = FakeRawClient([(TRUNCATED_RESPONSE, "max_tokens"), (TRUNCATED_RESPONSE, "max_tokens")])
    client = llm.LLMClient(raw_client=fake)

    outcome = llm.get_or_create_analysis(conn, section_id, "section_analysis", "v1", "prompt text", client=client)
    assert outcome.status == "truncated"  # distinct from the generic "error" status
    assert outcome.note is not None and "max_tokens" in outcome.note
    assert len(fake.calls) == 2  # exactly one retry, never looped further
    assert fake.calls[1][1] == config.LLM_TRUNCATION_RETRY_OUTPUT_TOKENS

    # Billed calls are still ledgered under 'error' (both attempts were
    # genuinely charged by Anthropic) -- "truncated" is an AnalysisOutcome-
    # level distinction for the run report, not a new ledger status. TWO
    # rows now, one per real attempt (2026-07-28 fix), not one.
    error_rows = conn.execute("SELECT * FROM llm_calls WHERE status = 'error'").fetchall()
    assert len(error_rows) == 2
    assert all("max_tokens" in row["note"] for row in error_rows)
    assert "even after retrying" in error_rows[1]["note"]


def test_empty_findings_response_is_valid(conn):
    section_id = _insert_section(conn)
    fake = FakeRawClient([EMPTY_RESPONSE])
    client = llm.LLMClient(raw_client=fake)

    outcome = llm.get_or_create_analysis(conn, section_id, "section_analysis", "v1", "prompt text", client=client)
    assert outcome.status == "ok"
    assert outcome.output["material"] is False
    assert outcome.output["findings"] == []


def test_oversized_section_skipped_not_truncated(conn, monkeypatch):
    monkeypatch.setattr(config, "LLM_MAX_INPUT_TOKENS_ESTIMATE", 10)  # trivially small for this test
    section_id = _insert_section(conn)
    fake = FakeRawClient([GOOD_RESPONSE])
    client = llm.LLMClient(raw_client=fake)

    outcome = llm.get_or_create_analysis(
        conn, section_id, "section_analysis", "v1", "this rendered prompt is far longer than 10 tokens worth of text",
        client=client,
    )
    assert outcome.status == "skipped"
    assert len(fake.calls) == 0  # never called at all
    assert conn.execute("SELECT COUNT(*) AS n FROM llm_calls").fetchone()["n"] == 0


def test_budget_refusal_flows_through_get_or_create_analysis(conn):
    section_id = _insert_section(conn)
    input_rate = config.LLM_PRICING["claude-sonnet-5"].input_per_mtok
    llm.record_result(
        conn, "claude-sonnet-5", input_tokens=int(config.LLM_BUDGET_USD / input_rate * 1_000_000), output_tokens=0, status="ok",
    )
    fake = FakeRawClient([GOOD_RESPONSE])
    client = llm.LLMClient(raw_client=fake)

    outcome = llm.get_or_create_analysis(conn, section_id, "section_analysis", "v1", "prompt text", client=client)
    assert outcome.status == "refused"
    assert len(fake.calls) == 0
    assert conn.execute("SELECT COUNT(*) AS n FROM llm_calls WHERE status='refused'").fetchone()["n"] == 1


# --- 2026-07-27 live-error-analysis fix: diagnosing an empty extraction ---
#
# A real ledger row (input_tokens=5260, output_tokens=4096 -- exactly the
# cap) extracted zero text and failed with "Expecting value: line 1 column 1
# (char 0)", with no record anywhere of what response.content actually
# held. These tests exercise _RealAnthropicClient.messages_create directly
# (constructed via __new__, bypassing the real anthropic.Anthropic() client
# entirely -- no network, no API key) against a fake SDK response shaped
# like the real anthropic.types.Message, to prove the fix logs the actual
# block types instead of silently assuming "the non-text blocks were
# filtered out and there was simply nothing else."


def _fake_sdk_response(content, stop_reason, output_tokens=4096, input_tokens=5260):
    from types import SimpleNamespace

    return SimpleNamespace(
        content=content,
        usage=SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens),
        stop_reason=stop_reason,
    )


def _real_client_with_fake_sdk(response):
    from types import SimpleNamespace

    real_client = llm._RealAnthropicClient.__new__(llm._RealAnthropicClient)
    real_client._client = SimpleNamespace(messages=SimpleNamespace(create=lambda **kwargs: response))
    return real_client


def test_real_client_logs_block_types_when_text_extraction_is_empty(caplog):
    import logging
    from types import SimpleNamespace

    caplog.set_level(logging.WARNING, logger="edgar.llm")
    # A "thinking" block only, no "text" block at all -- exactly the shape
    # that would previously vanish silently into an empty string.
    response = _fake_sdk_response(
        content=[SimpleNamespace(type="thinking", text=None)], stop_reason="max_tokens",
    )
    real_client = _real_client_with_fake_sdk(response)

    text, input_tokens, output_tokens, stop_reason = real_client.messages_create(
        "claude-sonnet-5", 4096, "prompt"
    )

    assert text == ""
    assert stop_reason == "max_tokens"
    warnings = [r for r in caplog.records if "empty text extracted" in r.message]
    assert len(warnings) == 1
    assert "thinking" in warnings[0].message  # names the actual block type, not just "empty"
    assert "max_tokens" in warnings[0].message


def test_real_client_silent_when_text_extraction_succeeds(caplog):
    import logging
    from types import SimpleNamespace

    caplog.set_level(logging.WARNING, logger="edgar.llm")
    response = _fake_sdk_response(
        content=[SimpleNamespace(type="text", text=GOOD_RESPONSE)], stop_reason="end_turn",
    )
    real_client = _real_client_with_fake_sdk(response)

    text, input_tokens, output_tokens, stop_reason = real_client.messages_create(
        "claude-sonnet-5", 4096, "prompt"
    )

    assert text == GOOD_RESPONSE
    assert stop_reason == "end_turn"
    assert not [r for r in caplog.records if "empty text extracted" in r.message]
