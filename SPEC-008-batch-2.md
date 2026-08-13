# Batch 2 — statement structure and a key-metrics tab

**Date:** 2026-08-11
**Depends on:** batch 1 (complete)
**Scope:** Line ordering, statement structure, and derived per-share/cash-flow metrics.
Still data-layer and config. No styling — the visual section separation stays in the render
batch.

Standing constraints from batch 1 all still apply: no paid calls, no prompt version bumps,
one commit per item, each item ships with its test, state the environment.

---

## Item 1 — Rebuild the cash flow statement in traditional order

The statement currently opens with **Cash from operations** — the operating section's
result — sitting above the items that produce it. Everything after is a flat list with no
ordering logic.

Rebuild it in the order a filed cash flow statement actually uses:

**Operating activities**
1. Net income *(the starting line — same canonical the income statement uses)*
2. Depreciation and amortisation
3. Stock-based compensation
4. Deferred income tax
5. Other non-cash adjustments
6. Change in receivables
7. Change in inventory
8. Change in payables
9. **Net cash provided by operating activities** *(subtotal)*

**Investing activities**
10. Capital expenditure
11. Acquisitions, net of cash acquired
12. Purchases of investments
13. Maturities and sales of investments
14. **Net cash used in investing activities** *(subtotal)*

**Financing activities**
15. Share repurchases
16. Dividends paid
17. Debt issued
18. Debt repaid
19. Finance lease principal paid
20. **Net cash provided by (used in) financing activities** *(subtotal)*

**Reconciliation**
21. Effect of exchange rates on cash
22. Net change in cash
23. Cash at beginning of period
24. Cash at end of period

Notes:

- **Remove Free cash flow from this statement.** It is not a GAAP line — it's a derived
  metric, and it moves to the key-metrics tab in item 2. Its presence here was a
  placement compromise, not a considered choice.
- **Net cash from financing** may already be missing — check. Only *net cash used in
  investing* was visible in the browser.
- **Cash at beginning / end of period** are new. Check coverage across all three companies
  before adding; if a company doesn't tag them, they follow the usual blank-with-reason rule.
- Sign convention: report what the company filed. Do not flip signs to make a section
  "read better" — an outflow filed as a positive number stays positive, with the label
  carrying the meaning as it does in the filing.
- Subtotals stay resolved as they are now (filed, or discrete-subtraction). Do not
  reintroduce the sum-the-section fallback that was correctly rejected — it reconstructed
  Micron's investing total 0 times out of 7.

### Resolution (implemented 2026-08-13)

`CASH_FLOW_LINES` rebuilt in the traditional order above. `cfo` moves from position 1 to
the operating section's own closing subtotal, relabelled "Net cash provided by operating
activities" — same canonical, same filed/discrete resolution, only position and label
change. `net_income` opens the section, reusing the income statement's own canonical and
`net_income_discrete` fallback (no new registry work). `net_cash_financing` was already
present (added in the render-batch follow-up, not missing as the spec's note suspected it
might be) — only its label changed, to "Net cash provided by (used in) financing
activities". `free_cash_flow` removed from this statement entirely; it moves to the key
metrics tab in item 2.

**Cash at beginning/end of period**, both new. "End" reuses the balance sheet's own `cash`
canonical directly (the same instant fact, at this column's period_end — no new concept).
"Beginning" has no filed concept of its own to alias; it's patched from the SAME `cash`
canonical read one calendar day before this column's period_start
(`_derive_cash_beginning_from_prior_instant`). Checked against the real corpus before
writing this: a duration fact's `start` date is consistently one day after the prior
instant's own `end` date, confirmed for all three companies (AMZN, NVDA, MU each checked
across six recent periods) — an XBRL context-boundary convention, not a fiscal-calendar
guess. Neither row carries `is_derived_quarter`: no arithmetic runs on the value, it's the
filer's own number at the instant XBRL itself uses to represent it.

**Coverage** (quarterly basis, full history): both new lines resolve 25/25 (AMZN), 24/24
(NVDA), 37/37 (MU) — full coverage, nothing to drop.

**Found, not asked for**: beginning/ending cash use the SAME narrow concept the balance
sheet's `cash` line always has (`CashAndCashEquivalentsAtCarryingValue`), while
`net_change_in_cash` uses the broader restricted-cash-inclusive concept (established in
batch 1 item 5). For AMZN, which carries some restricted cash, `ending − beginning` and
`net_change_in_cash` differ by roughly 1% in spot-checked quarters — each row is exactly
what its own concept reports, they just don't perfectly reconcile against each other. Not
a bug in either row; noted rather than silently invisible.

Sign convention unchanged throughout — every value is exactly what `xbrl_facts` holds, no
sign flips anywhere in this resolution path.

---

## Item 2 — "EPS & shares" becomes a key-metrics tab

Rename and widen the tab. It currently holds four lines; it should hold the figures an
analyst wants that aren't line items on any of the three statements.

Name it whatever reads best — **Key metrics** is the obvious candidate.

