# Batch 4 — remaining render-layer items

**Date:** 2026-08-14
**Depends on:** batch 3 (complete)
**Scope:** Render layer. Five items, checkable in one browser pass.

Standing constraints from batch 1 apply: no paid calls, no prompt version bumps, one commit
per item, each item ships with its test, state the environment.

---

## Item 1 — The line-item column is far too wide

It currently eats roughly a third of the table. The cause is item 6 of batch 3 sizing the
column to the registry's longest label (63 characters), so every row pays for one outlier.

**Fix the labels, not the width.** Filed statements abbreviate precisely because of this.
Use standard financial shorthand:

| Current | Shorter |
|---|---|
| Property, plant and equipment and finance-lease ROU assets, net | PP&E and finance-lease ROU assets, net |
| Property, plant and equipment, net | PP&E, net |
| Selling, general and administrative | SG&A |
| Research and development | R&D |
| Depreciation and amortisation | D&A |
| Net cash provided by operating activities | Net cash from operating activities |
| Net cash provided by (used in) financing activities | Net cash from financing activities |
| Retained earnings (accumulated deficit) | Retained earnings |
| Short-term debt and current portion of long-term debt | Short-term debt |

Those are suggestions, not a mandate — use judgement, but the test is that the abbreviation
is one a financial reader recognises instantly. Do not invent shorthand.

**Also shorten the growth row label.** `↳ Growth % (vs. period to the right)` repeats on
every second row and is a major contributor to the width. Make it `↳ Growth %` and move the
direction into the table caption, where it needs saying once.

Then resize the column to the new longest label. Keep the full unabbreviated text reachable
on hover if that's cheap; don't fight the widget for it if it isn't.

### Resolution (implemented 2026-08-16)

Applied every shorthand from the item's own table, plus a few more under the same "reader
recognises it instantly, not invented" test: "Net cash used in investing" (dropping
"activities" to match the operating/financing subtotals the item did name, so the three
section subtotals stay parallel rather than one being inconsistently longer) and "FX
effect on cash" ("FX" is as standard as PP&E/SG&A/R&D/D&A). Left alone where no
recognised abbreviation exists: "Acquisitions, net of cash acquired", "Maturities and
sales of investments", "Finance lease principal paid" — shortening these would mean
inventing shorthand, which the item explicitly rules out.

Longest label across every statement: 63 characters ("Property, plant and equipment and
finance-lease ROU assets, net") → 39 ("PP&E and finance-lease ROU assets, net"). Column
width resized to match (520px → 320px, same estimation method as batch 3 item 6 — a
generous, rounded-up figure at typical UI-font metrics, not a browser-measured exact fit).

The growth row's own label lost its "(vs. period to the right)" suffix (back to the
original `↳ Growth %`); the direction now appears once, in `statement_table`'s own
caption, only when `show_growth` is on. **Found while fixing this, not asked for**: batch
3 item 7's column reversal shipped with no on-screen notice of the new newest-to-oldest
order at all — added as its own, unconditional caption (not gated on `show_growth`, since
it governs reading the whole table).

**Full text on hover: not implemented.** Checked before deciding, not skipped by
default: `column_config`'s `help` is a per-COLUMN tooltip (confirmed against its own
`ColumnConfig` TypedDict — one `help` field, shared by every row in that column), not
per-cell — there is no mechanism in the installed 1.60.0 for a different tooltip per row
within the same column. Fighting the widget for something it doesn't expose is exactly
what the item says not to do; skipped rather than forced.

---

## Item 2 — Findings are not sorted by severity

On the Filings page, findings currently run Medium, Medium, Low, Low, Medium, Low, Low,
**High**, Medium, Medium, Low, **High**, Medium, **High**, Low.

The three High findings on Amazon's latest 10-Q — the $15.9B discrete tax expense, the
Anthropic revaluation, the Indian tax dispute — are scattered mid-list among fog-index
observations.

Sort descending by severity, then by category, then by whatever tie-break already exists.
Severity ordering is the whole reason the severity field exists; it currently does nothing
on this page.

