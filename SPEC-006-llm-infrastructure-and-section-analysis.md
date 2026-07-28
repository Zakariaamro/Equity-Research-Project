# SPEC-006 — LLM Infrastructure and Section Analysis

**Version:** 1.2
**For:** Claude Code
**Depends on:** SPEC-005 (complete, commit `ccc0927`)
**Reference:** `ARCHITECTURE.md` — sections 2, 6, 4.3
**Estimated effort:** 8–10 hours

**Changelog**
- v1.2 — Live error-analysis pass over the ledger after the first real, paid
  `analyze-sections --execute` run (2026-07-28; full account in `ARCHITECTURE.md` §4.3).
  Three fixes, all found from real data, none from a test failure:
  1. Truncation is read from the API's own `stop_reason`, never inferred from a JSON parse
     failure — retried once at a raised output cap (`LLM_TRUNCATION_RETRY_OUTPUT_TOKENS`),
     and now its own `"truncated"` outcome/report status, distinct from `"error"`. 3 of 6
     real pre-fix ledger error rows were truncation misfiled as a generic parse error.
  2. Empty text extraction now logs the API's actual content block types. Diagnosed live:
     every empty extraction in the first real run was a `['thinking']` block that consumed
     the entire output cap before any text — truncation's most extreme case, not a separate
     bug, and fix (1) already handles it.
  3. Every real API attempt now bills its own `llm_calls` row, immediately — found live
     that a truncated-then-successfully-retried call previously recorded only the retry's
     tokens, silently dropping the wasted first attempt's real billed cost. New ledger
     status `reconciliation` added for the one, reviewed, committed-script adjustment entry
     this required for the 4 historical rows it affected (their exact wasted-attempt input
     token counts are unrecoverable, so one labelled estimate, not four reconstructed rows).
- v1.1 — Pre-implementation review, verified against live sources before any code was
  written:
  1. Metrics removed from the section-analysis prompt entirely (R5). Each layer does only
     what it is uniquely able to do: `observations` owns numeric trends deterministically,
     the LLM's unique capability is reading disclosure language neither `metrics.py` nor
     `observations.py` can parse. Combining the two belongs in SPEC-007's brief, not here.
     Read three real in-window notes in full (Amazon's Income Taxes and Commitments and
     Contingencies, Micron's Government Incentives) before deciding this: all three are
     already self-contained and numerically dense in their own right, and none needed any
     of the 44 metrics computed for that period to be understood. Supplying all 44
     indiscriminately would have invited exactly the failure mode this spec is designed to
     avoid — commentary on numbers rather than disclosure — while `findings.quote`
     verification only partially guards against it (a real quote can still anchor a
     metrics-flavored finding). If sampled findings look thin during prompt development
     (R8), a small, note-topic-relevant metric subset can be trialled then, deliberately,
     not assumed now.
  2. R2 states explicitly that `input_hash` covers the fully rendered prompt, including
     every interpolated value — not the template. A cache that can serve a stale answer to
     a question no longer being asked is worse than no cache.
  3. `LLM_PRICING` verified live against Anthropic's official pricing documentation
     (`platform.claude.com/docs/en/docs/about-claude/pricing`, checked 2026-07-27) rather
     than assumed — see R1. Claude Sonnet 5's price changes on 2026-09-01 (introductory
     $2/$10 per MTok → standard $3/$15); budget estimates in this spec use the **post-
     September** rate throughout, so the numbers don't go stale on a known, calendared day.
     New `validate` check warns when a pricing entry's verification date is more than 60
     days old.
  4. Model selected: **Claude Sonnet 5**. See R1a for the full reasoning — cost is not the
     deciding factor (all three current-tier models fit inside the budget with large
     margin at the corrected call volume); quality is, and the specific risk this spec is
     built around (a model fabricating a finding rather than correctly returning empty)
     is a known weaker-model failure mode that a mid-tier-and-up model is better at
     avoiding.
  5. R4's volume estimate corrected: verified live against the real database (not
     estimated) at **18 in-window filings** and **273 non-boilerplate Notes sections**,
     not "roughly 15 filings × ~10 notes ≈ 150 calls." Nearly double the original
     estimate — still cheap in absolute terms at any of the three candidate models, but a
     real correction to a number the spec stated as fact.

---

## Objective

Build the LLM layer, and use it for one job: reading footnotes and producing
**quote-anchored findings** an analyst would want to see.

