"""Diagnostic probe: is extended thinking enabled by default, and what does disabling it
do to cost / truncation risk / output quality on a small sample? (2026-07-28, requested
after the live-error-analysis round found all 4 real truncations that run were a
"thinking" content block consuming the entire output cap before any text.)

Not part of the pipeline -- this bypasses `edgar.llm.get_or_create_analysis` entirely
(no cache check, no `analyses`/`findings` writes) so it can pass an explicit `thinking`
request parameter the production client does not currently send. Real, billed API calls;
each one is still recorded honestly to `llm_calls` (prompt_name="section_analysis_thinking_probe",
a prompt_version naming which condition), per this project's own "every call attempt appends
a row" ledger discipline -- just never against `analyses`, so it cannot collide with, or
pollute, the real per-section-per-version cache.

Sample: the same 4 real sections that truncated during the 2026-07-28 execute run (one
from each fiscal context, one company appearing twice) -- not cherry-picked for this probe,
they were already the project's known hardest real cases. Each is called ONCE, with
`thinking={"type": "disabled"}`, at the STANDARD `config.LLM_MAX_OUTPUT_TOKENS` cap (4096) --
directly comparable to that section's real production baseline (already on record: adaptive
thinking truncated at 4096, succeeded on retry at 8192 with the token counts/finding counts
printed below for comparison).

Run with:
    PYTHONPATH=. python3 scripts/probe_extended_thinking_2026_07_28.py
"""

from __future__ import annotations

import json

import anthropic

from edgar import analyze, config, db, llm, section_store

SECTION_IDS = (2168, 8, 2118, 2069)

# Real production baseline for these same 4 sections (adaptive/default thinking,
# retried at LLM_TRUNCATION_RETRY_OUTPUT_TOKENS=8192 after truncating at 4096) --
# read from `analyses.output_json` before running this probe, printed here so the
# comparison is visible without re-querying the database.
BASELINE_ADAPTIVE_THINKING = {
    2168: {"material": True, "findings": 3, "retry_output_tokens": 4415},
    8: {"material": True, "findings": 6, "retry_output_tokens": 5402},
    2118: {"material": True, "findings": 3, "retry_output_tokens": 5300},
    2069: {"material": True, "findings": 3, "retry_output_tokens": 4936},
}


def _render_for_section(conn, section_id: int) -> tuple[dict, str]:
    row = conn.execute(
        """
        SELECT s.id AS section_id, s.short_name, s.text_hash, s.accession_no,
               f.form_type, f.fiscal_year, f.fiscal_period, c.ticker, c.name AS company_name
        FROM sections s JOIN filings f ON f.accession_no = s.accession_no JOIN companies c ON c.cik = f.cik
        WHERE s.id = ?
        """,
        (section_id,),
    ).fetchone()
    template = analyze.load_prompt_template(config.SECTION_ANALYSIS_PROMPT_NAME, config.SECTION_ANALYSIS_PROMPT_VERSION)
    note_text = section_store.read_section_text(row["text_hash"])
    fiscal_period_str = analyze.format_fiscal_period(row["fiscal_year"], row["fiscal_period"])
    rendered = analyze.render_prompt(
        template,
        company=row["company_name"], ticker=row["ticker"], form_type=row["form_type"],
        fiscal_period=fiscal_period_str, note_name=analyze._canonical_note_name(row["short_name"]),
        note_text=note_text,
    )
    return dict(row), rendered


def _call_thinking_disabled(raw_client: anthropic.Anthropic, model: str, max_tokens: int, prompt: str):
    response = raw_client.messages.create(
        model=model,
        max_tokens=max_tokens,
        thinking={"type": "disabled"},
        messages=[{"role": "user", "content": prompt}],
    )
    block_types = [getattr(b, "type", None) for b in response.content]
    text = "".join(b.text for b in response.content if getattr(b, "type", None) == "text")
    return text, response.usage.input_tokens, response.usage.output_tokens, response.stop_reason, block_types


def main() -> None:
    conn = db.get_connection()
    raw_client = anthropic.Anthropic(api_key=config.get_anthropic_api_key(), max_retries=config.HTTP_MAX_RETRIES)

    print(f"{'section':>8} {'ticker':>6} {'in_tok':>7} {'out_tok':>8} {'stop_reason':>14} {'blocks':>10} "
          f"{'cost':>9} {'material':>9} {'findings':>9}  vs baseline (adaptive @8192 retry)")

    for section_id in SECTION_IDS:
        row, rendered = _render_for_section(conn, section_id)
        text, input_tokens, output_tokens, stop_reason, block_types = _call_thinking_disabled(
            raw_client, config.LLM_MODEL, config.LLM_MAX_OUTPUT_TOKENS, rendered
        )
        cost = llm.compute_cost(config.LLM_MODEL, input_tokens, output_tokens)

        parsed = None
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            pass
        material = parsed.get("material") if parsed else None
        n_findings = len(parsed.get("findings", [])) if parsed else None

        llm.record_result(
            conn, config.LLM_MODEL, input_tokens, output_tokens,
            "ok" if parsed is not None else "error",
            prompt_name="section_analysis_thinking_probe",
            prompt_version="disabled-2026-07-28",
            note=(
                f"diagnostic probe (thinking explicitly disabled) for section_id={section_id} "
                f"({row['ticker']}); content block types={block_types}, stop_reason={stop_reason}; "
                f"NOT written to analyses/findings -- comparison only."
            ),
        )

        baseline = BASELINE_ADAPTIVE_THINKING[section_id]
        print(
            f"{section_id:>8} {row['ticker']:>6} {input_tokens:>7} {output_tokens:>8} {stop_reason:>14} "
            f"{str(block_types):>10} ${cost:>8.4f} {str(material):>9} {str(n_findings):>9}  "
            f"vs material={baseline['material']} findings={baseline['findings']} "
            f"output_tokens={baseline['retry_output_tokens']}"
        )

    conn.close()


if __name__ == "__main__":
    main()