### Resolution (implemented 2026-08-16)

`data.get_findings_for_filing`'s entire sort key was `ORDER BY id` (insertion order) --
now `CASE severity WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END, category, id`, the
SAME severity-ordering expression `get_observations_for_filing` already uses a few lines
down (reused, not reinvented, so the two lists read consistently). `id` stays as the final
tie-break, unchanged. The page itself needed no change at all -- `_render_detail` already
just iterates `detail["findings"]` in whatever order the data layer hands back.

Verified against the exact real filing the item describes (AMZN's latest 10-Q,
`0001018724-26-000026`): all three High findings now sit first -- the Anthropic
revaluation (category `concentration`), then the $15.9B discrete tax expense and the
Indian tax dispute (both `litigation`, alphabetically after `concentration`, `id` breaking
the tie between them).

---

## Item 3 — The brief and the observations list duplicate each other

On the Filings page:

- Brief: *"Gross margin reached 52.3%, the highest level in 19 quarters."*
- Observations: *"High — Gross margin of 52.3% is the highest in 19 quarters."*

Same fact, one screen apart.

D10 solved exactly this on the Overview's "What changed?" by excluding observations already
cited by a kept brief sentence (`observation_ids_cited_in_brief`). The same exclusion was
never applied to the Filings page.

Reuse it. Do not write a second mechanism.

Check what's left afterwards — if the Filings observations list drops to two or three
low-severity items, say so, because that changes whether it earns its own section.

### Resolution (implemented 2026-08-16)

Applied the exact same two lines `overview.py` already uses — `observation_ids_cited_in_
brief(sentences)` then a list-comprehension filter — to `filings.py`'s `_render_detail`,
guarded the same way (only when a brief exists at all; nothing to exclude against
otherwise). No second mechanism written.

**What's left, checked against the real corpus**: of the 74 filings in the database, only
19 have a generated brief (the feature is recent; every older filing's observations list
is completely unaffected by this item, dedup has nothing to compare against). Across those
19, the exclusion drops 5–8 observations each, leaving 4–44 remaining (median 20). One
filing — AMZN's `0001018724-25-000123` — comes closest to the item's own trigger: 12 → 4
(one medium, three low). None drop to exactly two or three. **The section clearly still
earns its place**: a handful of filings sit in the high single digits after dedup, but the
typical case (median 20) is nowhere close to thin, and the one outlier is a single real
filing, not a pattern.

---

## Item 4 — Two of three chart series are the same colour

On the Metrics page, AMZN and MU render in near-identical blues and cannot be told apart.
Only NVDA is distinguishable.

Pick three genuinely distinguishable colours and check them against the dark theme.

This does **not** conflict with the no-colour rule: that rule forbids colour encoding
*severity or analytical meaning*. Distinguishing one company's line from another's is
identity, not judgement. Note the distinction in the spec so the rule doesn't get
misremembered later as "no colour anywhere".

### Resolution (implemented 2026-08-16)

**The rule distinction, stated for the record**: ARCHITECTURE.md/SPEC-005's no-colour rule
forbids colour encoding *severity or analytical meaning* — a red cell would claim "this
number is bad" without a reader having to read the label. A chart legend's colours encode
*which company's line is which*, a fact identical to the ticker already printed next to it
in the legend text; colour here is redundant with a label, not a silent claim standing in
for one. Not the same rule, and not an exception to it either — outside its scope.

**Root cause**: no colour was ever set explicitly in `metric_chart`'s `go.Scatter` calls —
every trace fell through to Plotly's own default categorical sequence, which happens to put
two similar blues in its first three slots. Fixed with Okabe-Ito, the standard colorblind-
safe categorical palette: `#E69F00` (AMZN), `#009E73` (NVDA), `#CC79A7` (MU), assigned by
each ticker's position in `config.WATCHLIST` — a company keeps the same colour whether
shown alongside all three others or alone, not whatever Plotly's own trace-iteration order
happens to be for the currently-selected subset.

