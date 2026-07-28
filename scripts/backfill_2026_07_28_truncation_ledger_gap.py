"""One-off reconciliation entry for the 2026-07-28 truncation-retry ledger gap.

**What happened.** The 2026-07-28 fix for stop_reason-driven truncation retried a
truncated call once at a higher output cap (`config.LLM_TRUNCATION_RETRY_OUTPUT_TOKENS`)
before giving up. Until the same day's follow-up fix, `get_or_create_analysis` only
wrote ONE `llm_calls` row per section -- for whichever attempt the loop ended on. A
truncated-then-successfully-retried section therefore billed only the RETRY's tokens;
the wasted first attempt (a real API call Anthropic charged for -- input tokens plus a
full output cap of a "thinking" block that never produced text) got no ledger row at
all. Confirmed against the real database for the four sections this affected during the
2026-07-28 analyze-sections execution run: each had exactly one `llm_calls` row, holding
only the second (successful) call's tokens.

**Why this is a RECONCILIATION row, not four reconstructed call rows.** The wasted
attempts' real `input_tokens` were never captured anywhere (the code discarded them,
and the API/log gave no independent record) -- only their `output_tokens` is known
exactly (4096, read from each response's `usage.output_tokens` before the text was
found empty; see `edgar.llm`'s "empty text extracted" warnings in the run log). Writing
four separate rows with an ASSUMED input-token count each would look like four ordinary,
measured ledger entries; they are not. This script writes exactly ONE row, `status =
"reconciliation"` (not "ok"/"error"/"refused" -- deliberately a fourth, distinct ledger
status so it can never be mistaken for a normal call or a refusal), whose note states
plainly what is exact (the 16,384 total wasted output tokens -- 4 x 4096, a real,
logged figure) and what is an assumption (the 40,617 total input tokens, taken from each
affected section's own successful retry under the reasoning that the identical rendered
prompt was sent both times -- the true wasted-attempt input token count is unrecoverable).

**Idempotent.** Refuses to run twice -- checks for its own marker in `llm_calls.note`
first.

Run once, after review, with:
    python3 scripts/backfill_2026_07_28_truncation_ledger_gap.py
"""

from __future__ import annotations

import sqlite3

from edgar import config, db

MARKER = "RECONCILIATION-2026-07-28-TRUNCATION-LEDGER-GAP"

# The four sections whose successful retry already has a real, recorded llm_calls row --
# read from there, not hardcoded, so this script re-derives its numbers from the live
# ledger rather than embedding them as separate unverifiable literals.
AFFECTED_SECTION_IDS = (2168, 8, 2118, 2069)

# Exact -- read live from each affected call's "empty text extracted" warning at
# execution time (edgar.llm._RealAnthropicClient.messages_create): every one of the four
# wasted attempts hit output_tokens=4096 precisely (the pre-retry cap,
# config.LLM_MAX_OUTPUT_TOKENS at the time), each with stop_reason="max_tokens".
WASTED_OUTPUT_TOKENS_PER_ATTEMPT = 4096


def main() -> None:
    conn = db.get_connection(config.DB_PATH)

    already_applied = conn.execute(
        "SELECT 1 FROM llm_calls WHERE note LIKE ?", (f"%{MARKER}%",)
    ).fetchone()
    if already_applied is not None:
        print(f"Already applied (found {MARKER!r} in llm_calls.note) -- nothing to do.")
        conn.close()
        return

    assumed_input_tokens_total = 0
    model = None
    for section_id in AFFECTED_SECTION_IDS:
        row = conn.execute(
            """
            SELECT a.model, lc.input_tokens
            FROM analyses a JOIN llm_calls lc ON lc.id = a.call_id
            WHERE a.section_id = ?
            """,
            (section_id,),
        ).fetchone()
        if row is None:
            raise RuntimeError(f"section_id={section_id}: no recorded analysis/call found -- aborting, nothing written")
        assumed_input_tokens_total += row["input_tokens"]
        model = model or row["model"]

    wasted_output_tokens_total = WASTED_OUTPUT_TOKENS_PER_ATTEMPT * len(AFFECTED_SECTION_IDS)
    pricing = config.LLM_PRICING[model]
    estimated_cost = (
        assumed_input_tokens_total / 1_000_000 * pricing.input_per_mtok
        + wasted_output_tokens_total / 1_000_000 * pricing.output_per_mtok
    )

    note = (
        f"{MARKER}: represents {len(AFFECTED_SECTION_IDS)} truncated FIRST attempts "
        f"(section_ids {AFFECTED_SECTION_IDS}) made during the 2026-07-28 analyze-sections "
        f"execution run, each real and billed by Anthropic, each truncated "
        f"(stop_reason=max_tokens) with a 'thinking' block consuming the entire output cap "
        f"before any text, each successfully retried once at a higher cap (that retry's tokens "
        f"are correctly recorded under its own 'ok' row -- see the matching analyses.section_id). "
        f"output_tokens={wasted_output_tokens_total} ({WASTED_OUTPUT_TOKENS_PER_ATTEMPT} x "
        f"{len(AFFECTED_SECTION_IDS)}) is EXACT, read from each response's usage.output_tokens "
        f"before the text was found empty. input_tokens={assumed_input_tokens_total} is ASSUMED "
        f"(each wasted attempt's own input_tokens was never captured -- the code that fixed this "
        f"gap discarded it; this script substitutes each section's own successful RETRY's "
        f"input_tokens, since the identical rendered prompt was sent both times). The true "
        f"wasted-attempt input token count is unrecoverable; cost_usd below is therefore an "
        f"estimate, not a measurement, and this row is deliberately status='reconciliation' -- "
        f"not 'ok'/'error'/'refused' -- so it is never mistaken for an ordinary billed call."
    )

    cursor = conn.execute(
        "INSERT INTO llm_calls "
        "(created_at, model, prompt_name, prompt_version, input_tokens, output_tokens, cost_usd, status, note) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "2026-07-28T00:00:00",  # nominal -- the row represents 4 distinct real moments, not one
            model,
            config.SECTION_ANALYSIS_PROMPT_NAME,
            config.SECTION_ANALYSIS_PROMPT_VERSION,
            assumed_input_tokens_total,
            wasted_output_tokens_total,
            estimated_cost,
            "reconciliation",
            note,
        ),
    )
    conn.commit()
    print(f"Inserted llm_calls id={cursor.lastrowid}, status='reconciliation', cost_usd={estimated_cost:.6f}")
    print(f"New lifetime total_spent: {conn.execute('SELECT COALESCE(SUM(cost_usd),0) AS s FROM llm_calls').fetchone()['s']:.4f}")
    conn.close()


if __name__ == "__main__":
    main()