This is the first spec that spends money. The project has a hard **$20 lifetime ceiling**,
so the budget guard is requirement one, not a later addition.

---

## The two things that make this safe

**1. Every finding must quote the filing, verbatim, and the quote is verified in code.**

The model returns a quote with each finding. Python then checks that quote appears
character-for-character in the source section. **Findings whose quote does not match are
discarded** — not warned about, discarded.

This is the strongest available control against fabrication, and it is mechanical rather
than a request. A model cannot invent a disclosure that isn't there, because the invented
sentence won't be found in the text.

**2. Money cannot be spent past the cap, by construction.**

A ledger records every call. The cap is checked before each call, against actual recorded
spend. There is no path to the API that bypasses it.

---

## Scope

**In scope**

1. `llm_calls` spend ledger and hard cap
2. `llm.py` — Anthropic client, caching, cost accounting
3. Versioned prompt files
4. `analyze.py` — section analysis producing `findings`
5. Verbatim quote verification
6. Sampling and dry-run for cheap prompt development
7. CLI, validate additions, tests

**Out of scope:** the narrative brief (SPEC-007), the interactive assistant, the dashboard,
MD&A, 8-K exhibits, market data, GitHub Actions.

---

## Requirements

### R1 — Spend ledger and hard cap

**Build this first. Do not write the API client until this exists and is tested.**

```sql
CREATE TABLE llm_calls (
    id             INTEGER PRIMARY KEY,
    created_at     TEXT NOT NULL,
    model          TEXT NOT NULL,
    prompt_name    TEXT,
    prompt_version TEXT,
    input_tokens   INTEGER NOT NULL,
    output_tokens  INTEGER NOT NULL,
    cost_usd       REAL NOT NULL,
    status         TEXT NOT NULL,   -- ok | error | refused
    note           TEXT
);
```

- **Every** call attempt appends a row, including failures and refusals. A ledger with
  gaps is not a ledger.
- `analyses` gains `call_id` referencing this table, and **drops** its own
  `input_tokens`, `output_tokens` and `cost_usd` columns. One source of truth for spend.
  The table is empty, so this costs nothing now and would be a migration later.
- Config: `LLM_BUDGET_USD = 20.00`, `LLM_WARN_FRACTION = 0.75`,
  `LLM_PRICING` mapping model to input and output dollars per million tokens.
- **Before every call**: sum recorded spend, add this call's estimated cost, and refuse
  with a typed error if the total would exceed the budget. Log a refusal row.
- Warn once per run when spend crosses the warn fraction.
- **Verify current pricing against Anthropic's documentation before setting `LLM_PRICING`,
  and record where the figures came from and the date.** Prices change; a stale table
  makes the cap meaningless.

#### R1a — `LLM_PRICING`: verified figures, source, and staleness

Verified live against Anthropic's official pricing page
(`https://platform.claude.com/docs/en/docs/about-claude/pricing`, redirected from the
canonical `docs.anthropic.com` URL), **checked 2026-07-27** — not assumed, not carried
over from training data. Each `LLM_PRICING` entry records the model, input/output cost
per million tokens, the source URL, and this verification date, so a future reviewer can
tell at a glance whether the table is still current rather than having to re-derive that.

| Model | Input ($/MTok) | Output ($/MTok) |
|---|---|---|
| Claude Opus 5 | 5.00 | 25.00 |
| Claude Sonnet 5 (through 2026-08-31) | 2.00 | 10.00 |
| **Claude Sonnet 5 (from 2026-09-01)** | **3.00** | **15.00** |
| Claude Haiku 4.5 | 1.00 | 5.00 |

**Claude Sonnet 5's price changes on a known, calendared date: 2026-09-01**, introductory
$2/$10 per MTok reverting to standard $3/$15. This is not a hypothetical staleness risk —
it is a concrete date this project's own LLM work may still be active past. **All budget
estimates in this spec (R1a's own worked figures, R4's volume estimate, R8's development
cost) use the post-September, standard rate throughout**, so the numbers here don't need
revisiting purely because a calendar page turned; if Sonnet 5 is still the active model
after 2026-09-01, `LLM_PRICING`'s entry for it must be updated to drop the introductory
row, not left pointing at an expired rate.