**Contrast against the dark theme, computed, not eyeballed**: WCAG relative-luminance
contrast ratio against Streamlit's default dark background (`#0e1117`) — AMZN 8.4:1, NVDA
5.5:1, MU 6.2:1. All comfortably clear of the 3:1 floor WCAG sets for graphical objects.

---

## Item 5 — The section-text viewer is unreadable

The Sections viewer on the Filings page opens with:

```
v3.26.1
Accounting Policies and Supplemental Disclosures 6 Months Ended
Jun. 30, 2026
Accounting Policies [Abstract]
Accounting Policies and Supplemental Disclosures ACCOUNTING POLICIES AND SUPPLEMENTAL
DISCLOSURESUnaudited Interim Financial Information...
```

Three problems, all artifacts of the SEC's R-file rendering:

1. A viewer version string (`v3.26.1`) leaking into the text.
2. The note title repeated three times before the content starts.
3. Words run together at former element boundaries (`DISCLOSURESUnaudited`), and no
   paragraph breaks anywhere in several thousand words.

**Do not modify the stored text.** It is the analysis layer's input and its hashes are
content-addressed — changing it would invalidate the section store and every cached analysis
downstream. This is a **display-time** cleanup only, exactly like currency escaping.

Strip the version string and the repeated title, and restore paragraph breaks where an
element boundary can be identified. If run-together words can't be split reliably, leave
them — a wrong split is worse than an ugly one.

### Resolution (implemented 2026-08-17)

