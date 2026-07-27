# Equity Research Platform — Architecture

**Version:** 2.3
**Date:** 2026-07-27
**Owner:** Zakaria
**Status:** Approved for V1 implementation

**Changelog**
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
| LLM | Anthropic API, section-level calls | Budget ≈ $20 total |

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

Eight tables.

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
    input_hash     TEXT NOT NULL,        -- sha256(section text + rendered prompt)
    output_json    TEXT NOT NULL,
    input_tokens   INTEGER,
    output_tokens  INTEGER,
    cost_usd       REAL,
    created_at     TEXT NOT NULL,
    UNIQUE(input_hash)                   -- this IS the response cache
);

CREATE TABLE findings (
    id           INTEGER PRIMARY KEY,
    analysis_id  INTEGER NOT NULL REFERENCES analyses(id),
    accession_no TEXT NOT NULL REFERENCES filings(accession_no),
    category     TEXT NOT NULL,          -- red_flag|guidance|management_language|note_item
    severity     TEXT,                   -- high|medium|low
    headline     TEXT NOT NULL,
    detail       TEXT,
    quote        TEXT,                   -- verbatim text from the filing
    created_at   TEXT NOT NULL
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

**`findings.quote`** — every AI claim must be anchored to verbatim filing text. The strongest
available control against hallucination, and structural rather than a prompt instruction.
A finding without a quote is a bug.

**`filings.status`** — makes the pipeline resumable and idempotent.

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
