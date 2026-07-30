# SPEC-007 — The Grounded Brief

**Version:** 2.1
**For:** Claude Code
**Depends on:** SPEC-006 (complete, commit `7a170da`)
**Reference:** `ARCHITECTURE.md` — sections 2, 6; SPEC-005 R11
**Estimated effort:** 6–8 hours
**Estimated API cost:** ~$0.50 for all 18 in-window filings (two passes per brief) — a
real-data simulation against Amazon's and Micron's most recent 10-Ks (2026-07-30 pre-
implementation review) measured ~$0.017/brief, ~$0.30–0.35 total; the stated figure keeps
real margin, not a tight fit.

**Changelog**
- v2.1 — Pre-implementation review against real data (2026-07-30), before writing `brief.py`.
  R2's selection rule, simulated against Amazon's and Micron's real most-recent 10-Ks, had
  three problems real data exposed that the spec text alone did not:
  1. **Observation selection now filters to each rule's CURRENT `rule_version`** before
     ranking (`config.RULE_REGISTRY[rule_name].version`), matching `validate.py`'s
     established pattern. Without this, stale rows from a superseded rule version can carry
     a different — and, found live, HIGHER — severity than the current version assigns
     (`section_appeared`/`section_disappeared` were `"high"` under v1, downgraded to `"low"`
     in v4; both versions' rows still sit in the table, by design, for historical
     comparison). An unfiltered selection query would silently rank a filing's oldest,
     already-superseded severity judgement above its current one.
  2. **Findings selection now caps at 2 per `category`**, mirroring the per-rule cap R2
     already had for observations. Simulated uncapped against Amazon's real 10-K: 8 of 8
     selected findings came from `category = "litigation"`, and the filing's ONLY
     `red_flag` finding (a $1.3B store-impairment charge) never reached the generator. This
     is the second time in this project a ranking with no diversity constraint has
     collapsed onto whichever dimension happened to be over-represented in the data — the
     first was SPEC-005 R11, for the identical reason, one layer down (`observations`, not
     `findings`).
  3. **The observation cap is refined for metric-subject rules**: capped at 2 per
     `(rule_name, metric_category)` — reusing `config.METRIC_REGISTRY[subject].category`
     from SPEC-005 — rather than 2 per `rule_name` alone, with an outer ceiling of 4 from
     any single rule regardless of how many categories it touches. `metric_multi_year_extreme`
     spans ~30 metrics across 7 categories (growth, margins, returns, capital_cash,
     working_capital, solvency, quality); a flat per-rule cap of 2 treats all 30 as one
     thing and, simulated against Amazon's real 10-K, kept two margin records
     (`gross_margin`, `operating_margin`) while `working_capital` and `returns` extremes
     never appeared at all, despite genuinely firing that period. Section-subject rules
     (`section_wording_changed`, `section_length_change`, `section_appeared`,
     `section_disappeared`, `readability_change`, `section_renamed`) are unaffected — they
     have no metric-category concept and keep the flat per-rule cap of 2.
  4. **The severity tie-break is now specified**: severity descending, then `id` ascending.
     Stated for reproducibility, not analytical merit — `id` order carries no informational
     claim about which of two equal-severity items matters more. The reason this must be
     specified at all: the same filing must produce an IDENTICAL selected set on every run,
     or `input_hash` (R1) varies run to run for a logically unchanged question, and the
     cache stops meaning anything.
  5. **R4's `aggregation` check now requires every summed number to carry the same unit.**
     Found live: the mechanical derived-sum verifier checks arithmetic, not units — summing
     a €746M fine and a $525M verdict into "$1.27B combined" passes the sum check
     numerically (746 + 525 = 1271 ≈ 1.27B) while silently conflating two different
     currencies. Arithmetic verification without unit verification is not verification (see
     `ARCHITECTURE.md`). Mismatched-unit sums are dropped, not summed.

  Also noted, not a change: `sourced_causal` is expected to fire rarely, by construction —
  this project's own upstream generators (`observations.py`'s statements, the
  section-analysis prompt) are deliberately built to avoid asserting causation, so the pool
  of sources that already state a cause is inherently thin. A mostly-empty type is correct,
  conservative behaviour here, not evidence the type is broken or unused.

- v2.0 — Supersedes v1.0 before implementation. v1.0 relied on reference enforcement plus a
  lexical causal-language check. That is not enough: a model can imply causation with no
  connective at all, and reference enforcement proves a sentence *cites* sources without
  proving it is *supported* by them. Two structural additions: typed sentences with
  per-type mechanical verification (R4), and an independent adversarial verifier pass (R5).

