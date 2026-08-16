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

---

## Item 4 — Two of three chart series are the same colour

On the Metrics page, AMZN and MU render in near-identical blues and cannot be told apart.
Only NVDA is distinguishable.

Pick three genuinely distinguishable colours and check them against the dark theme.

This does **not** conflict with the no-colour rule: that rule forbids colour encoding
*severity or analytical meaning*. Distinguishing one company's line from another's is
identity, not judgement. Note the distinction in the spec so the rule doesn't get
misremembered later as "no colour anywhere".

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

---

## When you're done

Report once. Note explicitly whether item 3 left the Filings observations list thin enough
to question, and whether item 5's paragraph reconstruction is reliable or best-effort.
