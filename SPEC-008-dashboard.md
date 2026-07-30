# SPEC-008 — Dashboard

**Version:** 1.1
**For:** Claude Code
**Depends on:** SPEC-007 (complete, commit `18ed734`)
**Reference:** `ARCHITECTURE.md`; SPEC-007 Residual Risk
**Estimated effort:** 8–12 hours
**Estimated API cost:** $0.00 — this spec makes no API calls at all

**Changelog**
- v1.1 — Pre-implementation review (2026-07-30), against real data and the installed
  Streamlit version (1.51.0). Seven changes, none yet built:
  1. **Overview anchors to the latest 10-K or 10-Q, never an 8-K** (R3). Confirmed live:
     NVIDIA's most recent 10-Q and an 8-K share the same filing date (2026-05-20); the 8-K
     has zero sections, metrics, findings, or brief. A page cannot be "about" a filing with
     nothing behind it. A more recent or same-day 8-K is noted, with a link to the Filings
     page, never silently dropped.
  2. **Every displayed value states its own period, not just the page** (new principle,
     below). The page states its anchor filing's date; each tile states the period of the
     specific number it shows. Confirmed live: `cash_conversion_cycle`, `beneish_m_score`,
     and `inventory_growth_less_revenue_growth` are all annual-basis metrics, but all three
     watchlist companies' current anchor filing is a 10-Q — their "latest" value is the last
     completed fiscal year, genuinely a quarter or more behind the header's date. A single
     page-level as-of date would misstate this.
  3. **Any section that can legitimately be empty says so explicitly, in words** (R8).
     "No red-flag findings in this filing" is information; a blank space reads as a bug.
     Confirmed live: zero of the two `red_flag` findings in the whole corpus fall on any of
     the three companies' current latest filing — this will be the common case, not an edge
     case, and every one of the three companies would show it today.
  4. **Micron's `roic`, `capex_to_revenue`, `capex_to_depreciation`, and `free_cash_flow` are
     all `None` for its own current latest quarter** (period_end 2026-05-28) — not stale
     history, the live value. Handled at display time per change 3. Diagnosed, not fixed,
     below (§ "Micron debt-tag diagnosis").
  5. **`app.py` is built on `st.navigation`/`st.Page`** (confirmed available, installed
     1.51.0), not the auto-discovered `pages/`-directory convention the original file tree
     implied by naming. Under the auto-discovered convention, `app.py`'s own script body
     runs only when `app.py` itself is the active page — a sidebar meant to persist "on
     every page" would need every one of the four page files to explicitly re-invoke a
     shared render call, with nothing structural stopping a future page from forgetting it.
     Under `st.navigation`/`st.Page`, the entry script genuinely re-executes on every
     navigation, so the sidebar and the auth gate are instantiated exactly once, in `app.py`,
     before dispatching to whichever page was selected. **Prefer the structure where the
     mistake is impossible over the one where it is merely discouraged.**
  6. **`components.py` owns every `st.session_state` key, including chart click-state for
     evidence panels — pages never touch `st.session_state` directly.** Same root cause as
     change 5 (Streamlit's rerun model has no cross-rerun memory except session_state), one
     more instance of it: a click-to-open evidence panel must remember which point was
     clicked across the rerun the click itself triggers. A new test
     (`test_no_session_state_outside_components`) asserts no page file references
     `st.session_state`, alongside the existing no-SQL check.
  7. **`streamlit` and `plotly` added to `pyproject.toml`** — installed in the environment
     but never declared as project dependencies until now.

---

## Objective

A Streamlit application that presents everything already built: statements, 45 metrics,
observations, 253 quote-verified findings, and 18 grounded briefs.

Two audiences, one app. An analyst wants the numbers. Anyone else wants the read. Both get
what they came for, and every claim on every page can be traced to a filing in two clicks.

---

## Three hard rules

**1. The dashboard never writes to the database.** Read-only, always. No exceptions. This
keeps it deployable anywhere, makes it impossible for a display bug to corrupt data, and
means it can be killed and restarted at any moment without consequence.

**2. No API calls.** The assistant is SPEC-009. Nothing here spends money.

**3. Generated text shows its evidence unconditionally. Computed values show evidence on
demand.**

Brief sentences and findings are model output, so their sources sit **adjacent to them,
never behind a click** — binding, from SPEC-007's Residual Risk. Metrics are deterministic
arithmetic, so their derivation opens on click. Different provenance, different disclosure.

---

## Architecture — three layers, no shortcuts

```
dashboard/
  app.py           entry point, auth, navigation
  data.py          ALL database access. Cached. Returns typed rows.
  components.py    ALL display logic. metric_card, metric_chart, evidence_panel, ...
  format.py        ALL value formatting, driven by the metric registry
  pages/
    overview.py    composition only
    financials.py  composition only
    metrics.py     composition only
    filings.py     composition only
```

**Rules, enforced by review:**

- **Pages contain no SQL.** They call `data.py`.
- **Pages contain no formatting.** They call `format.py`.
- **Pages contain no display logic.** They compose components.
- **Pages never touch `st.session_state`.** `components.py` owns every key (v1.1).

A page should read as: fetch, arrange, done. If a page is longer than about 80 lines,
something belongs in a component.

The reason is concrete: when you later want every number on the site to open an evidence
panel, that must be one change in `components.py`, not two hundred edits across five pages.

**`app.py` is built on `st.navigation`/`st.Page` (v1.1), not the classic auto-discovered
`pages/`-directory convention.** Under auto-discovery, `app.py`'s own script body executes
only when `app.py` is itself the active page — code written there (the sidebar selector,
the auth gate) would not appear on `pages/financials.py`, `pages/metrics.py`, or
`pages/filings.py` unless each of those files separately re-invokes it. Nothing in that
convention stops a future page from forgetting to; the control just silently would not be
there. `st.navigation`/`st.Page` (confirmed available, installed 1.51.0) genuinely
re-executes the entry script on every navigation, so `app.py` renders the sidebar and the
auth gate exactly once, before dispatching to whichever page was selected, and every page
gets them by construction. The existing `pages/*.py` file layout is unchanged — `st.Page`
accepts a file path or a plain callable, so this is a choice of how `app.py` wires the pages
together, not a restructure of where their code lives. **Prefer the structure where the
mistake is impossible over the one where it is merely discouraged.**

---

## Requirements

### R1 — Metric registry gains display metadata

Extend `MetricDef` in `config.py`:

- `display_name` — "Operating margin", not `operating_margin`
- `unit` — `percent` | `usd` | `usd_per_share` | `days` | `ratio` | `times`
- `precision` — decimal places

**All `usd` values render in millions, everywhere, without exception.** Amazon's revenue
shows as 637,960 rather than 638.0bn, and a $1.3bn impairment shows as 1,300. Consistency
matters more than per-item readability: mixing millions and billions across a page makes
comparison quietly harder, and millions is also the unit these filers use in their own
statements, so the dashboard matches its source. Column headers state `$m` explicitly.

`usd_per_share` is the exception and stays in dollars — a $0.10 EPS impact is not 0.0000001
of anything useful.
- `higher_is_better` — `True` | `False` | `None` where it isn't meaningful
- `group` — Growth | Margins | Returns | Capital & Cash | Working Capital | Solvency | Quality
- `description` — one sentence, plain English

**The dashboard renders itself from this.** Adding metric #46 makes it appear, correctly
formatted, in the right group, with a working evidence panel, and no page code changes.

`format.py` reads `unit` and `precision` and is the only place that decides how a number
looks. Inconsistent formatting is the fastest way to make a dashboard look amateur.

### R2 — Authentication

Simple password gate via `st.secrets`, checked before any page renders.

Not security theatre with a purpose — it's the L8 guardrail from SPEC-006A arriving early,
so that when SPEC-009 adds a metered assistant the gate already exists.

### R3 — Overview page

The first thing anyone sees. Company selector at the top.

**Header** — company, ticker, **the most recent 10-K or 10-Q** (never an 8-K), form type and
date, link to SEC. **A more recent or same-day 8-K is noted separately, with a link to the
Filings page — never used as the anchor filing and never silently dropped.** (v1.1: an 8-K
has no sections, metrics, findings, or brief — a page organised around observations,
metrics, findings, and a brief cannot be "about" a filing that has none of those. Confirmed
live: NVIDIA's most recent 10-Q and an 8-K share a filing date, 2026-05-20.)

**The brief** — top 6 sentences by the maximum severity of their cited sources, each with
its sources rendered immediately beneath it. `Show all N sentences` expands the rest.

Six, not eleven: the generator produced 8–15 sentences per filing against an instruction of
3–6 (SPEC-007 v2.2). Capping at display time costs nothing and loses nothing.

**Four questions**, each answered from data — not four tabs labelled after database tables:

| Question | Answered by |
|---|---|
| **What changed?** | Top observations for the latest filing, severity-ranked, max 2 per rule |
| **Is the business getting better or worse?** | Sparklines: revenue growth, gross margin, operating margin, ROIC |
| **Where is the cash going?** | Capex/revenue, capex/depreciation, free cash flow, cash conversion cycle |
| **Should I be suspicious?** | Beneish M-score against its threshold, depreciation rate, inventory-minus-revenue growth, and any `red_flag` findings |

A fifth question — *can I trust management?* — needs guidance tracking, which does not
exist yet. **Omit it rather than showing an empty promise.** Note it in the page as planned,
or leave it out entirely; do not render a placeholder that implies data.

**v1.1 — every tile states its own period.** `cash_conversion_cycle`, `beneish_m_score`, and
`inventory_growth_less_revenue_growth` are annual-basis metrics; the anchor filing for all
three watchlist companies right now is a 10-Q. Their "latest" value is the last completed
fiscal year — genuinely older than the header's date, not a formatting nuance. Each of these
tiles states the fiscal period its own number covers, distinct from the page's header date.
See "A page-level as-of date is not enough," below.

**v1.1 — empty is a fact, and it is said, not shown by absence.** Zero of the corpus's two
`red_flag` findings fall on any of the three companies' current latest filing — this is the
common case for this tile, not a rare edge case. "No red-flag findings in this filing" is
the correct, informative rendering; a blank space where that tile would be reads as broken.
The same principle applies to any tile whose underlying data is legitimately absent — see
R8.

### R4 — Financials page

Two views, both from data already held:

**Summary** — income statement, balance sheet and cash flow built from `xbrl_facts` using
the canonical concepts. Curated lines, not full GAAP presentation. Comparable across all
three companies because it uses the same concepts for each.

**As filed** — the `Statements` category section text, exactly as SEC rendered it.

Period selector. Both views clearly labelled so nobody mistakes one for the other.

### R4a — Company selection: one control, everywhere

**A multi-select in the sidebar, defaulting to all three companies, persisting across
pages.** One click switches between Micron alone, NVIDIA against Micron, or all three.

- Pages that show many companies at once (Metrics) chart every selected company.
- Pages that are inherently single-company (Overview, Financials, Filings) show tabs across
  the current selection.

This is the whole comparison mechanism. There is no separate mode to enter and no second
selector to keep in sync.

### R5 — Metrics page

All 45, grouped by `MetricDef.group`, charted for every company currently selected.

**With one company selected this is the individual view; with two or three it is the
comparison.** Same page, same charts, no mode switch — which is why there is no separate
Compare page in this spec.

- One chart per metric over time, annual and quarterly toggle.
- **Scale toggle for `usd` metrics: absolute or indexed to 100 at the first period.**
  Absolute is honest about size but makes Micron nearly invisible beside Amazon; indexed
  shows relative change and makes trajectories comparable. Both are legitimate and they
  answer different questions, so offer both rather than choosing. Default absolute.
  Ratio and percent metrics need neither — they share an axis naturally.
- **Click a point → evidence panel**: the formula string, the input values from
  `inputs_json`, the source filing, and a link to it on SEC.
- Table view toggle, CSV download.
- **NULL is never rendered as zero or blank.** Show "not available" with the recorded
  reason. A missing input and a zero are different facts about the world, and the
  dashboard must not blur what the metrics layer was careful to distinguish.

### R6 — Filings page

Every filing in the database — all 171, not just the analysed window.

Selecting one shows: metadata, its brief, its observations, its findings **each with its
verbatim quote displayed**, its section list, and a link to the filing on SEC.

Section text is readable in place. This is the audit trail made navigable — the page that
lets someone check any claim the rest of the site makes.

### R7 — No separate Compare page

Deliberately removed. R4a's sidebar multi-select makes the Metrics page the comparison
view whenever more than one company is selected, so a dedicated page would duplicate it
and create a second place for the same logic to drift.

### R8 — Display discipline

- **No severity colour coding.** Severity is conveyed by ordering and by an explicit text
  label, never by colour. Colour directs the eye before the brain reads anything, and
  getting that wrong misdirects attention in a tool whose entire purpose is directing
  attention correctly. Revisit later if the uncoloured version proves hard to scan — but
  start without it. This is a deliberate choice, not an omission.
- **Trends by default, tables on demand.** A line moving over time says more than sixty
  numbers in a grid.
- **Every page states its data's as-of date**, so nobody reads a stale deployment as current.
  **(v1.1) A page-level date is not, by itself, enough whenever the page mixes periods.**
  The page states its anchor filing's date; that is a fact about the deployment and the
  filing selected. It is a DIFFERENT fact from the period any individual number covers.
  Annual-basis metrics genuinely lag a quarterly anchor filing — `beneish_m_score` shown
  on Amazon's Q1 FY2026 Overview is the FY2025 value, a real fact about what is known, not
  a staleness bug. **Every displayed value states its own period, adjacent to the value**,
  never inferred from the page's header alone.
- **(v1.1) Any section that can legitimately be empty says so explicitly, in words.** A
  metric that is `NULL`, an observation list with nothing to show, a findings tile with no
  `red_flag` this filing — every one of these is a true, informative state, and every one
  renders text that says so plainly (e.g. "No red-flag findings in this filing," "Not
  available for FY2025 — see reason"). A blank space where a legitimately-empty section
  would be is indistinguishable from a bug; the fix is never to leave it blank.

---

## Constraints

- Streamlit. No other web framework.
- Charts: Plotly or Streamlit native. Nothing else.
- No new database columns except `MetricDef` display metadata in config.
- No writes to `app.db`, ever.
- No API calls.
- Read-heavy queries cached with `st.cache_data`; the cache must be invalidated when the
  database file's modification time changes.
- Type hints on all public functions in `data.py`, `components.py`, `format.py`.

---

## Acceptance Criteria

1. `streamlit run dashboard/app.py` starts the app locally.
2. The password gate blocks all pages until satisfied.
3. All four pages render for all three companies with no errors.
3a. The sidebar multi-select works for one, two and three companies, persists across pages,
    and Metrics charts exactly the selected set. Demonstrate NVIDIA-and-Micron only.
3b. All USD values render in millions with `$m` labelled; per-share values stay in dollars.
3c. No severity colour coding anywhere. Severity appears as text and ordering only.
4. **No page contains SQL, formatting logic, or display logic.** Verified by inspection and
   stated in the report.
5. Adding a metric to the registry makes it appear on the Metrics page, correctly formatted
   and grouped, with **no changes to any page file**. Demonstrate by adding a throwaway
   metric, showing it appears, then removing it.
6. Every metric chart point opens an evidence panel showing formula, inputs and source
   filing.
7. Every brief sentence displays its sources adjacent, not behind a click.
8. Every finding displays its verbatim quote.
9. A NULL metric renders as "not available" with its reason — never as 0, blank, or a gap
   in a chart that implies zero.
10. The Overview shows exactly 6 brief sentences by default, with the rest expandable.
11. `pytest` still passes; the dashboard does not break any existing test.
12. **Demonstration**: screenshots of the Overview page for Amazon and for Micron, and the
    Metrics page with one evidence panel open.

---

## Edge Cases

| Case | Required behaviour |
|---|---|
| Company has no brief for its latest filing | Show the filing and its observations; say plainly that no brief exists. |
| Metric is NULL for the selected period | "Not available", with reason. Never zero. |
| Metric is NULL for an entire company (e.g. Amazon's R&D) | Show the metric with a persistent explanation, not an empty chart. |
| Filing has no sections (8-Ks) | Show metadata and findings; do not imply sections are missing in error. |
| Quarterly series has a Q4 gap | Render the gap as a gap. **Do not interpolate.** |
| Database is missing or empty | Fail with a clear message naming the expected path. Do not render an empty shell. |
| A finding's quote is very long | Truncate for display with expansion, but never alter the text itself. |
| (v1.1) A company's most recent filing is an 8-K | Anchor the page to that company's latest 10-K/10-Q instead; note the 8-K with a link to the Filings page. |
| (v1.1) An annual-basis metric's latest value is older than the page's anchor filing | Show the value with its own period stated adjacent to it — never implied to be as of the header's date. |
| (v1.1) No `red_flag` findings on the current filing (the common case, not rare) | State it explicitly: "No red-flag findings in this filing." Never an empty gap. |
| (v1.1) Micron's `roic`/`capex_to_revenue`/`capex_to_depreciation`/`free_cash_flow` are `None` for its current latest quarter | "Not available", with reason, same as any other NULL metric (see "Micron debt-tag diagnosis" below for why — not fixed in this build). |

---

## Micron debt-tag diagnosis (v1.1, 2026-07-30 — diagnosed, not fixed)

**Root cause confirmed live** (fetched Micron's real companyfacts; not speculation): starting
with the FY2026 Q2 10-Q (period_end 2026-02-26, filed 2026-03-19), **Micron replaced its
`LongTermDebt` tag with a new combined concept, `LongTermDebtAndCapitalLeaseObligations`.**
`LongTermDebt` reported $8.844B as of 2025-11-27 (FY2026 Q1) and is absent from every filing
since — not zero, not omitted, simply retagged. The new tag reports $9.557B (2026-02-26) and
$5.140B (2026-05-28); a matching new `DebtAndCapitalLeaseObligations` tag (the combined
current+noncurrent total) confirms the arithmetic: $10.142B − $9.557B = $585M, exactly
`DebtCurrent` for that period.

**This is not a simple alias addition.** `total_debt`'s alias list (`config.py`) currently
holds `LongTermDebt` and `DebtLongtermAndShorttermCombinedAmount` — both BORROWINGS-only
concepts, deliberately excluding finance leases, which `_resolve_total_debt` (`metrics.py`)
adds separately and additively (`borrowings + finance_lease_liability_noncurrent +
finance_lease_liability_current`). Micron's new tag, per its own name, **already includes
the finance-lease component** — adding it to the existing alias list unchanged would double
count finance lease liabilities for every period reported under the new tag. This is exactly
the alias-purity violation ARCHITECTURE.md §2.1 already warns against generally (an alias
must be the same fact under a different name, never a broader quantity) — `total_debt`'s
current aliases and `LongTermDebtAndCapitalLeaseObligations` are not the same fact.

**A real fix needs, at minimum:** either a new canonical input (mirroring how
`ppe_and_lease_net` was split out from `ppe_net` for the same reason, §2.1) with its own
resolution branch that does NOT add finance leases again when the combined tag is present,
or a conditional inside `_resolve_total_debt` that detects which shape of tag resolved and
adds finance leases only when the resolved tag does not already include them. Either is more
than a config-only change. **Not implemented here** — this section exists so the operator
can decide the shape of the fix with the real facts in hand, per the standing instruction
not to fix this during the dashboard build.

---

## Testing Requirements

Streamlit UI is awkward to unit-test. Test the layers beneath it:

- `test_format_percent_precision`, `test_format_usd_scaling`, `test_format_days`
- `test_format_null_returns_not_available_never_zero`
- `test_data_layer_returns_expected_shape` for each `data.py` function
- `test_brief_top_six_ranks_by_source_severity`
- `test_metric_registry_display_fields_present_for_every_metric` — a metric without display
  metadata should fail loudly rather than render as a raw identifier
- `test_no_sql_outside_data_module` — grep-style check across `pages/` and `components.py`
- `test_no_session_state_outside_components` (v1.1) — grep-style check that no file under
  `pages/` references `st.session_state`; only `components.py` may

---

## Likely Files Affected

```
edgar/config.py           (MetricDef display metadata)
dashboard/app.py
dashboard/data.py
dashboard/components.py
dashboard/format.py
dashboard/pages/*.py
tests/test_dashboard_data.py
tests/test_format.py
pyproject.toml            (streamlit, plotly)
.streamlit/secrets.toml   (gitignored)
```

---

## Forward-Looking Concerns

1. **SPEC-009 adds the assistant** as a sixth page. It reuses SPEC-007's engine with a
   variable question, and the auth gate built here.
2. **Deployment** comes with the GitHub Actions spec — Streamlit Community Cloud serves
   from the repo, so a pipeline commit updates the dashboard automatically.
3. **Do not let the dashboard become the place logic lives.** If a page needs a number that
   doesn't exist, the fix is a new metric or observation, not a calculation in the display
   layer. That boundary is what keeps everything auditable.

---

## Notes for the Implementer

- Build `format.py` and `data.py` first, with tests. Pages last. Pages written before the
  layers beneath them always end up containing logic that belongs lower down.
- The Overview page is the one that gets screenshotted. Spend the extra hour there.
- Resist adding computation. Every number on this site should already exist in the
  database, computed by a layer that was tested.
- Report any discrepancy between this spec and observed behaviour rather than working
  around it silently. `ARCHITECTURE.md` must then be corrected.