---

## Objective

One short narrative per filing, written **only** from observations and findings already
verified — never from raw metrics, never from raw section text.

**The governing priority, stated by the project owner: no fabricated narrative is worth
more than having a narrative at all.** Where the two conflict, drop the sentence.

---

## What "making a connection" actually means

The word hides six operations with very different risk profiles. This distinction is the
core of the spec.

| Type | Example | Mechanically verifiable |
|---|---|---|
| `restatement` | "The Debt note changed materially." | Yes — one source |
| `juxtaposition` | "Inventory days rose; gross margin fell." | Yes — both sources exist |
| `aggregation` | "Three guarantees totalling $4.4B." | Yes — arithmetic |
| `grouping` | "Three of these concern lease obligations." | Mostly — a claim about the sources |
| *causation* | "Margins fell **because** inventory rose." | **No**, unless a source states it |
| *prediction* | "This **suggests** further compression." | **No**, ever |

**The first four carry nearly all the analytical value and require inventing nothing.**

Worked example from the real corpus. NVIDIA's findings separately include $3.5B of partner
lease guarantees, an $860M lease guarantee, and a $5B Intel commitment. Combined:

> *"NVIDIA extended roughly $9.4B of guarantees and commitments to partners and
> counterparties this year."*

An aggregation plus a grouping. Every figure sourced, the sum checkable, the timeframe
sourced. More useful than the three findings apart, and it fabricates nothing.

What must never appear:

> *"...suggesting NVIDIA is increasingly dependent on partner financing structures."*

Same facts, one invented implication.

---

## Scope

**In scope**

1. `briefs` and `brief_sentences` tables
2. Capped, ranked input selection
3. Typed-sentence output schema and per-type verification
4. Independent adversarial verifier pass
5. Versioned prompts (generator and verifier)
6. CLI, validate additions, tests

**Out of scope:** the interactive assistant (shares this engine, comes later), the
dashboard, MD&A, 8-K exhibits, market data, GitHub Actions.

---

## Requirements

### R1 — Schema

```sql
CREATE TABLE briefs (
    id             INTEGER PRIMARY KEY,
    accession_no   TEXT NOT NULL REFERENCES filings(accession_no),
    cik            TEXT NOT NULL REFERENCES companies(cik),
    prompt_name    TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    verifier_version TEXT NOT NULL,
    model          TEXT NOT NULL,
    input_hash     TEXT NOT NULL,
    call_id        INTEGER REFERENCES llm_calls(id),
    created_at     TEXT NOT NULL,
    UNIQUE(input_hash)
);

CREATE TABLE brief_sentences (
    id            INTEGER PRIMARY KEY,
    brief_id      INTEGER NOT NULL REFERENCES briefs(id),
    position      INTEGER NOT NULL,
    sentence_type TEXT NOT NULL,   -- restatement|juxtaposition|aggregation|grouping|sourced_causal
    text          TEXT NOT NULL,
    refs_json     TEXT NOT NULL,
    UNIQUE(brief_id, position)
);
```

`input_hash` includes the verifier version — a change to the verifier can change which
sentences survive, so it changes the output.

**Dropped sentences are not stored.** They are counted and reported. Storing rejected
output invites it being displayed by accident.

### R2 — Input selection

A filing can carry 30+ observations and 15+ findings. Feeding all of them costs tokens and
invites the model to mention everything, which is the opposite of a brief.

**Tie-break (applies to both rankings below): severity descending (high, medium, low), then
`id` ascending.** This is a reproducibility rule, not a claim that a lower `id` is more
analytically important than a higher one at the same severity. It exists so the SAME filing
produces an IDENTICAL selected set on every run — `input_hash` (R1) is computed over the
rendered prompt, which embeds whichever set was selected; a non-deterministic tie-break would
make the same logical question hash differently run to run, and the cache would stop meaning
anything.

