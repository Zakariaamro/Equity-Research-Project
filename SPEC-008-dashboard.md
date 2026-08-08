# SPEC-008 — Dashboard

**Version:** 1.15
**For:** Claude Code
**Depends on:** SPEC-007 (complete, commit `18ed734`)
**Reference:** `ARCHITECTURE.md`; SPEC-007 Residual Risk
**Estimated effort:** 8–12 hours
**Estimated API cost:** $0.00 — this spec makes no API calls at all (the dashboard remains
read-only; the real spend recorded this pass is SPEC-006/007 pipeline commands run to
ingest a genuinely new filing and regenerate the D6-invalidated brief corpus, not the
dashboard's own code)

**Changelog**
- v1.15 — D12, second review (SPEC-008-review-2-2026-08-04.md; decision log #68): Q4 was
  missing from the income statement for every company, every year -- no discrete Q4 10-Q
  exists structurally, only the FY 10-K's annual cumulative figure. The discrete-quarter
  mechanism built for cash flow (v1.14) generalized to all 10 income-statement lines, same
  fiscal-boundary logic, same fail-closed rule, same derived-cell marking, same sum-back
  check. Confirmed against real data: AMZN's derived Q4 2024 revenue is exactly
  187,792,000,000 (637,959,000,000 FY − 450,167,000,000 nine-month YTD), matching the
  review's own cited example. Balance sheet untouched, as instructed (instants need no
  subtraction). A pre-existing side effect found and flagged, not fixed (out of scope,
  "D12 only, don't touch anything else"): the Summary tab's period dropdown now shows two
  entries for one fiscal year-end (the annual one, and the newly-stored discrete-quarterly
  one) -- this already existed silently for cash flow since v1.14, not new to this pass.
- v1.14 — Cash-flow discrete-quarter derivation hardened with four approved additions
  (decision log #67): a `validate` sum-back check (category 33) comparing stored discrete
  quarters against a FRESHLY re-read filed FY figure, so staleness is caught before
  display rather than merely explainable after; `plausible_range` enforced at compute
  time (capex/sbc/dep_amort get a sign-only floor, cfo/free_cash_flow stay unbounded since
  a loss quarter is legitimate); the existing PPE `fallback_canonical` mechanism reused
  (`fallback_label == label` distinguishes a one-row merge from PPE's two-row split, no new
  flag) rather than a third resolution pattern invented; display collapsed to one row per
  line, filed or derived, marked per cell. MU's FCF-blank rate fell from 68% to 5%; cfo
  growth%, previously 0% populated for both MU and NVDA, reached 92%/83%.
- v1.13 — Live-found immediately after v1.12 shipped (decision log #66): `TextColumn`'s
  `alignment` kwarg (used for right-aligning C4's period columns) does not exist in
  Streamlit 1.51.0 — confirmed via `inspect.signature` against the actual environment,
  not the docs, which don't version-scope parameters. Root cause was an Anaconda-vs-
  `.venv` PATH resolution gap, not a code defect this project's own `.venv`-only testing
  could ever have caught. Fixed by removing the call (pyproject.toml's declared floor is
  `>=1.51`; a reader running exactly that is supported, not stale — text-only right-
  alignment via a Pandas Styler was considered and declined, same "colors and font
  weights only" uncertainty already used to reject font-style italic for the growth row)
  and by adding `components.environment_caption()` — always visible in the sidebar,
  warns below the declared floor — so a future environment gap is diagnosable from the
  running app instead of silently unreproducible from a Claude Code session's own `.venv`.
- v1.12 — C4 rebuilt on `st.dataframe` (found live: the first real browser look at C4,
  never done before this pass, found `st.columns`-per-row unreadable at real column counts
  — MU's 28 quarterly periods wrapped a date header into a vertical stack of digits and
  split a value across four lines; `st.columns` was never a table widget). Three fixes:
  1. **Native grid, not `st.columns`.** `st.dataframe` with `column_config.TextColumn
     (pinned=True)` freezes the Line item column; periods stay chronological left to right
     (growth reads left-to-right, comparing each cell to the one on its left — reversing
     would make every figure read backwards). No scroll-to-position API exists in the
     installed Streamlit version (checked the actual signature, not assumed) — solved
     instead by not needing to scroll in the common case: the table defaults to the newest
     8 quarterly / 5 annual periods (already the newest end, nothing to scroll past), with
     an opt-in "show full history" checkbox for the rest. Growth became a separate italic-
     labelled, grey `↳ Growth %` row directly beneath its line item (a `st.dataframe` cell
     has no second line the way `st.columns` + `st.caption` did) — constraint 1 (filed vs.
     derived must stay visually distinct) carried over via the row's own label text plus a
     Styler colour, not font-style italic (Streamlit's own docs commit only to "colors and
     font weights" surviving Styler, not font-style).
  2. **Structure, not decoration.** Zebra striping (by line-item row-pair, not by data
     value) and a border-color theme tweak (`.streamlit/config.toml`, app-wide since this
     is the only `st.dataframe` in the app) for a more visible header/row separation —
     still no colour or weight keyed to a number's own value or sign.
  3. **A blank cell's cause, restored.** The single-period Summary tab already distinguished
     three reasons a line has no value; the multi-period table collapsed all three into an
     unmarked "—". Now classified per cell (`data._classify_blank_cell`) and marked
     distinctly: `—` (never tagged/disclosed — never auto-hidden; AMZN's empty gross
     profit row is itself a fact), `— °` (this line's split-row complement — the other row
     carries this period instead, a presentation choice not a gap), `— ‡` (a derived metric
     not computed at this column's fallback duration — MU's quarterly free cash flow, where
     the cash-flow duration fallback fires but `free_cash_flow` was only ever computed at
     the true three-month duration). A footnote key explains whichever causes actually
     appear in the current window, same discipline as the pre-existing † duration-fallback
     marker. Verified against the real corpus, not just synthetic fixtures: AMZN gross
     profit classifies `gap`, MU's PP&E split rows classify `split`, MU's quarterly free
     cash flow classifies `duration` — all three confirmed live via AppTest against
     `data/app.db` across all three companies × both bases × all three statements (36
     combinations, zero exceptions) before this was called done.
- v1.11 — First pipeline run on a genuinely new filing since the dashboard was built,
  in two approved steps.
  1. **Ingest through section analysis.** AMZN's 10-Q (filed 2026-07-31, period
     2026-06-30) and same-week 8-K discovered, fetched, extracted, XBRL-ingested,
     metrics/observations computed, section-analyzed. A real packaging gap surfaced
     immediately: `anthropic` was never declared in `pyproject.toml` at all (only
     discoverable because this was the first real API call attempted since the
     dashboard build's own environment work) — added, matching the same "fix the
     declared dependency, not just the local venv" discipline as the earlier
     `dashboard*`/streamlit gap. A second real bug surfaced from `validate`:
     `compute-observations --ticker AMZN` (scoped) produced 4 determinism violations
     on MU's own stored observations — the cross-company same-day annotation
     (`_apply_severity_overrides`) requires the full batch, exactly as its own
     docstring already warned ("company by company would miss a same-day match... its
     peer wouldn't exist in the table yet"); re-run unscoped, fixed, confirmed clean.
     Actual cost: 8 new section calls, **$0.1881** (dry-run estimated $0.1745). Lifetime
     spend: $7.0923 of $8.50 (**$1.4077 remaining**).
  2. **`generate-briefs`, unscoped.** Picked up the new filing plus all 18 v1-cached
     briefs invalidated by D6's prompt bump in one run — see D6's entry above for why
     bundling, not scoping to just the new filing, was the deliberate call. Actual cost:
     **$0.5963** against a $0.3126 dry-run estimate — reported as a real overrun, not
     rounded away; root cause is the same generator-ignores-the-sentence-count-
     instruction pattern already on record from v1's own first run (decision log #51),
     unrelated to the v2 prompt change itself, and not something this pass tried to fix
     (ROADMAP-V2, parked until after V1). Lifetime spend: $7.6886 of $8.50 (**$0.8114
     remaining**).

  What the new filing exercised for the first time, checked directly rather than assumed
  clean: D11's exact-period match, the PP&E fallback, and the cash-flow duration fallback
  all resolved correctly against data they had never seen before (AMZN's new quarter
  continues tagging the combined `ppe_and_lease_net` concept, never plain `ppe_net`;
  cash-flow concepts continue tagging directly at the true quarterly duration, no fallback
  needed this quarter). C4's growth% computed correctly on a freshly-added rightmost
  column for the first time in both the split PP&E row and a plain cash-flow row. D8's
  bold mixed-period marker correctly fired on three annual-basis Overview tiles now a
  full year behind the new quarterly anchor. The Overview page's brief/red-flag sections
  correctly showed "No brief exists for this filing" before step 2 and a real, sourced
  brief after it, with no exceptions either way.
- v1.10 — C4 built (the Financials "Table" tab): line items as rows, periods as columns
  oldest to newest, C5's sub-tab bar for Income statement/Balance sheet/Cash flow,
  annual/quarterly toggle, growth toggle. Two design decisions made deliberately before
  building, per instruction, both checked against the real corpus first rather than argued
  from first principles:
  - **Constraint 2 (cash-flow duration mixing).** Checked the actual scope of "never mix
    durations, blank instead" before choosing it: MU would lose 19/28 (68%) and NVDA 12/18
    (67%) of quarterly cash-flow cells -- and MU's real tagging sequence within one fiscal
    year (3-month → 6-month → 9-month → 12-month, resetting next year) means virtually
    EVERY consecutive quarterly transition is duration-mismatched regardless, so growth
    would end up suppressed almost everywhere either way. Decided: show the real filed
    fallback value (marked, not blanked); suppress growth on the transition instead. The
    REAL rule is "suppress when the two cells' durations differ" -- implemented via a
    conservative proxy ("suppress if either cell used the duration fallback"), which is
    always safe (a fallback cell is by construction a different, larger duration) but not
    maximally precise (it also suppresses some same-duration pairs a year apart by
    coincidence). Both the real rule and the proxy relationship are named explicitly in
    `data._resolve_line_across_periods`'s docstring so a future reader doesn't conclude the
    fallback itself invalidates growth.
  - **Constraint 3 (PP&E split row).** Split, not a per-cell marker -- consistent with the
    standard already set for the single-period view ("a label must accurately name the
    number next to it, not one requiring a note"). A row only renders if it has at least
    one populated cell in the displayed range, checked symmetrically for BOTH the primary
    and fallback row (found live, mid-build: the original implementation only guarded the
    fallback row's inclusion, so a line 100% covered by the fallback would still have
    carried a permanently empty primary row -- fixed before it shipped, not after).
  - Growth% is computed entirely in `dashboard/data.py` (R8), never in the page --
    `get_income_statement_table`/`get_balance_sheet_table`/`get_cash_flow_table` return
    fully-populated rows with `growth_pct` already attached per cell.
  - The italic/dim styling on a growth figure is documented in `components.statement_table`
    as load-bearing, not decorative -- the only signal separating a filed number from a
    derived one, achieved with `st.caption` + markdown `*asterisks*` (native Streamlit only,
    no injected CSS, per the caret mechanism's own two-review lesson).
- v1.9 — All of v1.8's fixes confirmed working live (caret, popover, escaping, dark theme).
  Three items this pass:
  1. **Cosmetic: the popover showed a double chevron.** `st.popover`'s own built-in
     disclosure indicator already renders one; giving it a `"▾"` label doubled it up. The
     label is now empty (`st.popover` requires a `str`, not `None`) — the widget's own
     indicator is the entire visible caret. The source count stays on the `help=` tooltip.
  2. **C5: a shared sub-tab bar.** `components.sub_tab_bar(key, label, options)` — an
     `st.radio(horizontal=True)`, styled label-collapsed, keyed by `sub_tab__{key}` (component-
     owned session state, hard rule 3). Selection persists across an unrelated full rerun the
     same way the sidebar company selector already survives page switching (R4a) — confirmed
     live via `AppTest`, not assumed: a keyed `st.radio`'s state is untouched by an unrelated
     widget changing. Built and proven against its one real consumer (Metrics/C6) rather than
     also wiring it into Financials this pass, per instruction not to design it against a
     second, imagined use case.
  3. **C6: Metrics gains sub-tabs by category.** `_group_metrics()` already built `groups`
     by walking `METRIC_REGISTRY` in order (`dict.setdefault` preserves first-appearance
     order) — the actual defect was `render()`'s separate hardcoded `group_order` list of
     seven names, which would silently drop any metric added under an eighth category (AC5:
     the failure would be invisible, not loud). Removed; the sub-tab bar now iterates
     `groups` directly, tabs and labels both sourced from the registry, nothing retyped.
     **Live demonstration, extended per instruction:** a throwaway metric was added to
     `METRIC_REGISTRY` under a brand-new category ("Demonstration", not one of the existing
     seven) — confirmed via `AppTest` to produce an eighth Metrics-page tab with zero changes
     to `pages/metrics.py`, then removed cleanly (config.py's own `_valid_groups` import-time
     check needed the new category added alongside the metric and removed with it — a
     deliberate, loud registration point, not the same failure mode as the page-level
     hardcoded list). Locked in permanently by `tests/test_dashboard_metrics_page.py`, not
     left as a one-time manual demonstration.
- v1.8 — Sixth pass, three items:
  1. **The raw-HTML caret abandoned after a second live failure, plus a theme regression.**
     Confirmed live: the caret was still on its own line (the SAME mechanism that v1.7's fix
     targeted), and the app's theme had flipped from dark to light. Root cause of both,
     traced to the same thing: Streamlit sanitizes `unsafe_allow_html=True` content
     client-side via a bundled DOMPurify (confirmed present in the installed package's static
     JS, wired into the HTML-rendering path — a cheap, direct check, not a browser session),
     which strips `style` attributes, so none of the CSS from either raw-HTML attempt ever
     reached the browser. Replaced entirely with native Streamlit: `st.columns` (a narrow
     right-hand column) holding `st.popover("▾")` for the sources, sentence text in the wide
     left column — no raw HTML, nothing for a sanitizer to touch. `format.escape_html` and
     its call sites removed along with `unsafe_allow_html`; only `escape_markdown_currency` is
     needed again, verified against real `$`-bearing sentences post-change. The source count
     moved to the popover's `help=` tooltip rather than visible/inline text — the caret still
     renders ONLY when a sentence has sources, unchanged.
  2. **Theme restored, made explicit rather than implicit.** `.streamlit/config.toml` added
     (`[theme] base = "dark"`), tracked in git (unlike `secrets.toml`) — the app's theme is
     now a committed setting, not a client-side default a rendering hiccup can silently reset,
     regardless of whether the removed `<style>` block was in fact the cause.
  3. **MU's Financials page showed "Free cash flow: not tagged for this period" directly
     beneath its own two visible inputs** (cash from operations, capital expenditure) — read
     as a bug sitting between them. It is present, at the metric's own computed duration
     (three-month); the Cash flow section was showing those two inputs at the year-to-date
     fallback duration instead (v1.7's D11 follow-up), and `free_cash_flow` was never computed
     at that duration. The page does not derive on the fly — `get_statement_line_values`
     already reads `free_cash_flow` from `metrics`, never computes it from the lines shown —
     the NULL reason for a `METRIC_REGISTRY` line inside a fallback-duration section now says
     so specifically, rather than the generic "not tagged for this period."
- v1.7 — Fifth pass, two items:
  1. **Caret layout, confirmed broken live and fixed.** Even with `display:inline` on both
     `<details>` and `<summary>`, the caret rendered on its own line with a paragraph gap,
     plus a stray focus-outline artifact where the marker should be — CommonMark lists
     `details` among the tags that can start a raw HTML block, splitting it from the
     preceding text into a sibling block regardless of the element's own `display` CSS. Fixed
     by wrapping the entire construct (sentence text and caret together) in one outer `<span>`
     from the first character of the string, so the whole thing parses as one inline unit and
     `<details>` is never independently re-promoted. Marker/outline suppression moved onto
     inline `style=` attributes (survive regardless of whether a separate `<style>` tag does);
     the `<style>` block for the WebKit-specific `::-webkit-details-marker` pseudo-element
     remains best-effort. Still unverified in an actual browser this pass.
  2. **PP&E net fallback, checked across the whole corpus first.** `ppe_net` is absent for
     every AMZN period (2020-12-31 → 2026-03-31, not just the one reviewed) and every MU
     period from FY2021-Q1 onward — Amazon and (from FY2021) Micron both tag the combined
     `PropertyPlantAndEquipmentAndFinanceLeaseRightOfUseAssetAfterAccumulatedDepreciation
     AndAmortization` concept instead. `get_statement_line_values`'s line-tuple format gained
     an optional `(fallback_canonical, fallback_label)`, tried only when the primary is
     absent, with the SAME exact-period-match discipline as the primary (D11) — no alias
     added to `ppe_net` in `CONCEPT_REGISTRY` (would be the alias-purity violation already
     refused for Micron's debt tag). When the fallback resolves, the row's LABEL changes to
     `fallback_label`, not a note appended to the primary label — a reader scanning the row
     must see a label that accurately names the number next to it. Checking the corpus also
     surfaced the identical pattern for `debt_noncurrent` (Micron, 100% of the analyzed
     history) — left alone per instruction (no vetted canonical exists; `LongTermDebt` is
     already claimed by `total_debt`, and choosing a fallback is the same semantic judgment
     the standing "Micron debt-tag diagnosis" already deferred). Its NULL reason was still
     dishonest, though: "not tagged for this period" implies a period-specific gap when the
     absence is total. `concept_never_tagged`/`get_statement_line_null_reason` now say so
     explicitly, with a pointer to the standing diagnosis for `debt_noncurrent` specifically
     (the one concept with an actual written diagnosis to point to) — scoped to the
     company's own analyzed window (`metrics.period_end` range), not all of SEC history:
     Micron DID tag `LongTermDebtNoncurrent` in 2012-2013, which made a naive "any row, ever"
     check a false negative until caught by testing against the real corpus. Recorded as
     binding on C4 (Forward-Looking Concern 4): a fallback that changes a line's label works
     for one period at a time, but C4's table has periods as columns — Micron's `ppe_net`
     switching mid-history means a single row would silently mean two different things across
     its own columns; not solved here.
- v1.6 — Fourth pass, two items:
  1. **D11 over-correction, and the cash-flow YTD-only design question.** The reported
     failure (AMZN, Mar 31 2026: `LongTermDebt`, `NetCashProvidedByUsedInOperatingActivities`,
     `ShareBasedCompensation` all showing "not tagged" despite genuinely existing) could not
     be reproduced against the current code — `get_statement_line_values` already branches on
     `CONCEPT_REGISTRY[...].instant` and resolves all three correctly under a fresh-process
     `AppTest` run of the real page path; a hypothesis that would explain the reported debt
     failure (no instant/duration branch at all) does not explain the reported cfo/sbc
     failures, which match their exact tagged `period_start` under the current code. Treated
     as very likely the same stale-`st.cache_data` trap already documented in "A stale-cache
     trap in dashboard/data.py" — noted, and a combined instant+duration regression test
     added regardless (`test_statement_line_values_renders_both_instant_and_duration_facts_
     for_one_period`). Separately, confirmed live and real: 10-Q cash-flow concepts are
     routinely tagged year-to-date only (Micron's and NVIDIA's non-Q1 quarters mostly have NO
     three-month cash-flow facts tagged at all — verified across the real corpus, present
     counts as low as 0/5 and 1/5 of `CASH_FLOW_LINES`). Decided: the Cash flow section states
     its own natural duration rather than inheriting the selector's when the selector's
     duration has no data for it (`data.get_cash_flow_period`) — falls back to the ONE
     unambiguous alternative duration actually tagged, with an explicit note ("not tagged
     separately for this quarter -- showing the year-to-date figure instead"); refuses to
     guess (returns the original, unfixed duration and no note) when more than one alternative
     candidate exists, matching `get_statement_line_values`'s own refusal to pick an arbitrary
     tie-break.
  2. **C2 revision.** The "N sources" line (decision log #55) was found too heavy once seen
     across a full brief (12 sentences per company, each with its own count line) — the brief
     should read as continuous prose. Replaced with a small caret at the END of the sentence,
     inline, opening its sources in place; renders ONLY when a sentence has sources, so a
     sentence with none is conspicuous by the ABSENCE of a caret rather than by an explicit
     "0 sources" line. `st.expander` was rejected (block element, drops the caret onto its
     own line — the thing being fixed); `st.popover` was also rejected (a separate Streamlit
     widget with its own block in the vertical layout, unable to share one text flow with a
     preceding `st.markdown` call without `st.columns`, a fixed-width side-by-side layout, not
     inline text). A raw `<details>/<summary>` embedded in the same markdown string as the
     sentence text is the only mechanism that shares its flow — the first use of
     `unsafe_allow_html=True` in this dashboard, with both D1's currency-escape and a new
     `format.escape_html` (HTML-tag escape) applied to every piece of model-/DB-generated text
     before embedding. **Unverified without a real browser** (no browser tool available this
     session): whether this actually renders inline rather than wrapping to its own line, and
     whether Streamlit's client-side `$...$` math-mode parsing still reaches text inside a raw
     HTML block — `escape_markdown_currency` is applied regardless, as the safe default either
     way. This supersedes decision log #55's mechanism, not its AC7 amendment — see decision
     log #56 and AC7's own text below, both written to read as a sequence.
  3. **Minor:** `null_metric_tile`'s caption no longer repeats "Not available" (the value
     `st.metric` already shows) before the reason.
- v1.5 — Third pass, three items:
  1. **D3's second null path.** The first pass fixed `null_metric_tile` (`latest is None` —
     no row computed at all); Micron's actual tiles hit a different branch in `metric_tile`
     itself — a row EXISTS with `value=None` and a `null_reason`, which `format_metric_value`
     folded into one string straight into `st.metric`, truncated exactly like the already-
     fixed path. Same treatment now applies there too: short value, full reason in its own
     caption, and the row's genuine period still gets its own (D8-aware) caption. The
     original D3 test only exercised `null_metric_tile` directly — a new test now covers
     `metric_tile` with a row that exists and a NULL value, the path the first test missed.
  2. **D11 (new): Financials showed a period-end date with no duration, and could silently
     show the wrong one.** Micron's Financials page read "Revenue: 78,959" labelled only "as
     of May 28, 2026" — the nine-month year-to-date cumulative, not the 41,456 three-month
     quarter the Overview page's own margins are computed from, with nothing on either page
     stating a duration at all. Root cause: `get_statement_line_values` queried `xbrl_facts`
     by `period_end` alone for duration (non-instant) concepts; a 10-Q routinely tags such a
     concept twice for the same end date (three-month and nine-month), sharing a `filed_date`
     too, with no principled tie-break between them. The period SELECTOR itself was already
     correct (the `metrics` table has exactly one `period_start` per `(cik, name,
     period_end)` in the real corpus, confirmed live) — the bug was entirely in the raw-
     concept branch discarding `period_start` on the floor. Fixed: an EXACT
     `(period_start, period_end)` match, for both the `metrics`- and `xbrl_facts`-resolved
     branches (instant/balance-sheet concepts, which have no `period_start`, are unaffected).
     Every duration-based statement section, and the period selector itself, now states its
     duration in words (three-month / six-month / nine-month / FY — `format_duration_label`,
     driven by the existing `PERIOD_CLASSES` bands) alongside its end date.
  3. **C2 decided:** sources collapse behind a click; an unclickable, accurate source count
     ("1 source" / "3 sources" / "0 sources") stays visible on every brief sentence
     regardless, including the zero case, which renders with no expander at all (there is
     nothing inside it to expand) rather than an empty one. This amends AC7 rather than
     dropping it — see AC7's own updated text below and ARCHITECTURE.md decision log #55 for
     the argument on both sides. `[restatement]`/`[juxtaposition]`/`[grouping]` display
     prefixes dropped from the page; `sentence_type` is unchanged in the database (SPEC-007
     R4 still dispatches verification on it; ROADMAP-V2 still measures the type distribution
     from it — display only). D10 was not touched further, per instruction.
- v1.4 — Second pass on the same real-browser review: D3, D7, D8, D10. Also: D2's fix was
  correct but initially looked broken to the operator because of a stale `st.cache_data`
  entry, not the join — see "A stale-cache trap in dashboard/data.py" below.
  - **D3 (most important) — AC9's NULL reason was truncated by `st.metric` and
    unreachable**, satisfying AC9's letter, not its point. `null_metric_tile` now renders a
    short, untruncated value ("Not available") in the metric itself and puts the FULL
    reason in a caption line beneath it, where `st.caption` does not truncate. Tested via
    `AppTest` (`test_null_metric_reason_is_reachable_in_full`) — asserts the complete reason
    string, not a prefix, appears in the rendered output.
  - **D7 — Beneish M-score shown as a bare number** (`-2.75`), meaningless without its
    conventional threshold. Added `MetricDef.flag_threshold` (generic field, `None` for
    every metric except `beneish_m_score = -1.78`, so this is registry-driven rather than a
    hardcoded check on one metric's name) and a caption in `metric_tile`: "Conventional flag
    threshold: -1.78". Not an interpretation of the number, per SPEC-008's own constraint —
    just the threshold it is measured against.
  - **D8 — mixed as-of dates in one row, same type size and weight, easy to misread as
    comparable.** `metric_tile` gained an `anchor_period_end` parameter (the page's own
    anchor filing period, already known to the caller); when a tile's own period differs
    from it, the caption is bolded and says so explicitly ("a different period than this
    page's anchor filing") — weight, not colour, matching the project's existing severity
    convention (R8) applied to the same underlying principle here.
  - **D10 — the same observation appearing verbatim in both "The brief" and "What
    changed?"** Confirmed live before fixing: 4–6 of 8 top observations per company were
    already restated by a kept brief sentence. Considered whether a proper fix requires
    deciding AC7/C2 first (the instruction under which this pass was scoped) and concluded
    it does not: the actual defect is a set-membership problem (the same fact, shown twice),
    not a source-visibility problem — nothing about how either section displays what it
    displays needs to change. Added `components.observation_ids_cited_in_brief(sentences)`
    (reads the already-resolved `sources`' `kind`/`row.id`, not the raw `refs` citation
    strings) and excluded those observation ids from "What changed?" in `overview.py`; "The
    brief" is unchanged and keeps the richer, sourced version of the same fact. AC7 was not
    decided and did not need to be.
  - **D1 — currency/LaTeX corruption** (`$...$` parsed as inline math by Streamlit's
    markdown renderer, swallowing text between two dollar figures). Fixed with
    `format.escape_markdown_currency`, applied at the point narrative text reaches
    Streamlit. Reviewed a second time and found the seven hand-applied call sites
    insufficient (nothing stopped an eighth site from skipping the escape, and neither the
    unit test nor AppTest exercised the actual render path): replaced with three private
    `_safe_markdown`/`_safe_write`/`_safe_caption` wrappers in `components.py` that are now
    the *only* way `brief_sentence`/`observation_item`/`finding_item` reach Streamlit,
    enforced by a grep-style test on their function bodies
    (`test_narrative_renderers_use_the_escaping_wrapper`) plus three `AppTest`-based
    rendering-path tests that call the real functions and assert no bare `$` reaches a
    markdown-parsed element. `format_usd_per_share` deliberately still emits a raw `$`
    (its main destination, `st.metric`, does not markdown-parse its value — pre-escaping
    would show the literal string `\$0.10`); the one call site that reaches a
    markdown-parsed element with this value (`pages/metrics.py`'s table view) was a live
    landmine — safe only because each value lands alone in its own `st.write` call — fixed
    by escaping at that call site instead.
  - **D2 — company name missing everywhere** (`(MU)`/`(AMZN)` with an empty name).
    `data.get_anchor_filing` selected only `filings` columns; the caller patched `ticker`
    in by hand and never patched `company_name` in at all. Joined to `companies`, matching
    `get_filing`/`get_all_filings`; the caller-side patch removed.
  - **D4 / D9 — usd tiles and axes carried no unit; percent axes plotted the raw ratio.**
    R1 assumed a stated column header would carry the unit for usd values, true on the
    Financials page's statement tables, not true of `metric_tile`/`metric_chart`, which
    have no header for it to land on. Added `format.format_metric_label` (appends " ($m)"
    for usd metrics only — every other unit already self-labels its own value) and
    `format.scale_for_axis`/`format.axis_ticksuffix` (the same per-unit scale/suffix the
    single-value formatters already use, applied to a chart's y-axis). Fixed generally,
    across all 5 usd-unit metrics and all units, not just `free_cash_flow`.
  - **D5 — raw snake_case metric identifiers and unformatted ratios in observation
    statements** (`edgar/observations.py`'s deterministic statement templates used the raw
    metric name and `:.4g` on the raw value, e.g. "gross_margin of 0.8456..."). Added
    `_format_metric_value_for_statement` (duplicated per-unit logic, not imported from
    `dashboard.format` — `edgar` is the backend package `dashboard` depends on, never the
    reverse) and switched all five affected rule functions to `mdef.display_name` plus
    registry-formatted values. This changes stored text, so `compute-observations` was
    re-run against the real corpus (546 observations updated across the current rule
    version; `validate`'s `Observation determinism` check — which recomputes every rule in
    memory and diffs against what's persisted — confirmed clean afterward). Pre-existing
    rows under a superseded `rule_version` were left untouched, matching the determinism
    check's own existing exclusion for stale versions and the dashboard's own
    current-version-only read path — they were never displayed and are not part of this
    fix's scope.
  - **D6 — number formatting inconsistent between brief sentences** (Amazon: "51.82%";
    Micron, same metric, same sentence type: "0.8456" — the generator writes the prose, so
    formatting isn't guaranteed consistent run to run). Genuinely different in kind from
    D2/D4/D5/D9: this text is LLM-authored, not a missed registry lookup. The review's own
    two options were a stricter generator prompt (free to write, but the fix only reaches
    *future* brief generation, and re-running it is a real LLM cost) or registry-driven
    reformatting of the model's prose at render time (rejected: reliably mapping a bare
    number in freeform text back to the specific metric it came from, without a wrong
    guess corrupting the sentence, needs exactly the structure this fix is trying to get
    the model to preserve in the first place — building that heuristic under time pressure
    would trade one formatting bug for a correctness bug). Took the prompt option:
    `prompts/filing_brief_v2.md` adds an explicit instruction to copy a cited item's own
    number formatting verbatim, `BRIEF_GENERATOR_PROMPT_VERSION` bumped to `v2`. Bumping
    the version means the next `generate-briefs` run treats every filing as uncached and
    re-calls the LLM at real cost — **not run this pass**; existing stored briefs
    (Micron's "0.8456" included) are unchanged until that run is explicitly approved.

    **Deferral reversed 2026-08-04 — why.** Parked above on the assumption that
    regenerating 18 briefs would cost roughly what generating them the first time did
    (SPEC-007's own actual: $0.5250). A dry-run against the real corpus measured it at
    **$0.2963** instead — materially cheaper than the anchor the deferral was made
    against. Separately, AMZN filed a new 10-Q the same week, needing its own first-ever
    brief regardless. Running the two together, unscoped, was decided as the right call
    rather than scoping `generate-briefs` to just the new filing and leaving the 18 stale
    v1 briefs (Micron's "0.8456" included) parked indefinitely: ROADMAP-V2's
    generator-prompt items (including the sentence-count instruction — see below) are
    deliberately parked until after V1, so there is no near-term prompt change on the
    horizon that bundling would have pre-empted. Splitting the two runs would only have
    left a mixed v1/v2 corpus that grows by one more stale filing every time the pipeline
    runs, for no offsetting benefit.

    **What actually happened.** Measured cost: **$0.5963**, not the $0.3126 the pre-run
    dry-run estimated — a real, reportable overrun, not a rounding difference, and worth
    recording alongside the (correct) $0.2963 figure above precisely because the two
    numbers answer different questions (18-only regeneration vs. the actual 19-filing run
    that shipped). Root cause matches an already-documented pattern (ARCHITECTURE.md
    decision log #51, from v1's own first run): the generator still does not reliably
    respect the "3 to 6 sentences" instruction — this run kept 245 sentences across 19
    briefs (~12.9/brief), after the generator itself dropped 33 and the verifier dropped
    another 20 (298 generated before either check ran). The v2 prompt change (D6, above)
    only added the number-formatting instruction; it did not touch this, and fixing the
    sentence-count instruction is the same ROADMAP-V2 item, deliberately parked
    until after V1. All 19 briefs verified clean (`validate`, all hard-failing categories);
    Micron's "0.8456" is corrected in the regenerated corpus.
  - Flagged, not fixed, per explicit instruction: **D3** (AC9's NULL reason is truncated by
    `st.metric` and unreachable — AC9 is only nominally met) and **C2** (strip sentence-type
    prefixes and collapse evidence behind a click — contradicts AC7's "sources render
    immediately beneath the sentence, never behind a click"; requires a decision-log entry
    weighing both sides before implementation, not before this note).
- v1.2 — Real gap in how this build was verified, found the first time `streamlit run`
  was actually tried outside the environment used to write it (2026-07-30, reported by the
  operator). See "A real gap in AppTest verification" below — `dashboard` was never
  installed as an importable package in the project's own `.venv`; every check performed
  during the build (`pytest`, `streamlit.testing.v1.AppTest`) passed anyway because it ran
  through a different Python environment whose accidental CWD-based import resolution
  papered over exactly this gap. Fixed (`pip install -e .`, after `pyproject.toml`'s
  `packages.find` already listed `dashboard*`); the underlying lesson and the deployment
  implication are recorded below, not just the one-line fix.
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
7. **(Amended 2026-07-31, C2 — see ARCHITECTURE.md decision log #55, mechanism revised twice
   since, #56 then #58; read the whole sequence, not any one entry alone.)** Sources collapse
   behind a small caret next to the sentence (`st.columns` with a narrow right-hand
   `st.popover("▾")`, native Streamlit — no raw HTML, after two failed raw-HTML attempts:
   #56's `<details>`/`<summary>` stayed block-level regardless of CSS because Streamlit
   sanitizes `unsafe_allow_html` content client-side and strips `style` attributes, and
   separately coincided with the app's theme flipping to light). The caret renders ONLY when
   a sentence has sources — a sentence with none is a single un-columned line with no caret at
   all, so it is conspicuous by that absence next to every grounded sentence around it. Never
   a caret that opens to nothing. This is the binding constraint, replacing #55's visible "N
   sources" line (found too heavy at real scale) without reversing the underlying amendment: a
   reader can still tell, without clicking anything, whether a sentence is checkable at all.
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

## A real gap in AppTest verification (v1.2, 2026-07-30)

**What happened.** `streamlit run dashboard/app.py` failed for the operator with
`ModuleNotFoundError: No module named 'dashboard'`. Root cause: `pyproject.toml`'s
`[tool.setuptools.packages.find]` was updated during this build to include `dashboard*`
alongside `edgar*`, but the project's `.venv` had an editable install of `edgar` performed
*before* that change — an editable install's package mapping is fixed at install time, not
re-read on every import, so adding `dashboard*` to `pyproject.toml` did nothing to an
already-installed venv until `pip install -e .` ran again. The venv also never had
`streamlit`/`plotly` installed at all (both new dependencies added in this same build).
`pip install -e .` fixed both at once.

**Why every verification step taken during the build missed this, specifically.** This
build was verified with `pytest` and `streamlit.testing.v1.AppTest` (headless, no browser),
run via ad hoc `python3 -c "..."` invocations and `python3 -m pytest`, all launched with the
repository root as the working directory. Python's `-c` flag, and pytest's own rootdir
handling (via `tests/__init__.py`, per the operator's own diagnosis), both insert the
current working directory onto `sys.path` — so `import dashboard` resolved *by accident*,
through the working directory, in the exact environment used to write and check this code,
regardless of whether `dashboard` was ever properly installed anywhere. Confirmed directly:
the project's own `.venv` had never had `edgar` importable outside its own repo root either
— `pip show edgar` in the environment actually used to build this returned "not found" the
whole time, and every test still passed, for the identical reason.

**This is a structural blind spot in this verification method, not a one-off mistake.**
Any check that imports project code via a `-c` flag or a pytest run from the repo root will
silently succeed on a packaging gap that a real, independently-launched process — a
different working directory, a systemd unit, a Docker container, Streamlit Community
Cloud's own runner — will not paper over. `AppTest` itself is sound (it caught a real
`st.navigation` URL-collision bug during this same build, correctly); the surrounding
process of invoking it did not exercise the actual install boundary. **The fix going
forward: verification of anything packaging-related must run through the project's own
declared environment (`.venv/bin/python3`, not an ambient interpreter), and at least once
per build, from a working directory outside the repository** — exactly reproducing how an
unrelated process would actually launch it. Confirmed after the fact: `.venv/bin/streamlit
run` (absolute paths, launched from `/tmp`) now serves correctly.

**Deployment implication, since this is heading for Streamlit Community Cloud (Forward-
Looking Concern 2).** Streamlit Cloud builds its own fresh environment from the repo on
every deploy — it does not inherit whatever happens to be installed on a developer's
machine, so this exact failure mode (a stale or absent install of the project's own
packages) is the *default* outcome unless the build step explicitly installs this project,
not just its third-party dependencies. Concretely, before the first real deployment:
confirm Streamlit Cloud's build actually runs `pip install .` (or equivalent) against
`pyproject.toml` — not only a `requirements.txt`-style install of `streamlit`/`plotly`/
`requests`/`beautifulsoup4` that would leave `edgar`/`dashboard` themselves unimportable,
reproducing this exact error on the very first deploy. **Recorded as binding on the future
deployment/GitHub Actions spec**: its own acceptance criteria must include "a genuinely
fresh clone, fresh environment, `pip install .`, `streamlit run` succeeds" as an explicit,
automated check — not something caught by a developer's already-warm machine, which is
precisely how this one got through.

---

## A stale-cache trap in dashboard/data.py (v1.4, 2026-07-31 — noted, not fixed)

**What happened.** The D2 fix (joining `companies` into `get_anchor_filing`'s SQL) was
correct on disk and passed every automated test, but the operator's own already-running
`streamlit run` session kept showing the old, empty company name for a while after the fix
landed — resolved once the process/cache was refreshed. The join was never the problem by
that point; a stale `st.cache_data` entry, computed under the *old* SQL text before the fix,
was still being served.

**Why this can happen even though the cache key correctly includes the SQL text.**
`dashboard/data.py` funnels every single query through one decorated primitive,
`_cached_query(db_path_str, mtime_value, sql, params)` — every other function in the module
(`get_anchor_filing`, `get_filing`, `get_top_observations`, all ~20 of them) is a plain,
undecorated helper that builds a SQL string and calls it. In a genuinely fresh process, a
changed SQL string is a different argument value, so `st.cache_data` correctly computes a
new key and misses the stale entry — the caching design itself is sound. But `st.cache_data`
persists in memory for the life of the running server process, and a long-running `streamlit
run` dev session does not always reliably re-import an already-imported module's changed
code on every autoreload; when it doesn't, the OLD compiled `get_anchor_filing` — still
building the OLD SQL text — keeps running, and correctly (from its own point of view) keeps
hitting the OLD cache entry for it. The result looks exactly like "the fix isn't taking
effect," when the actual fix was already correct and the problem was one layer up, in
process/module staleness rather than in the query itself.

**The trap, stated generally:** because every query in this module shares the *one* cached
primitive, editing SQL text *anywhere* in `dashboard/data.py` carries this same
characteristic — correct on disk, but a long-running dev session can keep serving a stale
result until the cache is explicitly cleared (Streamlit's "Clear cache" control, or a
process restart) rather than merely refreshing the browser. This is not specific to
`get_anchor_filing`; it is a property of the module's whole caching architecture. **Flagged
here because C4 (a Bloomberg-style statement table) and C6 (Metrics sub-tabs by category)
will both rework this data layer heavily** — worth having this in mind up front rather than
re-discovering it mid-review a second time.

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
4. **Binding on C4 (Bloomberg-style statement table): a statement line's fallback can switch
   mid-row, and a per-line label can't express that.** `get_statement_line_values`'s fallback
   mechanism (PP&E follow-up, 2026-08-01) changes a line's LABEL when its narrower canonical
   is absent and a broader one resolves instead — correct for the current one-period-at-a-time
   view, where each rendered line has exactly one label for exactly one period. Confirmed
   live: Micron's `ppe_net` is present before FY2021-Q1 and absent (falling back to
   `ppe_and_lease_net`) after. C4's own table has periods as COLUMNS and one line item per
   ROW — a single "PP&E, net" row spanning Micron's history would show pure PP&E in its early
   columns and PP&E-plus-finance-lease-ROU-assets in its later ones, changing what the row
   MEANS partway across, with nothing in a per-line label marking where. **Not solved here**
   — C4 needs the fallback marked per CELL (each cell states its own label/footnote when it
   used the fallback), or the row split into two, one per canonical, each populated only
   where its own concept resolves. Decide the shape before building C4, not after noticing
   a row that quietly means two different things.

---

## Notes for the Implementer

- Build `format.py` and `data.py` first, with tests. Pages last. Pages written before the
  layers beneath them always end up containing logic that belongs lower down.
- The Overview page is the one that gets screenshotted. Spend the extra hour there.
- Resist adding computation. Every number on this site should already exist in the
  database, computed by a layer that was tested.
- Report any discrepancy between this spec and observed behaviour rather than working
  around it silently. `ARCHITECTURE.md` must then be corrected.