**New `validate` check (R10): warn when a pricing entry's verification date is more than
60 days old.** Informational, not a hard failure — a stale price doesn't corrupt data the
way a stale hash or a broken FK would — but the cap's entire meaning depends on the price
table being current, so a silent staleness warning is exactly the right weight: loud
enough to prompt a re-check, not so loud it blocks an unrelated `validate` run.

`python -m edgar.pipeline spend` reports total spent, remaining, and a breakdown by
prompt and model.

### R2 — LLM client (`llm.py`)

The only module permitted to call the Anthropic API — same rule as `edgar_client` for SEC.

- API key from environment (`ANTHROPIC_API_KEY`), never from source, with a clear error
  if unset.
- Model from config: **Claude Sonnet 5** (see R1a's reasoning, R2a below for the full
  argument).
- Structured output. Requests JSON matching a declared schema; a response failing to
  parse is retried once, then recorded as an error.
- **Cache before calling.** `input_hash` = sha256 of section text + rendered prompt +
  model + prompt version. If `analyses` already holds that hash, return it and make no
  call. The existing UNIQUE constraint on `input_hash` enforces this at the database level.
  **`input_hash` covers the fully rendered prompt — the actual string sent to the API,
  with every value already interpolated (note text, company, ticker, form type, fiscal
  period, note name) — not the template file's raw contents.** This is not a minor
  implementation detail: if the hash covered only the static template, two calls with
  different interpolated content but the same template and prompt version would collide,
  and a cache built to save money would instead silently serve the wrong answer to a
  different question. A cache that can serve a stale or mismatched answer to a question no
  longer being asked is worse than no cache at all — it fails silently, where no caching
  would at least call the API and get a real answer.
- Retries only on transient failures, with bounded attempts, reusing the backoff config.
- `estimate_cost(text, prompt)` returns an approximate cost without calling anything.
  Approximate token counts are fine; state the method.

#### R2a — Model selection: Claude Sonnet 5

Cost is not the deciding factor. At the corrected R4 volume (273 calls, not 150) and
post-September pricing (R1a), full-run cost estimates for every current-tier candidate
fit inside the budget with large margin:

| Model | Est. full-run cost (273 calls, ~3,000–3,500 input tokens each) |
|---|---|
| Haiku 4.5 | ~$2.30 |
| **Sonnet 5** | **~$4.60** |
| Opus 5 | ~$11.50 |

(Opus 5 alone would consume more than the entire AC9 target of "total spend under $10,"
before any prompt-development iteration is added — not a hard-cap violation, but it erodes
the stated headroom for SPEC-007 and the interactive assistant for a task that does not
obviously need Opus-tier reasoning depth.)

The deciding factor is quality, because this is the first spec where a fabricated finding
would actually go into `findings` and could survive undetected if the safety mechanism
(quote verification, R6) is bypassed by the model quoting real-but-unrelated text. Anthropic's
own guidance places structured, single-document classification and materiality judgment —
this task — in "Sonnet for most production workloads," not "Haiku for simple tasks." That
distinction matters concretely here, not abstractly: **a weaker model is more likely to
fabricate a finding rather than correctly return an empty response** when a note is
genuinely unremarkable, because "return nothing" is a harder instruction for a smaller
model to follow under implicit pressure to produce output than "return something plausible."
This is precisely the failure mode R5's empty-response requirement and R6's quote
verification exist to prevent — starting with a model less prone to it in the first place
is cheaper, in every sense, than catching more of its failures after the fact. Sonnet 5 is
the starting point; Opus 5 is a trivial-cost upgrade (not a re-architecture) if R8's sampled
discard rate or missed-materiality looks weak, and Haiku 4.5 is not recommended as primary
for exactly the reason above.

### R3 — Prompts as versioned files

`prompts/section_analysis_v1.md`. Each prompt file documents, in a header:

- **Purpose** — what question it answers
- **Inputs** — what is interpolated
- **Output** — the exact schema expected
- **Constraints** — what it must never do
- **Success criteria** — what a good response looks like
- **Failure cases** — known ways it goes wrong

A prompt change means a new version file. Old versions stay; the hash changes, so new
analyses are produced while old ones remain for comparison.

### R4 — What gets analysed

- Categories: **`Notes` only** for V1. Policies are excluded — the observations layer
  already detects policy wording changes, and including them roughly doubles cost.
  Extending later is a config change.