- **Observations**: filter to each rule's CURRENT `rule_version`
  (`config.RULE_REGISTRY[rule_name].version`) FIRST, before ranking — matching
  `validate.py`'s established pattern for "current version only" queries. `observations`
  deliberately retains rows from superseded rule versions for historical comparison (SPEC-005);
  those rows can carry a DIFFERENT severity than the current version assigns
  (`section_appeared`/`section_disappeared` were `"high"` under rule_version v1, downgraded to
  `"low"` in v4 — both sets of rows still exist in the table). Selecting without this filter
  would silently let a filing's oldest, already-superseded severity judgement outrank its
  current one.

  Then rank by severity (tie-break above) and take at most `BRIEF_MAX_OBSERVATIONS = 8`,
  subject to two caps applied together while walking the ranked list:
  - **Slot cap, `BRIEF_OBSERVATION_SLOT_CAP = 2`**: for a metric-subject rule
    (`config.RULE_REGISTRY[rule_name].subject_kind == "metric"`) the slot is `(rule_name,
    metric_category)`, where `metric_category = config.METRIC_REGISTRY[subject].category`
    (SPEC-005's existing category field — growth, margins, returns, capital_cash,
    working_capital, solvency, quality). For a section-subject rule the slot is `(rule_name,)`
    alone, unchanged from SPEC-005 R11 — there is no metric-category concept for a note name.
    An observation whose slot has already contributed 2 is skipped.
  - **Rule ceiling, `BRIEF_OBSERVATION_RULE_CEILING = 4`**: regardless of how many distinct
    slots a single `rule_name` contributes across, it may never supply more than 4 of the 8
    selected observations. Binding only for metric-subject rules that touch 3+ categories in
    one filing (a section-subject rule's slot cap of 2 already satisfies this ceiling
    trivially).

  Why the refinement: `metric_multi_year_extreme` alone spans ~30 metrics across 7 categories.
  A flat "2 per rule_name" cap (SPEC-005 R11's original form) treats all 30 as
  interchangeable — simulated against Amazon's real most-recent 10-K, the flat cap kept two
  margin records (`gross_margin`, `operating_margin`, both real 6-year highs) while
  `working_capital` and `returns` extremes that fired the same period never reached the
  selection at all, purely because margins happened to rank first among ties.

- **Findings**: rank by severity (tie-break above), take at most `BRIEF_MAX_FINDINGS = 8`,
  **with no more than `BRIEF_MAX_FINDINGS_PER_CATEGORY = 2` from any single `category`.**
  Simulated uncapped against Amazon's real most-recent 10-K: 8 of 8 selected findings came
  from `category = "litigation"` (a genuine, real cluster of matters that period), silently
  excluding the filing's only `red_flag` finding — a $1.3B store-impairment charge — along
  with its only `concentration` and `accounting_change` findings. **This is the second time
  in this project a ranking with no diversity constraint has collapsed onto whichever
  dimension happens to be over-represented in the data** — the first was SPEC-005 R11,
  discovered for `observations`/`metric_multi_year_extreme`, for the identical structural
  reason, one layer down. The general lesson: severity (or any single scalar) ranks WITHIN a
  dimension; it says nothing about whether that dimension should dominate every slot.

- Plus company name, ticker, form type, fiscal period, filing date.

Nothing else. No metrics table, no section text, no prior briefs. Each item carries its ID,
because the model must cite it.

**Selection is deterministic and happens in Python.** The model chooses phrasing among
pre-ranked material; it does not choose what matters. This is the mitigation for the one
failure mode that cannot be mechanically caught — see Residual Risk.

### R3 — Generator prompt

`prompts/filing_brief_v1.md`. Same header/template convention as SPEC-006 — **anything the
model must obey, including the output schema, goes below the `## Template` marker.** The
loader strips everything above it, and in SPEC-006 that mistake shipped a prompt asking for
a shape the model had never seen, producing silence that looked like correct behaviour.

```json
{
  "material": true,
  "sentences": [
    {
      "type": "aggregation",
      "text": "one sentence of plain English",
      "refs": ["obs:1234", "finding:567"]
    }
  ]
}
```

Constraints stated in the prompt:

- 3–6 sentences.
- Every sentence declares a `type` from the permitted set and cites at least one reference.
- **Never state a cause unless a cited source states it**, in which case use
  `sourced_causal`.
- **No predictions, forecasts, price targets, recommendations, or evaluative judgements.**
- **"No material developments this period" is valid and expected.** Most quarters are
  unremarkable, and a brief that manufactures significance every time is worthless.
- Plain English. No jargon absent from the sources.

### R4 — Per-type verification, in code

Every sentence is checked against the rules for its declared type. **Failures are dropped,
not warned about.**

| Type | Check |
|---|---|
| `restatement` | Exactly one reference. No causal connective. |
| `juxtaposition` | Two or more references. **No causal connective.** |
| `aggregation` | Every number verifies against the cited sources — present, or a correct sum or difference of numbers present, reusing SPEC-006's derived-sum verifier. **Every summed number must carry the same unit (currency symbol, or percent) — mismatched-unit sums are dropped, never combined.** Found live pre-implementation: the derived-sum verifier checks arithmetic, not units — a €746M fine plus a $525M verdict sums to "1.27B" numerically while conflating two different currencies into one figure. Arithmetic verification without unit verification is not verification (`ARCHITECTURE.md`). |
| `grouping` | Two or more references. No causal connective. No number absent from sources. |
| `sourced_causal` | **At least one cited source must itself contain a causal connective.** Otherwise dropped. Expected to fire RARELY, by construction — this project's own upstream generators (`observations.py`'s statements, the section-analysis prompt) deliberately avoid asserting causation, so the pool of sources that already state a cause is inherently thin. A mostly-empty type across many briefs is correct, conservative behaviour, not a defect to fix. |

