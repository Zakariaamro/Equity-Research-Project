# Batch 1 — data-layer completeness

**Date:** 2026-08-04
**Source:** `SPEC-008-review-2-2026-08-04.md`
**Scope:** Data layer only. No render-layer or styling work in this batch.

## Why this batch is scoped the way it is

Every item here is verifiable by machine — `validate`, the sum-back rule, `plausible_range`,
real-corpus counts. That verification has a good track record in this project.

Render-layer work is deliberately excluded. AppTest has missed the currency/LaTeX
corruption, the block-level caret, the stale cache, and the truncated NULL reason — four
defects, all render-layer, all invisible to the test suite and visible only in a browser.
Those items go in a separate, smaller batch where a human looking at the page is the test.

**Do not batch across that line.** If an item here turns out to need a render change to be
useful, implement the data half and note it; don't drift into styling.

## Standing constraints for this batch

1. **No paid API calls.** Remaining budget is ~$0.81. Nothing in this batch requires an LLM
   call. If something appears to, stop and say so — do not spend.
2. **No prompt version bumps.** A bump invalidates cached analyses and costs ~$6 to
   regenerate. Not in scope.
3. **State the environment.** Report which Python and which Streamlit you ran tests under.
   Environment mismatch has produced two false conclusions in this project already.
4. **One commit per item**, so a regression can be bisected to a single change.
5. **Each item ships with its test.** No item is done without one.
6. **Order matters.** Items 1–3 are foundations that later items rely on. Do them first and
   confirm `validate` is clean before proceeding.

---

## Item 1 — Year-over-year growth (D13)

The only growth currently shown is sequential. For a seasonal business this is close to
useless: Amazon's Q1 always falls and Q4 always jumps, so `-13.6%` QoQ says nothing about
the business. YoY is the number an analyst reads first.

Add year-over-year growth to the Financials table. YoY compares each quarter to the same
fiscal quarter one year earlier — `filings.fiscal_year` / `fiscal_period` answers this
directly, as it did for the discrete-quarter work. No date arithmetic.

**STOP HERE AND ASK** before implementing the display: toggle between sequential and YoY, or
both rows at once? Both-at-once triples row height on a table already criticised as too
tall; a toggle costs a click. Make the argument for one and let me choose. This is a
decision about how the tool reads day to day and it is mine, not yours.

Constraints:
- YoY is derived. Same status and marking as sequential growth.
- Fail closed when the year-ago quarter is missing or was itself unavailable.
- On the annual basis, YoY and sequential are identical. Do not render two identical rows —
  suppress the choice and label it once.
- Item 2's `n/m` rule applies to YoY as well.

---

## Item 2 — `n/m` for meaningless percentages (D14)

Micron's quarterly cash flow currently shows `+4097.2%`, `+1578.8%`, `-397.4%`, `+340.0%`.

Two separate problems:

- **Near-zero base.** Free cash flow moving from 72 to 3,022 is real; `+4097.2%` conveys
  nothing and crowds the column.
- **Sign crossing.** FCF going from `+72` to `-113` yields a percentage that is defined and
  meaningless. A move from profit to loss cannot be expressed as a percentage change.

Render `n/m` (not meaningful) when the base is zero, when the sign flips, or when the
absolute base is small relative to the line's own typical magnitude.

**Choose the threshold and document the reasoning in the spec** — don't pick a number
silently. State what it is, why, and what it does to the real corpus (how many cells change
from a number to `n/m`, per company).

This is a foundation item: item 1 depends on it.

### Resolution (implemented 2026-08-09)

**Threshold: 10% of the row's own median absolute value**, computed across every period
currently in that row (a property of THIS line for THIS company, not a global constant —
matches "the line's own typical magnitude" literally).

**Why 10%, not something else:** chosen by checking this spec's own cited example against
the real corpus, not picked blind. Micron's real free cash flow went from $72M to $3,022M
(`+4097.2%`, exactly reproducing the number quoted above) — $72M against Micron's real FCF
median ($786M) is a ratio of 0.092. A 5% threshold would **miss** this spec's own example;
10% is the smallest round number that catches it. Checked three candidates against the full
real corpus (all three companies, both cash flow and income statement, every quarterly
period) before choosing:

| Threshold | Cells flipped from a number to `n/m` |
|---|---|
| 5% | 55 (misses the review's own example) |
| **10% (chosen)** | **72** |
| 15% | 79 (7 more than 10%, diminishing returns) |

**Per company at 10%:** AMZN 23, NVDA 15, MU 34 — 72 total.

**By reason, at 10%:** 49 sign-crossing (independent of the threshold — a company's own
sign either flips between two periods or it doesn't), 23 near-zero-base, 0 exactly-zero-base
(a zero base already yields no `growth_pct` at all — division by zero — so there is nothing
to "flip"; those cells were blank before this item and remain blank, now for a stated reason
`n/m` rather than an unstated one).

**Sign-crossing definition:** flagged only when both the prior and current values are
non-zero and differ in sign. A move TO exactly zero (`100 -> 0`, a clean `-100%`) is
meaningful and is not flagged — only a genuine crossing (`72 -> -113`) is.

Implementation: `dashboard/data.py`'s `_classify_growth`/`_GROWTH_NEAR_ZERO_BASE_FRACTION`,
applied inside `_finalize_statement_row` so both sequential and (once item 1 lands) YoY
growth get the same treatment from one place, not two.

---

## Item 3 — Micron's interest expense of exactly 0 (D15)

Micron's Q3 FY26 income statement reads `Interest expense: 0`.

Micron carries billions in debt. Exactly zero for a quarter is almost certainly capitalised
interest or a tagging artifact, not a fact about the world. This project's own rule is that
zero and missing are different facts — a zero that should be NULL is that same error
inverted.

Diagnose it. Check whether the filed tag genuinely reports 0, whether a resolution step
produced it, and whether it recurs across periods or companies. **Report before fixing** —
if Micron really did tag zero, the display is correct and the finding is about the filing,
not the code.

---

## Item 4 — Derive gross profit where its components exist

Amazon's gross profit is blank across every period, but revenue and cost of goods sold are
both present, and the metrics layer already derives gross margin from them (51.82%, shown on
the Overview). The dashboard computes the ratio while showing nothing for the quantity it
comes from.

Derive gross profit as `revenue − cost of goods sold` where both are present and gross
profit is not filed. Same derived-cell marking as everything else.

**Strict limit:** this applies only to lines that are exact arithmetic on lines already
present. R&D is not derivable and must stay blank. Do not extend this to any line requiring
a judgement about what belongs in it. If you find other lines that qualify under that test,
list them and ask before adding them.

---

## Item 5 — Complete the cash flow statement

The current five lines are a summary, not a statement. Add the three-section structure:

- **Operating** — working-capital movements (receivables, inventory, payables), deferred
  taxes, other non-cash adjustments, and the operating subtotal.
- **Investing** — acquisitions, purchases and maturities of marketable securities, purchases
  of equity investments, and the investing subtotal.
- **Financing** — share repurchases, dividends paid, debt issued, debt repaid, finance-lease
  principal payments, and the financing subtotal.
- **Reconciliation** — effect of FX, net change in cash, beginning and ending cash.

The financing section matters most: right now you cannot tell whether any of these companies
repurchased a single share. Investing matters second — Amazon's Anthropic and OpenAI
purchases flow through it, and the dashboard currently shows their balance-sheet consequence
and none of the cash movement.

Report per-company coverage before building the display: for each proposed line, how many
periods actually resolve. A line that is blank for all three companies isn't worth a row.

Discrete-quarter derivation applies to these exactly as it does to the existing five.

### Resolution (implemented 2026-08-09)

**No line is blank for all three companies — all 15 kept.** Coverage, quarterly basis, real
corpus (`n` periods with a resolved value / periods on the statement):

| Line | Section | AMZN | NVDA | MU |
|---|---|---|---|---|
| Change in receivables | Operating | 0/25 | 21/24 | 35/37 |
| Change in inventory | Operating | 24/25 | 21/24 | 35/37 |
| Change in payables | Operating | 24/25 | 21/24 | 35/37 |
| Deferred income tax | Operating | 24/25 | 21/24 | 0/37 |
| Other non-cash adjustments | Operating | 24/25 | 21/24 | 0/37 |
| Acquisitions, net of cash acquired | Investing | 19/25 | 21/24 | 0/37 |
| Purchases of investments | Investing | 24/25 | 5/24 | 12/37 |
| Maturities/sales of investments | Investing | 24/25 | 21/24 | 24/37 |
| Share repurchases | Financing | 13/25 | 21/24 | 34/37 |
| Dividends paid | Financing | 0/25 | 23/24 | 23/37 |
| Debt issued | Financing | 24/25 | 0/24 | 28/37 |
| Debt repaid | Financing | 24/25 | 0/24 | 1/37 |
| Finance lease principal paid | Financing | 24/25 | 0/24 | 25/37 |
| Effect of exchange rates on cash | Reconciliation | 7/25 | 0/24 | 35/37 |
| Net change in cash | Reconciliation | 24/25 | 21/24 | 35/37 |

Every zero/near-zero cell was individually checked against the real filing data, not assumed
to be a gap:

- **AMZN receivables (0/25), dividends (0/25).** AMZN stopped separately tagging receivables
  changes after ~2013 (predates this project's analyzed window entirely) and has never paid a
  dividend. Both genuine facts about the company/filing, not bugs.
- **MU deferred tax, other non-cash, acquisitions (0/37 each).** MU does not tag any of these
  three concepts in the analyzed window (confirmed via `xbrl.ingest_company`'s own
  `unresolved` report, not inferred from the table).
- **NVDA debt issued, debt repaid, finance lease principal, FX effect (0/24 each).** NVDA does
  not separately tag quarterly-duration facts for any of these four in the analyzed window.
  Checked specifically for alternate tags before concluding this (see "aliases tried and
  rejected" below) — genuinely absent, not a naming miss.

**"Purchases of equity investments" is not its own line.** Amazon's Anthropic/OpenAI stakes
were checked specifically (the review's own example) and have no separate "equity method
investment" cash-flow tag — they roll into the generic "Purchases of investments" line,
which is the best available granularity for the company the review asked about. A narrower
line would have nothing to be narrower than for AMZN specifically.

**"Beginning and ending cash" was not added as a new line.** It is the SAME instant fact the
balance sheet's own "Cash and cash equivalents" row already carries (a period's ending cash
IS the next period's beginning cash) — not a new duration concept to curate.

**Two aliases tried and rejected**, found live via `validate` category 6 (alias agreement)
against the real corpus, not assumed to be safe because both resolved data:

- `PaymentsToAcquireAvailableForSaleSecuritiesDebt` (tried for `investment_purchases`,
  needed for NVDA's coverage) disagrees with `PaymentsToAcquireMarketableSecurities` by
  40-470% for Amazon, which tags both — a narrower concept (debt securities only, excluding
  equity), not a synonym. Removed; NVDA's coverage for this line dropped from 20/24 to 5/24
  as a direct, honest consequence.
- `RepaymentsOfDebt` (tried for `debt_repaid`, same reason) disagrees with
  `RepaymentsOfLongTermDebt` by 160-2400% for Amazon — a BROADER concept (evidently includes
  short-term/commercial-paper repayments). Removed; NVDA's coverage dropped from data-bearing
  to 0/24.
- `CashAndCashEquivalentsPeriodIncreaseDecrease` (tried for `net_change_in_cash`) disagrees
  with the primary (post-ASU-2016-18, restricted-cash-inclusive) tag by 5-500% for Amazon and
  Micron. Removed; no coverage impact (the primary tag already covers both companies fully).

One small (2.8-3.3%), old (2016-2017) disagreement was registered as an
`ALIAS_AGREEMENT_EXCEPTIONS` entry instead of removed — `investment_maturities`'s two aliases,
same Amazon tag-transition era already on record for `capex`'s own exception, not
investigated further given that precedent.

**A real bug found and fixed while investigating the alias conflicts**: the discrete-quarter
plausibility floor (decision log #67) was nulling Q1's DIRECT pass-through of a filed fact,
not just genuinely derived subtraction results -- Amazon really filed a negative Q1 2025
acquisitions figure (-$48M, likely a purchase-price adjustment on a prior deal), and the gate
was silently suppressing it because a positive-only floor is the norm for "Payments" concepts,
not a guarantee. Fixed: the floor now only applies to `fp != "Q1"`. Regression test added
(`test_discrete_quarter_plausibility_gate_never_applies_to_q1s_direct_pass_through`). The
real value is now shown and documented in `RANGE_EXCEPTIONS`, per this project's own D15 rule
(if the filing really says so, the display is correct).

---

## Item 6 — EPS and share counts on the income statement

`EarningsPerShareDiluted` and `WeightedAverageNumberOfDilutedSharesOutstanding` are already
in `xbrl_facts` — the observations layer cites diluted EPS growth — but neither appears on
the income statement, and share count appears nowhere.

Add basic EPS, diluted EPS, basic shares and diluted shares.

Note these are **not** dollar-millions. They need their own units and precision: EPS to two
decimals in dollars, share counts in millions. Do not let them inherit the `$m` column
label.

Without share count there is no way to see dilution, no way to see the effect of buybacks,
and no per-share figure of any kind. For equity research this is the most conspicuous
remaining gap.

---

## Item 7 — Balance sheet completeness

Missing and not derivable from what's shown: **total liabilities**, goodwill, intangibles,
retained earnings, short-term debt and current portion of long-term debt, operating lease
liabilities.

Total liabilities is the one that stings — the sheet shows total assets and total equity but
not the number between them.

Same reporting requirement as item 5: per-company coverage before building, and drop any
line that resolves for nobody.

---

## When you're done

Report once, covering:

- What landed, item by item.
- What you verified and **how** — machine checks by name, and which environment they ran in.
- `validate` output across all categories.
- Anything you found that isn't in this spec.
- Anything you chose not to do, and why.

Do not proceed to render-layer work. That's the next batch, and it needs a human looking at
a browser rather than a test suite.