New `format.clean_section_display_text(raw_text, short_name)`, called at the one render
site (`filings.py`'s `_render_detail`, right before `st.text`). `text` — exactly what
`section_store.read_section_text` returns — is never reassigned; the cleaned string is a
separate value passed straight to `st.text`. The stored row is read-only here, same
discipline as currency escaping.

**Version string**: reuses `config.XBRL_VIEWER_VERSION_LINE_PATTERN`, the pattern
SPEC-005's `section_store.normalize_for_wording_hash` already strips for its own,
unrelated purpose (wording-identity hashing) — not a second mechanism. Checked before
reusing it: it handles a variable number of dot-separated version segments
("v3.25.0.1" as well as "v3.26.1"), which a hand-rolled 3-segment-only pattern (my first
draft) would have silently missed.

**Repeated title**: checked against the real corpus before writing this, and found two
different shapes, not one — AMZN doubles as title-case-then-ALL-CAPS ("Accounting
Policies and Supplemental Disclosures ACCOUNTING POLICIES..."), NVIDIA repeats the same
case verbatim ("Groq Groq...", "Organization and Summary... Organization and
Summary..."). A loop strips `short_name` or `short_name.upper()`, whichever matches,
however many times it actually repeats — general to both shapes, not hard-coded to
exactly three repeats. The duration line ("6 Months Ended" / "12 Months Ended" / ...) is
stripped by starting-with-`short_name`, not by enumerating every SEC duration phrase; the
`[Abstract]` line by its own suffix, not by content match against `short_name` — checked
live that the two don't always agree ("Financial Instruments" vs. "Investments, Debt and
Equity Securities [Abstract]" for the same section).

**Run-together words**: restores a paragraph break at a 4+-letter ALL-CAPS run
immediately followed by a Titlecase word start (`DISCLOSURESUnaudited` →
`DISCLOSURES\n\nUnaudited`) — the shape of a former header directly abutting the body
text that used to sit in a different table cell. This went through two rounds of
false-positive checking against real filing text, not just the header cases it needs to
catch:

- A broader "any lowercase immediately followed by uppercase" rule was rejected first,
  after checking it against NVDA's own product name "GeForce" and confirming it would
  wrongly split into "Ge"/"Force".
- The narrower ALL-CAPS-run rule was then found, live against NVDA's "Stock-Based
  Compensation" section, to have its own false positive: a 2-letter minimum matched
  inside "RSUs"/"PSUs" (the acronym fragment "RS"/"PS" is itself a short ALL-CAPS run
  followed by a Titlecase-shaped "Us"), corrupting them into "RS\n\nUs"/"PS\n\nUs".
  Raising the minimum run length to 4 letters fixes this — every real header this
  function needs to catch is well over 4 letters — while leaving RSUs, PSUs, and the
  same-shaped ISOs/IPOs/SPACs untouched. Re-checked against the full corpus (all 2,190
  sections currently in the database) after the fix: zero remaining short-acronym
  false positives, zero leaked version strings.

**Left deliberately alone, per the item's own instruction**: a Titlecase-word directly
abutting another Titlecase word (`Stock Repurchase ActivityIn March 2022`,
`SecuritiesAs of December 31`) is structurally identical to a genuine camelCase brand
name using text alone — there is no reliable way to tell a real header join from a real
proper noun in this shape, so it is left unsplit. This is a real, known limitation, not
an oversight: some run-together joins in the viewer will still read run-together.

**Reliability, stated plainly**: the version-string, duration-line, date-line,
`[Abstract]`-line, and repeated-title stripping are all structural (matched by shape or
position, not guessed) and reliable. The ALL-CAPS-run paragraph-break restoration is
reliable for the specific shape it targets, but is deliberately conservative — it does
not attempt the harder, ambiguous Titlecase-to-Titlecase case at all. Overall: **best-
effort**, by design, not because the reliable part is in doubt.

---

## When you're done

Report once. Note explicitly whether item 3 left the Filings observations list thin enough
to question, and whether item 5's paragraph reconstruction is reliable or best-effort.

---

## Follow-up (approved 2026-08-18)

Three more items, raised after using the shipped batch.

### Follow-up item 1 — the line-item column is still a bit wide

Resolved — see the batch 4 item 1 resolution above, updated in place is not how this
project records changes, so recorded here instead: the binding label ("PP&E and
finance-lease ROU assets, net", 39 chars) went to 36 via ampersand-for-and ("PP&E &
finance-lease ROU assets, net"), the same convention "PP&E" itself already uses. "ROU
assets" was kept rather than dropped — it's the underlying XBRL concept's own wording,
not decoration. Two labels the original item 1 had declared unshortenable turned out to
have real forms after all: "Acquisitions, net" (a caption used verbatim in real condensed
cash-flow disclosures) and "Maturities/sales of investments" (reused from this project's
own config.py METRIC_REGISTRY, not invented a second time). Column width 320 → 300px, fit
to the new 36-char worst case by the same two-point estimate this project has used since
batch 3 item 6. The requested ~260px isn't reachable at 36 chars without inventing further
abbreviation ("fin." for "finance") — reported rather than forced.

### Follow-up item 2 — restore paragraph breaks at period-into-capital joins

`format.clean_section_display_text` now also restores a break at a full stop directly
followed by a capital letter with no space (`_SENTENCE_END_INTO_CAPITAL`) — this shape
has no legitimate reading other than a lost element boundary, since a real sentence end is
always followed by a space.

The one real risk — checked against the full corpus before, not after, committing — is a
multi-period initialism ("U.S.", "B.V.", "K.K.", district-court short forms "E.D."/"W.D."/
"N.D.") that has the identical local shape (one capital letter, a period, no space). A
naive version of this rule corrupted roughly 2,700 real "U.S." occurrences alone into
"U.\n\nS." across the corpus. Fixed by leaving a match untouched whenever the token
immediately before the period is exactly one uppercase letter — with one refinement found
live: "non-U.S." would otherwise merge "non-U" into a single 5-character token via the
hyphen and escape the exclusion, so the digit-hyphen lead is only glued to what follows it
when it's actually a digit (`10-`, `8-`, keeping SEC form codes like "10-K." intact as
4-character tokens so they still split), never an alphabetic prefix like "non-".

Re-verified against all 2,190 sections after the fix, same as the ALL-CAPS rule before it:
zero remaining false positives, and the original RSUs/PSUs fix confirmed still intact
alongside it. The known, accepted cost of the single-letter exclusion: a genuine
one-letter sentence ending (found once — a loan facility literally named "Term Loan A",
"...Term Loan A.On June 7, 2023...") stays unsplit rather than risk every "U.S." in the
corpus. Same trade this project already made for the ALL-CAPS rule.
