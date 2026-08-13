# Batch 3 — table readability (render layer)

**Date:** 2026-08-11
**Depends on:** batches 1 and 2 (complete)
**Scope:** The Financials table, as a thing a person reads. Render layer throughout.

## Why this batch is small

Batches 1 and 2 were large because every item was machine-verifiable. This one is not.
AppTest has now missed five render-layer defects in this project — the currency/LaTeX
corruption, the block-level caret, the stale cache, the truncated NULL reason, and (item 3
below) the same currency corruption a second time in a different call site.

**The test for this batch is a human looking at the page.** Keep it to these seven items so
one screenshot pass can check all of them.

Standing constraints from batch 1 still apply: no paid calls, no prompt version bumps, one
commit per item, each item ships with its test, state the environment.

---

## Item 1 — Indent subtotals on the cash flow statement

The statement is now correctly ordered but renders as 24 visually identical rows. Operating,
investing, financing and reconciliation are indistinguishable without reading every label.

**Indent the section subtotals** — `Net cash provided by operating activities`, `Net cash
used in investing activities`, `Net cash provided by (used in) financing activities` — so
each sits visibly to the right of the lines that feed it. That's how filed statements signal
a section closing, and it costs whitespace rather than extra rows.

Bold them too if it reads better, but **weight and indentation must be a function of the
row's structural role, never of its data.** The no-colour-by-data rule extends to any visual
emphasis.

Apply the same treatment to `Cash at end of period` if it reads well; leave `Net change in
cash` and `Cash at beginning of period` unindented.

Do not add section header rows. Indentation alone should carry it — check in a browser
whether it does before adding anything more.

### Resolution (implemented 2026-08-14)

Indented `cfo` ("Net cash provided by operating activities"), `net_cash_investing`,
`net_cash_financing`, and `cash_and_restricted_cash` ("Cash at end of period") — the last
one as a judgment call (the item allows it "if it reads well"): it's the statement's own
final closing balance, structurally the same kind of line as each section's own subtotal,
so it gets the same treatment. `net_change_in_cash` and `cash_beginning` are left alone
per the item's explicit instruction.

Indentation is TEXT (a 4-space prefix on the label), not CSS padding — checked against
this table's own existing pattern first: `_GROWTH_ROW_LABEL` already prefixes its label
with a turned arrow for exactly this reason (an earlier finding, carried in this file's
own comments, that Streamlit's Styler → `st.dataframe` grid translation is confirmed only
for `color` and `font-weight`, not `padding`). Bold uses `font-weight` via the Styler,
which the same comment already knows is safe. Both are keyed off the row's `canonical`
only — never a cell's own value, per the item's explicit rule — covered by a test that
seeds a subtotal row with a negative value and a blank cell and confirms the label stays
indented either way.

No section header rows added, per the item's own instruction. Whether indentation alone
reads clearly enough in a browser is the next pass's call, not verifiable from here.

---

## Item 2 — Effective tax rate renders as `0`

The Key metrics tab's `Effective tax rate (FCFF)` row shows `0` in every column, for every
company.

The underlying value is correct — it can be recovered from the FCFF figures. AMZN FY2020:
FCFF 27,376 − FCF 25,924 = 1,452, against interest expense of 1,647, implying a rate of
11.8%. FY2025 implies 19.6%. So the computation is right and the display is wrong: a
fraction being rendered with zero decimal places instead of as a percentage.

Fix the formatting. The row should read `11.8%`, `19.6%` and so on.

---

## Item 3 — The currency/LaTeX bug is back, in a new call site

The Key metrics caption renders as:

> EPS in `(2dp)`, shares and cash-flow figures in millions `( m)`, tax rate in %, FY periods

Both `$` characters have been consumed and the span between them turned into math mode.
This is D1 exactly — Streamlit's markdown treating `$...$` as inline LaTeX.

`escape_markdown_currency` exists and works. The problem is the **guard**: the `ast`-based
test added in batch 1 only inspects `components.py`'s three narrative renderers, so a
caption written in a page file was never covered.

Two parts:

1. Fix the caption.
2. **Widen the guard.** Any string reaching `st.markdown`, `st.write` or `st.caption` that
   contains a literal `$` must be escaped, wherever it lives — page files included, not just
   `components.py`. Extend the `ast` check to cover `dashboard/pages/*.py`, and verify it
   catches this specific caption before you fix it, so you know the test would have failed.

This is the second occurrence of one defect. The fix is the guard, not the instance.

---

## Item 4 — Right-align numbers, parentheses for negatives

Two conventions, both cheap, both high-leverage on how the table reads:

- **Right-align every numeric column.** Financial tables align numerals so digits line up by
  place value; left-aligned figures of different magnitudes are genuinely harder to compare
  down a column. Line-item labels stay left-aligned.
- **Negatives in parentheses** — `(8,821)` rather than `-8,821`. Standard in every filed
  statement.

The `-` for a missing value and `(0)` for a real negative zero must remain distinguishable.

---

## Item 5 — Fiscal labels on column headers

Column headers currently read `Dec 31, 2025`, `Aug 28, 2025`, `Jan 25, 2026` — three
companies, three fiscal calendars, no year labels.

Micron is the worst case: its year-end wanders between late August and early September, so
its annual columns look like an inconsistent date series rather than FY2017 through FY2025.
`Sep 3, 2020` and `Aug 29, 2019` are consecutive fiscal years and nothing on screen says so.

**Lead with the fiscal label**, calendar date secondary:

- Annual: `FY2025`
- Quarterly: `Q3 FY26`

`filings.fiscal_year` / `fiscal_period` already carry this — the same fields the
discrete-quarter work used. Do not compute the fiscal label from dates.

---

## Item 6 — Stop truncating labels and headers

Both are cut mid-word today:

- Column headers: `Jun 30, 202`, `Dec 31, 202` — the year is unreadable, which item 5
  partly fixes by shortening the label, but check it fully resolves.
- Line items: `Property, plant and equipment and…`, `Retained earnings (accumulated def…`,
  `Net cash provided by operating activ…`

Widen the label column, wrap, or add a tooltip carrying the full text. A truncated line item
on a financial statement is a line item a reader can't identify.

---

## Item 7 — Column scroll position — settle it

The table still opens on the oldest period. Requirement is unchanged: chronological left to
right, **opening scrolled to the newest (right-hand) edge.**

This has now cost three rounds. Resolve it definitively this time:

1. Try `direction: rtl` on the scroll container with `direction: ltr` on the inner content —
   the standard trick for opening a horizontal scroller at its right edge. The open question
   is whether an injected `<style>` block survives Streamlit's sanitiser; inline `style`
   attributes demonstrably don't, but a `<style>` tag may.
2. If that fails, check whether any other native mechanism exposes scroll position.
3. If both fail, **implement the fallback in the same pass rather than reporting back**:
   reverse the column order so newest is leftmost. Growth must still compare each period to
   the chronologically prior one — now the column to its right — and the row label must say
   so unambiguously. Add a test that a known growth figure is unchanged by the reversal;
   this is exactly the kind of flip that silently inverts a sign.

Whatever the outcome, record it in the spec, including which routes failed and why. If
Streamlit's rendering layer has dictated a third design decision in this project, that
pattern is worth having written down before the deployment spec is drafted.

---

## When you're done

Report once. Then stop — the next pass needs a browser, and there are four more
render-layer items from `SPEC-008-review-2` still outstanding (findings sorting,
brief/observations duplication on the Filings page, chart series colours, and the raw
section-text viewer). Those are batch 4.