**Contents, in this order:**

*Per share*
- Basic EPS
- Diluted EPS
- Basic shares outstanding
- Diluted shares outstanding

*Cash flow*
- Free cash flow — `CFO − capex`
- Free cash flow to the firm (FCFF)
- Free cash flow to equity (FCFE)

*Propose, don't assume* — a shortlist of other candidates worth including, with real-corpus
coverage for each before any is added. Likely: net debt, working capital, EBITDA, effective
tax rate. Report coverage and let me choose; don't add them silently.

### The FCFF caveat — read this before implementing

**FCF is arithmetic. FCFF is an assumption.** They must not be presented as equally solid.

```
FCF   = CFO − capex
FCFF  = CFO + interest expense × (1 − effective tax rate) − capex
FCFE  = CFO − capex + (debt issued − debt repaid)
```

FCF and FCFE are exact arithmetic on filed lines — same status as gross profit in batch 1's
item 4.

FCFF is not. It needs an **effective tax rate**, which this project does not have as a filed
fact and must construct as `income tax expense / pre-tax income`. That construction breaks
in real cases already in the corpus:

- **AMZN FY2022:** pre-tax income −5,936, income tax expense −3,217. An effective rate here
  is not meaningful.
- **MU FY2023:** pre-tax income −5,658, income tax expense 177 — an effective rate of −3.1%,
  making `(1 − t)` equal 1.031, so the formula would add back *more* than the interest
  expense.
- Any period where pre-tax income is at or near zero produces an unstable or absurd rate.

#### Which rate — checked against practice, not assumed

Two searches of valuation practice settled this (see Sources at the end of this section):

- **For historical figures, the period's own effective rate is standard.** Damodaran:
  *"when computing free cash flows for the past, it makes sense to leave the effective tax
  rate at its actual level."* Marginal rates are a forecasting device, not a reporting one.
  This dashboard reports history, so the effective rate is right.
- **Quarterly is the exception.** GAAP *requires* discrete items to be excluded from the
  annual effective rate and recognised in full in the quarter they arise, so a quarterly
  ETR is contaminated by construction. Research on analyst forecasting finds nearly 90% of
  analyst forecasts deviate from management ETR estimates that include discrete items, and
  that analysts disentangle those effects before use. Trailing-twelve-month is the standard
  smoothing. Amazon's **$15.9B discrete tax expense** tied to the Anthropic revaluation is
  precisely this problem inside the current corpus.

**Therefore, split by basis:**

| Basis | Tax rate used |
|---|---|
| Annual | That fiscal year's own effective rate |
| Quarterly | **Trailing-twelve-month** effective rate — `sum(tax expense, 4q) / sum(pre-tax income, 4q)` |

TTM also removes several failure cases by dilution, since one loss-making quarter is
outweighed by three profitable ones. It does not rescue a loss-making *year* — MU FY2023
stays unavailable — so the fail-closed rule still applies, with less to catch.

TTM needs four consecutive quarters. Where fewer exist (the earliest periods in the corpus),
FCFF is unavailable with a stated reason rather than falling back to a shorter window.

Requirements:

1. **Fail closed.** If the applicable pre-tax income (the year's, or the TTM sum) is negative
   or near zero by the same near-zero test batch 1 item 2 established, FCFF is unavailable
   with a stated reason. Do not substitute a statutory rate or clamp the rate into a
   `[0%, statutory]` band — some practitioners do, but this project refuses rather than
   invents.
2. **Mark FCFF distinctly from FCF and FCFE.** The existing derived marker says "this
   project computed it from filed figures". FCFF additionally rests on a constructed rate.
   That difference should be visible — a separate marker and footnote naming the tax-rate
   assumption.
3. **Record the formula and the rate** in the same way every other derived figure records
   its inputs, so a wrong number is diagnosable.

4. **Show the rate.** Add the effective tax rate used as its own row in this tab, labelled
   with which basis produced it (year's own, or TTM). A rate that drives a displayed number
   should be inspectable rather than buried.

Report FCFF's real coverage before building the display. If it fails closed in most periods
for most companies, say so — a metric that's unavailable two-thirds of the time may not
earn its row, and that's worth knowing before it's built rather than after.

Sources for the rate decision:
- Damodaran, *More on effective tax rates* — https://pages.stern.nyu.edu/~adamodar/New_Home_Page/valquestions/taxrate.htm
- Damodaran, *From Earnings to Cash Flows* (ch. 10) — https://pages.stern.nyu.edu/~adamodar/pdfiles/valn2ed/Print.pdf
- Bratten, Gleason, Larocque, Mills, *Forecasting Taxes: New Evidence from Analysts* — https://www.kellogg.northwestern.edu/~/media/Files/Departments/Accounting/Larocque%20Paper.ashx

---

## What is explicitly not in this batch

Visual section separation for the cash flow statement. The statement will still render as a
flat list after this batch — correctly ordered, but without headers or grouping. That is the
render batch's first item, and it needs a browser rather than a test suite.