- Bounded by `ANALYSIS_START_DATE = "2025-01-01"` on `filings.filing_date`. Archiving and
  extraction stay unbounded; **only the paid stage is bounded.**
- Boilerplate notes (`BOILERPLATE_NOTE_NAMES`) are skipped. Paying to analyse an insider
  trading policy disclosure is waste.

**Expected volume, measured live against the real database, not estimated: 18 in-window
filings (`filing_date >= ANALYSIS_START_DATE`, form type 10-K/10-Q) and 273
non-boilerplate `Notes` sections among them — roughly 15 filings × ~10 notes ≈ 150 calls
was the original estimate and undercounts by nearly 2x.** Still cheap at any candidate
model (R2a), but the volume this spec actually commits to is 273 calls, not 150 — worth
getting right before budgeting prompt-development iteration against the wrong number.

### R5 — The prompt's job

**Input per call: company, ticker, form type, fiscal period, note name, and the note
text. No computed metrics.**

Each layer does only what it is uniquely able to do. `observations` (SPEC-005) already
owns numeric trends, deterministically, auditably, and for free — a metric hitting a
multi-year extreme is already a verified statement before this spec exists. The LLM's
one unique capability here is reading disclosure *language*: what a filer chose to say,
how they said it, what changed in the telling. Supplying the note's own computed metrics
alongside it does not add information the model needs for that job, and does invite the
opposite of it — commentary on the numbers instead of the disclosure, which is a job this
project already has a deterministic, more reliable, and free mechanism for.

