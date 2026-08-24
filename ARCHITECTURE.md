# Equity Research Platform — Architecture

**Version:** 2.23
**Date:** 2026-08-08
**Owner:** Zakaria
**Status:** Approved for V1 implementation — SPEC-006 and SPEC-007 complete; SPEC-008 built, second real-browser review in progress (SPEC-008-review-2-2026-08-04.md), first genuinely new filing ingested since the dashboard build

**Changelog**
- v2.23 — D12 (decision log #68, SPEC-008 v1.15): Q4 was missing from the income statement
  for every company, every year -- the same discrete-quarter mechanism built for cash flow
  (v2.22), generalized rather than reinvented. `_DISCRETE_QUARTER_CANONICALS` now covers all
  10 income-statement lines; a new `Income Statement` METRIC_REGISTRY group. Confirmed
  against the real corpus: AMZN's derived Q4 2024 revenue reproduces the review's own
  example exactly (187,792,000,000). A pre-existing side effect (the Summary tab's period
  dropdown now shows two entries for one fiscal year-end) found and flagged, not fixed --
  out of scope per instruction ("D12 only").
- v2.22 — Cash-flow discrete-quarter derivation hardened (decision log #67, SPEC-008 v1.14):
  a `validate` sum-back check (category 33), `plausible_range` enforced at compute time
  (fail-closed on an implausible subtraction result), the PPE `fallback_canonical`
  mechanism reused instead of a third resolution pattern, and display collapsed from two
  rows to one with per-cell derived marking. MU's FCF-blank rate fell from 68% to 5%; cfo
  growth%, previously 0% populated, reached 92% (MU) and 83% (NVDA).
- v2.21 — Live-found immediately after v2.20 shipped (decision log #66, SPEC-008 v1.13):
  `TextColumn(alignment=...)` broke for the operator because a bare `streamlit` on PATH
  resolved to an unrelated Anaconda 1.51.0, not this project's `.venv` (1.60.0) -- clean
  under every verification this agent ran, wrong in the one environment that mattered.
  Fixed by removing the call (the declared floor, `>=1.51`, is a supported environment, not
  a stale one) and adding `components.environment_caption()`, always visible in the
  sidebar, so a future gap between this agent's `.venv` and an operator's actual runtime is
  immediately diagnosable rather than silently unreproducible.
- v2.20 — C4 rebuilt on `st.dataframe` (decision log #65, SPEC-008 v1.12): the first real-
  browser look at C4 found `st.columns`-per-row unreadable at real column counts (MU's 28
  quarterly periods). Pinned label column, chronological periods, a default newest-N window
  in place of an unavailable scroll-to-position API, growth moved to its own row, and a
  three-way blank-cell cause (never tagged / split-row complement / duration-absent) restored
  with a footnote key. Verified against the real corpus (36 company × basis × statement
  combinations, zero exceptions); the visual pass is the user's own, not claimed here.
- v2.19 — Investigated, did not implement, two proposed LLM cost changes (decision log #64):
  prompt caching on the section-analysis prompt (mechanically zero under the current
  interleaved template, ~25-40 stable prefix tokens against a 1,024 minimum — would need a
  genuine content-driven v4 to fix, never worth doing for caching alone) and the Batches API
  (a real 50% but only ≈$1-2/year at this project's scale, and it converts L3/L7 from a live
  circuit breaker to a pre-submission estimate gate — revisit threshold recorded at ≈$12/year
  in annual section-analysis spend). Added §2.6: a standing requirement that any future
  prompt-version bump to a cached prompt must state its corpus re-analysis cost alongside its
  rationale, the way SPEC-007's v2 change (#56) happened to but was never required to.
- v2.18 — First pipeline run on a genuinely new filing since the dashboard was built
  (decision log #61-63): AMZN's 2026-07-31 10-Q and same-week 8-K, in two approved steps.
  Step 1 (ingest through section analysis, $0.1881 actual) surfaced two real bugs before
  any dashboard code ran: `anthropic` was never declared in `pyproject.toml` (fixed,
  same discipline as #52), and scoping `compute-observations` to `--ticker AMZN` broke
  cross-company same-day observation annotation on MU's own stored data (re-run unscoped,
  fixed — the function's own docstring had already warned this exact scoping would do
  this). Step 2 reversed the D6 brief-regeneration deferral (decision #56) — approved,
  run unscoped, actual cost $0.5963 against a $0.3126 estimate, root cause the same
  generator-sentence-count pattern already on record at #51, not a new regression. All of
  D11's exact-period matching, the PP&E fallback, and the cash-flow duration fallback
  (built without this filing's data to test against) resolved correctly against it, and
  C4's growth% computed correctly on a freshly-added rightmost column for the first time.
- v2.17 — C4 built (decision log #60): the Financials "Table" tab, on top of C5's sub-tab
  bar. Both risky design decisions (cash-flow duration mixing, PP&E's split row) were
  proposed and checked against the real corpus before implementation, per instruction. A
  real bug (the split-row fallback-inclusion check was one-sided) was caught and fixed mid-
  build, with a test added, before it shipped.
- v2.16 — Seventh pass (decision log #59): all of v2.15's fixes confirmed working live. The
  popover's double chevron dropped (its own built-in indicator is now the entire visible
  caret). C5 built: `components.sub_tab_bar`, a shared horizontal sub-tab bar whose selection
  survives an unrelated rerun the same way the sidebar company selector already survives page
  switching (R4a) — confirmed live, proven against one real consumer rather than also wired
  into Financials this pass. C6 built: Metrics' hardcoded seven-category `group_order` list
  removed — `_group_metrics()` already derived the correct, registry-ordered grouping;
  sub-tabs now iterate it directly. The AC5 demonstration was extended live: a throwaway
  metric under a brand-new category produced an eighth tab with zero page-file changes, then
  was removed — locked in by a permanent test, not left as a one-time manual check.
- v2.15 — Sixth pass on the same real-browser review (decision log #58). C2's raw-HTML caret
  abandoned after a second live failure (still on its own line) plus a dark→light theme
  regression, both traced to Streamlit's client-side DOMPurify sanitization of
  `unsafe_allow_html` content stripping `style` attributes — confirmed cheaply via the
  installed package's bundled JS, not a browser session. Replaced with native Streamlit
  (`st.columns` + `st.popover("▾")`); `format.escape_html`/`unsafe_allow_html` removed
  entirely. `.streamlit/config.toml` added (`base = "dark"`, tracked in git) so the theme is
  an explicit setting rather than an implicit client-side default. Also fixed: MU's free cash
  flow read "not tagged" beside its own two visible, present inputs — it's genuinely computed
  only at a different duration than the one the Cash flow section was showing them at (v2.14's
  D11 follow-up); the NULL reason now says so.
- v2.14 — Fifth pass on the same real-browser review (decision log #57). The C2 caret's
  layout bug (decision #56) confirmed live and fixed: `<details>` is a CommonMark
  block-starting tag, splitting from preceding text into a sibling block regardless of its
  own `display:inline`; fixed by wrapping the whole construct in one outer `<span>` from the
  string's first character. `get_statement_line_values` gained a per-line
  `(fallback_canonical, fallback_label)`, checked across the real corpus first: `ppe_net` is
  absent for 100% of AMZN's history and MU's from FY2021-Q1 on. On fallback the row's LABEL
  changes, not a note on the primary label. `debt_noncurrent` has the identical shape for
  Micron but was left alone (no vetted canonical exists; the same semantic judgment the
  standing Micron debt-tag diagnosis already deferred) — only its NULL reason was corrected,
  to say the absence is structural rather than period-specific. Recorded as binding on C4:
  a label-level fallback works for one period at a time, but C4's periods-as-columns table
  would show a single row silently meaning two different things across its own columns —
  not solved here.
- v2.13 — Fourth pass on the same real-browser review (decision log #56, which supersedes
  #55's visible source-count line without reversing #55's AC7 amendment — read as a
  sequence). C2's "N sources" line was itself found too heavy at real scale; replaced with a
  small inline caret at the end of each sentence that opens its sources in place, rendered
  only when a sentence has sources (a sentence with none gets no caret at all, so its absence
  is now what makes it conspicuous) — the first use of `unsafe_allow_html=True` in this
  dashboard, with both D1's currency-escape and new HTML-tag-escaping applied to all
  model-/DB-generated text before it reaches raw HTML. `get_statement_line_values`'s D11 fix
  (decision #55) over-corrected for instant (balance-sheet) concepts in one reported case,
  though the code as reviewed already branches correctly on `CONCEPT_REGISTRY[...].instant`
  and the specific reported failure could not be reproduced against it — treated as very
  likely the same stale-`st.cache_data` trap already documented at #54. Separately, confirmed
  live: 10-Q cash-flow concepts are routinely tagged year-to-date only; the Cash flow section
  now falls back to the one unambiguous alternative duration when the quarterly one has no
  data, stating so explicitly, and still refuses to guess among multiple candidates.
  `null_metric_tile`'s caption no longer repeats "Not available" before its reason.
- v2.12 — Third pass on the same real-browser review (decision log #55): D3's second null
  path fixed (`metric_tile` itself, not just `null_metric_tile` — a row can exist with
  `value=None`, which used to truncate the same way); D11 fixed (`get_statement_line_values`
  now requires an exact `(period_start, period_end)` match for duration concepts, closing a
  real defect where a 10-Q's nine-month year-to-date figure could silently be shown where the
  three-month quarter was expected, sharing an end date and `filed_date` with no principled
  tie-break; every duration-based statement section and the period selector now state their
  duration in words). **AC7 amended, not dropped (C2)**: sources collapse behind a click; an
  unclickable, accurate source count (including "0 sources") stays visible on every brief
  sentence regardless, which is what preserves AC7's underlying constraint once the sources
  themselves move behind a click — the argument on both sides is recorded in full in decision
  log #55, since AC7's own text has now changed. `[restatement]`/`[juxtaposition]`/
  `[grouping]` display prefixes dropped from the page; `sentence_type` is unchanged in the
  database.
- v2.11 — Second pass on the same real-browser review (decision log #54): D3 (NULL reason
  unreachable behind `st.metric` truncation), D7 (Beneish M-score shown with no threshold),
  D8 (mixed as-of dates with no visual separation), and D10 (the same observation duplicated
  verbatim between "The brief" and "What changed?") fixed. Also noted, not fixed: a
  stale-`st.cache_data` trap in `dashboard/data.py` general to the whole module, found when
  D2's already-correct fix initially looked broken in a long-running dev session
  (SPEC-008 "A stale-cache trap in dashboard/data.py"). D10 was checked against the standing
  instruction to stop rather than half-fix if it required deciding AC7/C2 first — it did
  not, and AC7 remains undecided.
- v2.10 — First real-browser dashboard review (decision log #53): D1's currency-escaping
  fix made structural (three `_safe_*` render wrappers, a grep test, and `AppTest`
  rendering-path tests — the seven hand-applied call sites from the first pass weren't
  enough); D2 (missing company name), D4/D9 (usd tiles/axes with no unit; percent axes on
  the raw ratio scale), and D5 (raw snake_case identifiers and unformatted ratios in
  observation statements, fixed in `edgar/observations.py` and regenerated against the real
  corpus via `compute-observations`) all fixed. D6 (LLM-authored brief prose formatting
  drift between companies) fixed at the prompt level only (`filing_brief_v2.md`,
  `BRIEF_GENERATOR_PROMPT_VERSION` → `v2`) — invalidates every filing's brief cache;
  regenerating them is a real LLM cost, not incurred this pass. D3 and C2 flagged, not
  fixed, per explicit instruction.
- v2.9 — SPEC-008 (Dashboard) implemented; a real packaging/verification gap found the
  first time `streamlit run` was tried outside the build environment (decision log #52):
  `dashboard` was never installed in the project's own `.venv` (the editable install
  predated `pyproject.toml`'s `dashboard*` addition; `streamlit`/`plotly` were new
  dependencies too), and every build-time check passed anyway because it ran through a
  different Python environment whose accidental CWD-based import resolution masked it.
  Fixed with `pip install -e .`; the deployment implication (Streamlit Cloud builds a fresh
  environment on every deploy and will reproduce this exact failure unless its build step
  installs the project itself, not just third-party dependencies) is recorded as binding on
  the future deployment spec in SPEC-008 directly.
- v2.8 — SPEC-007 (The Grounded Brief) complete (decision log #51): capped/ranked
  observation+finding selection (R2 v2.1's diversity fixes), typed-sentence generation with
  per-type mechanical verification (R4, including the unit-aware aggregation check, §2.5),
  and an independent adversarial verifier pass (R5) — all built and tested against fakes
  first, including all three required pre-execution demonstrations. Full run: 18 briefs, 243
  sentences returned, 204 kept (restatement 70%, juxtaposition 19%, grouping 10%,
  sourced_causal/aggregation ~0.5% each), 19 dropped at the type check, 20 dropped by the
  verifier. Actual cost $0.5250 — slightly over the ~$0.50 estimate, because every brief
  ignored the prompt's "3 to 6 sentences" instruction (8–15 kept per brief, never once in
  range) -- reported as a real finding for a future prompt version, not tuned around, per
  AC12's "do not tune to improve them."
- v2.7 — SPEC-006 complete (decision log #50): remaining 17 sections executed after the
  thinking-disable fix, zero truncations and zero errors in the batch. Full corpus: 273/273
  sections analysed, 253 findings (NVDA 92, AMZN 81, MU 80; litigation 71, note_item 59,
  concentration 48, liquidity 37, accounting_change 36, red_flag 2; medium 163, low 54, high
  36). Lifetime spend $6.3792 of the re-aligned $8.50 cap. `validate` exits 0, all
  hard-failing categories clean.
- v2.6 — Live error-analysis round after the first real, paid `analyze-sections --execute`
  run (§4.3, decision log 43–49). Three ledger/truncation bugs found and fixed (stop_reason-
  driven truncation with a raised-cap retry; empty-text extraction now logs actual content
  block types, diagnosing every empty response as a `thinking` block consuming the whole
  output cap; every real API attempt now bills its own ledger row, closing a gap where a
  retried call recorded only the retry's tokens, reconciled via one labelled adjustment
  entry, never reconstructed rows). Root cause of the `thinking` blocks measured then fixed:
  extended thinking was on by default (adaptive, uncapped) on every call this project has
  ever made; disabled explicitly after a real-API probe showed lower cost, no truncation,
  and no loss of recall on the hardest known cases — applied without invalidating the
  existing cache (`input_hash` covers content, not request configuration, a policy now
  documented explicitly). `LLM_BUDGET_USD` re-aligned $10.00 → $8.50 against the real Console
  balance, which has drifted from the ledger's own total for the same reason it did in the
  2026-07-27 incident. Numeric-support checker gained ordinal-word normalisation and
  derived-sum verification after a by-hand review of every real unsupported-number finding
  found zero fabrications.
- v2.5 — SPEC-006A budget guardrails, written up after the 2026-07-27 incident (§4.2): a
  $10 balance was exhausted, ~$6.28 of it Claude Code billing to the same account because
  the project's own key shared `ANTHROPIC_API_KEY`'s name (decision log 40). Ten
  independent layers added (L1–L10, §4.2); `LLM_BUDGET_USD` cut to $10.00 (decision log
  41). A second, unrelated bug found while investigating: `LLM_PRICING`'s `claude-sonnet-5`
  entry used the wrong (future) rate, overstating the ledger by 50% against Console's real
  attribution — corrected, with existing rows backfilled (decision log 42).
- v2.4 — SPEC-006 pre-implementation review. `findings.category` corrected to match
  SPEC-006's actual vocabulary (`red_flag|accounting_change|litigation|concentration|
  liquidity|note_item`) — the prior comment (`red_flag|guidance|management_language|
  note_item`) was stale, predating the spec that defines the real categories. New
  `llm_calls` table (§6) — the sole source of spend; `analyses` drops its own token/cost
  columns in favor of `call_id`. `analyses.input_hash` now stated explicitly to cover the
  fully rendered prompt (every interpolated value), not the template — a hash keyed on
  the template alone would let differently-interpolated calls collide and silently serve
  a wrong cached answer. Decision log entry 39 added.
- v2.3 — Three more SPEC-005 post-implementation refinements. `BOILERPLATE_NOTE_NAMES`
  excluded from automatic rename detection (§2.4) — fixed a remaining false positive
  ("Pay vs Performance Disclosure" repeatedly paired with an unrelated accounting-
  standards note); no signal lost, since boilerplate names are already forced "low"
  regardless of rule. The 33%-eligible-periods firing-rate ceiling replaced by a
  per-filing contribution measure (§7a) — the old measure penalised rules checking many
  things per filing purely as an artifact of how many things they check; the new one
  measured precisely what the Amazon top-5 problem actually was:
  `metric_multi_year_extreme` contributes up to 24 observations to a single filing,
  more than double every other rule's maximum. New binding requirement for SPEC-006 and
  the dashboard: any future top-N observation display must cap contributions from one
  rule at 2 (§2.4) — the failure mode was found and measured here and must not be
  rediscovered independently later. Decision log entries 36–38 added.
- v2.2 — Severity reassigned by analytical materiality instead of detection method (§2.4,
  new) — the original scheme (every `section_appeared`/`disappeared` "high," every metric
  extreme "medium" regardless of which metric) let presentation artifacts outrank real
  economic signal, confirmed live. New: category-based severity for
  `metric_multi_year_extreme`, `BOILERPLATE_NOTE_NAMES`, cross-company simultaneity
  demotion, automatic note-rename detection, `MetricDef.headline` (excludes Beneish
  components and four intermediates from the two per-metric rules). Two real bugs found
  and fixed before shipping: the cross-company demotion was first scoped to every rule,
  not just section-subject ones, and silently zeroed every `metric_multi_year_extreme`
  observation on a real filing; and `section_wording_changed`/the new rename detector
  both used `difflib.SequenceMatcher.quick_ratio()`, an upper-bound heuristic unreliable
  for real similarity decisions (0.87 measured for two genuinely unrelated notes, true
  `.ratio()` 0.03) — `section_wording_changed`'s threshold was re-measured from scratch
  with the corrected metric (0.85 → 0.60). Decision log entries 33–35 added.
- v2.1 — Three SPEC-005 post-implementation refinements. `MetricDef.extreme_informative`
  (§7a) excludes five compounding dollar-level metrics from `metric_multi_year_extreme`
  (a record in a growing company's `ebitda`/`nopat`/`invested_capital`/`free_cash_flow`/
  `net_debt` restates growth, not an event); firing rate 37.7% → 34.4%, still above the
  33% design ceiling. AC6 amended: verified live that no watchlist company files a Q4
  10-Q (`{Q1, Q2, Q3}` only, all three companies, confirmed, not an NVDA/MU-specific
  quirk) — quarterly chains are 3 real periods per fiscal year, not 4, which is why
  NVIDIA's Q2 FY2023 `incremental_gross_margin` had only 3 valid prior quarters at that
  point; the 8-quarter minimum was not lowered to force it through. Derived Q4 figures
  (already in §8's V2 scope) flagged as the direct future fix. The absolute `app.db` size
  ceiling — already relaxed twice during SPEC-004 — is replaced by two reported figures
  (current size; measured marginal cost of one filing, 72 KB, measured live against a
  scratch copy of the real database) and a 15 MB soft ceiling for revisiting the storage
  design, rather than raising the number a fourth time. Decision log entries 30–32 added.
- v2.0 — SPEC-005 implemented: the Observations layer (§2 now five layers, not four).
  `sections` gains `normalized_text_hash` (wording identity, distinct from `text_hash`'s
  content identity) and readability columns; `filings` gains `fiscal_year`/`fiscal_period`,
  populated at XBRL ingest from companyfacts' own `fy`/`fp` labels, used for every
  fiscal-period prior-year match this spec needs instead of date arithmetic or a join into
  `xbrl_facts` (§6). New §2.3: state-based rules (ones whose condition can hold for many
  consecutive periods) fire only entering the condition, never while it persists —
  verified live as necessary (`capex_to_depreciation` fired on 71% of eligible periods
  without this). Two calibration findings surfaced live and resolved rather than
  patched over: `section_wording_changed` (renamed from `accounting_policy_changed`)
  measured 98.9%/98.3% exact-hash-change rates for Policies/Notes alike — not a
  normalization bug, filers really do lightly edit prose most years — so the rule uses a
  measured similarity-ratio materiality threshold instead of a category restriction; and
  `metric_multi_year_extreme` measures 38% overall (just above the 33% design ceiling),
  mechanically driven by the very small annual-history window at current corpus depth.
  Decision log entries 24–29 added.
- v1.9 — SPEC-004's last two live-validation residuals closed. Two more exceptions
  registers, same pattern as §2.2's alias-agreement one: `DEBT_RECONCILIATION_EXCEPTIONS`
  (keyed per `(cik, period_end)` — Amazon's real 2015-2016 ASU 2015-03 transition) and
  `RANGE_EXCEPTIONS` (keyed per `(metric, cik, period_start, period_end)` — NVIDIA's real
  Q2 FY2023 inventory write-down, Micron's real fiscal Q2 2024 discrete tax benefit).
  `validate` now exits 0 against the real database. Decision log entry 23 added.
- v1.8 — SPEC-004 category-6 (alias agreement) findings, resolved. Five more registry
  splits (§2.1): `dep_amort`/`depreciation`, `interest_expense`/`interest_expense_debt`,
  `equity`/`equity_including_nci`, `net_income`/`net_income_including_nci`, and
  `sbc` trimmed with no replacement. New §2.2: alias-agreement exceptions register, for
  the one disagreement (`capex`) kept as a true alias pair despite disagreeing, with a
  written, hand-verified reason. §6 NULL-discipline note refined: absence means zero for
  an additive component of a total the filer is required to disclose (ASC 842 finance
  leases), not for a primary measure — narrow, named exception to the general rule.
  Decision log entries 20–22 added.
- v1.7 — SPEC-004 post-implementation findings, resolved. §6 `fiscal_period` note
  corrected: Amazon directly tags a genuine implicit Q4 for several concepts; derivation
  is a fallback, not the default. §2.1 alias-purity rule reinforced by two more real
  cases (`ppe_and_lease_net`, finance lease liabilities) — both given their own
  canonical inputs rather than folded into existing ones. Decision log entries 17–19
  added.
- v1.6 — SPEC-004 pre-implementation review, verified live against `companyfacts` for
  all three companies. §1 watchlist table corrected: NVIDIA and Micron both run floating
  52/53-week fiscal years (was implied to be a Micron-only quirk; NVIDIA's `01-31` year
  end is not fixed). New §2.1 alias-list purity rule, added after finding two alias
  entries that paired a canonical input with a *different* accounting quantity rather
  than a true synonym (`LongTermDebt`, `ReceivablesNetCurrent`). §6 gains `xbrl_facts`
  columns `duration_days`/`filed_date` and a corrected note on Amazon's `GrossProfit`
  tag (it existed, but only for FY2007–2008 filings, not "never"). §7 replaced: metrics
  are now a declarative registry of 37 named metrics plus 8 Beneish components, not the
  10 hand-coded V1 ratios. Decision log entries 13–15 added.
- v1.5 — SPEC-003: `sections.text` moved out of SQLite into content-addressed files at
  `data/sections/{hash[:2]}/{hash}.txt.gz`; §6 `sections` schema drops the `text` column,
  `text_hash` becomes the sole link to content. §4 gains a note that this stops future
  repo growth but does not reclaim space already spent in `.git` history. Decision log
  entry 12 added.
- v1.4 — §3.7 added: R-files are served wrapped in an SGML `<DOCUMENT>...<TEXT>...
  </TEXT></DOCUMENT>` envelope, not bare HTML, confirmed against AMZN 10-K
  `0001018724-26-000004`'s `R18.htm`. `html_text.py` now strips this explicitly instead
  of relying on parser leniency to find `<body>` by accident.
- v1.3 — §6 `sections` UNIQUE constraint gains `source_file` (table was empty; no
  migration). §3.6 added: `index.json`'s `type` is a display icon class, not a document
  type; `{accession}-index.html`'s Document Format Files table is the authoritative
  source, confirmed against NVDA 8-K `0001045810-26-000019`.
- v1.2 — §3.3 8-K `items` field confirmed populated (was unverified). Archiving vs.
  analysis scope boundary made explicit: archiving unbounded, analysis bounded by a
  configurable start date (arrives with the spec that needs it).
- v1.1 — Watchlist set to AMZN / NVDA / MU with verified CIKs and fiscal year ends.
  Raw archive policy clarified. Retry parameters specified. Q4 reporting note added.
- v1.0 — Initial architecture.

---

## 1. Purpose

Automatically detect new SEC filings by a watchlist of companies, extract financial
statements and narrative sections, compute ratios, analyse the narrative with an LLM,
and surface the results on a dashboard — without the operator's laptop being on.

### Watchlist (verified against SEC 2026-07-25; fiscal calendar corrected 2026-07-26)

| Company | Ticker | CIK | Fiscal year end | Calendar | 10-K typically filed |
|---|---|---|---|---|---|
| Amazon.com Inc | AMZN | `0001018724` | 12-31 | Fixed calendar date | Early February |
| NVIDIA Corp | NVDA | `0001045810` | ~01-31 | **Floating 52/53-week** (last Sunday in January) | Late February |
| Micron Technology Inc | MU | `0000723125` | ~09-03 | **Floating 52/53-week** (Thursday closest to Aug/Sep boundary) | Early October |

**Three different fiscal calendars.** This is a feature, not an annoyance — it forces the
system to treat fiscal periods as data rather than assume calendar quarters, which is
correct behaviour for any real research tool. Never infer a period from a filing date.

**NVIDIA and Micron both run floating 52/53-week fiscal years, not fixed calendar year
ends.** Confirmed live during SPEC-004's pre-implementation review: NVDA's annual XBRL
durations are 363 or 370 days (never a flat 365), and its quarters run 90 or 97 days,
from the same extra-week mechanism as Micron's. Only Amazon has a truly fixed December 31
year end. The `fiscal_year_end` values above (`MMDD`) are the *typical* date, not an
exact one — **nothing in the codebase may assume a fixed year-end date for NVDA or MU**;
period boundaries must always be read from the filing's own reported dates, never
computed from `fiscal_year_end` + an offset.

**Development order:** Amazon first. It is the only watchlist member expected to file
during the V1 build window (Q2 10-Q, historically the first days of August). NVIDIA's
Q2 FY2027 10-Q lands around late August; Micron files nothing until its 10-K in early
October. Adding NVDA and MU is a `config.py` change, not a code change — that is the
test of whether the architecture is right.

---

## 2. Core Design Rule

Five layers of information, never mixed, enforced by separate tables:

```
FACTS            filings, sections, xbrl_facts     observed from SEC, never derived
   ↓
CALCULATIONS     metrics                           deterministic, reproducible, auditable
   ↓
OBSERVATIONS     observations                       deterministic, rule-based, verified by Python
   ↓
INTERPRETATION   analyses, findings                LLM output, always attributed
   ↓
PRESENTATION     dashboard                          reads all four, labels each clearly
```

Dependencies point **downward only**. A row in `xbrl_facts` never references a finding.
Every AI-generated row records the model, prompt version, and the source section it came from.

This is not a documentation convention. It is the schema. Violating it requires
restructuring tables, which is deliberately harder than doing it correctly.

**Observations (SPEC-005).** The layer between Calculations and Interpretation exists
specifically so the LLM is never asked to do two jobs at once: decide what matters, and
write about it. Python decides what matters — deterministically, via the rule registry in
`config.py` plus the engine in `observations.py` — and records a small, verified statement
with pointers back to the exact `metrics`/`sections` rows behind it. SPEC-006's LLM narrates
from these statements, never from raw metrics or raw section text, so it cannot invent a
trend that Python did not already establish as true.

### 2.1 Alias-list purity rule

Wherever a canonical input maps to an ordered list of source tags (concept aliases in
`xbrl.py`/`config.py`, and any future registry of the same shape), **every alias in the
list must denote the same accounting quantity under a different name — never a broader
or narrower quantity that merely resembles it.** A tag that represents a different fact
is a separate canonical input, not a fallback alias for an existing one.

Found and corrected during SPEC-004's pre-implementation review, against live data:

- `LongTermDebt` was listed as a fallback alias for `debt_noncurrent`. It is not a
  synonym — confirmed against all three watchlist companies, `LongTermDebt` is the
  *total* long-term debt (current + noncurrent combined; exact match against
  `LongTermDebtNoncurrent + LongTermDebtCurrent` in every period checked). Using it as a
  same-quantity fallback meant that summing `debt_noncurrent + debt_current` downstream
  double-counted the current portion for any company whose noncurrent-only tag had gone
  stale (Micron, whose `LongTermDebtNoncurrent` has no entries after FY2013).
- `ReceivablesNetCurrent` was listed as a fallback alias for `receivables`
  (`AccountsReceivableNetCurrent`). Not a synonym either — Micron tags both
  *simultaneously* across the same 15+ year span, and the values never match;
  `ReceivablesNetCurrent` is a consistently larger, broader figure.

Both were removed from their respective alias lists. Where a broader/narrower
relationship like this is genuinely useful (e.g. a reconciling total), it gets its own
canonical input — see `total_debt` in SPEC-004 R1b, which prefers a real combined-total
tag and only falls back to summing components when no combined tag exists for that
period, with the difference asserted by `validate` rather than assumed.

**Confirmed at scale (SPEC-004 R8 category 6, live):** once alias agreement was checked
automatically across the whole registry instead of relying on this being re-discovered
by hand, five more real violations of this rule turned up in one run —
`dep_amort`/`Depreciation`, `interest_expense`/`InterestExpenseDebt`,
`equity`/`...IncludingNoncontrollingInterest`, `net_income`/`ProfitLoss`, and
`sbc`/`AllocatedShareBasedCompensationExpense`. All five were the same shape: a
consistent, non-random disagreement across many periods, meaning the two tags describe
different quantities, not the same fact under different names. Each got the same
treatment as `LongTermDebt`/`ReceivablesNetCurrent` — split into its own canonical
input, never patched over as a fallback. The `equity`/`net_income` case was worse than
"imprecise" — it was a live ROE-correctness bug (§6, "Note on `metrics.equity`/`net_income`
parent vs. NCI" below) that DuPont's own reconciliation structurally could not detect.

### 2.2 Alias-agreement exceptions register

Not every disagreement category 6 finds is a broader/narrower split waiting to happen.
`capex`'s two aliases (`PaymentsToAcquirePropertyPlantAndEquipment`,
`PaymentsToAcquireProductiveAssets`) disagree by ~16% for Amazon in exactly one period —
the real 2017 tag transition (§3, concept drift) — not a *consistent* pattern across many
periods the way the five §2.1 cases were. `free_cash_flow` computed from
`PaymentsToAcquireProductiveAssets` was already hand-verified against Amazon's archived
FY2025 cash flow statement (SPEC-003/004 AC9), confirming that alias is trustworthy.

`config.ALIAS_AGREEMENT_EXCEPTIONS` holds this kind of case: canonical input → a written
reason. `validate` category 6 reports an excepted canonical's disagreements
informationally, alongside the reason, instead of as a hard failure. A canonical input
*not* in the register still hard-fails on any disagreement — the register documents an
accepted exception, it does not silence a finding.

### 2.3 State-based rules fire only on transition (SPEC-005)

Wherever a deterministic rule's condition is a **state** that can hold true for many
consecutive periods (a metric currently above/below a threshold; two metrics currently
diverging beyond a bound), the rule must fire only on the **transition into** that state,
never on every period the state persists. A rule that cannot tell "just crossed" from
"has been true for two years" will drown its own signal in repetition.

This is not a hypothetical concern — measured live, before the fix, against the real
database: `capex_to_depreciation` (SPEC-005's `metric_divergence`, naive "is it currently
above 1.5") fired on **71%** of eligible periods, because both Amazon and NVIDIA/Micron
are in sustained capex-expansion mode, so the condition is close to their normal state,
not an event. `fcf_conversion` (`metric_threshold_cross`, naive "is it currently below
0.5") fired on **50%** the same way. After requiring a transition (condition true this
period, false the immediately preceding period), the same checks measure 14.0% and well
under the design ceiling for every other declared threshold/divergence.

The principle does **not** apply to rules whose own mathematical shape is already
self-limiting: `metric_multi_year_extreme` ("is this a new record over the trailing
window") and `metric_sigma_move` ("is this more than 2σ from a moving trailing mean")
both naturally stop firing once a metric stabilizes, without needing an explicit
transition check — a moving window is already a kind of transition detector.

### 2.4 Severity is assigned by materiality, and display is a separate concern from detection (SPEC-005)

**Detection method is not materiality.** The original severity scheme assigned "high" to
every `section_appeared`/`section_disappeared` observation and "medium" to every metric
extreme, regardless of *what* appeared or *which* metric hit a record — a boilerplate
SEC-mandated disclosure item showing up "new" this year outranked a five-year low in
gross margin. Confirmed live: Amazon's and Micron's top-5-by-severity observations were
dominated by presentation artifacts, not economics. Severity is now computed from what
was actually found, not from which rule found it:

- `metric_multi_year_extreme`: "high" only for `margins`/`returns`/`working_capital`
  metric categories — a multi-year record in one of these *is* the analytical claim.
- `metric_threshold_cross`/`metric_divergence`: uniformly "high" — any declared hard
  threshold crossing is material regardless of which one.
- `section_appeared`/`section_disappeared`: downgraded to "low" — a note appearing is a
  presentation change, not a number moving.
- **`BOILERPLATE_NOTE_NAMES`** (four SEC-mandated disclosure items, confirmed live to
  roll out industry-wide within the same real-world window across the whole watchlist —
  Pay vs Performance, Cybersecurity Risk Management, Insider Trading Arrangements,
  Insider Trading Policies and Procedures): any observation about one of these is "low"
  **regardless of which rule produced it**.
- **Cross-company simultaneity**: if the same rule fires on the same **section name**
  (never a metric name — see the bug note below) for more than one watchlist company
  within 365 days, it is demoted to "low" — a same-name event hitting every company at
  once is a taxonomy or regulatory change, not company-specific information.
- **Automatic note-rename detection**: a disappeared name and an appeared name within one
  year-over-year transition, whose actual text similarity clears a threshold, are reported
  as one "low" `section_renamed` observation instead of a disappeared+appeared pair.
  Boilerplate names never participate (already "low" either way; excluding them removed a
  real false-positive class, since two different boilerplate disclosures can share enough
  generic template language to look similar without being the same note).

**Two real bugs found and fixed before this shipped, not designed around in advance:**
(1) cross-company demotion was first applied to every rule, not just section-subject
ones — every watchlist company files an annual 10-K within the same ~12 months as every
other, so a metric subject like `asset_turnover` (the identical string for every company
by construction) always has a same-window "peer" for a reason with zero relationship to
any real event; this silently zeroed every `metric_multi_year_extreme` observation on a
real filing before being scoped to section-subject rules only. (2) both the wording-change
rule and the rename detector used `difflib.SequenceMatcher.quick_ratio()`, a fast
upper-bound heuristic (character-multiset overlap) rather than real sequence matching —
two genuinely unrelated notes scored 0.87 under `quick_ratio()` against a true `.ratio()`
of 0.03. Every use was switched to the real `.ratio()`, and `section_wording_changed`'s
threshold was re-measured from scratch with the corrected metric (0.85 → 0.60; the old
value, re-measured correctly, would have fired on 61–67% of comparisons — it only looked
calibrated because it was calibrated against the wrong number).

**Display is a separate concern from detection, and belongs at the display layer, not
here.** Measuring how much of a single filing's observation list each rule can occupy
(mean/max observations contributed per filing, replacing an eligible-periods-fired
percentage that structurally penalised rules checking many things per filing) showed
`metric_multi_year_extreme` can contribute up to 24 observations to one filing — more
than double every other rule's maximum. Every one of those 24 claims is independently
true and verifiable; the problem is entirely that an uncapped top-N selection becomes "the
rule that fired most on this filing" rather than "the most material things about this
filing." The fix — cap any future top-N observation selection (SPEC-006's LLM narration,
the dashboard's Overview page) at 2 contributions per rule, then fill remaining slots from
the next rule — is recorded as a binding requirement on those future specs rather than
solved here, since SPEC-005 itself has no top-N display surface: `observations` remains
the complete, uncapped, unranked record, and the cap applies only where a bounded
selection is actually made.

### 2.5 Arithmetic verification without unit verification is not verification (SPEC-007)

Found in SPEC-007's pre-implementation review (2026-07-30), before `brief.py` was written:
SPEC-006's derived-sum verifier (`analyze._is_verified_subset_sum`) checks that a claimed
number equals a subset-sum of numbers present in a source — correctly, and it was built to
verify arithmetic, nothing else. It has no concept of what a number's UNIT is, because
`analyze.py`'s numeric tokens are bare digit strings with no unit attached. That is exactly
the gap: a sentence claiming *"$1.27B combined"* from a source with a $525M verdict and a
€746M fine passes the arithmetic check (525 + 746 = 1271 ≈ 1.27B is correct addition) while
silently treating two different currencies as one figure. **A check that confirms the sum is
correct is not the same claim as a check that confirms the sum means anything** — the second
requires knowing that every addend, and the total, denote the same unit (a currency, or a
percentage, never mixed).

This generalises beyond SPEC-007: any future mechanical check that verifies an arithmetic
relationship between extracted numbers (a sum, a difference, a ratio) must also verify that
every number involved shares a unit before treating the arithmetic as meaningful. Verifying
that 2 + 2 = 4 is not verifying that two apples and two dollars make four of anything.

---

### 2.6 A prompt-version bump states its invalidation cost in the same breath as its rationale

Found investigating LLM cost infrastructure (2026-08-07): at this project's real scale
(≈12 filings/year), normal section-analysis operation costs ≈$2.40/year, while a single
prompt-version bump invalidates the entire cached corpus and costs ≈$6 to re-analyze (297
sections × $0.0202/section, measured) — more than TWO YEARS of normal per-call spend, in one
edit. Cache invalidation, not per-call price, is this project's dominant cost lever. It has
been done right exactly once, by luck of habit rather than by rule: SPEC-007's v2 prompt
change (decision log #56) stated its $0.2963 regeneration cost alongside its rationale before
being approved. Nothing forced that; a future prompt edit made without the same discipline
would not be caught by any guardrail in SPEC-006A, which sizes L3/L6/L7 around a single run's
cost, not a version bump's.

**Standing requirement, binding on every future prompt-version change to any cached
prompt** (`SECTION_ANALYSIS_PROMPT_VERSION`, `BRIEF_GENERATOR_PROMPT_VERSION`,
`BRIEF_VERIFIER_VERSION`, or any prompt added later that a cache/`input_hash` scheme is
built around): **the proposal must state the re-analysis/regeneration cost of the corpus it
invalidates in the same message as the content rationale, before the change is made** — not
discovered afterward via a dry-run someone happens to run. The dry-run tooling to compute
this already exists (`analyze-sections`/`generate-briefs` without `--execute` reports exactly
this number for the current corpus); the requirement is that it gets RUN and CITED at
proposal time, not that new tooling gets built. A prompt change whose invalidation cost has
not been stated has not been fully proposed.

---

## 3. Verified Findings (2026-07-25)

Confirmed against live SEC data. These are the basis for the design.

### 3.1 `FilingSummary.xml` solves section segmentation

Every XBRL filing has `FilingSummary.xml` listing each report with a `MenuCategory`:

| MenuCategory | Contents | V1 action |
|---|---|---|
| `Cover` | Cover Page, Audit Information | Metadata only |
| `Statements` | Income statement, balance sheet, cash flow, equity, comprehensive income | **Ingest** |
| `Notes` | Individually named footnotes | **Ingest — primary AI input** |
| `Policies` | Accounting policies | Ingest |
| `Tables` | XBRL table detail | **Skip — redundant** |
| `Details` | XBRL tagging detail | **Skip — redundant** |

Confirmed on Amazon 10-K `0001018724-26-000004` (FY2025) and 10-Q
`0001018724-26-000014` (Q1 2026). Structure identical across both form types.

Amazon 10-K notes: Description of Business & Accounting Policies · Financial Instruments ·
Property and Equipment · Leases · Acquisitions, Goodwill & Acquired Intangibles · Debt ·
Commitments and Contingencies · Stockholders' Equity · Income Taxes · Segment Information
(plus Insider Trading and Cybersecurity boilerplate).

**Consequence:** section extraction is XML parsing plus targeted HTML fetches, not
document-structure inference. This removed the largest risk in the project.

### 3.2 MD&A is NOT in the R-files

R-files contain only XBRL-tagged financial statement content. Item 7 (MD&A) and
Item 1A (Risk Factors) live in the primary document and require separate extraction.

**Known trap:** the string "Item 7. Management's Discussion" also appears in the table
of contents. Take the *last* match, not the first.

### 3.3 8-K handling: filter on Item 2.02

Forward guidance appears in the earnings press release, filed as **Exhibit 99.1 to an
8-K carrying Item 2.02 ("Results of Operations and Financial Condition")** — roughly
four per year per company.

**Rule:** ingest 8-Ks only when Item 2.02 is present. **The substantive content is in
Exhibit 99.1, not the 8-K body** — see §4.1.

**Verified (SPEC-001 implementation):** the `items` field in the EDGAR submissions API
IS populated, confirmed against live AMZN, NVDA, and MU data (e.g. `"2.02,9.01"`). The
filing-index-page fallback is retained anyway, as insurance against any filing where
the submissions JSON's `items` value is blank — exclude only if both sources yield
nothing.

### 3.4 Filter at the source

Amazon filed accessions `-25-000070` through `-25-000132` in one stretch of 2025,
overwhelmingly Form 4 insider transactions. The monitor must filter by form type
before doing anything else.

### 3.5 Filing sizes vary widely

Amazon 10-K ≈ 12 MB. NVIDIA ≈ 11 MB. Micron ranges 17–46 MB. This matters because
raw filings are committed to the repo — see §4.1 and the accepted-debt note.

### 3.6 There are two different "filing index" resources, and only one has document types

Easy to conflate; confirmed empirically during SPEC-002 review against NVDA 8-K
`0001045810-26-000019`.

- **`index.json`** (`.../{accession}/index.json`) — the machine-readable directory
  listing. Its `type` field is a **display icon class** (`text.gif`, `compressed.gif`,
  `image2.gif`), not a document type. Every file in the NVDA filing above, including the
  actual Exhibit 99.1, came back `text.gif`. Unusable for identifying exhibit numbers.
- **`{accession}-index.html`** — the human-readable filing page. Its "Document Format
  Files" table has a `Type` column that is SEC-declared and authoritative: it correctly
  labels NVDA's `q4fy26pr.htm` as `EX-99.1` even though the filename gives no hint.
  This is the resource that actually resolves the SPEC-001 Exhibit 99.1 false positive.

**Consequence:** the `-index.html` table does not enumerate every archived file either —
viewer-support files (`FilingSummary.xml`, `R*.htm`, `MetaLinks.json`, `report.css`,
`Show.js`) never appear in it. A complete, typed manifest requires both resources:
`index.json` for the full file list, `-index.html` for types where they exist. This is
exactly the kind of thing that would otherwise be rediscovered painfully in six months.

### 3.7 R-files are served wrapped in an SGML envelope, not as bare HTML

Confirmed empirically during SPEC-002 implementation against AMZN 10-K
`0001018724-26-000004`'s `R18.htm` (Income Taxes note).

Fetching an R-file directly from its archive URL —
`https://www.sec.gov/Archives/edgar/data/{cik}/{accession_no_nodash}/R18.htm` — does
**not** return bare HTML. It returns:

```
<DOCUMENT>
<TYPE>XML
<SEQUENCE>31
<FILENAME>R18.htm
<DESCRIPTION>IDEA: XBRL DOCUMENT
<TEXT>
<html>...</html>
</TEXT>
</DOCUMENT>
```

**Reproduction:** `curl -A "<UA>" https://www.sec.gov/Archives/edgar/data/1018724/000101872426000004/R18.htm`
— HTTP 200, `content-type: text/html`, byte-identical on repeat fetch. Not a caching
artifact or a fluke of one filing; this is how SEC serves the R-file endpoint.

**Why this matters:** `BeautifulSoup`'s `html.parser` happens to be lenient enough to
find the nested `<body>` regardless of the malformed SGML tags wrapping it (it nests
everything under `<document><type><sequence>...<text><html><body>`, and `.body` still
finds it) — so naive code that assumes bare HTML will silently produce correct-looking
output *by accident*. That is a worse failure mode than an outright error: it means the
parsing was never actually validated to handle this, and a future SEC formatting change
could silently corrupt extracted text with no signal. SPEC-002 R4's `html_text.py`
therefore strips this envelope explicitly before parsing and raises if no `<body>` is
found afterward, rather than depending on parser leniency.

---

## 4. Deployment

Constraint: must run without the operator's machine being on. Cost target: $0 infrastructure.

| Concern | Choice | Why |
|---|---|---|
| Scheduler + compute | **GitHub Actions** (cron workflow) | Always-on, free on public repos, built-in secrets, no Linux admin |
| State | **SQLite committed to the repo** | Runners are ephemeral; repo is the durable store |
| Raw filings | gzipped in `data/raw/` | Permanent archive |
| Dashboard | **Streamlit Community Cloud** | Free, deploys from the same repo, redeploys on push |
| LLM | Anthropic API, section-level calls | Budget ≈ $10 total (§4.2) |

**Why not a VM:** SSH, systemd and deployment would consume ~8 of ~30 available hours
and teach operations rather than system design. Revisit if the watchlist exceeds ~10 companies.

### 4.1 Raw archive policy

**Archive every document containing original content. Do not archive SEC-generated
renderings of content already archived.**

| Form | Archive |
|---|---|
| 10-K, 10-Q | Primary document + `FilingSummary.xml` |
| 8-K | **All documents listed in the filing index**, including Exhibit 99.1 |

Rationale: R-files are SEC renderings derived from the primary document plus its XBRL —
regenerable, so archiving them is duplication. Exhibit 99.1 is genuinely separate content
and is the *only* place guidance appears; an 8-K archived without it is worthless.
8-K filings are small, so archiving them wholesale costs little.

**Archiving is unbounded. LLM analysis is not.** Discovery and archival run over a
company's full filing history with no date floor — archiving is free (SEC hosting,
no API cost) and unbounded history is valuable test material for later specs.
Analysis is different: it calls a paid LLM per section, and an unbounded first run
would walk the *entire* filing history and consume the whole project budget in one
pass. Analysis must therefore be bounded by a configurable start date. That config
key is not added in SPEC-001 — it arrives with the spec that introduces `analyze.py`,
since nothing consumes it yet.

**Accepted debt (time-boxed, deliberate):**
- SQLite-in-git does not scale past a handful of companies. Fine at V1 size.
- Repo growth: three companies at roughly 15 MB per annual filing, gzipped, is tens of
  MB per year. Acceptable now; revisit before adding a fourth company or backfilling
  more than three years.
- GitHub Actions cron drifts 5–30 minutes. Irrelevant given filings are analysed within hours.
- Streamlit Community Cloud apps are public. All source data is public; no secrets in the DB.

**Note (SPEC-003):** section text was moved out of `app.db` into content-addressed files
under `data/sections/` (§6) specifically because it was the dominant contributor to the
33.7 MB `app.db` committed after SPEC-002. This stops *future* growth from repeating that
pattern — it does not shrink `.git` history already spent on the pre-migration `app.db`
blobs committed during SPEC-001 and SPEC-002. Reclaiming that would require a history
rewrite, deliberately out of scope; the existing `.git` directory stays at its
pre-migration size permanently.

### 4.2 Budget Guardrails (SPEC-006A)

**The incident (2026-07-27).** A $10 prepaid API balance was exhausted. Of it, ~$3.79 was
this project's own pipeline — working exactly as designed, correctly capped by the
`llm_calls` ledger and `LLM_BUDGET_USD`. The remaining ~$6.28 was **Claude Code itself**,
billing to the same Anthropic account because `ANTHROPIC_API_KEY` happened to be set in
the shell environment — $5.24 of it on Opus 5, a model this project never calls and never
priced. The pipeline's own cap was correct and tested throughout. It simply had no view of
a *different process* spending against the *same account*, because nothing routes an
unrelated process's spend through this project's ledger. Recorded honestly: the original
cap was right, and it was still insufficient, because a budget guard only ever protects
what routes through it.

A second, independent problem was found and fixed while investigating the incident: the
ledger's own pricing table for `claude-sonnet-5` used the post-2026-09-01 standard rate
($3/$15 per MTok) throughout, on the reasoning that overstating cost was "the conservative
direction." That reasoning was wrong in practice — `llm_calls` is also meant to reconcile
against Console's real balance, and a ledger that silently overstates its own numbers by
50% (recorded $5.6859 for the same calls Console attributed $3.7906 to this project — the
exact 3:2 ratio of $3/$15 vs $2/$10, confirmed by recomputing the stored token counts) is
not doing its one job, no matter which direction the error runs. Fixed by using the rate
Anthropic is *actually* billing (introductory $2/$10, through 2026-08-31) and backfilling
the 197 existing `llm_calls` rows to it once, by hand — see `config.LLM_PRICING`'s
docstring and `config.LLM_SONNET5_RATE_REVIEW_DATE`, which forces a `validate` warning
when the introductory rate expires so this cannot drift silently a second time.

**The fix is not a better single cap. It is layers that fail independently**, so no single
mistake — a misrouted client, a careless prompt bump, a loop in a scheduled job — can drain
the account by itself.

| Path | Stopped by |
|---|---|
| Pipeline run costs more than expected | L3 per-run cap, L4 confirmation |
| Prompt version bump silently invalidates the cache and re-runs everything | L5 cache-impact warning |
| Bug causes a retry or call loop | L3 per-run cap, L6 call ceiling |
| GitHub Actions job misbehaves overnight, unattended | L7 scheduled-run cap |
| Public assistant endpoint is hammered | L8 assistant caps (spec not yet written) |
| Another process (Claude Code, a script, a future tool) bills to the same key | **L1 only** |
| Everything else, including the unknown | **L1 only** |

Only L1 catches what the code cannot see — exactly the class of failure that happened.

| Layer | What it does | Where |
|---|---|---|
| L1 | Prepaid balance, auto-reload OFF; monthly Console spend limit; balance kept near what the next phase needs, not a comfortable buffer | **Operator action — no code enforces this** |
| L2 | Lifetime cap, `LLM_BUDGET_USD = 8.50` (re-aligned 2026-07-28, was 10.00 — see §4.3 and decision log #46), tracks money actually available, NOT the ledger's own recorded total (they measure different things and need periodic re-alignment) | `llm.ensure_budget_available` |
| L3 | Per-run cost ceiling, `LLM_MAX_RUN_COST_USD = 2.00`, overridable via `--max-run-cost` | `llm.RunGuard` |
| L4 | `--confirm-cost N` required above `LLM_CONFIRM_THRESHOLD_USD = 1.00`; N must match the dry-run estimate | `pipeline.cmd_analyze_sections` |
| L5 | Cache-hit/new-call report every run; `--acknowledge-cache-invalidation` required above `LLM_CACHE_INVALIDATION_WARN = 50` | `analyze.run_analysis` (dry-run pass) |
| L6 | Call-count ceiling, `LLM_MAX_CALLS_PER_RUN = 300`, deliberately redundant with L3 | `llm.RunGuard` |
| L7 | `--scheduled` clamps the run-cost ceiling to `LLM_SCHEDULED_RUN_MAX_COST_USD = 0.50`, refuses `--sample` | `analyze.run_analysis` / binding on the GitHub Actions spec |
| L8 | Per-session question limit; daily spend ceiling separate from the pipeline's; deployment private and password-gated | **Not implemented — binding on the assistant spec when written** |
| L9 | Warns on startup if the generic `ANTHROPIC_API_KEY` is set (this project reads `EQUITY_RESEARCH_ANTHROPIC_API_KEY` instead) | `llm.check_environment_canary`, called from `pipeline.main` |
| L10 | Every paid command prints this run's cost, lifetime spend, remaining budget; `validate` adds ledger/pricing reconciliation | `pipeline.cmd_analyze_sections`, `validate.py` categories 19/25/26 |

**L1 is an operator responsibility no code change can enforce.** Concretely, before
resuming any paid run:
- Confirm the Anthropic Console account has a prepaid balance with **auto-reload
  disabled**. With auto-reload on, a runaway process refills its own budget indefinitely —
  the balance stops being an absolute ceiling.
- Set a **monthly spend limit** in Console if the account tier offers one.
- Top up to roughly what the next phase of work needs, not a round number kept "for
  convenience" — a small balance is itself a guard, the same logic as L2's "money actually
  available" framing.
- Never export the project's key as `ANTHROPIC_API_KEY` in a shared or persistent shell
  profile — that is precisely how Claude Code picked it up on 2026-07-27. Use
  `EQUITY_RESEARCH_ANTHROPIC_API_KEY` (§ env var rename, decision log) and export it
  narrowly (a per-invocation prefix, a `.env` loaded only by this project's own tooling).

**L8 is recorded here so it cannot be forgotten**, per SPEC-006A: the assistant spec, when
written, must implement a per-session question limit, a daily spend ceiling separate from
the pipeline's own budget, and a private, password-gated deployment. None of this is
implemented yet because the assistant does not exist yet.

---

### 4.3 Truncation, empty responses, and the per-attempt ledger invariant (2026-07-28)

**Two bugs, found from a real error-analysis pass over the ledger, not from a test
failure.** Six real `llm_calls` error rows existed before this fix; recomputing them
against their own recorded `output_tokens` showed three were truncation misfiled as a
generic parse error (one at the OLD 1,024-token cap — the incident `LLM_MAX_OUTPUT_TOKENS`
itself already documents — two at the CURRENT 4,096-token cap, meaning raising the cap
once already had not fixed truncation, only moved where it binds) and one extracted
**zero text** with no record anywhere of why.

1. **Truncation is now read from the API's own `stop_reason` field**, never inferred from
   a JSON parse failure — the two are not the same signal. A response that fails to parse
   but reports `stop_reason="end_turn"` is the model emitting bad JSON on its own, retried
   at the same cap (unchanged, generic behaviour). A response with `stop_reason="max_tokens"`
   is now its own class: retried once at `LLM_TRUNCATION_RETRY_OUTPUT_TOKENS` (8,192, double
   the normal cap), and only recorded as the distinct `"truncated"` `AnalysisOutcome` status
   — never lumped into `"error"` — if that retry also fails. `analyze.RunStats.truncated` and
   the `analyze-sections` execute-run report count it separately from `errors`, because a
   *rising* truncation rate is the signal that the cap needs raising again, and that signal
   was previously invisible, buried inside a generic parse-error count alongside unrelated
   schema failures.

2. **Empty text extraction now logs the actual content block types the API returned**,
   rather than assuming the text-block filter correctly discarded only irrelevant
   non-text content. Run against the real API for the first time (2026-07-28
   `analyze-sections --execute`), this immediately diagnosed the mystery: every empty
   extraction this run (4 of them, all 4 also the truncation cases above) had content block
   types `['thinking']` and `stop_reason="max_tokens"` — the model's internal reasoning
   consumed the *entire* output cap before it ever reached the text block with the actual
   answer. This is not a separate failure mode from truncation; it is truncation's most
   extreme case (thinking alone exceeds the cap), and fix (1)'s stop_reason-driven retry at
   a higher cap handles it directly — all 4 real truncations this run succeeded on retry
   (`RunStats.truncated == 0` for the run, despite 4 transient truncation events along the
   way).

3. **A third bug, found only because this was the project's first execution of the retry
   path against the real (billed) API rather than a free `FakeRawClient`**: when a truncated
   call succeeded on retry, `get_or_create_analysis` recorded only the retry's tokens —
   the wasted first attempt (a real API call Anthropic billed for: input tokens plus up to
   4,096 output tokens of a "thinking" block that produced no text) got **no ledger row at
   all**. This is a direct violation of this project's own founding ledger discipline (SPEC-006
   AC1, "every call attempt appends a row") and the same shape of failure as decision log
   #42 (a ledger that silently disagrees with what Anthropic actually billed) — just running
   in the *other* direction (too low, not too high) this time. Confirmed against all 4 real
   truncate-then-retry sections from the 2026-07-28 run: each had exactly one `llm_calls`
   row, holding only the successful retry's tokens.

   **Fixed going forward**: every real API attempt now bills its own `llm_calls` row and its
   own `RunGuard.record_call`, the instant it happens (`edgar/llm.py`, `get_or_create_analysis`'s
   `_bill` helper) — a section needing 2 real attempts produces 2 ledger rows, always, whether
   it's a generic parse retry or a truncation retry, regardless of how the section ultimately
   resolves. Covered by `test_every_real_attempt_produces_exactly_one_ledger_row`
   (`tests/test_llm.py`), parametrized over every retry shape — the property test that should
   have existed when the retry path was first written, and would have caught this
   immediately in a unit test rather than requiring a real, paid execution to surface it.

   **The 4 already-affected historical rows were NOT reconstructed.** Their wasted attempts'
   real `input_tokens` were never captured anywhere and are unrecoverable — only their
   `output_tokens` (4,096 each, read from the live response before the empty-text warning
   fired) is exactly known. Writing 4 separate rows with an assumed input-token count each
   would look like 4 measured ledger entries when they are not. Instead,
   `scripts/backfill_2026_07_28_truncation_ledger_gap.py` (reviewed before running, idempotent,
   committed to the repo) inserts **one** row with a new, distinct ledger status,
   `status = 'reconciliation'` — never `'ok'`/`'error'`/`'refused'` — whose note states plainly
   which figure is exact (16,384 total output tokens, 4 × 4,096) and which is an assumption
   (40,617 total input tokens, taken from each affected section's own successful retry, on
   the reasoning that the identical rendered prompt was sent both times). Lifetime spend after
   the reconciliation entry: **$5.9894** of the $10.00 budget (up from the $5.7443 the ledger
   showed before the entry existed) — the true cost of the calls this bug undercounted.

   **A 5th real, billed, still-unrecorded attempt exists and was deliberately left OUT of the
   reconciliation entry above.** Section 1937 needed a generic (non-truncation) parse retry
   during the same 2026-07-28 run — the SAME pre-existing gap (§ decision log #45) affects the
   ordinary retry path too, not only the truncation-retry path, and always has (it predates
   this incident entirely). Unlike the 4 truncation cases, this wasted attempt's real
   `output_tokens` was never logged anywhere — `stop_reason` was not `"max_tokens"`, so no
   diagnostic warning fired for it — meaning there is no exact figure to anchor even a labelled
   estimate to, only an unknown real cost. Recorded here in prose rather than invented into a
   ledger row with a fabricated number attached to it: this is precisely the residual a
   labelled reconciliation entry cannot always close, and precisely why manual reconciliation
   against Console (next paragraph) remains a standing operator responsibility, not a
   one-time fix.

**Operator responsibility this incident adds, alongside L1 (§4.2): no code change makes
`llm_calls`'s lifetime total automatically agree with Console's real usage figure forever —
only manual, periodic reconciliation between the two catches the *next* accounting gap this
same class of bug could reintroduce, the way this one was caught by a report the operator
asked for, not by validate.py.** `validate.py` category 25 (`llm_cost_recomputation_mismatches`)
checks stored `cost_usd` against `compute_cost` for existing `'ok'`/`'error'` rows — it cannot
detect a call that was made but never got a row at all, which is exactly what this bug was.

**Root cause of the "thinking" blocks, measured then applied (2026-07-28).** The Anthropic
SDK's `messages.create` accepts a `thinking` request parameter with three modes —
`enabled` (fixed `budget_tokens`), `disabled`, and `adaptive` (the model decides per-request
whether to reason, with no budget cap of its own — it shares the same `max_tokens` ceiling as
the text output, which is exactly the truncation risk this section is about). This project's
client (`edgar/llm.py`, `_RealAnthropicClient.messages_create`) has never set this parameter at
all; every real call this project has ever made has been running under whatever the API
defaults to when `thinking` is omitted, which — confirmed by the real `['thinking']` blocks
appearing unrequested — is not `disabled`.

`scripts/probe_extended_thinking_2026_07_28.py` re-ran the same 4 real sections that
truncated during the 2026-07-28 execute run, this time with `thinking={"type": "disabled"}`
explicitly, at the standard 4,096-token cap (no retry-cap involved). Real, billed, one-off
diagnostic calls — recorded honestly to `llm_calls` under `prompt_name =
"section_analysis_thinking_probe"`, never written to `analyses`/`findings`. Result, compared
against each section's own real production baseline (adaptive thinking, truncated at 4,096,
succeeded on retry at 8,192):

| section | ticker | thinking disabled: tokens / stop_reason / findings | baseline (adaptive, retried at 8,192): tokens / findings |
|---|---|---|---|
| 2168 | MU | 835 out, `end_turn`, **4** findings, $0.0189 | 4,415 out (after truncating at 4,096 first), 3 findings |
| 8 | AMZN | 1,706 out, `end_turn`, **7** findings, $0.0572 | 5,402 out (after truncating first), 6 findings |
| 2118 | AMZN | 952 out, `end_turn`, **4** findings, $0.0258 | 5,300 out (after truncating first), 3 findings |
| 2069 | NVDA | 588 out, `end_turn`, **3** findings, $0.0202 | 4,936 out (after truncating first), 3 findings |

Every one of the 4 hardest known real cases completed in a single call, well inside the
standard cap, with **zero** `thinking` content blocks — confirming thinking was never actually
required to be off by design, only never turned off. Output tokens dropped 78–89%; each
section's total real cost (both the wasted truncated attempt and its retry, under adaptive
thinking) was 67–83% higher than the single disabled-thinking call. Materiality determination
was identical (`true`) in all 4; kept-finding counts were equal or higher with thinking
disabled, never lower — no sign that removing it cost recall on this sample.

**Applied**: `_RealAnthropicClient.messages_create` now sends `thinking={"type": "disabled"}`
explicitly on every real call (decision log #48). This does NOT invalidate the existing cache.
`llm.compute_input_hash` covers content the model SEES — the rendered prompt, model, and
prompt version — never request CONFIGURATION (`thinking`, `max_tokens`, `temperature`, ...).
The 257 analyses already produced under adaptive thinking answer the same question this hash
tracks, and were already validated by this project's own mechanisms (quote verification, the
numeric-support checker below) under the configuration that produced them; regenerating all 257
would cost real money to re-derive output already known to be sound. **The general rule going
forward**: a request-configuration change believed to MATERIALLY alter output quality forces
regeneration through a deliberate `SECTION_ANALYSIS_PROMPT_VERSION` bump (which DOES invalidate
the cache, by existing design, R3) — decided case by case against whether the regeneration cost
is worth the improvement, never by silently folding every tunable request parameter into
`input_hash` itself, which would invalidate the whole cache on every knob turn regardless of
whether that knob mattered. This one was a deliberate choice not to bump: thinking's removal is
believed to reduce truncation/cost, not change what a well-formed, already-validated response
says, so a version bump was judged not worth its cost here — a future change believed to alter
FINDINGS THEMSELVES (a prompt wording change, a schema change) still gets one.

**`LLM_BUDGET_USD` re-aligned 10.00 → 8.50 (2026-07-28, decision log #46).** The ledger's
recorded lifetime total and the real Console prepaid balance are not the same number and were
never going to be — the original $10 balance absorbed both this project's ledgered spend and
the Claude Code leak (decision log #40), but `LLM_BUDGET_USD` only ever tracked the former.
Setting the cap to what the ledger shows, rather than below what Console actually has left,
turns L2 into a rubber stamp on money that may already be gone. The new value is NOT derived
from the ledger total (that would repeat the same mistake) — it is set to sit comfortably
below the real Console balance at re-alignment time (~$2.80), the same "money actually
available, not a round number" principle L1/L2 were already built on. These two numbers —
`LLM_BUDGET_USD` and the real Console balance — measure different things and will drift apart
again the moment anything spends against the same key outside this ledger's view, exactly as
happened before; re-aligning them, periodically, is L1's existing operator responsibility
(§4.2) applied on an ongoing basis, not a one-time correction.

**Numeric-support checker improved, enforcement still off (2026-07-28, decision log #49).**
Reviewing all 6 real "unsupported number" findings from the 2026-07-28 run by hand: 5 of 6 were
the model correctly ADDING disclosed figures the checker had no way to verify (three customers
at 27%, 18%, 12% correctly summed to "57% combined"); 1 of 6 was a checker artifact ("Q4"
against a source that spelled out "fourth quarter"). Zero were fabrications. Two additions to
`analyze.check_numeric_support`, `NUMERIC_SUPPORT_ENFORCE` left `False` either way — this
changes what the metric measures, not whether it discards:

1. **Ordinal-word normalisation.** `extract_numeric_tokens` only ever produces digit-form
   tokens (`_NUMBER_RE` cannot match a word), so a source that spells out an ordinal the model
   wrote as a digit ("fourth quarter" vs. the model's "Q4") registered as unsupported for a
   reason that has nothing to do with grounding. Bounded to ordinals 1st–20th, the only
   spelled-out form actually observed in this corpus, and applied ONLY within numeric-support
   checking — never `verify_quote` (R6), which must stay strictly verbatim; treating "fourth"
   and "4" as equal there would let a paraphrased quote pass.
2. **Derived-sum verification.** A number absent from both quote and note gets one more check:
   is it a correct subset-sum of the quote's OWN numbers? If so, it is `derived_verified` — a
   real, arithmetic-checked tier, distinct from both presence-based support and from
   `unsupported`. This is not a relaxation: a number that merely *looks* like a plausible sum
   but is arithmetically wrong (the actually dangerous case — a checker that can't verify
   arithmetic can't tell a correct sum from an incorrect one that happens to look the same
   shape) still fails this check and stays `unsupported`. Scoped to the quote's own numbers
   only, matching the same grounding principle as supported-in-quote vs. supported-in-note-only.

`RunStats`/`SectionResult` gained `numeric_tokens_derived_verified` (and
`numeric_derived_verified_rate`), reported alongside the existing quote/note-only split, never
folded into it — "verified by arithmetic" and "found verbatim" remain visibly different kinds
of evidence.

---

## 5. Module Layout

```
edgar/
  config.py         Watchlist, CIKs, form types, note allow-list, concept aliases, model names
  db.py             Schema creation, connection handling
  edgar_client.py   ALL HTTP to SEC. Rate limiting + User-Agent live here and nowhere else
  monitor.py        Poll for filings not yet in the database
  fetch.py          Download and archive per §4.1
  sections.py       Parse FilingSummary, fetch R-files, extract MD&A, clean to text
  xbrl.py           Pull companyfacts, normalise into xbrl_facts
  metrics.py        Compute ratios from xbrl_facts, record formula + inputs
  llm.py            Anthropic client, hash-based response cache, cost accounting
  analyze.py        Apply prompts to sections, write analyses + findings
  pipeline.py       Orchestration. Imports everything; nothing imports it
prompts/            Versioned prompt files, e.g. note_materiality_v1.md
                    Each file is split by a `## Template` marker: everything ABOVE it is
                    documentation for humans (Purpose/Inputs/Output/Constraints/Success
                    criteria/Failure cases, SPEC-006 R3) and is STRIPPED by
                    analyze.load_prompt_template; only what is below it is sent to the
                    model. Any instruction the model must obey — including the output
                    schema — therefore has to appear below the marker. Documenting the
                    schema only in the header silently ships a prompt that asks for a
                    shape the model was never shown (see SPEC-006 v1→v2, found in the
                    sampled development run, not in tests: every unit test supplies its
                    own template string, so none of them exercised the real file's
                    header/template split).
dashboard/app.py    Streamlit
tests/
data/
  raw/              Gzipped filings
  app.db            SQLite
.github/workflows/  poll.yml
```

**Rules:**
- Dependencies form a directed acyclic graph. `pipeline.py` is the only orchestrator.
- `edgar_client.py` is the sole module that makes requests to sec.gov.
- No literal values outside `config.py`.
- Every module is importable and testable in isolation.

---

## 6. Database Schema

Nine tables.

```sql
-- ============ REFERENCE ============
CREATE TABLE companies (
    cik              TEXT PRIMARY KEY,   -- zero-padded 10 digits
    ticker           TEXT NOT NULL,
    name             TEXT NOT NULL,
    fiscal_year_end  TEXT                -- 'MMDD'
);

-- ============ LAYER 1: FACTS ============
CREATE TABLE filings (
    accession_no   TEXT PRIMARY KEY,     -- '0001018724-26-000004'
    cik            TEXT NOT NULL REFERENCES companies(cik),
    form_type      TEXT NOT NULL,        -- '10-K' | '10-Q' | '8-K'
    filing_date    TEXT NOT NULL,        -- ISO 8601
    period_end     TEXT,                 -- ISO 8601
    fiscal_year    INTEGER,              -- SPEC-005: companyfacts fy label; NULL for 8-K
    fiscal_period  TEXT,                 -- SPEC-005: companyfacts fp label; NULL for 8-K
    items          TEXT,                 -- 8-K items, comma-separated; NULL otherwise
    primary_doc    TEXT,                 -- 'amzn-20260331.htm'
    raw_path       TEXT,                 -- directory holding gzipped archives
    discovered_at  TEXT NOT NULL,
    status         TEXT NOT NULL         -- discovered|fetched|sectioned|analyzed|failed
);

CREATE TABLE sections (
    id                    INTEGER PRIMARY KEY,
    accession_no          TEXT NOT NULL REFERENCES filings(accession_no),
    category              TEXT NOT NULL,  -- Statements|Notes|Policies|MDA|RiskFactors|Exhibit
    short_name            TEXT NOT NULL,  -- 'Income Taxes'
    source_file           TEXT,           -- 'R14.htm' | 'primary'
    position              INTEGER,
    text_hash             TEXT NOT NULL,  -- sha256 of cleaned plain text; sole link to content
    normalized_text_hash  TEXT,           -- SPEC-005: wording identity, see note below
    word_count            INTEGER,        -- SPEC-005
    sentence_count        INTEGER,        -- SPEC-005
    complex_word_count    INTEGER,        -- SPEC-005
    UNIQUE(accession_no, category, short_name, source_file)
);

CREATE TABLE xbrl_facts (
    id            INTEGER PRIMARY KEY,
    cik           TEXT NOT NULL REFERENCES companies(cik),
    taxonomy      TEXT NOT NULL,         -- 'us-gaap' | 'dei'
    concept       TEXT NOT NULL,
    unit          TEXT NOT NULL,         -- 'USD'
    period_start  TEXT,
    period_end    TEXT NOT NULL,
    fiscal_year   INTEGER,
    fiscal_period TEXT,                  -- 'FY' | 'Q1' | 'Q2' | 'Q3'
    value         REAL NOT NULL,
    accession_no  TEXT,                  -- NULL if the source accn isn't in `filings`
    form_type     TEXT,
    duration_days INTEGER,               -- period_end - period_start; NULL for instant facts
    filed_date    TEXT,                  -- the API's `filed` value; latest wins at compute time
    UNIQUE(cik, concept, unit, period_start, period_end, accession_no)
);

-- ============ LAYER 2: CALCULATIONS ============
CREATE TABLE metrics (
    id           INTEGER PRIMARY KEY,
    cik          TEXT NOT NULL REFERENCES companies(cik),
    accession_no TEXT REFERENCES filings(accession_no),
    period_start TEXT NOT NULL,          -- SPEC-004: part of the key, see note below
    period_end   TEXT NOT NULL,
    name         TEXT NOT NULL,          -- 'operating_margin'
    value        REAL,
    formula      TEXT NOT NULL,          -- 'OperatingIncomeLoss / Revenues'
    inputs_json  TEXT NOT NULL,
    calc_version TEXT NOT NULL,
    computed_at  TEXT NOT NULL,
    UNIQUE(cik, period_start, period_end, name, calc_version)
);

-- ============ LAYER 3: AI INTERPRETATION ============
CREATE TABLE analyses (
    id             INTEGER PRIMARY KEY,
    section_id     INTEGER NOT NULL REFERENCES sections(id),
    prompt_name    TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    model          TEXT NOT NULL,
    input_hash     TEXT NOT NULL,        -- sha256 of the FULLY RENDERED prompt (every
                                          -- interpolated value included), SPEC-006 R2
    output_json    TEXT NOT NULL,
    call_id        INTEGER REFERENCES llm_calls(id),  -- SPEC-006: sole source of spend
    created_at     TEXT NOT NULL,
    UNIQUE(input_hash)                   -- this IS the response cache
);

CREATE TABLE findings (
    id           INTEGER PRIMARY KEY,
    analysis_id  INTEGER NOT NULL REFERENCES analyses(id),
    accession_no TEXT NOT NULL REFERENCES filings(accession_no),
    category     TEXT NOT NULL,          -- red_flag|accounting_change|litigation|
                                          -- concentration|liquidity|note_item (SPEC-006 R5)
    severity     TEXT,                   -- high|medium|low
    headline     TEXT NOT NULL,
    detail       TEXT,
    quote        TEXT,                   -- verbatim text from the filing
    created_at   TEXT NOT NULL
);

-- ============ SPEC-006: LLM SPEND LEDGER ============
CREATE TABLE llm_calls (
    id             INTEGER PRIMARY KEY,
    created_at     TEXT NOT NULL,
    model          TEXT NOT NULL,
    prompt_name    TEXT,
    prompt_version TEXT,
    input_tokens   INTEGER NOT NULL,
    output_tokens  INTEGER NOT NULL,
    cost_usd       REAL NOT NULL,
    status         TEXT NOT NULL,        -- ok | error | refused | reconciliation (§4.3)
    note           TEXT
);

-- ============ SPEC-005: OBSERVATIONS (between Calculations and Interpretation) ============
CREATE TABLE observations (
    id            INTEGER PRIMARY KEY,
    cik           TEXT NOT NULL REFERENCES companies(cik),
    accession_no  TEXT REFERENCES filings(accession_no),  -- the CURRENT period's filing
    period_end    TEXT NOT NULL,
    rule_name     TEXT NOT NULL,
    rule_version  TEXT NOT NULL,
    subject       TEXT NOT NULL,         -- metric name or section short_name
    severity      TEXT NOT NULL,         -- high | medium | low
    statement     TEXT NOT NULL,
    refs_json     TEXT NOT NULL,         -- BOTH sides of any comparison, see note below
    created_at    TEXT NOT NULL,
    UNIQUE(cik, period_end, rule_name, rule_version, subject)
);
```

### Note on `xbrl_facts.duration_days` / `filed_date` (SPEC-004)

Both are observed values or pure arithmetic over observed values — `duration_days` is
`period_end − period_start`, `filed_date` is the API's own `filed` field — so they
belong in the facts layer, not derived later. `duration_days` is what period
classification (quarterly/half-year/three-quarter/annual/other) is computed from; the
API's own `frame` field is deliberately never used for this. `filed_date` is what
restatement selection (latest `filed_date` wins, per concept) is computed from.

**Concept alias resolution happens per period, never once per company.** For a given
canonical input (e.g. `revenue`, `debt_noncurrent`) and a given `(period_start,
period_end)`, the first alias with a value *for that period* wins — which alias won for
an earlier or later period is irrelevant and may differ. Resolving "which alias does
this company use" once, company-wide, is a different and wrong algorithm: Amazon tagged
`GrossProfit` in filings covering FY2007–2008 and has not since (zero entries with
`fy >= 2018`, confirmed live). A company-wide resolution would treat `GrossProfit` as
"available" for Amazon based on that seventeen-year-old tag and silently fail to compute
`gross_margin` via the revenue−cogs fallback for every modern period. See SPEC-004 R1a.

### Note on `sections.text` (removed in SPEC-003)

Cleaned section text is not stored in the database. It lives in gzipped,
content-addressed files at `data/sections/{hash[:2]}/{hash}.txt.gz`, keyed by
`text_hash`. The file path is never stored — it is derivable from the hash, and storing
it would let the two disagree. `text_hash` is therefore not just a cache key (as it was
under SPEC-002) but the sole link between a row and its content.

Why: SQLite files don't delta-compress in git, and section text was ~32 MB of the 33.7 MB
`app.db` committed after SPEC-002. Text is immutable — a filed note never changes — so it
belongs in the same class of storage as `data/raw/`, not in the one file rewritten on
every pipeline run. See SPEC-003 and the §4.1 note above. This is the reasoning future
specs should follow when deciding where something belongs: **keep mutable state small;
make large, immutable data content-addressed and out of the database.**

### Note on `metrics.period_start` (SPEC-004, added during implementation)

`period_end` alone is not a sufficient key for a metric row. Discovered against real
data while testing idempotency: Amazon has a 365-day-duration fact and a 90-day-duration
fact that both end on `2025-06-30` (an XBRL trailing-twelve-month figure happens to close
on the same calendar date as an ordinary quarter). A `basis="both"` metric (e.g.
`revenue_yoy`, computed once for the annual period and once for the quarterly period
sharing that end date) was silently overwriting one instance with the other on every
`compute-metrics` run — the two computations never diverged in *content* by more than
their own correct values, but the row alternated between them forever, which is exactly
the non-idempotency this project's tests exist to catch. `period_start` was added to the
key specifically because `period_end` collisions across duration classes are real, not
hypothetical — this is the same class of risk as R3's "quarterly and YTD share an end
date," just one level more surprising (annual and quarterly sharing an end date).

### Note on `metrics.equity` / `metrics.net_income`: parent vs. NCI-inclusive (SPEC-004)

`net_margin` uses `net_income`; `equity_multiplier` and `roe` use `equity`. Before
SPEC-004's category-6 fix, `net_income` could resolve via `ProfitLoss`
(noncontrolling-interest-inclusive) in a period where `equity` resolved via
`StockholdersEquity` (parent-level only) — a real basis mismatch producing a wrong `roe`,
not just an imprecise one. **This could not have been caught by DuPont's own
reconciliation** (`net_margin × asset_turnover × equity_multiplier` vs. `roe`): all three
factors and the target are built from the same (possibly mismatched) `net_income` and
`equity` values, so a consistent wrong basis reconciles perfectly with itself. It took a
check against the *other* alias (validate category 6, alias agreement) to surface it.
`net_income` and `equity` now each have exactly one alias, removing the possibility
structurally rather than relying on catching it downstream every time.

### Note on NULL discipline: primary measures vs. required-disclosure components (SPEC-004)

The general rule remains "absence is not zero" (SPEC-002 R9 origin, applied throughout
this project). SPEC-004's `total_debt` needed a narrow, named exception: finance lease
liabilities are an *additive component of a total the filer is required to disclose* —
ASC 842 requires a lessee with finance leases to recognize and tag
`FinanceLeaseLiability{Noncurrent,Current}`. A company that never tags them has no
finance leases; the absence itself is the disclosure, not an unknown. Contrast with a
*primary* measure like `borrowings` (still NULL if absent — a missing tag there could
mean anything) or `interest_expense` (unchanged — absence still NULL). This distinction
is worth naming explicitly because it is easy to over-generalize either direction: reading
"absence is not zero" too literally made `total_debt` permanently NULL for NVIDIA
(confirmed: it discloses only operating leases); reading this exception too broadly would
license guessing zero for inputs where absence is genuinely ambiguous. It applies to
finance lease liabilities specifically, because ASC 842 is what makes the absence
diagnostic — not to XBRL inputs in general.

### Why these columns exist

**`metrics.formula` and `metrics.inputs_json`** — every ratio on the dashboard can display
its own derivation. An analyst tool showing "Operating margin: 11.2%" without letting you
see numerator and denominator is a toy. Auditability at no cost.

**`analyses.input_hash` with a UNIQUE constraint** — the database itself prevents paying
twice for an identical call. Change the prompt, the hash changes, you get a fresh analysis
and the old one is retained for comparison. Prompt versioning and cost control from one column.
Covers the *fully rendered* prompt (every interpolated value, not the template) — SPEC-006
R2 — since a hash keyed only on the static template would let two calls with different
interpolated content collide and silently serve the wrong cached answer.

**`findings.quote`** — every AI claim must be anchored to verbatim filing text. The strongest
available control against hallucination, and structural rather than a prompt instruction.
A finding without a quote is a bug.

**`filings.status`** — makes the pipeline resumable and idempotent.

**`llm_calls` as the sole source of spend (SPEC-006)** — `analyses` no longer carries its
own `input_tokens`/`output_tokens`/`cost_usd`; every one of those lives in `llm_calls`,
referenced by `analyses.call_id`, and every call attempt (including failures and
refusals) appends a row there. One ledger, checked before every call, is what makes the
lifetime cap (`LLM_BUDGET_USD`, $10.00 as of SPEC-006A §4.2) a real limit rather than a
number nobody enforces — and, as the 2026-07-27 incident showed, only a real limit for
spend that actually routes through this ledger in the first place.

### Note on `sections.UNIQUE`

Changed from `UNIQUE(accession_no, category, short_name)` to `UNIQUE(accession_no,
category, short_name, source_file)` during SPEC-002 review, before any row had ever been
written to the table. Two reports in the same filing can legitimately share a
`MenuCategory`/`ShortName` pair; `source_file` (the distinct R-file, e.g. `R14.htm`)
disambiguates them without requiring `short_name` to ever be anything but the verbatim
SEC value. Because the table was empty, this was a schema edit, not a migration.

### Note on `fiscal_period`

There is deliberately no `Q4`. Companies do not file a Q4 10-Q; the fourth quarter is
covered by the 10-K, and SEC XBRL reports `FY` for annual figures, not a `Q4` `fp` value.

**Correction (SPEC-004, found live):** an earlier version of this note said Q4 "must be
derived" (FY minus Q1+Q2+Q3) as if no directly-tagged quarterly figure ever existed for
it. That's not quite right. Amazon's 10-K directly tags a genuine three-month duration
fact ending on the fiscal year-end date — for revenue, operating income, net income, tax
expense, and diluted EPS — as part of its own supplementary disclosure, alongside the
full-year figure. That fact is real, company-reported, and distinct from the annual one
(same end date, different start date); it is not a derived value and should be used
directly where present, exactly like any other quarterly fact. Derivation (FY − Q1 − Q2
− Q3) is needed **only** where no such directly-tagged fact exists for a given company
or concept — it is a fallback, not the default path. When implemented, the derivation
still belongs in `metrics`, not `xbrl_facts` — it would be a calculation, not an
observed fact — but "belongs in metrics" no longer means "always computed," since the
fact frequently already exists in `xbrl_facts`.

Consequence for period classification: a genuine implicit-Q4 fact and the annual fact
for the same fiscal year necessarily share an end date. This is not period-mixing (see
SPEC-004 R3a) — the two facts have different `period_start` values and the engine keys
on the full `(period_start, period_end)` pair, never on `period_end` alone — but any
code that assumes "annual metrics come only from `filings` rows shaped like a 10-K
period" must still restrict to real `filings.period_end` values (SPEC-004 R3a), since
duration alone (350–380 days) is not sufficient to distinguish a real fiscal year-end
from an unrelated 365-day window that happens to end nearby.

### Note on `sections.text_hash` vs. `normalized_text_hash` (SPEC-005)

`text_hash` means **content identity**: any byte differs, the hash differs, and it
remains the sole link to the content-addressed store (§4.1, SPEC-003). `normalized_text_hash`
means **wording identity**: computed over the text after stripping the XBRL viewer's
version-stamp line (a rendering artifact present at the start of every R-file's body,
confirmed §3.7, unrelated to content) and masking every numeric token to one placeholder.

Measured live before this was relied on for anything: exact `normalized_text_hash`
equality still changes on 98.9% of fiscal-year-matched Policies comparisons and 98.3% of
Notes comparisons — confirmed by direct diff to be genuine, small, real prose edits
(filers do lightly rewrite disclosure language most years), not a normalization gap. The
column's *meaning* (wording identity, exact) stays as specified; SPEC-005's
`section_wording_changed` rule additionally computes a similarity ratio for anything the
hash says differs, and fires only past a measured materiality threshold — the hash
answers "identical or not," the rule answers "materially different or not," and those are
deliberately different questions. See SPEC-005 R1a/R5a.

### Note on `filings.fiscal_year` / `fiscal_period` (SPEC-005)

Populated at XBRL ingest time (`xbrl.ingest_company`) from the companyfacts API's own
`fy`/`fp` labels — scanned across every concept in the payload, not just configured ones,
so coverage does not depend on which canonical inputs happen to be tagged in a given
filing. NULL for 8-Ks (companyfacts has no entries for them).

Verified live before this was written: every one of 61 real NVDA/MU accessions checked
maps to exactly one consistent `(fy, fp)` pair across all of its own facts — the API's
fiscal labels are internally self-consistent per filing, with no observed exceptions. If
a future filing ever produces conflicting labels for the same accession, the ingest code
leaves it unresolved (NULL) rather than guessing.

This is what makes SPEC-005's section prior-year matching robust to NVIDIA's and
Micron's floating 52/53-week fiscal years **by construction**: the columns are not
derived from any date at all, so unlike a calendar-day-window heuristic there is no
tolerance value to get wrong when a fiscal year runs 371 days instead of 364. Section
prior-year matching, and the fiscal-period lookups `metric_stopped_computing` and
`depreciation_rate`'s YoY divergence need, all use these columns exclusively — never date
arithmetic, never a join into `xbrl_facts`. (`metrics.py`'s own SPEC-004 YoY/QoQ
mechanism — date-offset with a tolerance window, §7 below — is unchanged; it was already
live-verified and SPEC-005 does not touch it. This column is for what SPEC-005 itself
introduces.)

### Note on `observations.accession_no` / `refs_json` (SPEC-005)

`accession_no` holds the **current period's** filing only — the filing the observation is
*for*. A comparison rule (most of them: everything except the single-period Beneish-style
checks) necessarily reads two periods' worth of data; the prior period's row id goes in
`refs_json` alongside the current period's, never into `accession_no`. `refs_json` is a
JSON list of `{"table": "metrics"|"sections", "id": <int>}` covering every row behind the
statement, both sides of any comparison — an observation whose `refs_json` cannot be
walked back to the exact rows that produced it is a bug (SPEC-005 R2).

---

## 7. Metric Set

Computed in `metrics.py` from `xbrl_facts`. Version as `CALC_VERSION` (`v1`). Superseded
the original 10-ratio V1 list with SPEC-004: metrics are now a **declarative registry**
in `config.py` (name, canonical inputs, basis, plausible range, whether a prior period is
needed) plus a small computation engine in `metrics.py`, rather than hand-written
per-ratio functions. Adding a metric is a config entry; if it requires new code, the
design is wrong (exceptions — Beneish, DuPont components — are named and justified,
never silent).

**37 named metrics, plus the 8 Beneish component indices stored individually** (see
SPEC-004 R6/R7 for full definitions and formulas):

| Category | Metrics |
|---|---|
| Growth | `revenue_yoy`, `revenue_qoq`, `operating_income_yoy`, `eps_diluted_yoy` |
| Margins | `gross_margin`, `operating_margin`, `net_margin`, `ebitda`, `ebitda_margin`, `rnd_intensity`, `sga_intensity`, `incremental_gross_margin` |
| Returns | `effective_tax_rate`, `nopat`, `invested_capital`, `roic`, `roe`, `asset_turnover`, `equity_multiplier`, `fixed_asset_turnover` |
| Capital & cash | `capex_to_revenue`, `capex_to_depreciation`, `free_cash_flow`, `fcf_margin`, `fcf_conversion`, `sbc_to_revenue`, `depreciation_rate` |
| Working capital (annual basis) | `days_inventory`, `days_receivables`, `days_payables`, `cash_conversion_cycle`, `inventory_growth_less_revenue_growth` |
| Solvency | `net_debt`, `net_debt_to_ebitda`, `interest_coverage`, `current_ratio` |
| Quality | `beneish_m_score` (+ 8 stored components: DSRI, GMI, AQI, SGI, DEPI, SGAI, LVGI, TATA) |

`net_margin × asset_turnover × equity_multiplier` is the DuPont decomposition of `roe`
and must reconcile to it within 1% — asserted directly by `validate`, not assumed.
Balance-sheet inputs use ending balances, not averages, throughout.

**Implementation note — concept aliasing.** Companies tag the same idea differently.
Amazon reports net sales as `RevenueFromContractWithCustomerExcludingAssessedTax`, not
`Revenues`. `config.py` holds an ordered alias list per canonical concept; resolution
happens **per period, never once per company** (§6 note above) — `metrics.py` takes the
first alias with a value for that specific period. Where no input resolves for a period,
write `NULL` — never a guess. See §2.1 for the rule on what may and may not appear in an
alias list: every alias must be the same fact under a different name, never a broader or
narrower quantity (`total_debt` exists as its own canonical input for exactly this
reason — it is not an alias of `debt_noncurrent`).

**Amazon's `GrossProfit` history, corrected.** Earlier revisions of this document said
Amazon "does not report gross profit directly," implying the tag was simply never used.
Confirmed live: Amazon *did* tag `GrossProfit`, but only in filings covering fiscal years
2007–2008 (filed 2009–2010); it has not appeared in any filing covering `fy >= 2018`.
`gross_margin` still falls back to (revenue − cogs) ÷ revenue for Amazon in every modern
period — the mechanism is unchanged — but the reason is "stopped tagging it sixteen years
ago," not "never had it," and getting this precisely right is what motivated the
per-period (rather than per-company) resolution rule above. Micron and NVIDIA, as
semiconductor manufacturers, do report `GrossProfit` currently.

---

## 7a. Observation Rule Set (SPEC-005)

Eleven rules (the original nine `SPEC-005 R5` table rows —
`section_appeared`/`section_disappeared` share one row — plus `section_renamed`, added
post-implementation, R5e), declarative in `config.RULE_REGISTRY` plus a small engine in
`observations.py`, mirroring §7's metric-registry split exactly. Full definitions,
thresholds, and the governing transition-only-firing principle (§2.3) are in SPEC-005 R5;
severity-by-materiality (including the two mechanisms that can only ever lower it further
— `BOILERPLATE_NOTE_NAMES` and cross-company simultaneity) is in §2.4; not duplicated here.

| Rule | Reads | Severity |
|---|---|---|
| `metric_multi_year_extreme` | `metrics` | high (margins/returns/working_capital) or medium (other) |
| `metric_sigma_move` (quarterly-only) | `metrics` | medium |
| `metric_threshold_cross` | `metrics` | high |
| `metric_divergence` | `metrics` | high |
| `section_wording_changed` | `sections` (Policies + Notes) | high |
| `section_length_change` | `sections` (Policies + Notes) | medium |
| `section_appeared` / `section_disappeared` | `sections` (Notes) | low |
| `section_renamed` | `sections` (Notes) | low |
| `metric_stopped_computing` | `metrics` | medium |
| `readability_change` | `sections` (Policies + Notes) | low |

All severities above are subject to the `BOILERPLATE_NOTE_NAMES` and cross-company
overrides (§2.4), which can only ever demote further, never promote.

**Calibration measure replaced (§2.4, R8b):** the original "share of eligible periods
fired, flag above 33%" measure structurally penalised a rule checking many things per
filing (`metric_multi_year_extreme`, 45 metrics) against one checking a single condition,
regardless of how much of any *one* filing's list either actually occupies — exactly the
wrong question for a display-budget concern. Replaced by mean/max observations
contributed to a single filing, measured live (all three companies, current rule
versions):

| Rule | Mean / filing | Max / filing |
|---|---|---|
| `metric_multi_year_extreme` | 6.9 | **24** |
| `metric_sigma_move` | 4.6 | 12 |
| `readability_change` | 4.4 | 11 |
| `section_wording_changed` | 3.9 | 11 |
| `section_length_change` | 3.6 | 10 |
| `section_appeared` | 2.3 | 5 |
| `section_disappeared` | 2.3 | 7 |
| `metric_stopped_computing` | 1.7 | 5 |
| `section_renamed` | 1.2 | 2 |
| `metric_threshold_cross` | 1.1 | 2 |
| `metric_divergence` | 1.0 | 1 |

Every rule fires at least once (zero dead rules). `metric_multi_year_extreme`'s max of 24
— more than double every other rule's maximum — is the precise, quantified version of the
Amazon top-5 problem: not miscalibration (every one of those 24 claims on its worst
filing is independently true and verifiable), but a display-layer risk that an uncapped
top-N selection would surface "the rule that fired most on this filing" rather than "the
most material things about it." Fixed at the display layer, not the detection layer — see
§2.4's top-N cap, binding on SPEC-006 and the dashboard.

One further gap, unrelated to display: NVIDIA's Q2 FY2023 `incremental_gross_margin` (the
AC6 case) does not fire, because only 3 valid prior quarters exist for that specific
metric at that point — short of the 8-quarter minimum, and verified to trace back to a
structural fact true for all three companies (none files a Q4 10-Q, so every quarterly
chain is 3 real periods per fiscal year, not 4). Reported, not patched around; see the
SPEC-005 changelog and decision log entry 31.

---

## 8. Scope

### V1 — Must have
- Poll for new 10-K, 10-Q, and Item 2.02 8-K filings for the watchlist
- Store raw filings permanently per §4.1
- Extract statements, notes, and MD&A into `sections`
- Ingest XBRL facts; compute the metric set above (§7)
- LLM analysis of notes and MD&A producing quote-anchored findings
- Streamlit dashboard showing statements, ratios, findings, and provenance
- Runs unattended on GitHub Actions

### V2 — Should have
- Year-over-year comparison of the same note across filings
- Material change detection
- Historical backfill (same code path, different inputs)
- Derived Q4 figures
- Email or push notification on high-severity findings

**Parked ideas generated during V1 build-out — evaluated, deliberately not pursued, and not
designed — live in `ROADMAP-V2.md`, not here.** One home for them, so they can be revisited
without either being lost or quietly expanding V1's scope in the meantime.

### Future — Could have
- Cross-company comparison (NVDA/MU supply-chain linkage is a natural first case)
- Earnings call transcripts
- Full risk factor diffing
- Valuation modelling

---

## 9. Decision Log

| # | Decision | Rationale | Date |
|---|---|---|---|
| 1 | Facts / Calculations / Interpretation as separate tables | Structural enforcement beats convention | 2026-07-25 |
| 2 | GitHub Actions over a rented VM | $0, no ops burden, ~8 hours saved | 2026-07-25 |
| 3 | SQLite committed to git | Ephemeral runners need durable state; free version history | 2026-07-25 |
| 4 | Section-level LLM calls, never whole filings | 10× cheaper and produces better analysis | 2026-07-25 |
| 5 | R-files via FilingSummary for statements and notes | Removes the largest technical risk | 2026-07-25 |
| 6 | 8-Ks filtered to Item 2.02 only | Guidance without the noise | 2026-07-25 |
| 7 | Automate last — manual CLI first, cron at the end | Automation multiplies bugs and hides failures | 2026-07-25 |
| 8 | Watchlist AMZN / NVDA / MU | AI-infrastructure basket; three distinct fiscal calendars stress the design correctly | 2026-07-25 |
| 9 | Archive original content, not SEC renderings | Ex-99.1 is unique and essential; R-files are regenerable | 2026-07-25 |
| 10 | Build against Amazon first | Only watchlist member expected to file during the build window | 2026-07-25 |
| 11 | Archiving unbounded; LLM analysis bounded by a configurable start date | Archiving is free and unbounded history is useful test material; unbounded analysis would burn the entire LLM budget on one run | 2026-07-26 |
| 12 | Section text moved out of SQLite into content-addressed files (`data/sections/`); `sections.text_hash` is the sole link | Mutable state (`app.db`, rewritten every run) must stay small; large, immutable data belongs outside it, one write ever, mirroring `data/raw/` | 2026-07-26 |
| 13 | Concept alias resolution is per-period, never per-company | Amazon's `GrossProfit` tag is real but stale (FY2007–2008 only); a company-wide resolution would silently break the modern-period fallback it exists to provide | 2026-07-26 |
| 14 | Alias lists may only contain true synonyms; a broader/narrower quantity gets its own canonical input (`total_debt`) | `LongTermDebt` (total) and `ReceivablesNetCurrent` (broader) were found paired as "aliases" for narrower quantities, risking silent double-counting (Micron debt) or wrong substitution | 2026-07-26 |
| 15 | NVIDIA and Micron watchlist entries corrected to floating 52/53-week fiscal years | Confirmed live (NVDA annual durations of 363/370 days); prevents any future code from assuming a fixed year-end date for either company | 2026-07-26 |
| 16 | `metrics.period_start` added to the table and its UNIQUE key | Found via real Amazon data during implementation: a 365-day and a 90-day duration fact end on the same calendar date, so `period_end` alone let a `basis="both"` metric silently overwrite one class's row with the other every run | 2026-07-26 |
| 17 | `ppe_and_lease_net` added as its own canonical input; never an alias of `ppe_net` | Micron folded finance-lease ROU assets into its PP&E line from FY2021 — a broader measure, same shape of problem as `LongTermDebt`/`ReceivablesNetCurrent` (§2.1) | 2026-07-27 |
| 18 | `total_debt` now adds finance lease liabilities; NULL (not zero) for companies that never tag them | Micron's real "Long-term debt" balance sheet line includes finance leases; a company with only operating leases (NVIDIA) genuinely may have none, but "never tagged" and "zero" stay different statements per R9 | 2026-07-27 |
| 19 | Annual/quarterly metrics restricted to real `filings.period_end` values, not any 350–380/80–100-day duration fact | Amazon's directly-tagged implicit Q4 (corrected fiscal_period note above) shares an end date with the annual figure but is not itself a fiscal year-end; computing a full metric sweep against it produced phantom chart points | 2026-07-27 |
| 20 | Five more canonical-input splits (`depreciation`, `interest_expense_debt`, `equity_including_nci`, `net_income_including_nci`; `sbc` trimmed with no replacement) | Live alias-agreement check (validate category 6) found consistent, non-random disagreements — same shape as `LongTermDebt`/`ReceivablesNetCurrent`, confirming §2.1's rule generalizes | 2026-07-27 |
| 21 | Alias-agreement exceptions register added (`config.ALIAS_AGREEMENT_EXCEPTIONS`) | `capex`'s one-period Amazon disagreement is a real tag-transition artifact, not a broader/narrower split, and is already hand-verified via AC9 — needed a place to write down an accepted exception rather than either hard-failing forever or silently dropping the check | 2026-07-27 |
| 22 | NULL discipline narrowly refined: absence = zero for a required-disclosure additive component (finance leases, ASC 842), still NULL for primary measures | Applying "absence is not zero" literally made `total_debt` permanently NULL for NVIDIA; ASC 842 makes the absence itself diagnostic for finance leases specifically, so the exception is named and scoped rather than a general loosening | 2026-07-27 |
| 23 | Debt-reconciliation and range exceptions registers added, keyed per-period (not per-canonical, unlike the alias register) | Amazon's ASU 2015-03 debt transition and NVIDIA's/Micron's real range findings are checked, one-off, explained values, not standing properties of an input or metric — a future unrelated finding for the same company/metric must still hard-fail | 2026-07-27 |
| 24 | `section_wording_changed` (renamed from `accounting_policy_changed`) applies to both Policies and Notes, gated by a measured similarity-ratio threshold, not a category restriction | Live measurement: exact `normalized_text_hash` equality changes on 98.9%/98.3% of Policies/Notes comparisons — nearly identical between categories, and confirmed by direct diff to be genuine incremental prose editing, not a normalization bug. A category choice cannot fix a materiality problem | 2026-07-27 |
| 25 | State-based observation rules (`metric_threshold_cross`, `metric_divergence`) fire only entering a condition, never while it persists (§2.3) | Live measurement before the fix: `capex_to_depreciation` fired on 71% of eligible periods, `fcf_conversion` on 50% — both are sustained-state conditions for this watchlist, not events | 2026-07-27 |
| 26 | `metric_multi_year_extreme` minimum history split by basis (8 prior quarterly periods, 4 annual); `metric_sigma_move` stays quarterly-only at 8 | A flat 8-period rule left every annual-only metric (Beneish, working capital) permanently dead at current corpus depth (0 eligible periods for any company, ever); ordering-based extremes are robust with few points, a standard deviation is not | 2026-07-27 |
| 27 | `metric_stopped_computing` requires the same metric to have computed at the same fiscal period one year earlier (via `filings.fiscal_year`/`fiscal_period`), not merely "non-null last period" | Without this, `revenue_qoq` "stopped computing" on every single Q1 for every company, forever — a structural gap (no directly-tagged Q4 to compare against), rediscovered as a false event every year | 2026-07-27 |
| 28 | `config.NOTE_NAME_ALIASES` / `FLUCTUATING_NOTE_NAMES` added | Same alias-purity discipline as §2.1, applied to SEC's own FilingSummary `ShortName` values: three verified Micron renames (never co-occurring, same underlying XBRL element) would otherwise register as false `section_appeared`+`section_disappeared` pairs; one housekeeping note's presence legitimately toggles quarter to quarter and is excluded from those two rules only | 2026-07-27 |
| 29 | `filings.fiscal_year`/`fiscal_period` added, populated at XBRL ingest from companyfacts' own `fy`/`fp` labels | Verified live: every one of 61 real NVDA/MU accessions maps to exactly one consistent `(fy, fp)` pair. Gives SPEC-005's section prior-year matching an authoritative label immune to 52/53-week calendar drift by construction, replacing the date-window heuristic used only during this spec's own live calibration measurements | 2026-07-27 |
| 30 | `MetricDef.extreme_informative` added, excluding `ebitda`/`nopat`/`invested_capital`/`free_cash_flow`/`net_debt` from `metric_multi_year_extreme`; rule bumped to `rule_version` `v2` | A "record" in a compounding dollar-level metric mostly restates that a growing company grew, not that something happened; a record in a ratio (margin, return, days-outstanding) is information regardless of the dollar scale underneath it. Measured live: firing rate 37.7% → 34.4% | 2026-07-27 |
| 31 | AC6 amended rather than the 8-quarter minimum lowered | Verified live: no watchlist company files a Q4 10-Q (`{Q1, Q2, Q3}` only, confirmed for all three companies) — every quarterly chain is 3 real periods per fiscal year, not 4, which is why NVIDIA's Q2 FY2023 `incremental_gross_margin` had only 3 valid prior quarters at that point. A structural, self-resolving gap, verified and documented rather than closed by weakening the general minimum-history rule for every metric on every company. Derived Q4 figures (already in §8's V2 scope) flagged as the direct future fix | 2026-07-27 |
| 32 | `app.db`'s absolute size ceiling replaced by a growth measure (current size + measured marginal cost per filing) and a 15 MB soft ceiling | The absolute number had already been relaxed twice during SPEC-004 (5 MB → 6 MB) and needed relaxing again after SPEC-005; a number that keeps moving was never the right criterion. Measured live (scratch copy of the real database, one Micron 10-Q reprocessed end to end): 72 KB per filing — at that rate the 15 MB soft ceiling is roughly a decade away at current watchlist size | 2026-07-27 |
| 33 | Observation severity reassigned by analytical materiality (metric category for `metric_multi_year_extreme`, uniform "high" for threshold/divergence crossings, "low" for section appeared/disappeared), not by which rule detected it | Confirmed live: the original scheme let a boilerplate SEC disclosure item showing up "new" this year outrank a five-year low in gross margin — Amazon's and Micron's top-5-by-severity observations were dominated by presentation artifacts, not economics | 2026-07-27 |
| 34 | `BOILERPLATE_NOTE_NAMES` and cross-company-simultaneity demotion added, scoped to section-subject rules only | Four SEC-mandated disclosure items (Pay vs Performance, Cybersecurity Risk Management, Insider Trading Arrangements, Insider Trading Policies and Procedures) confirmed live to roll out industry-wide within the same real-world window across the whole watchlist — a regulatory-calendar event, not company-specific information. Scoping to sections only was itself a live-found-and-fixed bug: applied to every rule, it silently demoted every `metric_multi_year_extreme` observation on a real filing, since every company files annually within ~12 months of every other regardless of any real event | 2026-07-27 |
| 35 | Automatic note-rename detection added (`section_renamed`); `difflib.SequenceMatcher.quick_ratio()` replaced with real `.ratio()` everywhere it's used for a similarity decision | `quick_ratio()` is a fast upper-bound heuristic (character-multiset overlap), not real sequence matching, and is unreliable for this purpose — verified live, two genuinely unrelated Micron notes scored 0.87 under `quick_ratio()` against a true `.ratio()` of 0.03. `section_wording_changed`'s threshold was re-measured from scratch with the corrected metric (0.85 → 0.60); the old value, re-measured correctly, would have fired on 61–67% of comparisons | 2026-07-27 |
| 36 | `BOILERPLATE_NOTE_NAMES` excluded from rename-pairing on either side | Verified live: even after the `.ratio()` fix, "Pay vs Performance Disclosure" repeatedly paired with an unrelated accounting-standards note at the threshold boundary, because two different boilerplate SEC-template disclosures share enough generic structural language to look similar without being the same note. No signal lost — boilerplate names are already forced "low" regardless of which rule reports them | 2026-07-27 |
| 37 | The 33%-eligible-periods firing-rate ceiling replaced by a per-filing contribution measure (mean/max observations contributed to a single filing, per rule) | The old measure counted firings per `(subject, period)`, structurally penalising a rule checking many things per filing (45 metrics) against one checking a single condition, regardless of how much of any ONE filing's list either actually occupies. Measured live: `metric_multi_year_extreme` contributes up to 24 observations to a single filing, more than double every other rule's maximum — the precise, quantified version of the Amazon top-5 problem | 2026-07-27 |
| 38 | Top-N observation selection must cap contributions from a single rule at 2 (then fill from the next rule) — recorded as binding on SPEC-006 and the dashboard, not implemented here | SPEC-005 has no top-N display surface of its own; the failure mode (one rule's internal ranking dominating a filing's list) was found and measured in this spec (entry 37) and must not be rediscovered independently by whichever future spec builds the first real top-N selection | 2026-07-27 |
| 39 | SPEC-006's section-analysis prompt carries no computed metrics; model is Claude Sonnet 5 | Read three real in-window notes in full before deciding: all were self-contained and numerically dense in their own right, needing none of the 44 period metrics to be understood — supplying them anyway would invite commentary on numbers rather than disclosure, the opposite of the LLM layer's unique job. Sonnet 5 chosen on quality, not cost (all candidates fit the budget with large margin at the corrected 273-call volume): a weaker model is likelier to fabricate a finding rather than correctly return empty, the exact failure mode this spec's quote verification exists to catch | 2026-07-27 |
| 40 | Project's Anthropic key env var renamed `ANTHROPIC_API_KEY` → `EQUITY_RESEARCH_ANTHROPIC_API_KEY` | The 2026-07-27 incident's root cause: this project's key shared a name with the Anthropic SDK's own default variable, which Claude Code reads automatically. The two were never connected in code, only accidentally co-located in the shell environment. A project-specific name makes that confusion structurally impossible and is what makes the new L9 canary (§4.2) meaningful | 2026-07-27 |
| 41 | `LLM_BUDGET_USD` cut $20.00 → $10.00; six new independent budget layers (L3–L7, L9, L10) added, none replacing L2 | A single cap only protects what routes through it — Claude Code's spend never did. §4.2's threat-model table shows every layer's blast radius is different; L3/L6 overlap on purpose (one trusts cost arithmetic, one doesn't) | 2026-07-27 |
| 42 | `LLM_PRICING['claude-sonnet-5']` corrected $3.00/$15.00 → $2.00/$10.00 (the rate actually in effect through 2026-08-31); 197 existing `llm_calls` rows backfilled to match; `LLM_SONNET5_RATE_REVIEW_DATE` added | The "conservative" post-September rate was conservative for the CAP but wrong for the LEDGER, which must reconcile against Console: recorded spend was 50% over Console's real attribution (exactly the 3:2 ratio of the two rates) for the same 197 calls, confirmed by recomputing stored token counts. A ledger that lies about cost, even in the "safe" direction, is worse than a ledger that tells the truth and a cap that is simply smaller | 2026-07-27 |
| 43 | Truncation now read from the API's `stop_reason` field, retried once at `LLM_TRUNCATION_RETRY_OUTPUT_TOKENS` (8,192), and counted as its own `AnalysisOutcome`/`RunStats` status, `"truncated"`, distinct from `"error"` | Recomputing 6 real ledger error rows found 3 were truncation misfiled as a generic parse error (2 at the current 4,096 cap, 1 at the prior 1,024 cap) — a JSON parse failure cannot distinguish "the model was cut off" from "the model emitted bad JSON on its own," and only one of those calls for a higher-cap retry (§4.3) | 2026-07-28 |
| 44 | Empty text extraction now logs the actual API content block types instead of silently trusting the text-block filter | Diagnosed a real mystery ledger row (4,096 output tokens billed, zero text extracted) the moment this ran against the real API: all 4 empty extractions this run were `['thinking']` blocks that consumed the entire output cap before any text — not a distinct bug from truncation, its most extreme case (§4.3) | 2026-07-28 |
| 45 | Every real API attempt now bills its own `llm_calls` row and `RunGuard.record_call`, immediately — never deferred to "whichever attempt the retry loop ends on"; new ledger status `'reconciliation'` added for one-off, reviewed, committed-script adjustments to real spend a since-fixed bug failed to capture at the time | Found live, only because this was the project's first execution of the retry path against the real (billed) API: a truncated-then-successfully-retried section recorded only the retry's tokens, silently dropping the wasted first attempt's real billed cost — same shape of failure as #42 (ledger disagrees with Console), running in the other direction. The 4 truncation-retry cases were reconciled with one labelled adjustment entry, not 4 reconstructed ones, since the wasted attempts' real input token counts are unrecoverable; a 5th case (a generic, non-truncation parse retry, section 1937 — the same pre-existing gap, not new) had no output-token figure at all logged and was deliberately left undocumented in the ledger rather than assigned a fabricated number (§4.3) | 2026-07-28 |
| 46 | `LLM_BUDGET_USD` re-aligned $10.00 → $8.50 | The ledger's recorded lifetime total ($5.99) and the real Console prepaid balance (~$2.80) are different numbers measuring different things — the original $10 balance absorbed both this project's ledgered spend AND the Claude Code leak (#40), but the cap only ever tracked the former. Re-aligned below the real Console balance, not derived from the ledger total; the two require periodic re-alignment against Console going forward, the same standing operator responsibility as L1 (§4.2/§4.3) | 2026-07-28 |
| 47 | Extended "thinking" confirmed enabled by default (adaptive mode, no budget cap of its own, sharing `max_tokens` with the text output) on every real call this project has made | A 4-section real-API probe with `thinking={"type":"disabled"}` explicitly set: all 4 (previously truncating) sections completed in a single call inside the standard 4,096 cap, zero `thinking` blocks, 78–89% fewer output tokens, 67–83% lower cost per section, equal-or-more kept findings, identical materiality calls. Confirms the truncation risk and much of the per-call cost were coming from an unrequested, uncapped reasoning mode a structured-JSON-extraction task does not need (§4.3) | 2026-07-28 |
| 48 | `thinking={"type": "disabled"}` now sent explicitly on every real call (`_RealAnthropicClient.messages_create`) — applied, not just measured | Directly acts on #47's measurement. Not a cache-invalidating change: `compute_input_hash` covers content the model sees (prompt, model, prompt version), never request configuration, so the 257 analyses already cached under adaptive thinking remain valid cache hits — a deliberate policy, not an oversight (§4.3) | 2026-07-28 |
| 49 | Numeric-support checker gained two tiers: ordinal-word normalisation ("fourth" → "4", numeric-support-only, never `verify_quote`) and derived-sum verification (an absent number that is a correct subset-sum of the quote's own numbers → `derived_verified`, distinct from `unsupported`) | Both found live reviewing the 6 real unsupported-token findings from the 2026-07-28 run: 5 of 6 were the model correctly summing disclosed percentages (real evidence a presence-only checker cannot distinguish from a wrong-looking sum, which is the actually dangerous case); 1 of 6 was "Q4" vs. the source's spelled-out "fourth quarter" — a checker artifact, not a real ungrounded number. `NUMERIC_SUPPORT_ENFORCE` stays False either way — this improves what the metric MEASURES, not whether it discards (§4.3) | 2026-07-28 |
| 50 | SPEC-006 complete: remaining 17 sections executed after the thinking-disable fix | Zero truncations, zero errors in the batch — direct confirmation of decision log #47/#48's fix under real, additional load, not just the original 4-section probe. Full corpus: 273/273 sections analysed, 253 findings, $6.3792 of the re-aligned $8.50 budget spent, `validate` exits 0 | 2026-07-28 |
| 51 | SPEC-007 (The Grounded Brief) complete | Full run: 18 briefs, 204 kept sentences (restatement 70%, juxtaposition 19%, grouping 10%, sourced_causal/aggregation ~0.5% each), 19 dropped at the R4 type check, 20 dropped by the R5 verifier. Cost $0.5250, ~1.8x the $0.2905 dry-run estimate — every brief ignored the "3-6 sentences" instruction (8-15 kept per brief, always), which is the direct cause; reported as a real finding for a future prompt version, not silently tuned around (SPEC-007 v2.2, AC12's "do not tune to improve them") | 2026-07-30 |
| 52 | Packaging/verification gap: `dashboard` was never installed in the project's own `.venv` (`pip install -e .` had not been re-run since `pyproject.toml` added `dashboard*`; `streamlit`/`plotly` were new deps too) | `streamlit run` failed for the operator with `ModuleNotFoundError`. Every build-time check (`pytest`, `AppTest`) had passed anyway — all invoked via `python3 -c`/pytest from the repo root, both of which put the repo root on `sys.path` by accident, masking that `edgar` itself was never importable outside the repo root in the actual environment used to build this either. Fixed with `pip install -e .`; confirmed after with `.venv/bin/streamlit run`, absolute paths, launched from outside the repo. Binding on the future deployment spec (SPEC-008 "A real gap in AppTest verification"): its acceptance criteria must include a genuinely fresh clone + fresh install + `streamlit run` check, since Streamlit Cloud builds a fresh environment on every deploy and will reproduce this exact failure if the build step only installs third-party dependencies | 2026-07-30 |
| 53 | First real-browser dashboard review (D1/D2/D4/D5/D6/D9 addressed; D3/C2 flagged, not fixed): currency-escaping made structural (three `_safe_*` render wrappers are the only path three narrative-rendering functions have to Streamlit, enforced by a grep test on their function bodies, backed by `AppTest`-based rendering-path tests) rather than left as seven hand-applied call sites; `edgar/observations.py`'s statement templates switched from raw snake_case metric names and `:.4g` ratios to `mdef.display_name` and registry-formatted values; `dashboard/format.py` gained `format_metric_label`/`scale_for_axis`/`axis_ticksuffix` so a usd tile/chart carries a unit and a percent axis reads on the same scale as its own tile | Reviewed as insufficient on the first pass: unit tests on the escaping helper and on the observation formatter don't prove any call site actually invokes them — only a rendering-path test (AppTest) or a determinism-check rerun against the real corpus does. `compute-observations` was re-run against `data/app.db` after the D5 fix (546 rows updated under the current rule version; pre-existing rows under a superseded `rule_version` were left untouched, matching the determinism check's own existing exclusion and the dashboard's current-version-only read path) — `validate` confirmed clean after. D6 (LLM-authored prose formatting drift between briefs) is a different kind of gap from the other four: fixed at the prompt level (`filing_brief_v2.md`, `BRIEF_GENERATOR_PROMPT_VERSION` → `v2`, requiring the model to copy a cited item's own number formatting verbatim) rather than by regex-reformatting the model's freeform prose at render time, since reliably mapping a bare number back to the metric it came from needs the very structure this fix is trying to get the model to preserve — a wrong guess there trades a formatting bug for a correctness bug. Bumping the prompt version invalidates every filing's brief cache; the next `generate-briefs` run will re-call the LLM at real cost for all of them — deliberately not run this pass, pending approval (SPEC-008 v1.3) | 2026-07-30 |
| 54 | Second review pass (D3/D7/D8/D10): `null_metric_tile` no longer puts a NULL reason where `st.metric` would truncate it (short value + full reason in a caption instead); `MetricDef.flag_threshold` added (generic, `None` except `beneish_m_score = -1.78`) and surfaced as a caption; `metric_tile` gained `anchor_period_end` and bolds its caption when a tile's own period diverges from the page's anchor filing; `components.observation_ids_cited_in_brief` excludes an observation from "What changed?" when a kept brief sentence already restates it | D2's own fix (decision #53) was correct but initially looked broken to the operator because of a stale `st.cache_data` entry serving pre-fix results, not the join itself — `dashboard/data.py`'s entire query surface shares one cached primitive (`_cached_query`), so this same "correct on disk, stale in a long-running dev session" trap applies to editing SQL anywhere in that module, not just this one call site (noted for C4/C6, which will rework this data layer heavily; SPEC-008 "A stale-cache trap in dashboard/data.py"). D10 was confirmed live before fixing (4–6 of 8 top observations per company were already restated in the brief) and was explicitly checked against the instruction to stop rather than half-fix if a proper solution required deciding AC7/C2 first: it does not — the defect is set-membership (the same fact shown twice), not source-visibility, so neither section's own display rules changed and AC7 remains undecided, as instructed (SPEC-008 v1.4) | 2026-07-31 |
| 55 | D3's SECOND null path fixed (`metric_tile`, not just `null_metric_tile`): a row can exist with `value=None` and a `null_reason`, which `format_metric_value` folded into one string straight into `st.metric` -- truncated exactly like the already-fixed path, just reached differently. D11 fixed: `get_statement_line_values` now requires an EXACT `(period_start, period_end)` match for duration (non-instant) concepts, instead of `period_end` alone -- a 10-Q routinely tags a duration concept (revenue, cogs, ...) twice for the same end date (the three-month quarter and the nine-month year-to-date cumulative), and the old query had no principled way to prefer one; confirmed live, Micron's revenue at period_end=2026-05-28 had a 41,456 three-month row and a 78,959 nine-month row sharing a `filed_date`, and the tie-break landed on the wrong one. Every duration-based statement section and the period selector itself now state their duration in words (three-month / six-month / nine-month / FY), not just an end date. **AC7 amended, not dropped (C2):** sources now collapse behind a click; an unclickable, accurate source count ("1 source" / "3 sources" / "0 sources") renders on every brief sentence regardless, including the zero case, which gets no expander at all rather than an empty one. `[restatement]`/`[juxtaposition]`/`[grouping]` display prefixes dropped; `sentence_type` itself is unchanged in the database | The period selector (`metrics` table's own distinct `period_start`/`period_end` pairs) was already correct -- confirmed live, zero rows in the real corpus have more than one `period_start` per `(cik, name, period_end)` in `metrics`; the bug was entirely in the raw-`xbrl_facts` branch discarding `period_start` on the floor. On C2 -- the argument on both sides: AC7 existed because a source a reader has to click for is a source they stop checking, closing SPEC-007's own "fabricated emphasis" residual risk ("a model can say only true things and still mislead by what it foregrounds... a reader checks it in seconds" -- binding on the dashboard spec by SPEC-007's own text). Against literal AC7: D10's fix already showed most restatement sentences closely paraphrase their one source, so showing full source text adjacent, always, for every one of up to 12 sentences plus their sources costs real page length for a common case that rarely needs verification; the page is long enough that page length is the explicit open question before deciding C3's accordion. The count is the resolution, not a compromise of the original constraint: a reader still sees, with zero clicks, whether a sentence is grounded at all (this is MORE conspicuous for the critical zero-source case than before, since it no longer competes visually with verbose source captions on every other sentence) and can still open any specific sentence's sources in one click when the count or severity warrants it -- the constraint AC7 protects (nothing is fabricated-and-unverifiable-without-effort) survives; only the DEFAULT rendering of sources that are actually fine changes. Binding going forward, same as the count itself: if a future change ever removes the always-visible count, AC7's constraint is gone too, not merely re-styled | 2026-07-31 |
| 56 | **Supersedes #55's visible "N sources" line; does NOT reverse #55's AC7 amendment itself -- read as a sequence, not a contradiction.** #55's visible count line was itself found too heavy once seen at real scale (12 sentences per company, each with its own count line) -- the brief should read as continuous prose. Replaced with a small caret at the END of each sentence, inline, opening its sources in place (`components._safe_sentence_html`, the first use of `unsafe_allow_html=True` in this dashboard). The caret renders ONLY when a sentence has sources; a sentence with none gets no caret at all, so its ABSENCE next to every other, grounded sentence is what now carries the signal #55's visible zero-count used to carry directly. `st.expander` was rejected for this (a block element -- it would drop the caret onto its own line, the exact thing being fixed); `st.popover` was also rejected (a separate Streamlit widget, laid out in its own block in Streamlit's vertical stack -- it cannot share one text flow with a preceding `st.markdown` call short of `st.columns`, a fixed-width side-by-side layout, not inline text). A raw `<details>/<summary>` embedded in the SAME markdown string as the sentence text is the only mechanism that can share its flow, hence the first `unsafe_allow_html=True` here -- both D1's currency-escape and new HTML-tag-escaping (`format.escape_html`) are applied to every piece of model-/DB-generated text before it is embedded, inside one function, not left to a caller | D3/D11 also fixed this same pass: `metric_tile` had a second, unfixed null path (a row can exist with `value=None`, folded by `format_metric_value` into one string that `st.metric` truncates exactly like the already-fixed `null_metric_tile` path); `get_statement_line_values`'s exact-`(period_start, period_end)`-match fix (decision #55) over-corrected for instant (balance-sheet) concepts, which have no `period_start` at all -- fixed by branching on `CONCEPT_REGISTRY[...].instant` (already present in the code reviewed here, confirmed live it resolves correctly; the specific AMZN debt/cfo/sbc failure reported could not be reproduced against the current code via a fresh-process `AppTest` run of the real page path, and is treated as very likely the same stale-`st.cache_data`-in-a-long-running-session trap already documented at #54, since a bug that would explain the reported debt failure exactly (no instant/duration branch at all) would NOT explain the reported cfo/sbc failures, which match their exact tagged `period_start` under the current code). Separately, confirmed live across the real corpus: 10-Q cash-flow concepts are routinely tagged year-to-date only (Micron and NVIDIA's non-Q1 quarters mostly have NO three-month cash-flow facts tagged at all) -- `get_cash_flow_period` now falls back to the one unambiguous alternative duration when the quarterly one has no data, stating so explicitly ("not tagged separately for this quarter -- showing the year-to-date figure instead"), and refuses to guess (matching #55's own refusal) when more than one alternative candidate exists. `null_metric_tile`'s caption no longer repeats "Not available" (the metric's own value) as a prefix before the reason | 2026-07-31 |
| 57 | Caret layout fix (confirmed broken live): `<details>` is a CommonMark block-starting tag, so it split from the preceding sentence text into a sibling block regardless of its own `display:inline` -- fixed by wrapping the WHOLE construct in one outer `<span>` from the string's first character, with marker/outline suppression moved onto inline `style=` attributes rather than relying solely on a separate `<style>` tag. `get_statement_line_values` gained an optional per-line `(fallback_canonical, fallback_label)`, checked across the real corpus first (not just the one AMZN period reviewed): `ppe_net` is absent for 100% of AMZN's history and MU's history from FY2021-Q1 on, both tagging the combined `ppe_and_lease_net` concept instead. On fallback, the row's LABEL changes to `fallback_label` (not a parenthetical note on the primary label) -- a reader scanning the row must see a label that accurately names the number next to it, not one requiring a note to avoid being misled. `debt_noncurrent` has the identical shape for Micron (100% of the analyzed history) but was left alone, since no vetted canonical exists for it and choosing one is the same semantic judgment the standing "Micron debt-tag diagnosis" already deferred -- its NULL reason was still corrected to say the absence is structural, not period-specific, scoped to the company's own analyzed window (`metrics.period_end` range) after a naive "any row, ever" check produced a false negative against Micron's 2012-2013 tagging, years before the analyzed window begins | Reused `_resolve_ppe_net`'s existing, already-proven metrics-layer fallback pattern (`edgar/metrics.py`) rather than inventing a new one -- duplicated at the statement-line layer per this project's established "small duplicated helper over cross-layer import" convention (matching `_classify_duration`), not imported, since `dashboard` depends on `edgar`, never the reverse. Recorded as binding on C4 (SPEC-008 Forward-Looking Concern 4): a label-level fallback is correct for one period at a time, but C4's table has periods as columns -- Micron's `ppe_net` switching mid-history means a single row would silently mean two different things across its own columns; C4 needs the fallback marked per cell, or the row split, decided before C4 is built rather than discovered after | 2026-08-01 |
| 58 | **The #57 caret CSS fix was confirmed still broken live, on the SAME `<details>`/`<span>` mechanism, plus the app's theme flipped from dark to light -- raw HTML abandoned entirely, not patched a third time.** Root cause of both symptoms found to be the same: Streamlit sanitizes any `unsafe_allow_html=True` content client-side via a bundled DOMPurify (confirmed present and wired into the HTML-rendering path in the installed package's static JS -- a cheap, direct check, not a guess), which strips `style` attributes, so none of #56/#57's CSS (inline or in a `<style>` block) ever reached the browser; the injected `<style>` block is suspected as the theme-flip cause, though not proven beyond that DOMPurify sanitization is confirmed to run on exactly this content. Replaced with native Streamlit: `st.columns` (a narrow right-hand column) holding `st.popover("▾")` for the sources, sentence text in the wide left column -- no raw HTML, nothing for a sanitizer to touch, nothing that can reach the app's theme. `.streamlit/config.toml` added (`[theme] base = "dark"`) to make the theme an explicit, committed setting rather than an implicit client-side default that a rendering hiccup can silently reset, regardless of whether the `<style>` block was in fact the cause. `format.escape_html` and its call sites removed along with `unsafe_allow_html` -- only `escape_markdown_currency` is needed again, verified against real `$`-bearing brief sentences post-change. Separately, found live: MU's Financials page showed "Free cash flow: not tagged for this period" directly beneath its own two visible inputs (cash from operations, capital expenditure) -- looked like a bug sitting between them. It is present, at the metric's own computed duration (three-month); the Cash flow section was showing those two inputs at the D11-follow-up year-to-date fallback duration instead (decision #57), and `free_cash_flow` was never computed at that duration. The page does not derive on the fly (`get_statement_line_values` already reads `free_cash_flow` from `metrics`, never computes it from the two lines shown) -- the NULL reason for a `METRIC_REGISTRY` line inside a fallback-duration section now says so specifically, rather than the generic "not tagged for this period" | Confirming DOMPurify sanitization was cheap and direct (grep the installed package's bundled JS, not a browser session) -- worth doing before a third attempt at a mechanism already twice found insufficient, per the instruction not to spend long chasing it further once confirmed. `st.popover`/`st.columns` sidestep the entire class of "does this survive sanitization" question these two attempts kept running into, at the cost of the source count being a hover tooltip on the popover (`help=`) rather than inline text -- the same trade C2 v2 already made, now on a mechanism that actually reaches the browser | 2026-08-04 |
| 59 | v1.9's caret confirmed to double-render its own disclosure chevron next to the `"▾"` label -- dropped, `st.popover`'s own indicator is now the entire visible caret. **C5:** `components.sub_tab_bar(key, label, options)` added (`st.radio(horizontal=True)`, label-collapsed, component-owned `sub_tab__{key}` session state) -- confirmed live, not assumed, that a keyed `st.radio`'s selection survives a full rerun triggered by an unrelated widget, the same guarantee R4a already gives the sidebar company selector. Built and proven against ONE real consumer (Metrics/C6), not also wired into Financials this pass. **C6:** Metrics' `render()` had a hardcoded `group_order` list of seven category names sitting next to `_group_metrics()`, which already built its `groups` dict correctly ordered by walking `METRIC_REGISTRY` (`dict.setdefault` preserves first-appearance order) -- the hardcoded list was the actual defect (AC5: a metric added under an eighth category would be computed into `groups` but never rendered, silently). Removed; sub-tabs now iterate `groups` directly, both tab set and labels sourced from the registry | The AC5 demonstration was extended live, not just re-asserted: a throwaway metric was added to `METRIC_REGISTRY` under a brand-new category ("Demonstration"), confirmed via `AppTest` to produce an eighth tab with zero changes to `pages/metrics.py`, then removed. Doing this required also adding "Demonstration" to `config.py`'s own `_valid_groups` import-time check and removing it after -- a deliberate, LOUD registration point (raises `RuntimeError` on a typo) is not the same failure mode as the page-level list it was found alongside (which dropped an unrecognised category silently); both needed updating together for a genuinely new category, only one of them was the bug. Locked in permanently by `tests/test_dashboard_metrics_page.py` (registry-injection tests, not a full page render) rather than left as a one-time manual demonstration | 2026-08-04 |
| 60 | **C4 built: the Financials "Table" tab** (`data.get_income_statement_table`/`get_balance_sheet_table`/`get_cash_flow_table`, `components.statement_table`, C5's sub-tab bar for the three statement types). Two constraints decided BEFORE building, per instruction, both checked against real data rather than argued abstractly: (1) cash-flow duration mixing -- show the real filed fallback value (marked, not blanked), suppress growth on the transition instead of hiding the whole row; the REAL rule ("suppress when the two cells' durations differ") and its conservative proxy ("suppress if either cell used the duration fallback") are both named explicitly in code so the fallback itself is never mistaken for the reason growth is invalid. (2) PP&E split row, not a per-cell marker -- consistent with the single-period view's own standard; a row renders only if it has ≥1 populated cell in the range, checked symmetrically for both the primary AND fallback row | Constraint 1 (querying `get_cash_flow_period` blank instead) was checked empirically before being rejected: MU would lose 68% and NVDA 67% of quarterly cash-flow cells, and MU's real tagging sequence (3/6/9/12-month, resetting yearly) means nearly every consecutive quarterly transition is duration-mismatched regardless -- growth ends up suppressed almost everywhere either way, so blanking the VALUES too would have thrown away real, filed numbers for no additional safety. A real bug was caught mid-build, before shipping: the first cut of the split-row logic only guarded the FALLBACK row's inclusion against being empty, not the primary row's -- a line 100% covered by the fallback (a real, if currently uncommon, shape) would still have carried a permanently empty primary row on every table. Fixed and covered by a dedicated test before commit, not left for the next review to find | 2026-08-04 |
| 61 | `anthropic` was never declared in `pyproject.toml` at all -- found only because this was the first real Anthropic API call attempted since the dashboard build touched the project's packaging (`import anthropic` failed at call time, inside `edgar/llm.py`'s lazily-imported `_RealAnthropicClient`, after the dry-run estimate had already printed correctly, since estimation never imports the real client). Fixed by declaring it in `pyproject.toml` and reinstalling via `pip install -e .` -- the same discipline as decision #52's `dashboard*`/streamlit gap: fix the declared dependency, not just the local venv, so a fresh clone or a CI/deploy environment doesn't hit the identical failure | Every prior real API call this project has made (SPEC-006's original run, SPEC-007's) happened in a venv that had `anthropic` installed by some out-of-band step, never recorded in `pyproject.toml` -- this venv's own history (decision #52) already proved editable installs and ambient environment state can silently diverge from what's actually declared; this is the same class of gap, just for a different package, caught the same way (a real, unscoped invocation, not a dry-run or a test suite that never imports the real client) | 2026-08-04 |
| 62 | `compute-observations --ticker AMZN` (scoped to the newly-ingested filing) produced 4 `validate` determinism violations on MU's OWN stored observations, not AMZN's -- re-run unscoped, fixed, confirmed clean | `_apply_severity_overrides`'s cross-company same-day annotation (SPEC-005: "Also observed for another watchlist company around the same time...") requires the FULL batch to detect a same-day match across companies -- `compute_observations`'s own docstring already states this explicitly ("computing and writing company by company would miss a same-day match... its peer wouldn't exist in the table yet"), and scoping the very first live command of this ingest to `--ticker AMZN` (a natural instinct when only one company has new data) reproduced exactly the failure mode the docstring warns against. No code changed -- the existing unscoped `compute-observations` call was simply the correct one to run | 2026-08-04 |
| 63 | `generate-briefs`'s D6 prompt-version-bump deferral (decision #56/SPEC-008) reversed and executed, unscoped, deliberately bundling the new AMZN filing's first brief with the 18 stale v1 regenerations rather than scoping to just the new one -- ROADMAP-V2's generator-prompt items (including the still-unfixed "3 to 6 sentences" instruction) are parked until after V1, so there was no near-term prompt change bundling would pre-empt; splitting the runs would only grow a mixed v1/v2 corpus by one filing every pipeline run, for no offsetting benefit. Actual cost **$0.5963** against a **$0.3126** dry-run estimate -- reported as a real overrun, not rounded away | Root cause is the SAME pattern already on record at decision #51 (v1's own first generation run, $0.5250 actual vs $0.2905 estimated): the generator still does not reliably respect the "3 to 6 sentences" instruction -- this run kept 245 sentences across 19 briefs (~12.9/brief, after the generator itself dropped 33 and the verifier dropped 20 of 298 generated). The v2 prompt change (decision #56) only added the number-formatting instruction and never touched sentence count, so this recurring the same way was not a regression introduced this pass -- confirming it is a durable, ROADMAP-V2-scoped gap, not a fluke of one run | 2026-08-04 |
| 64 | **Investigated, not implemented, both recorded so neither gets re-proposed without this analysis (SPEC-006A).** Prompt caching on the section-analysis prompt: mechanically ZERO under the current template, not merely marginal -- read the actual template rather than assuming its shape: the six substitutions are interleaved with the static instructions (`%%NOTE_NAME%%` sits in the OPENING sentence; the bulk of the ~2,500 static tokens sit AFTER the fully-variable `%%NOTE_TEXT%%`), caching is strictly prefix-based, and the only genuinely stable prefix across calls within one filing is ~25-40 tokens against Claude Sonnet 5's verified 1,024-token minimum -- below the minimum, `cache_control` is silently ignored, no error, no partial benefit. Front-loading the static instructions would fix this but is a real prompt-content change (a v4), out of scope for "same prompts," and would invalidate the whole 297-section v3-cached corpus (~$6 to re-analyze) for a caching saving that could never recoup it at this project's scale -- worth doing only as part of a genuine CONTENT-driven v4 revision, never for caching alone. Batches API: real 50% saving, verified against current pricing (not the headline figure) and against the measured $0.0202/section rate, not assumed -- ≈$0.0101/section, ≈$1-2/year at this project's real volume. Not worth it at that scale, particularly having just measured the brief estimator 1.9x low on a different prompt in the same investigation -- a live reminder that this project's OWN estimates are not always trustworthy, which is exactly the property batching gives up: `RunGuard.check_before_call` gates every call on REAL, already-billed cost from `record_call`; a batch's per-request cost is invisible until the whole batch reaches `processing_status: "ended"` (verified: no incremental/streaming access while `in_progress`), so L3/L7 would have to move from a live circuit breaker to a one-time pre-submission estimate gate. **Revisit threshold: annual section-analysis spend reaching ≈$12/year** (≈5x today's ≈$2.40/year) -- the point at which batching's 50% saving (≈$6/year) reaches the same order of magnitude as this project's OWN demonstrated dominant cost (a single prompt-version re-analysis, ≈$6, decision #64's own other half) and becomes a real engineering trade-off rather than a rounding error. A number, not a feeling, and cheap to check: `spend`'s section_analysis total, annualised | The investigation's own more useful result, not either proposed change: at ≈12 filings/year, normal section-analysis operation is ≈$2.40/year while a single prompt-version bump is ≈$6 -- cache invalidation, not per-call price, is this project's dominant cost lever, and it has only ever been handled with the required discipline (state the invalidation cost alongside the rationale, before the change) once, for SPEC-007's v2 bump (decision #56), by habit rather than by rule. Made a standing requirement at ARCHITECTURE.md §2.6, binding on every future prompt-version change to any cached prompt, not something that happened to be done well one time | 2026-08-07 |
| 65 | C4 (the Financials statement table) rebuilt on `st.dataframe`, its first real-browser look since v1.10 built it (SPEC-008 v1.12) -- `st.columns`-per-row was found unreadable at real column counts: MU's 28 quarterly periods wrapped a date header into a vertical stack of digits and split a value across four lines, because `st.columns` was never a table widget, just N independently-wrapping blocks. Three fixes, all native Streamlit, no injected CSS/HTML (same discipline as the caret lesson): (1) `column_config.TextColumn(pinned=True)` freezes the label column; periods stay chronological left-to-right since growth reads left-to-right against the prior column. No scroll-to-position API exists in the installed Streamlit version (checked the actual signature, not assumed) -- solved by not needing to scroll in the common case: defaults to the newest 8 quarterly/5 annual periods, full history opt-in via a checkbox. Growth became a separate grey `↳ Growth %` row beneath its line item (a grid cell has no second line the way `st.columns` + `st.caption` did); the filed-vs-derived distinction (constraint 1) now carries via the row's own label text plus a Styler colour, deliberately NOT font-style italic -- Streamlit's own docs commit only to "colors and font weights" surviving Styler into the grid, not font-style, so italic was not trusted for a load-bearing distinction. (2) Zebra striping by row-PAIR (not by data value) plus a border-color theme tweak, for structure over decoration. (3) A blank cell's cause, restored: the single-period Summary tab already distinguished three reasons a line has no value (never tagged, split-row complement, derived-metric-absent-at-duration) and the multi-period table had collapsed all three into one unmarked "—" -- classified per cell and marked distinctly (—, — °, — ‡) with a footnote key, never auto-hiding a real disclosure gap (AMZN's empty gross profit row is itself a fact this project exists to surface) | Verified against the real corpus, not just synthetic fixtures, before being called done: AppTest against `data/app.db` across all three companies × both bases × all three statements (36 combinations) raised zero exceptions, and the three blank-cause categories were confirmed against real data specifically -- AMZN gross profit classifies `gap`, MU's PP&E split rows classify `split`, MU's quarterly free cash flow (where the cash-flow duration fallback fires but `free_cash_flow` was only ever computed at the true three-month duration) classifies `duration`. The dev server was left running for the user's own visual pass; this agent has no browser-interaction tool available this session (the Claude in Chrome extension was declined earlier), so the pixel-level check is explicitly the user's, not claimed here | 2026-08-07 |
| 66 | Decision #65's `st.column_config.TextColumn(..., alignment="right")` broke immediately for the operator: `alignment` does not exist on `TextColumn` in Streamlit 1.51.0 -- confirmed via `inspect.signature`, not the docs, which do not version-scope the parameter list at all. Root cause was an environment gap, the same SHAPE as decision #52's `dashboard`/`streamlit` packaging gap, not the same mechanism: a bare `streamlit` on the operator's PATH resolved to an unrelated Anaconda-global install (1.51.0) rather than this project's own `.venv` (1.60.0, where `alignment` exists and the code had been verified). Every verification this agent ran before calling C4 done used `.venv/bin/streamlit` exclusively -- clean by every test available to it, and still wrong for the environment that actually mattered, because that environment was never checked. Fixed two ways: (1) the `alignment` call removed rather than the version floor raised -- pyproject.toml's declared minimum is `>=1.51`, so a reader running exactly that minimum is a legitimate, supported environment, not a stale one to be argued around; text-only right-alignment via a Pandas Styler was considered (the user offered it explicitly) and deliberately NOT attempted -- Streamlit's own docs commit only to "colors and font weights" surviving a Styler into the interactive grid, the same uncertainty already used to justify not using font-style italic for the growth row (decision #65), so gambling on `text-align` for a feature nobody asked for was declined on the same grounds. (2) `components.environment_caption()`, called once from `app.py`, always shows the actually-running Streamlit version in the sidebar (a warning, not just a caption, below the declared floor) -- so "which Streamlit is this" is answerable by looking at the running app, not by an agent re-deriving it from whichever interpreter happened to build the feature | The verification gap, stated generally: testing exclusively through `.venv/bin/streamlit` proves the code works in ONE environment, never proves it works in THE operator's environment, and the two are only guaranteed identical if something actively keeps them so -- nothing here did. `README.md`'s `source .venv/bin/activate` step is necessary but not sufficient (a shell's own init script re-prepending a global interpreter after activation reproduces exactly this gap); `environment_caption()` does not prevent the gap from recurring, it makes recurrence immediately visible instead of silently producing a failure this agent cannot reproduce from its own `.venv`. Binding on this agent's own verification habit, not just on this codebase: a library call whose availability isn't already exercised by an EXISTING test should be checked against the project's declared floor version, not against whatever happens to be newest in the environment used to write it | 2026-08-08 |
| 67 | Cash-flow discrete-quarter derivation (decision #65's "‡ not computed at this duration" gap) hardened with four approved additions (SPEC-008 C4, 2026-08-08). (1) A new `validate` category (33/33a): the four stored discrete quarters must sum to a FRESHLY re-read filed FY figure -- an algebraic identity that only fails to tie when an input is stale or inconsistent, catching restatement drift a static `inputs_used` audit trail could only explain after the fact, not before display. Zero violations against the real corpus. (2) `plausible_range` (already on `MetricDef`, previously unused at compute time) checked INSIDE `compute_discrete_quarter_metrics`, not left to a post-hoc validate pass -- `capex_discrete`/`sbc_discrete`/`dep_amort_discrete` get `(0.0, inf)` (a sign check: these are cash outflows/expenses that cannot legitimately go negative; a restated endpoint that makes the subtraction go negative becomes a null with a reason, never a displayed figure); `cfo_discrete`/`free_cash_flow_discrete` stay unbounded since a negative operating quarter is a real, legitimate outcome, not an error. (3) Reused the EXISTING PPE `fallback_canonical` mechanism rather than inventing a third resolution pattern -- `fallback_label == label` (inferred from the caller's own data, no new flag) distinguishes PPE's two-row split (genuinely different concept) from cash flow's one-row merge (same concept, sourced two ways); this let the OLD `is_duration_fallback`/†-as-wrong-duration mechanism be deleted outright, not left dormant, since nothing can set it True anymore. (4) Display collapsed from two rows to one, per cell: filed where tagged (AMZN), derived where not (MU, NVDA), marked with † (now meaning "derived by subtraction", not "wrong duration") -- the filed YTD figure stays reachable via the unchanged Summary tab / `get_cash_flow_period`. Growth% now computes for these cells since every cell is genuinely the true discrete quarter | Confirmed against the real corpus, not assumed: MU's FCF-blank rate fell from 68% to 5%, NVDA's from 67% to 25%; MU's cfo growth%, previously 0% populated (every cell suppressed by the old duration-fallback proxy), reached 92%; NVDA reached 83%. AMZN, which already tagged discretely, picked up a bonus it was never explicitly promised: its own Q4 -- never discretely tagged in the 10-K either -- now derives too, the same mechanism catching a gap in a company assumed to be a non-problem | 2026-08-08 |
| 68 | D12 (SPEC-008-review-2-2026-08-04.md): Q4 missing from the income statement for every company, every year -- confirmed in `xbrl_facts` before touching code, not inferred: at every fiscal Q4, only the 10-K's 365-day cumulative fact exists, never a discrete 3-month one, for any company. Structurally identical to decision #67's cash-flow gap (a real filed number for the wrong window, versus none at all) but with a different root cause: cash flow's MU/NVDA problem was a TAGGING CHOICE (cumulative vs discrete); Q4's is STRUCTURAL -- there is no "Q4 10-Q," the fourth quarter is only ever reported via the FY 10-K. Same fix, generalized rather than reinvented: `_DISCRETE_QUARTER_CANONICALS` extended from cash flow's 4 to all 10 income-statement lines (`revenue` through `net_income`); `_fiscal_year_start` (previously hardcoded to `cfo` as a "representative concept", justified by cash flow's one-duration-per-filing property) now resolves each canonical's OWN Q1 anchor instead -- a latent fragility even in the cash-flow-only version, now removed rather than carried forward, since nothing guarantees a company tags `revenue`'s Q1 the same day as `cfo`'s. `INCOME_STATEMENT_LINES` gets the identical `fallback_label == label` merge treatment `CASH_FLOW_LINES` already has -- zero new dashboard mechanism. A new METRIC_REGISTRY group, `Income Statement` (`_valid_groups` extended), since none of the seven existing groups fit a raw income-statement dollar level -- confirmed live that this needs no dashboard code: `_group_metrics`'s registry-derived ordering (proven once already at AC5) placed the new tab correctly with zero page changes. Balance sheet deliberately untouched, per instruction and per the same reasoning already on record: instants have no duration to mismatch. **Side effect found, not fixed, flagged for the user's call**: because `revenue_discrete` etc. are now stored at the TRUE discrete (period_start, period_end) for Q4, `get_statement_period_ends` (used by the Summary tab's period dropdown, not the table) now returns TWO distinct entries for a fiscal year-end date -- the pre-existing annual one and the new discrete-quarterly one -- both correctly labelled but visually duplicated in that one dropdown. Confirmed this already existed silently for cash flow since decision #65/#67, not newly introduced by D12; out of scope for "D12 only, don't touch anything else" | AMZN's derived Q4 2024 revenue reproduces the review's own example exactly: 637,959,000,000 FY − 450,167,000,000 nine-month YTD = 187,792,000,000, both real filed figures. Revenue blanks fell from 5/25 (AMZN), 6/24 (NVDA), 5/37 (MU) to 0, 1, 0 against the real corpus; `validate` category 33 (now covering all 15 discrete metrics, cash flow and income statement together, since the mechanism is shared) reports zero sum-back violations for either statement | 2026-08-08 |
| 69 | `LLM_BUDGET_USD` re-aligned $8.50 → $13.50 (SPEC-009 P1) | Operator topped up the real Console balance by $5.00. 13.50 = the prior 8.50 cap + the 5.00 added, which independently checks out as recorded lifetime spend ($7.6886 at re-alignment) + real remaining balance ($5.8114) -- the same two-numbers-should-sum-to-this-cap verification #46 used, not a fresh assumption. Same standing caveat as #41/#46: only as good as the last time it was checked against the actual Console balance, not exact indefinitely -- re-check and re-align again the next time real money moves against the account outside this ledger's view | 2026-08-24 |