Universal checks on every sentence regardless of type:

- Every reference resolves to an observation or finding **belonging to this filing** and
  **supplied in the input**. A model cannot cite what it never saw.
- Zero resolving references → dropped.
- Any predictive or modal construction (`will`, `expects`, `likely`, `suggests`,
  `indicates`, `points to`, `implies`) → dropped, regardless of type.
- Unrecognised `type` → dropped.

Causal connectives, in config: *because, due to, driven by, as a result of, caused by,
owing to, reflecting, attributable to, stemming from*.

This converts "please don't fabricate" from a request into a typed contract. A causal claim
cannot be smuggled into a juxtaposition, because juxtapositions are checked for it.

### R5 — Adversarial verifier pass

Lexical checks miss semantics. A model can imply causation with no connective at all —
*"Inventory rose. Margins fell."* — and no pattern catches that.

So each surviving sentence gets a **second, independent call**:

- Input: **that one sentence and its cited sources only.** Nothing else — not the other
  sentences, not the rest of the corpus, not the company context.
- Prompt (`prompts/brief_verifier_v1.md`): adversarial. *Identify any claim in this
  sentence that is not supported by these sources. Answer supported / unsupported, with the
  specific unsupported claim if any.*
- **Unsupported → the sentence is dropped.**

Batch the sentences of one brief into a single verifier call to keep cost down, but the
verifier must still see only the sentences and their own cited sources.

Report the verifier drop rate per prompt version. **A rising rate means the generator got
looser; a zero rate over many briefs means the verifier is not actually adversarial and
should be checked with a deliberately bad sentence.**

Include such a test: a hand-written sentence containing an unsupported causal claim must be
rejected by the verifier.

### R6 — CLI

```
python -m edgar.pipeline generate-briefs [--ticker T] [--accession A] [--execute]
python -m edgar.pipeline show-brief --accession A
```

- Dry run by default. `--execute` required to spend. All SPEC-006A guardrails unchanged.
- `show-brief` prints each sentence with its type and its **resolved sources beneath it**,
  the way the dashboard must present it.

### R7 — Validate additions

| Check | Behaviour |
|---|---|
| Orphan references | Every ref resolves to a live observation or finding. **Hard failure.** |
| Cross-filing references | No brief cites a source from another filing. **Hard failure.** |
| Type validity | Every stored sentence has a recognised `sentence_type`. **Hard failure.** |
| Re-verification | Re-run the R4 type checks against stored sentences. Any failure means corruption. **Hard failure.** |
| Empty briefs | Reported, not a failure — a quiet quarter is legitimate. |
| Drop rates | Generator-side and verifier-side, per prompt version. Informational. |

---

## Residual Risk — stated, not solved

Four categories of fabrication, and their status:

| Risk | Status |
|---|---|
| Fabricated facts | **Closed** — references plus SPEC-006 quote verification |
| Fabricated arithmetic | **Closed** — derived-sum verification |
| Fabricated causation | **Closed** — typed sentences, lexical checks, verifier pass |
| **Fabricated emphasis** | **Managed, not closed** |

A model can say only true things and still mislead by what it foregrounds. There is no
mechanical check for this.

The mitigation is that **selection is deterministic**: observations are ranked by rule-based
severity computed in Python, findings by severity, both before the model sees anything. The
model chooses phrasing among pre-ranked material; it does not choose what matters.

The second mitigation is presentational and **binding on the dashboard spec**: every
sentence displays its sources adjacent to it, never behind a click. If both automated layers
miss something, a reader checks it in seconds.

Record this table in `ARCHITECTURE.md`.

---

## Acceptance Criteria

1. Dry run reports filings and estimated cost; default makes no calls.
2. Briefs generated for all 18 in-window filings for under $1.00 including verification.
3. Every stored sentence has a valid type, at least one resolving reference, and passes its
   type's check. Verified in code, not by inspection.
