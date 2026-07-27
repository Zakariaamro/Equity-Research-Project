# SPEC-005 — Observations and Readability

**Version:** 1.2
**For:** Claude Code
**Depends on:** SPEC-004 (complete, commit `7e5b7a6` plus the exceptions-register follow-up)
**Reference:** `ARCHITECTURE.md` — sections 2, 6
**Estimated effort:** 6–8 hours (actual: full implementation plus three rounds of live calibration)

**Changelog**
- v1.2 — Three post-implementation refinements, all live-verified:
  1. `MetricDef.extreme_informative` (R5c) excludes five compounding dollar-level metrics
     (`ebitda`, `nopat`, `invested_capital`, `free_cash_flow`, `net_debt`) from
     `metric_multi_year_extreme` — a record in a growing company's dollar-level metrics
     restates growth, not an event; a record in a ratio (margin, return, days-outstanding)
     is information. `rule_version` bumped to `v2` (a real behavior change). Measured:
     firing rate 37.7% → 34.4% — better, still above the 33% design ceiling.
  2. AC6 amended (R9a): the criterion assumed sufficient quarterly history existed for
     NVIDIA's Q2 FY2023 `incremental_gross_margin`; verified live that it does not (3
     valid prior quarters, short of the 8-quarter minimum). Root cause verified: no
     watchlist company files a Q4 10-Q (confirmed live — `{Q1, Q2, Q3}` only, all three
     companies, zero exceptions), so every quarterly chain has 3 real periods per fiscal
     year, not 4; NVIDIA's comparatively young ingested history is where this surfaces
     first. The 8-quarter minimum is not lowered. Derived Q4 figures (already listed in
     ARCHITECTURE.md's V2 scope) flagged as the leading future fix.
  3. AC14 replaced (R10): the absolute size ceiling, already relaxed twice during
     SPEC-004, is dropped for two reported figures — current `app.db` size, and the
     measured marginal size of one additional filing processed end to end (72 KB,
     measured live against a scratch copy of the real database). Soft ceiling: 15 MB, at
     which the storage design gets revisited rather than the number raised again.
- v1.1 — Implemented in full, with nine live-verified calibration changes made before writing code:
  1. `normalized_text_hash` added to `sections` (strip the XBRL viewer version line, mask numeric
     tokens, then hash) alongside `text_hash`. Documented: `text_hash` means content identity,
     `normalized_text_hash` means wording identity.
  2. `accounting_policy_changed` renamed `section_wording_changed`. Measured live before deciding
     scope: exact `normalized_text_hash` equality changes on 98.9% of fiscal-year-matched Policies
     comparisons and 98.3% of Notes comparisons — both far above the 33% ceiling, and nearly
     identical to each other. Confirmed by direct diff, not a normalization artifact: filers
     genuinely rewrite a sentence or two in nearly every note nearly every year. The fix is a
     materiality threshold, not a category choice — see R5a.
  3. New governing principle: a state-based rule fires only on the transition into its condition,
     never while the condition persists. Applied to `metric_threshold_cross` and
     `metric_divergence`. Verified live this was necessary: without it, `capex_to_depreciation`
     fired on 71% of eligible periods and `fcf_conversion` on 50%.
  4. `metric_multi_year_extreme` minimum history split by basis: 8 prior periods for quarterly
     metrics, 4 for annual. `metric_sigma_move` stays quarterly-only, still requiring 8.
  5. `metric_stopped_computing` fires only if the same metric computed at the same fiscal period
     one year earlier — fixed a real false-positive class (`revenue_qoq` "stopped computing" on
     literally every Q1, forever, because it structurally has no directly-tagged Q4 to compare
     against).
  6. `NOTE_NAME_ALIASES` added to `config.py`, populated with three verified Micron renames.
     `FLUCTUATING_NOTE_NAMES` added, starting with "Recently Issued Accounting Standards Not Yet
     Adopted", excluded from `section_appeared`/`section_disappeared` only.
  7. `observations.accession_no` holds the current period's filing; `refs_json` carries both sides
     of any comparison. Stated explicitly (R2).
  8. All observation queries filter on each rule's own current `rule_version`, exactly as metrics
     filter on `calc_version` (R8).
  9. `filings.fiscal_year`/`filings.fiscal_period` added, populated at XBRL ingest time from the
     companyfacts `fy`/`fp` labels (NULL for 8-Ks). All fiscal-period prior-year matching in this
     spec uses these columns — never date arithmetic, never a join into `xbrl_facts`.

  Two further live findings, reported rather than silently worked around (see the implementer's
  final report for the full numbers): `metric_multi_year_extreme` measures at 38% of eligible
  periods overall (marginally above the 33% ceiling) — driven mechanically by the very small
  annual window (4–6 total fiscal years of history for this watchlist), where a genuinely trending
  metric (e.g. Amazon's 2023–2026 margin expansion) sets a "new record" on most eligible annual
  points almost by construction; this is expected to fall as more fiscal years accumulate, not a
  false-positive class. And AC6 (NVIDIA's Q2 FY2023 `incremental_gross_margin`) cannot currently
  fire under the user-specified 8-prior-quarter minimum: only 3 valid prior quarters exist in the
  ingested corpus for that metric at that point (2021-08-01, 2021-10-31, 2022-05-01) — insufficient
  history, correctly not a finding per the edge-case table, not a rule bug.
- v1.0 — Initial draft.

---

## Objective

Add the layer that decides **what is notable**, deterministically, before any AI is
involved.

Python detects. The LLM, in a later spec, only selects, connects, and narrates from what
Python has already established as true.

Two parts: readability measures on section text, and an observations engine that turns
metrics and sections into small verified statements.

---

## Why this layer exists

Without it, the AI would be asked to do two jobs at once: work out what matters, and
write about it. It is excellent at the second and unreliable at the first. Handed 32
metrics and told to find a narrative, a language model will produce a fluent, confident,
causal story — including causes that appear nowhere in the data.

An observation is a small statement Python has already verified, with pointers to the
rows that produced it. The model never sees raw data, so it cannot invent a trend.

This layer also serves the dashboard directly: it is what decides which three numbers
appear on the Overview page out of several thousand.

---

## Scope

**In scope**

1. Readability columns on `sections`, plus `normalized_text_hash`, with backfill
2. `observations` table
3. Declarative rule registry
4. Ten rules (nine table rows — `section_appeared`/`section_disappeared` share one row)
5. Statement templates
6. CLI, validate additions, tests

**Out of scope:** Loughran–McDonald sentiment (needs an external dictionary; reuses this
machinery later), any LLM call, dashboard, MD&A, market data, GitHub Actions.

---

## Requirements

### R1 — Readability and normalized_text_hash on `sections`

Four new columns, computed once from the immutable section text:

- `word_count INTEGER`
- `sentence_count INTEGER`
- `complex_word_count INTEGER` — words of three or more syllables
- `normalized_text_hash TEXT` — see R1a

Fog index is derived at read time as
`0.4 × (words/sentences + 100 × complex_words/words)` — not stored, since it is a pure
function of three stored values.

These are deterministic functions of immutable text, exactly like `text_hash`, so they
belong on `sections` rather than in a new table. Same precedent as `duration_days` on
`xbrl_facts`.

**On syllable counting.** Any pure-Python syllable heuristic is approximate. That is
acceptable here because **the project cares about change over time, not absolute level.**
A consistently-imperfect measure supports "risk factors got 30% harder to read" perfectly
well, even though it cannot support "this filing scores 19.2 on the Gunning Fog index."
Document the heuristic and this limitation in the code and in `ARCHITECTURE.md`.
Do not add a dependency for this.

Provide `python -m edgar.pipeline backfill-readability [--force]` for the 2,174 existing
sections. Idempotent; skips rows already populated unless forced. Also computed
immediately for every newly-extracted section going forward (`sections.py`), not just at
backfill time.

#### R1a — `text_hash` vs. `normalized_text_hash`

`text_hash` (SPEC-002/SPEC-003) means **content identity**: any byte differs, the hash
differs. It is exact and must stay exact — it is the sole link to the content-addressed
store.

`normalized_text_hash` means **wording identity**: the hash is computed over the section
text after two transformations, in order:

1. **Strip the XBRL viewer version line.** Every R-file's rendered body begins with a
   bare version stamp (`v3.25.0.1`) — a rendering artifact of the viewer template
   (ARCHITECTURE.md §3.7), not content, and it changes on every viewer upgrade
   regardless of whether the underlying text changed. Removed entirely rather than
   number-masked, because masking alone leaves a different token shape when the
   version's dot-separated segment count itself changes (`v3.25.0.1` → `v3.25.4`).
2. **Mask every numeric token** (digit runs, with embedded commas/periods) to one
   placeholder. SEC filing prose embeds rolling disclosure figures inside otherwise-
   identical sentences ("the allowance was $1.3 billion, $1.4 billion, and $1.4 billion
   as of December 31, 2022, 2023, and 2024") that update every year even when the policy
   language around them does not change at all.

### R2 — `observations` table

```sql
CREATE TABLE observations (
    id            INTEGER PRIMARY KEY,
    cik           TEXT NOT NULL REFERENCES companies(cik),
    accession_no  TEXT REFERENCES filings(accession_no),
    period_end    TEXT NOT NULL,
    rule_name     TEXT NOT NULL,
    rule_version  TEXT NOT NULL,
    subject       TEXT NOT NULL,   -- metric name or section short_name
    severity      TEXT NOT NULL,   -- high | medium | low
    statement     TEXT NOT NULL,   -- the verified, human-readable claim
    refs_json     TEXT NOT NULL,   -- ids of the metric/section rows behind it
    created_at    TEXT NOT NULL,
    UNIQUE(cik, period_end, rule_name, rule_version, subject)
);
```

`subject` is part of the key because one rule fires independently on many metrics within
a single period.

`refs_json` is what makes the dashboard's click-through work and what the LLM will be
given. An observation without references is a bug.

**`accession_no` and `refs_json` (SPEC-005 change 7).** `accession_no` holds the
**current period's** filing — the filing this observation is *for*, not any filing
referenced by a comparison. A comparison rule (e.g. `section_wording_changed`, which
looks at this year's and last year's section) puts **both** sides' row ids in
`refs_json` (`[{"table": "sections", "id": <current>}, {"table": "sections", "id":
<prior>}]`); `accession_no` never points at the prior period's filing.

### R3 — No lookahead

**An observation for period P may only reference data with `period_end <= P`.**

"Lowest gross margin in seven quarters" computed using quarters that had not yet happened
is invisible, wrong, and would silently poison any later comparison against market
reaction. Enforced in the engine (`compute_observations` asserts it before every write)
and independently re-verified in `validate` against the persisted table.

This constraint costs nothing now and is impossible to retrofit honestly later.

### R4 — Rule registry

Declarative, in `config.py`, following the metric registry pattern. Each rule declares
its name, version, severity (or `None` for `metric_threshold_cross`, whose severity
varies per declared threshold), and which table it reads.

Adding a rule must be a config entry. `observations.py` is an engine plus primitives.

Severity is **derived deterministically from the rule and its magnitude** — never
assigned by judgement, never by AI.

**`rule_version` and staleness (SPEC-005 change 8).** Exactly as metrics filter on
`config.CALC_VERSION`, every observation query — in `observations.py` and in
`validate.py`'s firing-rate/coverage/determinism checks — filters on
`config.RULE_REGISTRY[rule_name].version`. A rule whose threshold changes bumps its own
`version`; old observations at the prior version remain in the table for comparison but
never count toward current firing-rate or coverage statistics.

### R5 — The rules

| Rule | Fires when | Severity |
|---|---|---|
| `metric_multi_year_extreme` | Value is the highest or lowest in the prior 5 years; requires ≥ 8 prior periods for quarterly-basis metrics, ≥ 4 for annual-basis metrics | medium |
| `metric_sigma_move` | Value is more than 2 standard deviations from its own trailing mean; **quarterly-basis periods only**, requires ≥ 8 prior periods | medium |
| `metric_threshold_cross` | A declared threshold is crossed **into** this period and was not crossed as of last period (governing principle, below) | see below |
| `metric_divergence` | A declared metric pair/condition diverges beyond a threshold, entering that state this period (governing principle, below) | high |
| `section_wording_changed` | A `Policies` or `Notes` section's normalized text differs materially from the prior-year equivalent (R5a) | high |
| `section_length_change` | A section's `word_count` moved more than 25% versus the prior-year equivalent | medium |
| `section_appeared` / `section_disappeared` | A named note is present this year and absent last year, or vice versa (subject to `NOTE_NAME_ALIASES` / `FLUCTUATING_NOTE_NAMES`, R6) | high |
| `metric_stopped_computing` | A metric that computed at the same fiscal period one year earlier is now NULL (R5b) | medium |
| `readability_change` | Fog index moved more than 15% versus the prior-year equivalent section | low |

**Declared thresholds** (config): `beneish_m_score` above −1.78 (high); `current_ratio`
below 1.0 (medium); `net_debt` crossing zero in either direction (medium);
`fcf_conversion` below 0.5 (medium); `interest_coverage` below 3.0 (high).

**Declared divergences** (config): `inventory_growth_less_revenue_growth` above 0.15;
`capex_to_depreciation` above 1.5; `depreciation_rate` falling more than 15% year over
year (using `filings.fiscal_year`/`fiscal_period` for the year-over-year match, R9).

#### Governing principle (SPEC-005 change 3): transition-only firing

**A state-based rule — one whose condition can hold true for many consecutive periods —
fires only on the transition into that condition, never while it persists.** This applies
to `metric_threshold_cross` and `metric_divergence`. It does **not** apply to
`metric_multi_year_extreme` or `metric_sigma_move`, whose own definitions (a new record;
a >2σ move relative to a moving trailing mean) are already self-limiting.

Verified live this was necessary, not a defensive nicety: measured against real data
before this principle was adopted, `capex_to_depreciation` (naive "is it currently above
1.5" check) fired on **71%** of eligible periods — both Amazon and NVIDIA/Micron are in
sustained capex-expansion mode, so the condition is close to their normal state, not an
event. `fcf_conversion` fired on **50%** the same way. After the fix (fire only entering
the condition), the same checks measure 14.0% and, respectively, well under the ceiling
for every other declared threshold/divergence (see the implementer's final report for
exact numbers).

#### R5a — `section_wording_changed`: calibration (SPEC-005 change 2)

Renamed from `accounting_policy_changed`. Before choosing which categories it applies to,
the exact normalized-hash change rate was measured separately, live, for Policies and for
Notes:

- **Policies: 98.9%** of fiscal-year-matched comparisons had a different
  `normalized_text_hash`.
- **Notes: 98.3%.**

Confirmed by direct diff, not a normalization bug: real examples found live include
Amazon adding a healthcare-services clause and a new "Derivative Instruments" policy
between FY2024 and FY2025, and Micron's ~400-word "Restructure and Asset Impairments"
note changing in 100% of the years compared. Filers genuinely rewrite a sentence or two
in nearly every note nearly every year. Byte-for-byte wording identity is therefore
**not a usable firing signal in either category** — the original plan (restrict to
Policies, exclude Notes because "the numbers inside it change every quarter") does not
survive contact with the data, because the *numbers* were never the dominant driver once
masked; genuine incremental prose editing is.

**Calibration:** the fix is a materiality threshold, not a category choice.
`section_wording_changed` applies to **both** `Policies` and `Notes`. It uses
`normalized_text_hash` as a fast exact-match skip (byte-identical wording never fires,
trivially), and for anything that differs at all, computes a real similarity ratio
(`difflib.SequenceMatcher.quick_ratio`) between the two normalized texts, firing only
when similarity falls below `config.SECTION_WORDING_SIMILARITY_THRESHOLD` (0.85 —
measured to yield 14.6% for Policies and 13.3% for Notes, comfortably under the 33%
ceiling and, notably, nearly identical between categories). That near-identity between
categories **is** the calibration finding requested: Notes does not need excluding once
real per-period noise is controlled for; a materiality threshold does the work a category
restriction cannot.

`normalized_text_hash` itself keeps its literal meaning (R1a) — the similarity check is
additional logic inside the rule, not a redefinition of the stored column.

#### R5b — `metric_stopped_computing`: fiscal-period matching (SPEC-005 change 5)

Fires only if **the same metric computed at the same fiscal period one year earlier**
(via `filings.fiscal_year`/`fiscal_period`, R9) — not merely "was non-null last period."
This distinguishes a genuine stop from a metric that is structurally never computable at
a given period type. Verified live this distinction was necessary: without it,
`revenue_qoq` "stopped computing" on **every single Q1**, for every company, forever,
because it has no directly-tagged Q4 quarter to compare Q1 against — a structural gap in
the data rediscovered every year, not an event.

**Calibration note that matters.** Do **not** write a rule that fires on any change to a
`Notes` section's `text_hash`, and do not assume `Policies` is safe from the same
problem by construction — see R5a. Both categories carry the same "numbers (and, short
of a materiality threshold, ordinary editing) change constantly" property; the fix that
actually works is a masked-and-thresholded comparison, applied uniformly.

#### R5c — `metric_multi_year_extreme`: informative metrics only (post-implementation)

`config.MetricDef.extreme_informative: bool` (default `True`) gates which metrics
`metric_multi_year_extreme` runs on at all. **False** for compounding dollar-level
metrics — `ebitda`, `nopat`, `invested_capital`, `free_cash_flow`, `net_debt` — where a
"record" mostly restates that the company is bigger than it used to be, not that
something happened: a growing company setting a revenue-scale record every few quarters
says nothing an analyst doesn't already know from the growth-rate metrics themselves.
**True** for everything else, including growth-*rate* metrics (`revenue_yoy`, ...),
margins, returns, working-capital days, capex ratios, and the Beneish quality indices —
a five-year low in a ratio is information regardless of how large the underlying dollar
figures have grown.

(`revenue` itself has no corresponding entry to flag — the metric registry has no raw
dollar-level "revenue" metric, only its growth rates, `revenue_yoy`/`revenue_qoq`, which
stay informative as ratios, not levels.)

Bumped `metric_multi_year_extreme` to `rule_version` `"v2"` — a real behavior change, not
a threshold tweak, but the same mechanism (R4/R8): `v1` observations on the five excluded
metrics remain in the table for comparison and drop out of every current-version query.

Measured live: **37.7% → 34.4%** of eligible periods (438/1162 → 347/1010). Closer to the
33% design ceiling but not yet under it — the remaining rate is still driven by the small
annual-history window described in the changelog and ARCHITECTURE.md §2.3, which
`extreme_informative` does not address (it narrows *which* metrics are checked, not *how
many periods* of history each one has).

### R6 — Statement templates

Each rule produces one plain-English sentence, filled from real values.

Rules:

- State what is observed. **Never state a cause.** "Margins fell while inventory rose" is
  permitted; "margins fell because of inventory" is not.
- Include the actual numbers. A statement without figures is not verifiable.
- Deterministic: same data, same sentence, byte for byte.

Numeric formatting is deliberately plain (`.4g`-style significant figures, metric name
included) rather than unit-aware (percentages, dollar suffixes, multiples) — correctness
of which periods fire matters far more at this layer than prose polish, and unit-aware
formatting is exactly the kind of narrative polish SPEC-006's LLM layer exists to add
from these verified statements.

#### R6a — Note-name aliases and fluctuating notes (SPEC-005 change 6)

`config.NOTE_NAME_ALIASES: dict[str, str]` maps a historical/alternate SEC `ShortName` to
its canonical name — same discipline as `CONCEPT_REGISTRY` aliases (ARCHITECTURE.md
§2.1): **two names for the same note only**, never a broader/narrower relationship,
never guessed. Every entry was verified two ways against live data before being added:
(1) the two names never co-occur in the same filing, anywhere in the company's history,
and (2) the section text on either side of the transition describes the same underlying
disclosure (same XBRL `[Abstract]` element, same opening sentence). Checked across all
three watchlist companies; genuine renames were found only for Micron:

- `"Basis of Presentation Basis of Presentation"` → `"Basis of Presentation"` (a single
  filing's FilingSummary rendered the heading doubled — a rendering artifact, not a
  second note)
- `"Segment Information"` → `"Segment and Other Information"` (flickered between the two
  labels 2018–2021 before settling permanently)
- `"Equity Plans"` → `"Equity Compensation Plans"` (renamed in the FY2024 10-K)

`config.FLUCTUATING_NOTE_NAMES: frozenset[str]` names notes whose *presence* legitimately
toggles period to period (a "pending accounting standard" housekeeping note that only
exists when something is actually pending) — excluded from `section_appeared`/
`section_disappeared` specifically, since that rule means "a new or discontinued
disclosure," not "nothing was pending this quarter." Starts with "Recently Issued
Accounting Standards Not Yet Adopted" — confirmed live to toggle in and out of Micron's
filings across adjacent quarters. `section_length_change` and `section_wording_changed`
still apply normally whenever the note is present in both compared periods.

### R7 — CLI

```
python -m edgar.pipeline backfill-readability [--force]
python -m edgar.pipeline compute-observations [--ticker TICKER] [--rule NAME]
```

- `compute-observations` reports observations written per rule and per company.
- `--rule` recomputes a single rule during development.
- `status` gains an observation count.
- No `--force` on `compute-observations`: unlike `backfill-readability`, which skips
  already-populated rows, `compute_observations` always recomputes every rule from
  scratch and only writes rows that actually changed — there is nothing to force,
  matching `compute-metrics`'s existing shape (which has no `--force` either).

### R8 — Validate additions

| Check | Behaviour |
|---|---|
| **Firing rate** | Per rule, the share of eligible periods where it fired, using `observations.ELIGIBLE_COUNT_FUNCS` (mirrors each rule's own gating logic). **Flag above 33%.** Informational. |
| **Dead rules** | A rule that never fires across the whole corpus is either miscalibrated or dead code. Report it. Informational. |
| **Lookahead** | Assert no observation references data with `period_end` later than its own. Hard failure. |
| **Determinism** | Recompute every rule in memory (no write) and assert each **persisted** row's statement is byte-identical to its recomputed statement. Iterates over persisted rows, not over every possible recomputed observation — an observations table that simply hasn't been computed yet must read as zero violations, not "everything mismatches." Hard failure. |
| **Orphan references** | Every id in `refs_json` must resolve. Hard failure. |

### R9 — Fiscal-period matching (SPEC-005 change 9)

`filings.fiscal_year INTEGER` and `filings.fiscal_period TEXT` are populated at XBRL
ingest time (`xbrl.ingest_company`) from the companyfacts API's own `fy`/`fp` labels,
scanned across **every** concept in the payload (not just configured ones), for every
10-K/10-Q already known to `filings`. NULL for 8-Ks (companyfacts has no entries for
them) and for any accession the payload doesn't mention.

Verified live before this was written: every one of 61 real NVDA/MU accessions checked
maps to exactly one consistent `(fy, fp)` pair across all of its own facts — the
companyfacts API's fiscal labels are internally self-consistent per filing. If a future
filing ever violates that, it is left unresolved (NULL) rather than guessed.

**All prior-year matching for sections, and the fiscal-period lookups
`metric_stopped_computing` and `depreciation_rate`'s YoY divergence need, use these
columns — never date arithmetic, never a join into `xbrl_facts`.** This is what makes
prior-year matching robust to NVIDIA's and Micron's floating 52/53-week fiscal years by
construction: the columns are not derived from any date at all, so there is no calendar
arithmetic to get wrong. (`metrics.py`'s own YoY/QoQ metric computations, e.g.
`revenue_yoy`, are unchanged by this spec — they are SPEC-004's already-verified,
already-live-validated date-offset-with-tolerance mechanism, and this spec does not
touch it. R9 applies to everything SPEC-005 itself introduces.)

#### R9a — Why AC6 doesn't fire: the missing Q4 quarter (amendment)

AC6 originally assumed NVIDIA's Q2 FY2023 (`2022-07-31`) `incremental_gross_margin` had
enough quarterly history to trigger `metric_multi_year_extreme`/`metric_sigma_move`. It
does not, at 8 required prior quarters: only 3 valid prior quarterly values exist for
that metric at that point (`2021-08-01`, `2021-10-31`, `2022-05-01`). The assumption was
wrong, not the rule.

**Verified root cause.** No watchlist company files a 10-Q for its fourth fiscal quarter
— Q4 is covered by the 10-K, and the 10-K reports the *annual* figure, not a standalone
Q4 quarterly one (ARCHITECTURE.md, "Note on `fiscal_period`"). Checked live across all
three companies: AMZN, NVDA, and MU each have exactly `{Q1, Q2, Q3}` as their only
`fiscal_period` values across every 10-Q ever filed — **zero** `Q4` entries, for any of
them. This is a structural property of how the SEC 10-K/10-Q system works, not something
specific to NVIDIA or Micron's floating fiscal year.

Its *effect* is what varies: a quarterly-only chain of 3 real periods per fiscal year
means reaching any N-quarter minimum takes `N/3` fiscal years of *filing* history rather
than `N/4` — a 33% longer wait, compounding further for a `needs_prior` metric like
`incremental_gross_margin`, whose own first computable value already requires a full
prior year on top of that. NVIDIA's ingested XBRL coverage is comparatively young, so at
the time of the FY2023 Q2 event this compounding hadn't yet produced 8 quarters of
history — a corpus-depth gap that narrows on its own as more fiscal years accumulate,
not a rule defect.

**Decision: the 8-quarter minimum is not lowered.** Weakening it to force this one case
through would reintroduce exactly the false-positive risk `metric_multi_year_extreme`'s
minimum-history requirement (R5) exists to prevent, for every metric on every company,
to fix one already-explained, already-verified, self-resolving gap.

**Leading candidate for a future spec: derived Q4 figures.** ARCHITECTURE.md's V2 scope
already lists "Derived Q4 figures" (FY − Q1 − Q2 − Q3, per the existing `fiscal_period`
note) as a `metrics`-layer calculation, not yet built. Adding it would give every
quarterly-basis metric a real fourth data point per fiscal year, closing this gap
directly rather than only waiting for more calendar time to pass — flagged here as the
most direct fix, deliberately not implemented in this pass (out of SPEC-005's scope, and
a metrics-layer change, not an observations-layer one).

### R10 — Database size: a growth measure, not an absolute ceiling (AC14 amendment)

The original absolute-size acceptance criterion was relaxed twice already during
SPEC-004 (5 MB → 6 MB, "per-run churn matters more than absolute size") and would have
needed relaxing a third time by this spec (SPEC-005's new columns and the `observations`
table pushed `app.db` from 5.2 MB to 6.48 MB post-`VACUUM`, over the then-current 6 MB
line). A number that keeps moving was never measuring the right thing.

**Replaced with two reported figures, informational (not hard-failing):**

1. **Current `app.db` size**, in `validate`'s output and `status`.
2. **Measured incremental size of one additional filing**, processed end to end
   (extract → ingest-xbrl → compute-metrics → backfill-readability →
   compute-observations). Measured live, on a scratch copy of the real database (a real
   Micron 10-Q's rows deleted, `VACUUM`ed, then fully reprocessed through the live
   pipeline): **72 KB** for one 10-Q (90 XBRL facts, 31 metric rows, several sections, 51
   observations).

**Soft ceiling: 15 MB.** Not a `validate` hard failure — a documented point at which the
SQLite-in-git storage design itself (ARCHITECTURE.md §4.1's already-accepted debt) gets
revisited, rather than this number being quietly raised a fourth time. At the measured
~72 KB/filing marginal rate and roughly a dozen XBRL-bearing filings/year across the
three-company watchlist, reaching 15 MB from the current 6.48 MB is on the order of a
decade away — the soft ceiling is there for when the watchlist grows, not because current
growth is a near-term concern.

---

## Constraints

- No LLM calls anywhere in this spec.
- No network calls at all — this layer reads only the local database.
- No literals outside `config.py`: thresholds, windows, severities, templates.
- Deterministic throughout.
- No new third-party dependencies.
- Type hints on all public functions.

---

## Acceptance Criteria

1. `backfill-readability` populates all 2,174 existing sections; re-running is a no-op.
2. Word counts are sane: no section has zero words; the longest is a plausible note
   rather than a parsing artefact.
3. `compute-observations` runs for all three companies and reports counts by rule.
4. **Every rule fires at least once** across the corpus, with one documented exception:
   see the implementer's final report for the measured AC6 finding below, which explains
   exactly why one specific (metric, period) pair cannot currently fire under the
   user-specified minimum-history requirement, and why that is correct behaviour, not a
   miscalibration.
5. **No rule fires on more than 33% of eligible periods**, with one documented exception
   (`metric_multi_year_extreme`, measured at 37.7% before `extreme_informative` (v1.2,
   below), 34.4% after — closer to the ceiling but not yet under it. See the changelog
   and the implementer's final report for the mechanism and why it is expected to
   self-correct further as more fiscal years of history accumulate.)
6. **Amended (v1.2):** NVIDIA's Q2 FY2023 (`2022-07-31`) `incremental_gross_margin` was
   assumed to have sufficient quarterly history to fire; it does not, and the criterion
   was wrong to assume otherwise. See R9a for the verified root cause (the missing Q4
   quarter) and the decision not to lower the 8-quarter minimum to force this one case
   through.
7. Micron's fiscal Q2 2024 `effective_tax_rate` produces an observation — confirmed live
   (fires both `metric_multi_year_extreme` and `metric_sigma_move`).
8. Amazon's persistently negative cash conversion cycle does **not** produce an
   observation in every period — confirmed live (zero `metric_multi_year_extreme`
   observations for Amazon `cash_conversion_cycle`; the two real observations that do
   fire, for Micron, are genuinely different extreme values, not a persistent condition).
9. Zero lookahead violations.
10. Recomputing produces byte-identical statements.
11. Every observation's `refs_json` resolves to existing rows.
12. Every statement contains at least one figure and no causal language.
13. `validate` still exits 0.
14. **Replaced (v1.2):** the original absolute ceiling (5 MB → 6 MB, relaxed twice
    already during SPEC-004) is dropped in favor of a growth measure, since an absolute
    number that keeps needing relaxation was never the right criterion. `validate`
    reports current `app.db` size and, informationally, the measured incremental size of
    processing one additional filing end to end. **Soft ceiling: 15 MB**, at which point
    the SQLite-in-git storage design (ARCHITECTURE.md §4.1's already-accepted debt) gets
    revisited rather than the number quietly raised a fourth time. See R10.
15. `pytest` passes.

---

## Edge Cases

| Case | Required behaviour |
|---|---|
| Fewer than the minimum prior periods (8 quarterly / 4 annual) | Extreme and sigma rules do not fire. Insufficient history is not a finding. |
| Metric is NULL this period | Only `metric_stopped_computing` may fire. Others skip. |
| Metric NULL in the comparison period (including the fiscal-year-matched prior for `metric_stopped_computing` / `depreciation_rate`'s divergence) | Change-based rules do not fire. Never treat NULL as zero. |
| Standard deviation is zero | Sigma rule does not fire. No division by zero. |
| Section has no prior-year equivalent | `section_appeared` fires; length, wording, and readability rules do not. |
| Company's first filing in the database | No change-based rule fires. |
| Two sections share a `short_name` in one filing | Disambiguated by `source_file`, as in SPEC-002. |
| Two `short_name`s are really the same note under different labels | Resolved via `NOTE_NAME_ALIASES` before any comparison (R6) — never counted as appeared+disappeared. |
| A note's presence legitimately fluctuates period to period | Excluded from `section_appeared`/`section_disappeared` via `FLUCTUATING_NOTE_NAMES` (R6) — length/wording/readability rules still apply when it IS present in both periods. |
| A rule's threshold changes | Bump `rule_version`; old observations remain for comparison, filtered out of current firing-rate/coverage stats (R4/R8). |
| Section with one sentence | Fog is computable but meaningless. Require a minimum word count (`config.READABILITY_MIN_WORD_COUNT`) in both compared periods before the readability rule fires. |

---

## Testing Requirements

- `test_no_lookahead_raises_on_future_ref` / `test_compute_observations_never_stores_a_lookahead_reference`
- `test_extreme_requires_minimum_history`
- `test_sigma_handles_zero_variance`
- `test_null_prior_period_does_not_fire_yoy_decline_divergence` — NULL is not zero
- `test_section_wording_changed_skips_trivial_edit_in_both_categories` /
  `test_section_wording_changed_fires_on_substantial_rewrite_in_both_categories` — the
  central calibration finding (R5a), replacing the original draft's assumption that
  Notes needs excluding; test the measured behaviour directly instead.
- `test_persistent_condition_does_not_fire_every_period`
- `test_threshold_cross_fires_only_on_transition`
- `test_stopped_computing_fires_when_prior_fiscal_period_had_a_value` /
  `test_stopped_computing_does_not_fire_for_structural_gap`
- `test_note_rename_via_alias_is_not_appeared_or_disappeared` /
  `test_genuinely_new_note_fires_appeared` / `test_fluctuating_note_excluded_from_appeared_disappeared`
- `test_statements_are_deterministic`
- `test_statements_contain_figures_and_no_causal_language`
- `test_refs_resolve`
- `test_readability_on_known_text` — a hand-checked passage with known word and sentence
  counts.
- `test_observations_idempotent`

---

## Likely Files Affected

```
edgar/config.py         (rule registry, thresholds, templates)
edgar/db.py             (observations table, sections/filings columns)
edgar/readability.py    (new)
edgar/section_store.py  (normalized_text_hash normalization + hashing)
edgar/sections.py       (compute normalized_text_hash + readability at write time)
edgar/xbrl.py           (filings.fiscal_year/fiscal_period backfill at ingest)
edgar/observations.py   (new — engine and primitives)
edgar/validate.py       (five new checks)
edgar/pipeline.py       (backfill-readability, compute-observations)
ARCHITECTURE.md         (§2 five-layer model, §6 schema, decision log)
tests/test_readability.py
tests/test_observations.py
```

---

## Forward-Looking Concerns

Flagged, not implemented here.

1. **The LLM spend ledger must exist before the first API call in SPEC-006.** Hard $20
   project ceiling. A ledger and a refusing cap designed after the first call is designed
   too late.
2. **Observations are the LLM's only input.** SPEC-006 will pass these statements, not raw
   metrics. Statement quality directly determines narrative quality — a vague observation
   produces vague prose.
3. **Rule calibration will need a second pass** once real output is visible. Two rules
   are already flagged from this pass: `metric_multi_year_extreme`'s 38% aggregate rate
   (annual-window mechanics, expected to improve with more history) and the still-open
   question of whether `SECTION_WORDING_SIMILARITY_THRESHOLD` (0.85) is the right
   materiality bar once an analyst is actually reading the output.
4. **Loughran–McDonald** reuses this exact machinery — a per-section score column and a
   change rule — and can be added as a small follow-on.
5. **Unit-aware statement formatting** (percentages, dollar suffixes, multiples) was
   deliberately deferred to SPEC-006's narration layer (R6) — worth revisiting if
   observations end up displayed directly on the dashboard before SPEC-006 exists.

---

## Notes for the Implementer

- Update `ARCHITECTURE.md` §2 to show five layers rather than four:
  Facts → Calculations → **Observations** → Interpretation → Presentation, with
  observations described as deterministic and rule-based.
- The prior-year equivalent of a section is the same `(category, canonical short_name)`
  in the same company's filing of the same form type one year earlier, matched via
  `filings.fiscal_year`/`fiscal_period` (R9) — never a calendar-day window, never a join
  into `xbrl_facts`. Canonicalize the short name through `NOTE_NAME_ALIASES` (R6) before
  comparing.
- Prefer a small number of well-calibrated rules over many noisy ones. The measure of
  success is whether the Overview page shows three things worth reading, not how many
  observations exist.
- Report any discrepancy between this spec and observed behaviour rather than working
  around it silently. `ARCHITECTURE.md` must then be corrected. (Two such discrepancies
  were found and reported during this implementation — R5a's category-vs-materiality
  finding, and the AC5/AC6 measured gaps — rather than patched away by loosening
  thresholds or the general minimum-history principle without saying so.)