This was verified, not assumed, before writing the prompt: three real in-window notes
(Amazon's Income Taxes and Commitments and Contingencies, Micron's Government Incentives —
also R8's recommended development samples) were read in full. All three are already
dense with their own numbers and narrate their own arithmetic in plain language ("the 2025
Tax Act increased our income tax provision, primarily due to a decrease in the foreign
income deduction"; "operating income benefited by $588 million (approximately 87% in
COGS and 13% in R&D)"). None needed any of the 44 metrics computed for that period to be
understood. `findings.quote` verification (R6) only partially guards against a
metrics-flavored finding — it proves a quote is real, not that it *supports* the specific
claim built around it, so a model could still anchor a numbers-driven finding to a real
but loosely-related quote and pass verification.

**If sampled findings look thin during prompt development (R8), a small, note-topic-
relevant metric subset can be trialled then** — deliberately, as a measured next step
with a specific note showing the gap, not assumed into the V1 design because metrics
happened to be available.

Output schema:

```json
{
  "material": true,
  "findings": [
    {
      "category": "red_flag | accounting_change | litigation | concentration | liquidity | note_item",
      "severity": "high | medium | low",
      "headline": "one sentence, under 120 characters",
      "detail": "two or three sentences of explanation",
      "quote": "verbatim text copied from the note"
    }
  ]
}
```

Constraints, stated in the prompt and enforced in code where possible:

- Every finding carries a **verbatim** quote from the supplied note.
- **No forecasts, no price targets, no investment recommendations.**
- No causal claims unless the filing itself states the cause.
- **"No material findings" is a valid and expected response.** Most notes in most quarters
  contain nothing an analyst needs. A prompt that cannot return nothing will invent
  something, so make the empty response explicitly acceptable and unremarkable.

### R6 — Verbatim quote verification

After each response, before writing anything:

- Normalise whitespace on both the quote and the source, then require the quote to appear
  as a substring of the source section text.
- **Discard any finding that fails.** Log it with the prompt version so hallucination rate
  is measurable over time.
- Require a minimum quote length (config, roughly 40 characters). A three-word quote
  matches everything and proves nothing.
- Record per-run: findings returned, findings kept, findings discarded. **The discard rate
  is the single most important quality metric in this spec.**

### R7 — Persistence

- One `analyses` row per section analysed, holding the raw response and its `call_id`.
- One `findings` row per surviving finding, with its quote and `analysis_id`.
- Re-running with an unchanged prompt version makes no calls and writes no rows.
- A section whose `text_hash` changed produces a new analysis; the old one is retained.

### R8 — Development without burning the budget

Prompt iteration is where money disappears. Running all 150 sections for each of five
prompt drafts costs five times the full run.

- `--sample N` selects N sections deterministically (seeded, and the seed is reported) for
  prompt development. **Iterate on 5–10 sections, never the full set.**
- `--dry-run` reports the number of calls, estimated tokens and estimated cost, and makes
  no calls.
- `--limit N` caps calls in a run.
- The default with no flags must be a dry run. **Spending money should require an explicit
  flag.**

### R9 — CLI

```
python -m edgar.pipeline analyze-sections [--ticker T] [--accession A] [--sample N] [--limit N] [--execute]
python -m edgar.pipeline spend
```

`--execute` is required to make real calls. Report calls made, cache hits, findings kept,
findings discarded, and total cost.

### R10 — Validate additions

| Check | Behaviour |
|---|---|
| Ledger reconciliation | Sum of `llm_calls.cost_usd` matches the reported total. Hard failure on mismatch. |
| Budget headroom | Report spent and remaining. Hard failure if spend exceeds budget — that would mean the cap leaked. |
| Orphan findings | Every finding resolves to an analysis and a section. Hard failure. |
| Quote integrity | **Re-verify every stored finding's quote against its section text.** Hard failure on any mismatch — a stored finding that no longer matches its source is corruption. |
| Discard rate | Report per prompt version, informational. |

---

## Constraints

- All Anthropic calls through `llm.py`. No exceptions.
- No secrets in source or in the database.
- No literals outside `config.py` — model names, pricing, budget, thresholds, categories.
- **No network in unit tests.** Mock the client; use recorded responses as fixtures.
- Type hints on all public functions.
- Nothing in this spec may modify `metrics`, `observations` or `xbrl_facts`.

---

## Acceptance Criteria

1. `llm_calls` exists and every call attempt appends a row, including refusals.
2. **The cap is proven**: with `LLM_BUDGET_USD` temporarily set to a value below current
   spend, the next call is refused with a typed error and a logged row. Demonstrate this.
3. `analyses` has `call_id` and no longer carries its own token or cost columns.
4. `spend` reports a total matching the ledger sum.
5. `analyze-sections` with no flags performs a dry run and makes no calls.
6. `--dry-run` estimates cost for the full set; report the figure before executing anything.
7. Prompt developed on a sample of no more than 10 sections. Report the sample seed and
   the total spent during development.
8. Full run completes for all in-window Notes across the three companies.
9. **Total project spend after the full run is under $10**, leaving headroom for SPEC-007
   and the assistant. Report actual spend.
10. Every stored finding's quote appears verbatim in its section text — verified in code,
    not by inspection.
11. Discard rate reported per prompt version.
12. At least one analysed section returns `"material": false` with no findings, proving
    the empty response path works rather than being theoretically available.
13. Re-running makes zero calls and writes zero rows.
14. No finding contains a forecast, price target or recommendation. Spot-check and report.
15. `validate` exits 0.
16. `pytest` passes with no network access.

---

## Edge Cases

| Case | Required behaviour |
|---|---|
| `ANTHROPIC_API_KEY` unset | Fail immediately, naming the variable. Never attempt an anonymous call. |
| Budget would be exceeded | Refuse, log a refusal row, exit non-zero with the shortfall. |
| Response is not valid JSON, `stop_reason` is not `"max_tokens"` | The model emitted bad JSON on its own — retry once at the SAME output cap, then record a generic error. Never partially parse. |
| API reports `stop_reason == "max_tokens"` (v1.2, §4.3) | Truncation, read from the API directly, never inferred from the parse failure. Retry once at `LLM_TRUNCATION_RETRY_OUTPUT_TOKENS` (a raised cap); if that retry also fails, record the distinct `"truncated"` status, never generic `"error"`. Counted separately in the run report — a rising rate means the cap needs raising again. |
| Response extracts to empty text (v1.2, §4.3) | Log the API's actual content block types and `stop_reason` — never assume non-text blocks were correctly filtered with nothing of substance behind them. Diagnosed live: every real case was a `"thinking"` block that consumed the whole output cap, i.e. the truncation case above, not a separate failure mode. |
| A section needs more than one real API attempt (generic retry or truncation retry, v1.2, §4.3) | EVERY real, billed attempt gets its OWN `llm_calls` row and its own `RunGuard` accounting, the instant it happens — never deferred to whichever attempt the retry loop ends on. A section needing 2 real attempts produces 2 ledger rows, always. |
| Model returns a quote with altered whitespace or punctuation | Normalise whitespace only. Altered wording is a failed match — discard. |
| Model returns a quote not found in the note text (invented, paraphrased, or — if a future version trials the metric subset flagged in R5 — lifted from a metrics block) | Fails verification, since the source checked is the note text only. |
| Section text exceeds the context limit | Skip, log with the section identity. Do not silently truncate — a truncated note produces findings about a document that does not exist. |
| Note is boilerplate | Skipped before any call. |
| Filing is outside `ANALYSIS_START_DATE` | Skipped before any call. |
| API returns 429 or 529 | Retry with backoff within budget; the ledger records each attempt. |
| Same section analysed with an unchanged prompt | Cache hit, no call, no ledger row. |
| A past ledger gap is found (real spend a since-fixed bug never recorded, v1.2, §4.3) | Never reconstruct rows from assumed data. One reviewed, committed, idempotent script writes ONE row with a distinct `status = "reconciliation"`, whose note states plainly what is measured versus assumed. |

---

## Testing Requirements

- `test_budget_cap_refuses_call` — with recorded spend near the limit, the next call is
  refused.
- `test_ledger_records_refusals`
- `test_cache_prevents_second_call`
- `test_quote_verification_rejects_fabricated_quote`
- `test_quote_verification_accepts_whitespace_differences`
- `test_quote_verification_rejects_too_short_quote`
- `test_missing_api_key_fails_fast`
- `test_default_is_dry_run` — no flags, no calls.
- `test_invalid_json_retried_once_then_error`
- `test_oversized_section_skipped_not_truncated`
- `test_empty_findings_response_is_valid`
- `test_truncation_read_from_stop_reason_not_inferred_from_parse_failure` (v1.2) — a parse
  failure with `stop_reason="end_turn"` is a generic error, not truncation.
- `test_truncated_call_retried_once_at_higher_cap_then_succeeds` /
  `test_truncated_call_still_truncated_after_retry_is_recorded_distinctly` (v1.2)
- `test_real_client_logs_block_types_when_text_extraction_is_empty` /
  `test_real_client_silent_when_text_extraction_succeeds` (v1.2)
- `test_every_real_attempt_produces_exactly_one_ledger_row` (v1.2) — parametrized over
  every retry shape (single ok, generic retry then ok/exhausted, truncation retry then
  ok/exhausted); the ledger's core invariant, as a property test. This is the test that
  would have caught the 2026-07-28 per-attempt billing gap when the retry path was first
  written, instead of requiring a real, paid execution to surface it.
- Fixtures: recorded real responses, including one good, one with a fabricated quote, and
  one with no findings.

---

## Likely Files Affected

```
edgar/config.py     (budget, pricing, model, categories, ANALYSIS_START_DATE)
edgar/db.py         (llm_calls, analyses changes)
edgar/llm.py        (client, cache, ledger)
edgar/analyze.py    (section analysis, quote verification)
edgar/validate.py   (five new checks)
edgar/pipeline.py   (analyze-sections, spend)
prompts/section_analysis_v1.md
tests/fixtures/llm_*.json
tests/test_llm.py
tests/test_analyze.py
scripts/backfill_2026_07_28_truncation_ledger_gap.py  (v1.2, one-off, idempotent, §4.3)
```

---

## Forward-Looking Concerns

1. **SPEC-007** builds the narrative brief on `observations` plus these findings. It
   reuses every piece of this infrastructure and should be cheap.
2. **The interactive assistant** shares the same client, ledger and cap. A public endpoint
   spending from this budget must be password-gated — already decided, deployment is
   private.
3. **Discard rate is the prompt quality signal.** A rising rate across versions means a
   prompt change made fabrication more likely. Track it deliberately.
4. **8-K exhibits and MD&A** will reuse this pipeline with different prompts.

---

## Notes for the Implementer

- Build and test R1 before writing a single line that calls the API. The cap must exist
  before there is any way to spend.
- Develop the prompt against the same handful of sections repeatedly, so responses are
  comparable across versions.
- Amazon's Income Taxes and Commitments and Contingencies notes, and Micron's Government
  Incentives note, are good development samples — all three are substantive and the last
  is known to have changed materially.
- Do not tune the prompt to produce more findings. A prompt that finds something in every
  note is a prompt that fabricates. Most notes are genuinely unremarkable.
- Report any discrepancy between this spec and observed behaviour rather than working
  around it silently. `ARCHITECTURE.md` must then be corrected.