4. No sentence references a source from another filing.
5. **A deliberately fabricated sentence with an unsupported causal claim is rejected by the
   verifier.** Demonstrate.
6. **A deliberately fabricated aggregation with a wrong sum is rejected by the type check.**
   Demonstrate.
7. Generator and verifier drop rates reported per prompt version.
8. At least one brief is 3–6 sentences and reads as plain English an analyst would accept.
9. Re-running makes zero calls and writes zero rows.
10. `validate` exits 0.
11. `pytest` passes with no network access.
12. **Demonstration**: print the full brief for Amazon's most recent 10-K and Micron's most
    recent 10-K, each sentence with its type and resolved sources. Do not tune to improve
    them.

---

## Edge Cases

| Case | Required behaviour |
|---|---|
| Filing has no observations and no findings | No call. Empty brief with a reason. Do not pay to be told nothing happened. |
| Model returns `material: false` | Valid. Store an empty brief. |
| Every sentence dropped | Store an empty brief, report loudly. Means the prompt is broken, as in SPEC-006 v1. |
| Verifier drops every sentence | Same — report loudly rather than storing silence. |
| Model cites an unsupplied ID | Drop the reference; drop the sentence if none remain. |
| Verifier itself returns unparseable output | Treat as unsupported. **Fail closed.** |
| Prompt or verifier version bump | New hash, new brief, old one retained. |

---

## Testing Requirements

- `test_sentence_without_refs_is_dropped`
- `test_reference_to_unsupplied_item_is_dropped`
- `test_cross_filing_reference_rejected`
- `test_juxtaposition_with_causal_connective_dropped`
- `test_sourced_causal_requires_causal_source`
- `test_aggregation_with_wrong_sum_dropped`
- `test_aggregation_with_correct_sum_kept`
- `test_aggregation_with_mismatched_units_dropped` — the real case found in pre-implementation
  review (v2.1): a €746M fine and a $525M verdict summed to "$1.27B combined" must be dropped
  even though 746 + 525 = 1271 checks out arithmetically; the two addends do not share a unit.
- `test_predictive_language_dropped_any_type`
- `test_unrecognised_type_dropped`
- `test_verifier_rejects_unsupported_causal_claim`
- `test_verifier_unparseable_output_fails_closed`
- `test_observation_selection_filters_to_current_rule_version` — a stale, superseded-version
  row with a higher severity than the current version must not outrank a current-version row.
- `test_observation_selection_caps_two_per_slot` — for a section-subject rule, 2 per
  `rule_name` (unchanged behaviour). For a metric-subject rule, 2 per `(rule_name,
  metric_category)`, not 2 per `rule_name` alone.
- `test_observation_selection_rule_ceiling_of_four` — a metric-subject rule touching 3+
  categories in one filing still never supplies more than 4 of the 8 selected observations.
- `test_finding_selection_caps_two_per_category`
- `test_selection_tie_break_is_severity_then_id` — two runs against unchanged data select the
  identical set in the identical order.
- `test_briefs_idempotent`

---

## Likely Files Affected

```
edgar/config.py       (caps, prompt versions, causal and predictive term lists)
edgar/db.py           (briefs, brief_sentences)
edgar/brief.py        (new — selection, generation, type verification, verifier pass)
edgar/validate.py     (six new checks)
edgar/pipeline.py     (generate-briefs, show-brief)
prompts/filing_brief_v1.md
prompts/brief_verifier_v1.md
tests/test_brief.py
```

---

## Forward-Looking Concerns

1. **The interactive assistant reuses this engine unchanged** — same selection, same typed
   sentences, same verifier. The only difference is that the question varies instead of
   being fixed. Build `brief.py` so the question is a parameter, not a constant.
2. **The dashboard must display sources adjacent to every sentence.** Binding, per Residual
   Risk above.
3. **Metrics remain absent from the LLM's inputs**, deliberately. If briefs read thin on
   quantitative context, the fix is richer *observations*, not raw metrics in the prompt.

---

## Notes for the Implementer

- Do not tune toward longer or more emphatic briefs. Most quarters are quiet, and a system
  that says so is more useful than one that does not.
- If the verifier never rejects anything, suspect the verifier before congratulating the
  generator. Test it with a deliberately bad sentence.
- Reference IDs should be short and unambiguous (`obs:1234`, `finding:567`).
- The demonstration in AC12 is the real gate. Drop rates say the machinery works; only
  reading two real briefs says whether it is worth having.
- Report any discrepancy between this spec and observed behaviour rather than working
  around it silently. `ARCHITECTURE.md` must then be corrected.
