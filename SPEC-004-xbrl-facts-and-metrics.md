# SPEC-004 — XBRL Facts and Financial Metrics

**Version:** 2.4
**For:** Claude Code
**Depends on:** SPEC-003 (complete, commit `0cb1767`)
**Reference:** `ARCHITECTURE.md` — sections 2, 6, 7
**Estimated effort:** 8–11 hours

**Supersedes** the earlier draft SPEC-004 (10 metrics, hand-coded). This version uses a
declarative metric registry and covers 32 metrics including Beneish M-score and DuPont
decomposition. Delete the old file if present.

**Changelog**
- v2.4 — Closing two residuals from v2.3's live validation, with `validate` exiting 0 in
  the healthy state as a result. R8 category 4's 4 AMZN debt reconciliation findings and
  category 1's 2 remaining range findings are each real, checked, explained values, not
  bugs — registered in two new exceptions registers (`DEBT_RECONCILIATION_EXCEPTIONS`,
  `RANGE_EXCEPTIONS`), following the same pattern as `ALIAS_AGREEMENT_EXCEPTIONS`: named
  entries with a written reason, reported informationally, never a widened threshold. One
  self-correction along the way: `incremental_gross_margin`'s remaining violation was
  previously diagnosed as a denominator artifact; checked directly, it isn't (§R6a
  updated accordingly) — it's NVIDIA's real Q2 FY2023 write-down.
- v2.3 — Category-6 (alias agreement) findings from live validation, resolved. `dep_amort`
  split from `depreciation` (DD&A vs pure depreciation are different quantities);
  `interest_expense_debt` split out of `interest_expense`; `equity_including_nci` and
  `net_income_including_nci` split out of `equity`/`net_income` (mixing parent-level and
  NCI-inclusive figures across numerator/denominator was a latent ROE bug DuPont's own
  reconciliation could not have caught, since it uses the same two mismatched inputs
  consistently); `AllocatedShareBasedCompensationExpense` removed from `sbc`. A new
  alias-agreement exceptions register holds the one disagreement kept as an alias
  (`capex`'s 2017 AMZN transition), justified by the AC9 hand-verification. R9's NULL
  rule refined: absence means zero for an additive component of a total the filer is
  required to disclose (finance leases under ASC 842), not for a primary measure —
  `total_debt` now computes for NVIDIA. Four plausible ranges widened; one guard
  (`incremental_gross_margin`'s revenue-delta floor) raised instead. AC16 relaxed to 6MB.
- v2.2 — Post-implementation findings from live execution, resolved. `ppe_net` gets a
  second, broader canonical input (`ppe_and_lease_net`) rather than a fallback alias —
  Micron folded finance-lease ROU assets into its PP&E line from FY2021. `total_debt`
  now adds finance lease liabilities explicitly (Micron's real "Long-term debt" balance
  sheet line is borrowings *plus* finance leases, not borrowings alone). Operating lease
  liabilities added as their own canonical inputs, stored but unused. Annual/quarterly
  metrics restricted to real `filings.period_end` values — computing metrics for every
  365-day-duration fact produced phantom periods (Amazon's 10-K discloses an implicit,
  directly-tagged Q4 for several concepts, which is not a genuine fiscal-year-end).
  R8 category 5 redesigned (was flagging the benign Q4 case as a hard failure); two new
  R8 categories added (alias agreement, unverified YoY drift). §7's Q4 note corrected.
- v2.1 — Pre-implementation live verification against real `companyfacts` for all three
  companies (findings folded into R1 below). Per-period alias resolution made an explicit
  requirement (R1). Debt registry restructured: `total_debt` added as its own canonical
  input, `LongTermDebt` removed from `debt_noncurrent`'s aliases (R1). `receivables`
  trimmed to a single alias (R1). R8 gains a debt reconciliation check. New acceptance
  criterion for a Micron `net_debt` hand-verification. R7 gains disclosure-line
  requirements for the DEPI and TATA substitutions, matching how AQI's omission is
  already handled.

---

## Objective

Ingest structured financial data from SEC's XBRL `companyfacts` API into `xbrl_facts`,
then compute 32 financial metrics into `metrics`, each recording its own formula and
input values.

This is the **Facts → Calculations** half of the architecture. No AI, nothing
non-deterministic. Same inputs must always produce the same outputs.

---

## Design Principle: declarative, not hand-coded

Metrics are declared as data in `config.py`, not written as individual functions.
`metrics.py` is a small engine plus a library of primitives.

Adding metric #33 must be a config entry, not new code. If it requires a new function,
the design is wrong.

A handful of metrics (Beneish, DuPont components) are too complex for a primitive and
may name a dedicated function — but those are exceptions, and each must be justified in
a comment.

---

## Scope

**In scope**

1. Concept registry with ordered aliases
2. Metric registry — declarative definitions
3. `xbrl.py` — fetch and normalise `companyfacts`
4. Period classification by computed duration
5. Restatement selection
6. `metrics.py` — computation engine and primitives
7. Validation command with internal consistency checks
8. CLI, tests

**Out of scope:** readability (SPEC-005), observations (SPEC-005), any LLM call, dashboard,
MD&A, market data, GitHub Actions, derived Q4 figures.

---

## Requirements

### R1 — Concept registry

`companyfacts` returns tens of thousands of facts per company. Storing all of them would
add ~20 MB to `app.db` and undo SPEC-003. **Ingest only the concepts below.**

Each canonical input maps to an **ordered** alias list; the first that resolves wins.

| Canonical input | Aliases, in priority order | Unit |
|---|---|---|
| `revenue` | `RevenueFromContractWithCustomerExcludingAssessedTax`, `Revenues`, `RevenueFromContractWithCustomerIncludingAssessedTax`, `SalesRevenueNet` | USD |
| `cogs` | `CostOfGoodsAndServicesSold`, `CostOfRevenue`, `CostOfGoodsSold` | USD |
| `gross_profit` | `GrossProfit` | USD |
| `sga_expense` | `SellingGeneralAndAdministrativeExpense`, `GeneralAndAdministrativeExpense` | USD |
| `rnd_expense` | `ResearchAndDevelopmentExpense` | USD |
| `operating_income` | `OperatingIncomeLoss` | USD |
| `pretax_income` | `IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest`, `IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments` | USD |
| `tax_expense` | `IncomeTaxExpenseBenefit` | USD |
| `net_income` | `NetIncomeLoss` | USD |
| `net_income_including_nci` | `ProfitLoss` — separate canonical input, not an alias (R1f); stored, not consumed by any metric | USD |
| `eps_diluted` | `EarningsPerShareDiluted` | USD/shares |
| `diluted_shares` | `WeightedAverageNumberOfDilutedSharesOutstanding` | shares |
| `total_assets` | `Assets` | USD |
| `current_assets` | `AssetsCurrent` | USD |
| `current_liabilities` | `LiabilitiesCurrent` | USD |
| `equity` | `StockholdersEquity` | USD |
| `equity_including_nci` | `StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest` — separate canonical input, not an alias (R1f); stored, not consumed by any metric | USD |
| `cash` | `CashAndCashEquivalentsAtCarryingValue` | USD |
| `short_term_investments` | `ShortTermInvestments`, `MarketableSecuritiesCurrent`, `AvailableForSaleSecuritiesDebtSecuritiesCurrent` | USD |
| `inventory` | `InventoryNet` | USD |
| `receivables` | `AccountsReceivableNetCurrent` | USD |
| `payables` | `AccountsPayableCurrent`, `AccountsPayableAndAccruedLiabilitiesCurrent` | USD |
| `ppe_net` | `PropertyPlantAndEquipmentNet` | USD |
| `ppe_and_lease_net` | `PropertyPlantAndEquipmentAndFinanceLeaseRightOfUseAssetAfterAccumulatedDepreciationAndAmortization` — a separate, broader canonical input, **not an alias of `ppe_net`** (R1d) | USD |
| `ppe_gross` | `PropertyPlantAndEquipmentGross` | USD |
| `debt_noncurrent` | `LongTermDebtNoncurrent` | USD |
| `debt_current` | `LongTermDebtCurrent`, `DebtCurrent` | USD |
| `finance_lease_liability_noncurrent` | `FinanceLeaseLiabilityNoncurrent` | USD |
| `finance_lease_liability_current` | `FinanceLeaseLiabilityCurrent` | USD |
| `operating_lease_liability_noncurrent` | `OperatingLeaseLiabilityNoncurrent` — stored, not consumed by any metric yet (R1e) | USD |
| `operating_lease_liability_current` | `OperatingLeaseLiabilityCurrent` — stored, not consumed by any metric yet (R1e) | USD |
| `total_debt` | Computed: borrowings (`LongTermDebt`/`DebtLongtermAndShorttermCombinedAmount`, else `debt_noncurrent + debt_current`) **+ `finance_lease_liability_noncurrent` + `finance_lease_liability_current`** (R1b) | USD |
| `interest_expense` | `InterestExpense`, `InterestExpenseNonoperating` | USD |
| `interest_expense_debt` | `InterestExpenseDebt` — separate canonical input, not an alias (R1f); stored, not consumed by any metric | USD |
| `dep_amort` | `DepreciationDepletionAndAmortization`, `DepreciationAmortizationAndAccretionNet` (DD&A, for EBITDA) | USD |
| `depreciation` | `Depreciation` — separate canonical input, not an alias of `dep_amort` (R1f); pure depreciation, used by `capex_to_depreciation`, `depreciation_rate`, and Beneish `DEPI`, each falling back to `dep_amort` where untagged | USD |
| `sbc` | `ShareBasedCompensation` | USD |
| `cfo` | `NetCashProvidedByUsedInOperatingActivities`, `NetCashProvidedByUsedInOperatingActivitiesContinuingOperations` | USD |
| `capex` | `PaymentsToAcquirePropertyPlantAndEquipment`, `PaymentsToAcquireProductiveAssets` — a documented alias-agreement exception, not a synonym pair (R1g) | USD |

**Verified against live `companyfacts` for all three companies** (2026-07-26). Findings:

- Amazon's `GrossProfit` claim needed correcting: Amazon **did** tag `GrossProfit`, but
  only in filings covering fiscal years 2007–2008 (filed 2009–2010); zero entries with
  `fy >= 2018`. It is not that Amazon never used the tag — it stopped, sixteen years ago.
  This is precisely why **resolution must be per-period, not per-company** (see the
  requirement below): if `gross_profit` were resolved once for Amazon based on whether
  the concept exists *anywhere* in its history, the stale 2007–2008 tag would make it
  "resolve," and every modern period would then read a NULL `gross_profit` instead of
  falling through to the revenue−cogs computation in `gross_margin` — silently breaking
  the exact fallback path this spec exists to exercise (AC7).
- `rnd_expense` resolves to nothing for Amazon specifically (confirmed absent across all
  542 of its `us-gaap` concepts, not merely mistagged) — Amazon does not disclose R&D as
  a separate XBRL line. Resolves for NVDA and MU, so AC2 is satisfied; `rnd_intensity`
  will be permanently NULL for Amazon, which is correct, not a bug.
- The original `debt_noncurrent` and `receivables` alias lists each paired a canonical
  input with a second "alias" that turned out to be a **different accounting quantity**,
  not the same fact under a different tag — see the restructuring below and
  `ARCHITECTURE.md`'s new alias-purity rule. Every other alias pairing in this table was
  confirmed to be genuinely the same fact (e.g. Amazon/Micron dual-tagging the same
  pretax-income figure under two element names during a 2021 transition — see R5).
- All other canonical inputs resolve as expected for all three companies. `EarningsPerShareDiluted`
  and `WeightedAverageNumberOfDilutedSharesOutstanding` confirmed tagged in units
  `USD/shares` and `shares` respectively, matching this table exactly.

### R1a — Resolution is per period, never per company

**Resolution happens independently for each `(cik, period_start, period_end)` a metric
needs, never once for a company's whole history.** For a given canonical input and a
given period, try each alias in order and use the first one that has a value *for that
specific period*; which alias won for an earlier or later period is irrelevant.

Getting this wrong is not a hypothetical: see the Amazon `GrossProfit` finding above. A
company-wide "does this company ever use this tag" resolution is a different, wrong
algorithm that happens to look correct in the common case and fails silently in exactly
the cases an alias list exists to handle.

This also means concept drift (R5) is a real per-period signal, not just a name for a
hypothetical: the resolved alias for one input can differ period-to-period for the same
company even when both aliases have data across overlapping periods (see the NVIDIA
`pretax_income` case in R5's testing note).

### R1b — Debt: `LongTermDebt` is a total, not the noncurrent portion

Removed from `debt_noncurrent`'s aliases. In the US-GAAP taxonomy, and confirmed against
live data for all three companies, `LongTermDebt` is a computed total —
`LongTermDebt = LongTermDebtNoncurrent + LongTermDebtCurrent` matches exactly across
every period where a company tags all three (checked against 12 Micron periods, exact
match every time; also holds for NVIDIA). Using it as a fallback for `debt_noncurrent`
means that when `debt_noncurrent + debt_current` is summed downstream (`net_debt`,
`invested_capital`, Beneish `LVGI`), the current portion is counted twice for any company
whose `LongTermDebtNoncurrent` has gone stale and falls back to `LongTermDebt` — which is
exactly what happens to Micron in recent fiscal years (`LongTermDebtNoncurrent` has no
entries after FY2013; recent periods only tag `LongTermDebt` and, separately, `DebtCurrent`).

`total_debt` is the new canonical input every downstream consumer must use instead of
summing `debt_noncurrent + debt_current` directly. It prefers a genuinely combined tag
(`LongTermDebt`, or `DebtLongtermAndShorttermCombinedAmount` if a company ever uses that
element instead) for the period; only falls back to summing the two components when
neither combined tag resolves for that period. `debt_noncurrent` and `debt_current`
remain in the registry as their own facts (still worth ingesting and displaying), but
`net_debt`, `invested_capital`, and Beneish `LVGI` must all be rewritten to consume
`total_debt`, never to sum the components themselves — see R6 and R7.

Note also (not a bug, just confirmed while checking this): Amazon's `LongTermDebt` is
*not* exactly equal to `LongTermDebtNoncurrent + LongTermDebtCurrent` — it runs
consistently ~0.6% higher across 55 checked periods (likely unamortized discount/issuance
costs folded into the combined total but not the split lines). This is additional reason
to prefer the combined tag when present rather than always summing components: summing
would *understate* Amazon's total debt slightly, not just risk double-counting Micron's.

**Update: `total_debt` must also add finance lease liabilities.** Hand-verifying `net_debt`
against Micron's actual FY2025 balance sheet (post-implementation) found the "Long-term
debt" line ($14,017M) is not `LongTermDebt` alone ($11,533M) — it is `LongTermDebt` +
`FinanceLeaseLiabilityNoncurrent` ($2,484M), an entirely separate concept never in the
registry. Micron's own Debt note carries a combined total row (principal, current,
long-term, and total net carrying amount across *all* instruments including finance
leases) that reconciles exactly: borrowings ($11,533M) + finance lease noncurrent
($2,484M) + finance lease current ($560M) = $14,577M, matching the note's own total.
`total_debt` is now:

```
total_debt = borrowings + finance_lease_liability_noncurrent + finance_lease_liability_current
```

where `borrowings` is exactly the combined-tag-preferred logic above (unchanged) and
**required** — `total_debt` is NULL if `borrowings` doesn't resolve. The finance lease
components are different: **superseded by R1h below.** An earlier version of this
requirement required both finance lease components to resolve too, or `total_debt` was
NULL — applying R9's "absence is not zero" literally. In practice this made `total_debt`
permanently NULL for NVIDIA, which discloses only operating leases
(`OperatingLeaseLiability{Noncurrent,Current}` are tagged;
`FinanceLeaseLiability{Noncurrent,Current}` never are — confirmed, not a gap in
ingestion). R1h refines this: finance lease liabilities are a required-disclosure
additive component under ASC 842, so an absent one is treated as $0, not NULL —
`total_debt` now computes for NVIDIA as `borrowings + 0 + 0`, with `formula` recording
that the zero was assumed, not observed.

### R1c — Receivables: `ReceivablesNetCurrent` is not a synonym

Removed from `receivables`'s aliases. Confirmed against 8 recent Micron periods where
Micron tags both `AccountsReceivableNetCurrent` and `ReceivablesNetCurrent`
*simultaneously* (not a tag transition — both appear across the same 15+ year span): the
values never match, and `ReceivablesNetCurrent` is consistently larger (e.g. $26.9B vs.
$31.0B at one recent quarter-end), evidently including receivables categories
`AccountsReceivableNetCurrent` excludes. Treating it as a fallback alias would have been
low-risk in practice (Micron's `AccountsReceivableNetCurrent` never actually goes
missing, so the fallback would never have fired) but the alias list should not model a
broader quantity as if it were a synonym for a narrower one, per the new
`ARCHITECTURE.md` rule. `receivables` now has a single alias; a company that only tags
`ReceivablesNetCurrent` produces NULL for `receivables`, correctly.

### R1d — PP&E: a second canonical input, not a fallback alias

Micron's `PropertyPlantAndEquipmentNet` has no entries after mid-2020; from FY2021 it
tags `PropertyPlantAndEquipmentAndFinanceLeaseRightOfUseAssetAfterAccumulatedDepreciationAndAmortization`
instead — a **broader** measure (PP&E *plus* finance-lease right-of-use assets, net),
not the same fact renamed. Same shape of problem as `LongTermDebt`/`ReceivablesNetCurrent`:
this is a separate canonical input, `ppe_and_lease_net`, never a fallback alias of
`ppe_net`.

Every metric that consumes `ppe_net` (`fixed_asset_turnover`, `depreciation_rate`,
Beneish `AQI`, Beneish `DEPI`) must resolve through a shared primitive that tries the
pure measure first and falls back to the broader one only when the pure measure is
absent for that period, recording the substitution in `formula` — exactly the same
pattern `gross_margin` already uses for `gross_profit`/revenue−cogs, and
`depreciation_rate` already uses for `ppe_gross`/`ppe_net`. `depreciation_rate` in
particular now has three tiers: `ppe_gross` → `ppe_net` → `ppe_and_lease_net`.

### R1e — Operating lease liabilities: stored, not yet consumed

`operating_lease_liability_noncurrent` / `_current` are added to the registry so the
data is captured, but no metric reads them in this spec. They exist so that an
adjusted-leverage metric (total debt including operating leases, a common analyst
adjustment) is a future config entry, not a future re-ingest. Do not wire them into
`total_debt` or any other metric here.

### R1f — Four more splits, found by the alias-agreement check (R8 category 6)

Live validation surfaced 168 real disagreements once category 6 automated the
alias-purity rule across the whole registry. Four were the same shape of bug as
`LongTermDebt`/`ReceivablesNetCurrent`: a "fallback alias" that is actually a different,
broader accounting quantity, confirmed by consistent (not random) disagreement across
many periods. Each gets its own canonical input; the broader one is stored, unused by
any metric here (same pattern as R1d/R1e):

- **`dep_amort`** (DD&A) vs **`depreciation`** (`Depreciation` alone). Differ 20–40%
  across dozens of periods for all three companies — `DepreciationDepletionAndAmortization`
  includes amortization of intangibles and depletion that pure depreciation doesn't.
  `dep_amort` keeps its DD&A aliases (correct for EBITDA, which wants the full add-back).
  `depreciation` is new, for the metrics that specifically want the depreciation-only
  figure (`capex_to_depreciation`, `depreciation_rate`, Beneish `DEPI`) — each resolves
  `depreciation` first and falls back to `dep_amort` only where `depreciation` is
  untagged, disclosing the substitution in `formula`, exactly like `ppe_gross`'s existing
  fallback to `ppe_net`.
- **`interest_expense`** vs **`interest_expense_debt`**. NVIDIA periods differ up to
  100% (`InterestExpenseDebt` is sometimes exactly $0 while `InterestExpense` is not) —
  total interest expense is not the same fact as interest expense on debt specifically
  (it can include lease interest, for one). `InterestExpenseDebt` removed from
  `interest_expense`'s aliases; stored as its own input, unused by any metric here.
- **`equity`** vs **`equity_including_nci`**, **`net_income`** vs
  **`net_income_including_nci`**. `StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest`
  and `ProfitLoss` are NCI-inclusive; `StockholdersEquity` and `NetIncomeLoss` are
  parent-level. Mixing them across a ratio's numerator and denominator is a real
  correctness bug, not just an imprecise label — see R1h below for why DuPont's own
  reconciliation could never have caught it.
- **`sbc`**: `AllocatedShareBasedCompensationExpense` removed, no replacement added.
  Micron's real gap (~1.4–3.8%, consistent across periods) is economically explicable —
  some SBC is capitalized into inventory rather than expensed — but there's no metric in
  this spec that wants the "allocated to expense" figure specifically, so there's nothing
  to fall back to it for. If a future metric needs it, it gets its own canonical input
  then, not resurrection as an alias.

### R1g — Alias-agreement exceptions register

Not every disagreement found by category 6 is a broader/narrower split waiting to
happen. `capex`'s two aliases (`PaymentsToAcquirePropertyPlantAndEquipment`,
`PaymentsToAcquireProductiveAssets`) disagree by ~16% for Amazon in exactly one period
(2016-12-31, around Amazon's real tag transition — see R5's drift mechanism). Unlike the
R1f cases, this isn't a *consistent* pattern across many periods pointing at two
different quantities — it's a one-off, and `free_cash_flow` computed from
`PaymentsToAcquireProductiveAssets` was already hand-verified against Amazon's archived
FY2025 cash flow statement (AC9), confirming that alias is the right one to trust going
forward.

`config.py` gets `ALIAS_AGREEMENT_EXCEPTIONS`: a dict of canonical input → written
reason, checked by `validate` category 6. A canonical input in the register has its
disagreements reported informationally (with the reason shown), not as a hard failure.
**Anything category 6 finds that is not in the register still hard-fails.** The register
is for accepted, justified exceptions — a place to write down *why* an alias pair is
allowed to disagree, not a way to silence a finding without explaining it.

### R1h — NULL discipline refined: primary measures vs. required-disclosure components

R9 originally said "absence is not zero," full stop, and this spec applied that
literally to `total_debt`'s finance lease components — with the effect that NVIDIA's
`total_debt` (and everything built on it) became permanently NULL, because NVIDIA
discloses only operating leases. On reflection this over-applies the rule.

**Refined rule:** absence means *unknown* for a primary measure — the thing a metric is
actually trying to observe, where "not tagged" could mean "doesn't exist," "exists but
wasn't disclosed this way," or genuinely anything. Absence means *zero* for an additive
component of a total that the filer is **required** to disclose if it were non-zero.
Finance lease liabilities are the second kind: ASC 842 requires a lessee to recognize and
disclose finance lease liabilities if it has any. A company with finance leases will tag
`FinanceLeaseLiability{Noncurrent,Current}`; a company that never tags them has no
finance leases, not an unknown amount of them — the absence itself is the disclosure.

Applied to `total_debt`: `borrowings` remains a primary measure (absence is NULL,
unchanged) — a company's total debt cannot be assumed zero just because a tag is
missing. The two finance lease components are now treated as required-disclosure
additive components: `total_debt` computes whenever `borrowings` resolves, treating an
absent finance lease component as $0, and `formula` records explicitly whether leases
were included from real tags or assumed zero, so the distinction stays visible rather
than silently disappearing into a number. `validate` gains a category reporting every
company where the zero-assumption was applied (R8), so this is disclosed at the
portfolio level too, not just per-row.

This is a narrow, named exception to R9, not a reversal of it — it applies to finance
lease liabilities specifically because ASC 842 makes absence diagnostic. It should not
be read as license to assume zero for any other missing input; every other NULL rule in
this spec (interest expense, short-term investments, receivables, ...) is unchanged.

### R1i — Debt-reconciliation exceptions register

Same pattern as R1g, for R8 category 4. Amazon's `LongTermDebt` (combined tag) and
`debt_noncurrent + debt_current` (summed components) disagree ~1.1% for exactly four
periods: 2015-12-31 through 2016-09-30. Real cause, checked: ASU 2015-03 (effective for
Amazon in fiscal 2016) reclassified debt issuance costs from an asset to a
contra-liability, and the combined tag and the split components didn't finish reflecting
the reclassification on the same reporting date — a real accounting-standard transition,
not a tagging error, and not the double-counting bug R1b exists to catch (confirmed: the
gap is small, consistent with a reclassification timing difference, not the ~5–20%+ a
double-count or wrong-quantity alias produces).

`config.py` gets `DEBT_RECONCILIATION_EXCEPTIONS`: a dict of `(cik, period_end)` → written
reason, checked by `validate` category 4. Keyed per-period rather than per-company (unlike
R1g's canonical-input keying) because a debt reconciliation exception is about specific
transition periods for a specific company, not a standing property of an input — a future
Amazon debt reconciliation disagreement in an unrelated period must still hard-fail.

### R1j — Range exceptions register

Same pattern again, for R8 category 1. Two of the four range violations widened in R6a
resolved on their own once the bounds moved; two did not, because they were never guard-
or bound-fixable — they are real, individually-checked values:

- **NVIDIA `incremental_gross_margin`, 2022-07-31 (Q2 FY2023):** the gaming-GPU inventory
  write-down quarter. Gross profit fell $1.3B on $197M of revenue growth (2.94% of
  revenue — above even the raised 2% guard, so R6a's guard was never going to catch this
  one; **the original diagnosis that this was a denominator artifact was wrong**, and is
  corrected here rather than left standing).
- **Micron `effective_tax_rate`, fiscal Q2 2024 (period ended 2024-02-29):** pretax income
  of $170M against a $622M discrete tax *benefit* (net income $793M) — consistent with a
  deferred-tax valuation-allowance release as Micron returned to marginal profitability
  after the 2022–2023 memory downturn.

`config.py` gets `RANGE_EXCEPTIONS`: a dict of `(metric, cik, period_start, period_end)` →
written reason, checked by `validate` category 1. **Never a widened threshold** — the
whole point of this register is to keep the range meaningful by handling the individually-
checked, explained exception separately, rather than stretching the band to include it
(which would let the *next*, unchecked value of similar size through silently).

With both registers in place, `validate` exits 0 against the real database in the healthy
state: every remaining finding in categories 1 and 4 is either gone (after R6a's range
widening) or registered (R1i, R1j), and no category 1–6 finding is unregistered.

### R2 — Ingest (`xbrl.py`)

- Fetch `https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json` via `edgar_client`.
- Responses are several MB. Do not hold unnecessary copies in memory.
- Store only configured concepts, and only in the units declared above.
- Do **not** archive the raw JSON. It is free to re-fetch and changes as filings arrive,
  so it is neither immutable nor worth committing.

**Schema additions to `xbrl_facts`:**

- `duration_days INTEGER` — `period_end − period_start`; NULL for instant facts
- `filed_date TEXT` — the API's `filed` value

Both are observed values or pure arithmetic over observed values, so they belong in the
facts layer. Update `ARCHITECTURE.md` §6.

### R3 — Period classification

**The subtlest requirement here. Read carefully.**

A Q3 10-Q reports both a three-month and a nine-month figure ending on the same date,
and `fp` says `Q3` for both. Filtering on `fp` alone mixes quarterly and year-to-date
values into one series. The resulting chart looks plausible and is wrong.

Classify every duration fact from `duration_days`:

| Class | Range (days) |
|---|---|
| quarterly | 80–100 |
| half-year | 170–190 |
| three-quarter | 260–285 |
| annual | 350–380 |
| other | anything else — excluded from metrics |

Facts with no `period_start` are **instant** (balance sheet).

Annual metrics use only annual facts. Quarterly metrics use only quarterly facts.
**Never mix.** Thresholds live in `config.py`.

### R3a — Duration alone is not enough: restrict to real fiscal period ends

**Found post-implementation, real and material.** A 350–380 day duration is necessary
but not sufficient for "this is a genuine fiscal year." Amazon's 10-K directly tags an
*implicit Q4* — a real, company-disclosed three-month duration ending on the fiscal
year-end date — for revenue, operating income, net income, tax expense, and diluted EPS
(see the corrected `ARCHITECTURE.md` fiscal_period note). That fact's *end* date matches
the annual fact's *end* date exactly, so both a quarterly-classed and an annual-classed
duration fact exist for the same date — correctly kept separate by the engine (keyed on
the full `(period_start, period_end)` pair, never on `period_end` alone), but the
annual-classed one is not a real fiscal year, and computing a full sweep of metrics
against it produces phantom points that don't correspond to anything a chart should show.

**Fix:** `filings.period_end` is the authoritative set of real fiscal period ends —
it comes from the SEC submissions API, not from duration arithmetic. Restrict the
"annual" period set to `period_end` values that appear in a `filings` row with
`form_type = '10-K'` for that `cik`, and the "quarterly" set to `period_end` values from
`form_type = '10-Q'` rows. A duration fact whose class and end date don't correspond to
an actual filed 10-K/10-Q of that kind is excluded from metrics entirely, not computed
and discarded.

This is **not** a date floor — no history is excluded by age, only by not corresponding
to a real filed annual or quarterly period. Do not add a start-date bound here; that is a
distinct, deliberately separate future concern (see the LLM spend/analysis-bounding note
in Forward-Looking Concerns).

### R4 — Restatement selection

For a given `(cik, concept, unit, period_start, period_end)`, the value from the filing
with the latest `filed_date` wins. That is current best knowledge.

All values remain in `xbrl_facts`; selection happens at computation. The facts layer
records what was reported; the calculation layer decides what to use.

### R5 — Concept drift detection

If `revenue` resolves to `Revenues` in 2015 and to
`RevenueFromContractWithCustomerExcludingAssessedTax` in 2020, the series may not be
comparable across that boundary.

- Detect when the resolved alias for a canonical input **changes** across a company's
  time series.
- Report it in the `validate` command output with the period where it changed.
- Do not block computation. Flag it.

This is a real analyst concern, not a technical nicety: silently splicing two definitions
into one chart is exactly the class of error this project exists to avoid.

**Real fixture, already found: NVIDIA's `pretax_income` transition.** NVIDIA tagged
annual pretax income under
`IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments`
from FY2010 through FY2021 (last filed as current-year in the FY2021 10-K, filed
2021-02-26). Starting with the FY2022 10-K (filed 2022-03-18), NVIDIA switched to
`IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest`
— and that filing's FY2020 comparative was retroactively retagged under the new element
too (both tags carry identical values, e.g. $2,970,000,000 for FY2020, confirmed on both).
So the per-period resolved alias for `pretax_income` is the older tag for FY2010–FY2019
and the newer tag from FY2020 onward, with the drift boundary landing at the FY2022
filing. Use this as the real fixture for `test_concept_drift_detected` rather than a
synthetic one — it is exactly the scenario R5 exists to catch, with real values.

### R6 — Metric registry

Declarative in `config.py`. Each entry declares: name, canonical inputs, formula string,
basis (annual / quarterly / both), plausible range, and whether it needs a prior period.

**32 metrics:**

**Growth**
| Name | Definition |
|---|---|
| `revenue_yoy` | revenue ÷ prior-year same fiscal period − 1 |
| `revenue_qoq` | quarterly only; revenue ÷ prior quarter − 1 |
| `operating_income_yoy` | NULL if prior period ≤ 0 |
| `eps_diluted_yoy` | NULL if prior period ≤ 0 |

**Margins**
| Name | Definition |
|---|---|
| `gross_margin` | gross_profit ÷ revenue; if gross_profit absent, (revenue − cogs) ÷ revenue |
| `operating_margin` | operating_income ÷ revenue |
| `net_margin` | net_income ÷ revenue |
| `ebitda` | operating_income + dep_amort |
| `ebitda_margin` | ebitda ÷ revenue |
| `rnd_intensity` | rnd_expense ÷ revenue |
| `sga_intensity` | sga_expense ÷ revenue |
| `incremental_gross_margin` | Δ gross profit ÷ Δ revenue; NULL if \|Δ revenue\| < 2% of revenue (raised from 1% live — see R6a) |

**Returns**
| Name | Definition |
|---|---|
| `effective_tax_rate` | tax_expense ÷ pretax_income |
| `nopat` | operating_income × (1 − effective_tax_rate) |
| `invested_capital` | total_debt + equity − cash |
| `roic` | nopat ÷ invested_capital |
| `roe` | net_income ÷ equity |
| `asset_turnover` | revenue ÷ total_assets |
| `equity_multiplier` | total_assets ÷ equity |
| `fixed_asset_turnover` | revenue ÷ ppe_net (falls back to ppe_and_lease_net if ppe_net absent for the period — R1d) |

`net_margin × asset_turnover × equity_multiplier` is the DuPont decomposition of `roe`,
and their product must reconcile to `roe` — see R8.

**`net_income` and `equity` must both be parent-level, or both NCI-inclusive — never one
of each.** `net_margin` uses `net_income`; `equity_multiplier` and `roe` use `equity`.
Before R1f, `net_income` could resolve via `ProfitLoss` (NCI-inclusive) in one period
while `equity` resolved via `StockholdersEquity` (parent-level) — same period, mismatched
basis, wrong `roe`. **DuPont's own reconciliation could not have caught this**: it
recomputes `net_margin × asset_turnover × equity_multiplier` and compares to the stored
`roe`, but `roe` is `net_income ÷ equity` using the *same two* (possibly mismatched)
values reconciliation is built from — a consistent wrong basis reconciles perfectly with
itself. This is exactly why category 6 (alias agreement, checking against the *other*
alias, not against internal consistency) was needed to find it. R1f's split removes the
possibility structurally: `net_income` and `equity` now have exactly one alias each.

**Balance-sheet inputs use ending balances, not averages.** Averages introduce a second
failure mode for marginal accuracy gain. Record this in each `formula` string so it is
visible rather than assumed.

**Capital and cash**
| Name | Definition |
|---|---|
| `capex_to_revenue` | capex ÷ revenue |
| `capex_to_depreciation` | capex ÷ depreciation; fall back to dep_amort where depreciation untagged, saying so in `formula` (R1f) |
| `free_cash_flow` | cfo − capex |
| `fcf_margin` | free_cash_flow ÷ revenue |
| `fcf_conversion` | free_cash_flow ÷ net_income; NULL if net_income ≤ 0 |
| `sbc_to_revenue` | sbc ÷ revenue |
| `depreciation_rate` | depreciation (fall back to dep_amort, R1f) ÷ ppe_gross; fall back to ppe_net, then to ppe_and_lease_net, saying which in `formula` |

**Working capital** (annual basis; quarterly figures annualised where noted)
| Name | Definition |
|---|---|
| `days_inventory` | inventory ÷ cogs × 365 |
| `days_receivables` | receivables ÷ revenue × 365 |
| `days_payables` | payables ÷ cogs × 365 |
| `cash_conversion_cycle` | days_inventory + days_receivables − days_payables |
| `inventory_growth_less_revenue_growth` | inventory YoY − revenue YoY |

**Solvency**
| Name | Definition |
|---|---|
| `net_debt` | total_debt − cash − short_term_investments |
| `net_debt_to_ebitda` | net_debt ÷ ebitda; NULL if ebitda ≤ 0 |
| `interest_coverage` | operating_income ÷ interest_expense |
| `current_ratio` | current_assets ÷ current_liabilities |

**Quality**
| Name | Definition |
|---|---|
| `beneish_m_score` | See R7. Annual only. |

### R6a — Ranges catch implausible values; guard the denominator instead of the range

Live `validate` runs (category 1) found real occurrences outside four declared ranges —
`effective_tax_rate`, `eps_diluted_yoy`, `operating_income_yoy`, `interest_coverage` —
all genuine values from real volatility (Micron's memory-cycle earnings swings,
near-zero prior-period denominators), not errors. Those four ranges widen (config.py
records the new bounds; this spec does not repeat them, to avoid two sources of truth
that drift apart).

`incremental_gross_margin` does **not** get a wider range; the guard (minimum
`|Δrevenue|` floor, R6) raises from 1% to 2% of revenue instead. **The principle stands,
but the diagnosis of the specific live case needs a correction.** The original
assumption was that the one observed violation (-6.6, NVIDIA) was a near-zero `Δrevenue`
amplifying noise into something range-shaped. Checked directly against the underlying
values after raising the guard: `Δrevenue` there is $197M against $6,704M revenue —
2.94% of revenue, above even the *new* 2% floor, so the guard was never going to
suppress it. It is a real, large earnings event: NVIDIA's quarter ended 2022-07-31 (Q2
FY2023), the gaming-GPU inventory write-down quarter, where gross profit fell $1.3B on
essentially flat revenue. **The finding is correctly reported and correctly not a
computation bug — it is a genuinely extreme, real, disclosed value, not a
denominator-driven artifact.** The guard is still the right mechanism *in general* — a
plausible-range check on the output can't distinguish "genuinely extreme" from
"denominator near zero, ratio exploded," and raising the floor is real, cheap protection
against the artifact case — but do not assume every value outside this metric's range is
that artifact without checking. This one wasn't, and remains an accepted, explained
finding (AC15) rather than a bug or a reason to widen the range.

Eight indices comparing year *t* to *t−1*, combined linearly. Annual only; NULL where the
prior year is unavailable.

| Index | Definition |
|---|---|
| DSRI | (receivables/revenue)ₜ ÷ (receivables/revenue)ₜ₋₁ |
| GMI | gross_marginₜ₋₁ ÷ gross_marginₜ |
| AQI | (1 − (current_assets + ppe_net) ÷ total_assets)ₜ ÷ same at t−1 — ppe_net falls back to ppe_and_lease_net (R1d) |
| SGI | revenueₜ ÷ revenueₜ₋₁ |
| DEPI | dep_rateₜ₋₁ ÷ dep_rateₜ, where dep_rate = depreciation ÷ (depreciation + ppe_net) — depreciation falls back to dep_amort, ppe_net falls back to ppe_and_lease_net (R1d, R1f) |
| SGAI | (sga/revenue)ₜ ÷ (sga/revenue)ₜ₋₁ |
| LVGI | ((total_debt + current_liabilities)/assets)ₜ ÷ same at t−1 |
| TATA | (net_income − cfo) ÷ total_assets |

LVGI uses `total_debt` (R1b), never `debt_noncurrent + debt_current` summed directly —
this index inherits the same Micron double-counting risk `net_debt` had, and must not be
reintroduced here.

**One more substitution to disclose, same standard as AQI below:**

- **DEPI** now prefers `depreciation` (pure, R1f) and falls back to `dep_amort` (DD&A)
  only where `depreciation` is untagged for that period — recorded in `formula` exactly
  like `ppe_gross`'s fallback to `ppe_net`. This is no longer a blanket substitution
  applied to every period the way it was before `depreciation` existed as its own input;
  disclose it only on the periods where the fallback actually fires.
- **TATA** uses `net_income` where the original model specifies income from continuing
  operations. Identical for all three companies today (none report discontinued
  operations), but record it anyway: append `"[net income substituted for income from
  continuing operations]"` to the `formula` string, so the substitution is visible before
  it ever has a chance to matter.

```
M = −4.84 + 0.920·DSRI + 0.528·GMI + 0.404·AQI + 0.892·SGI
    + 0.115·DEPI − 0.172·SGAI + 4.679·TATA − 0.327·LVGI
```

Store the M-score **and all eight component indices** as separate metrics. The score
alone is not actionable; the components are what tell you which behaviour triggered it.

AQI here omits the "other securities" term from the original specification, which is not
reliably tagged in XBRL. **Record this deviation in the `formula` string and in
`ARCHITECTURE.md`.** A model applied with an undocumented modification is worse than one
not applied at all.

Threshold: above −1.78 is conventionally treated as a flag. Do not hard-code
interpretation into `metrics` — thresholds belong in SPEC-005's observation rules.

### R8 — Validation command

`python -m edgar.pipeline validate`

Given the emphasis on robustness, this is a first-class deliverable, not a test helper.
It must run against the real database and report, without modifying anything:

1. **Range violations** — any metric outside its declared plausible range. **Report
   grouped by `(metric, direction)`** (above/below the declared bound), with a count and
   the observed min/max per group — not one line per occurrence. 58 individual findings
   compress to a handful of groups; the point is to let ranges be widened once, with
   evidence, not to make someone scroll past every occurrence to find the pattern. Do not
   widen any range as part of implementing this — reporting is not the same decision as
   recalibrating a bound, and the evidence should inform that decision, not this spec.
   **Skip any `(metric, cik, period_start, period_end)` in `config.RANGE_EXCEPTIONS`**
   (R1j) — report it informationally, with the register's written reason, instead of as a
   hard failure. Anything not in the register still hard-fails.
2. **DuPont reconciliation** — `net_margin × asset_turnover × equity_multiplier` versus
   `roe`. Flag differences beyond 1% relative. Disagreement means a concept mapping is
   wrong.
3. **Gross profit cross-check** — where both `gross_profit` and `cogs` are reported,
   confirm `revenue − cogs ≈ gross_profit` within 1%. Disagreement means the revenue or
   cogs alias is wrong for that company.
4. **Debt reconciliation** — where a combined debt tag (`LongTermDebt` or
   `DebtLongtermAndShorttermCombinedAmount`) and both components (`debt_noncurrent`,
   `debt_current`) resolve for the same period, assert
   `combined ≈ debt_noncurrent + debt_current` within 1% relative; report violations with
   company, period, and both values. This turns the twelve-period Micron finding from the
   pre-implementation review into a permanent, automated assertion rather than a one-time
   observation — it must keep catching this if it ever recurs, for any company, not just
   confirm it happened once. **Skip any `(cik, period_end)` in
   `config.DEBT_RECONCILIATION_EXCEPTIONS`** (R1i) — report it informationally, with the
   register's written reason. Anything not in the register still hard-fails.
5. **Period-mixing check — redefined.** The original definition ("assert every metric's
   inputs share one duration class") was implemented as "no `period_end` may appear in
   more than one duration class," which flagged 158 real, benign cases on live data
   (Amazon's directly-tagged implicit Q4 sharing an end date with the annual figure — see
   R3a). That check verifies a property about the *data*, not about any *metric row*, and
   produces noise instead of signal. Redefined to check what R8 actually asked for: for
   every stored metric row, every current-period (`_t`) input value must correspond to a
   real `xbrl_facts` row at that row's own exact `(period_start, period_end)`; every
   prior-period (`_t-1`) input value must correspond to a real fact of the same duration
   class as the row's own period. This is a real assertion — it would catch a future bug
   that let a value from a different period leak into a computation — and on correct
   data it asserts something that is true by construction, so it reads zero. It remains a
   hard failure: a non-zero result here means the engine actually mixed periods, which is
   exactly the class of bug this spec exists to prevent.
6. **Alias agreement (new).** For every canonical input with more than one alias, where
   two aliases both resolve for the exact same period, assert they agree within 1%
   relative. This automates the alias-purity rule (`ARCHITECTURE.md` §2.1) across the
   whole registry going forward, rather than relying on it being re-discovered by hand
   the way `LongTermDebt` and `ReceivablesNetCurrent` were. A violation means two aliases
   thought to be the same fact are not, for at least one company/period. **Skip any
   canonical input listed in `config.ALIAS_AGREEMENT_EXCEPTIONS`** (R1g) — report its
   disagreements informationally, alongside the register's written reason, instead of as
   a hard failure. A canonical input not in the register still hard-fails on any
   disagreement; the register is for named, justified exceptions, not a way to silence a
   finding without writing down why.
7. **Concept drift** — R5 output.
8. **Unverified YoY drift (new, informational).** For a YoY-style metric whose current-
   and prior-period values resolved via *different* aliases of the same canonical input
   (a concept-drift boundary, category 7), check whether those two aliases were ever
   *co-tagged* for that company — i.e. whether there exists any period where both have a
   value, which is how the NVIDIA `pretax_income` transition was confirmed to be a true
   synonym (R5). If the two aliases were never co-tagged, the YoY calculation is
   *assuming* the two definitions are equivalent, not demonstrating it, and that
   assumption should be visible.
9. **Coverage** — per company and metric, the share of periods where a value was computed
   versus NULL, so systematic gaps are visible rather than invisible.
10. **Unresolved concepts** — any canonical input with no alias for any company.
11. **Finance lease zero-assumption (new, informational).** One line per company where
    `total_debt` computed by assuming $0 for an absent finance lease component (R1h),
    with a count of periods affected. Keeps the zero-assumption visible at the portfolio
    level, not just inside individual `formula` strings.

Exit non-zero if category 1, 2, 3, 4, 5, or 6 produces any finding not covered by an
alias-agreement exception. Those indicate
incorrect numbers. Categories 7–11 are informational.

### R9 — NULL discipline

- Any unresolvable input yields NULL. **Never guess, never substitute zero.** A missing
  input and a zero value are different statements about the world.
- Divide by zero yields NULL — not an exception, not infinity.
- Every NULL records a machine-readable reason: which input was missing, or which guard
  triggered.
- `formula` names the concepts actually used. `inputs_json` records their values.
- `revenue_yoy` and every YoY metric must match on **fiscal** period, not calendar dates.
  Three companies, three fiscal calendars; date matching will silently compare wrong
  periods.

### R10 — CLI

```
python -m edgar.pipeline ingest-xbrl     [--ticker TICKER]
python -m edgar.pipeline compute-metrics [--ticker TICKER] [--metric NAME]
python -m edgar.pipeline validate        [--ticker TICKER]
```

- `ingest-xbrl` reports facts written per company and concept, and names every configured
  concept resolving to nothing.
- `compute-metrics` reports metrics computed, and separately metrics written NULL with
  reason counts.
- `--metric` recomputes one metric during development.
- `status` gains fact and metric counts.
- Logging at INFO. No `print()` outside the CLI layer.

---

## Constraints

- All HTTP through `edgar_client`.
- No literals outside `config.py` — concepts, thresholds, coefficients, ranges.
- No LLM calls anywhere in this spec.
- No network in unit tests. Commit a trimmed real `companyfacts` fixture.
- Deterministic. Same inputs, same outputs.
- Type hints on all public functions.
- `app.db` must remain under 6 MB after ingest and compute (relaxed from 5 MB — live
  execution landed at 5.18 MB after restricting metrics to real fiscal period ends
  (R3a), and the fiscal-period fix is what actually mattered: absolute size matters far
  less than *what grows on every run*. A `metrics` table that recomputes cleanly to the
  same size each run is fine at 5.18 MB; a table that grew unbounded by 100KB/run because
  the wrong periods kept accumulating would not have been fine at 3 MB. Re-running any
  command must still change no rows (AC17) — that property is the one worth holding a
  hard line on, not the absolute byte count).

---

## Acceptance Criteria

1. `ingest-xbrl` succeeds for all three companies with per-concept counts reported.
2. Every canonical input resolves for at least one company; any that resolves nowhere is
   reported explicitly.
3. `xbrl_facts` contains only configured concepts in declared units.
4. Every duration fact has non-NULL `duration_days`; every instant fact has NULL.
5. Annual metrics exist for all three companies across at least eight fiscal years.
6. **Range assertions pass** for Amazon's most recent fiscal year: `operating_margin`
   0.02–0.30, `net_margin` 0.0–0.25, `current_ratio` 0.5–2.5, `revenue_yoy` −0.20–0.50.
7. `gross_margin` derives from reported `GrossProfit` for NVDA and MU, and from
   revenue − cogs for AMZN. Both paths exercised; `formula` reflects which was used.
8. **No metric mixes duration classes.** Asserted directly, not assumed.
9. `free_cash_flow` for one Amazon fiscal year verified by hand against the archived cash
   flow statement, and the working shown in the report.
10. `beneish_m_score` computes for all three companies for at least five years, with all
    eight components stored.
11. DuPont reconciles to `roe` within 1% for every period where all four exist.
12. At least one metric is NULL with a recorded reason.
13. Every metric row has non-empty `formula` and `inputs_json`, and concepts named in
    `formula` appear in `inputs_json`.
14. YoY metrics for NVDA and MU compare periods twelve months apart, verified.
15. `validate` exits 0 against the real database (categories 1–6 clean) — achieved via
    `RANGE_EXCEPTIONS`/`DEBT_RECONCILIATION_EXCEPTIONS`/`ALIAS_AGREEMENT_EXCEPTIONS`
    covering every real, checked, explained finding; nothing unregistered remains.
16. `app.db` under 6 MB (relaxed from 5 MB — see Constraints).
17. Re-running any command changes no rows and creates no duplicates.
18. `pytest` passes.
19. **Micron's `net_debt` hand-verified** against its reported balance sheet for one
    recent fiscal year, with the working shown in the report. Micron's
    `LongTermDebtNoncurrent` has no entries after FY2013, so the automated debt
    reconciliation check (R8 category 4) has no recent Micron periods where both a
    combined tag and both components resolve to cross-check against — a manual check is
    the only way to confirm `total_debt` is right for Micron *today*, not just
    historically. Now that `total_debt` includes finance leases (R1b), the long-term
    component (`borrowings_noncurrent + finance_lease_liability_noncurrent`) must
    reconcile to the FY2025 balance sheet's reported "Long-term debt" line ($14,017M).
20. `app.db` row count and size reported after restricting metrics to real fiscal
    period ends (R3a) — no target given; report what it actually is.
21. `beneish_m_score` non-null for Micron for at least 5 years (closing the AC10 gap,
    now that `ppe_net` has a fallback — R1d).

---

## Edge Cases

| Case | Required behaviour |
|---|---|
| Concept absent for a company entirely | Not an error. Dependent metrics NULL with reason. |
| Same period from several filings | Latest `filed_date` wins; all rows retained. |
| Quarterly and YTD facts share an end date | Distinguished by `duration_days`. Never merged. |
| 52/53-week fiscal year (**NVIDIA and Micron both** — confirmed live: NVDA annual durations of 363/370 days, not a fixed 365; NVIDIA's year end floats (last Sunday in January), it is not merely "late January") | Duration thresholds are ranges for this reason, for both companies, not Micron alone. |
| Interest expense not reported | `interest_coverage` NULL. Absence is not zero. |
| Combined debt tag and components disagree by more than 1% | Reported by `validate` category 4. Does not block computation — `total_debt` still prefers the combined tag — but surfaces a genuine tagging inconsistency for that company/period. |
| Two different duration classes share a `period_end` (found live: Amazon has a 365-day TTM-style fact and a 90-day quarterly fact both ending 2025-06-30) | `metrics` is keyed on `(cik, period_start, period_end, name, calc_version)`, not `period_end` alone — added during implementation after this collision made a `basis="both"` metric silently overwrite one class's row with the other on every run. `period_start` is part of the schema and the UNIQUE constraint. |
| Negative operating income | Valid. Margins may be negative. Do not clamp or filter. |
| Negative equity | `roe` and `equity_multiplier` NULL, with reason. |
| Prior-year period missing | YoY NULL. Never substitute a nearby period. |
| `ppe_gross` not tagged | Fall back to `ppe_net`, then `ppe_and_lease_net`, record the substitution in `formula`. |
| `ppe_net` not tagged but `ppe_and_lease_net` is (Micron, FY2021+) | Fall back to `ppe_and_lease_net`, record the substitution — it is a broader measure, not the same fact. |
| `total_debt`'s finance lease components never tagged for a company (NVIDIA: operating leases only) | `total_debt` NULL for that company, permanently. Absence is not zero, even though the practical effect is broad — see R1b. |
| Beneish input missing for t−1 | M-score NULL. Do not compute from partial components. |
| `effective_tax_rate` negative or above 1 | Valid — tax benefits happen. Do not clamp. Flag in `validate` if outside −0.5 to 1.0. |
| `companyfacts` 404 | Typed error naming the company. |
| Restatement changes a prior figure | Metrics recompute from the latest value. Expected. |

---

## Testing Requirements

- Fixture: trimmed real `companyfacts` with multiple concepts, instant and duration facts,
  and at least one restated period.
- `test_only_configured_concepts_ingested`
- `test_duration_classification` — 90, 181, 273, 365-day facts classify correctly; 45-day
  classifies as `other`
- `test_quarterly_and_ytd_not_mixed` — the central risk; test directly
- `test_latest_filed_wins`
- `test_missing_input_yields_null_not_zero`
- `test_divide_by_zero_yields_null`
- `test_gross_margin_both_paths`
- `test_yoy_matches_fiscal_not_calendar` — use a non-December year end
- `test_dupont_reconciles_to_roe`
- `test_beneish_components_against_hand_computed_example`
- `test_concept_drift_detected` — use the real NVIDIA `pretax_income` fixture (R5): the
  resolved alias for NVDA must come back as
  `IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments`
  for FY2019 and earlier and switch to
  `IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest`
  from FY2020 onward, with the drift reported at that boundary.
- `test_per_period_resolution_not_company_wide` — the Amazon `GrossProfit` case: a
  company with a stale, ancient tag for one input must still resolve every *other*
  period via the fallback alias, not read NULL company-wide because the primary alias
  "exists somewhere."
- `test_total_debt_prefers_combined_tag` and `test_total_debt_falls_back_to_components` —
  both paths of R1b's resolution exercised.
- `test_debt_reconciliation_check` — combined tag and components agreeing passes;
  disagreement beyond 1% is reported (R8 category 4).
- `test_receivables_has_no_fallback_alias` — confirms `ReceivablesNetCurrent` was removed;
  an entity tagging only that concept produces NULL for `receivables`, not a value.
- `test_formula_names_concepts_actually_used`
- `test_metric_registry_inputs_exist_in_concept_registry` — a startup consistency check;
  a declared metric referencing an undeclared input must fail loudly at import
- `test_ingest_idempotent`, `test_compute_metrics_idempotent`
- `test_ppe_net_falls_back_to_ppe_and_lease_net` — the real Micron FY2021+ case; confirms
  `ppe_and_lease_net` is a separate canonical input, not an alias, and that the
  substitution is recorded in `formula`.
- `test_total_debt_includes_finance_leases` — the real Micron FY2025 case: confirms
  `total_debt` reconciles to the reported combined debt-note total ($14,577M), and that
  the long-term component alone (`borrowings_noncurrent + finance_lease_noncurrent`)
  reconciles to the reported "Long-term debt" balance sheet line ($14,017M).
- `test_total_debt_null_when_finance_lease_never_tagged` — a company with only operating
  leases (real: NVIDIA) produces NULL `total_debt`, not a borrowings-only guess.
- `test_periods_restricted_to_real_filing_period_ends` — an annual-duration fact whose
  end date is not any `filings.period_end` for a 10-K is excluded from the annual period
  set; likewise for quarterly against 10-Q. Use the real Amazon implicit-Q4 case as the
  fixture.
- `test_period_mixing_check_reads_zero_on_correct_data` — the redefined category 5;
  must NOT flag Amazon's real implicit-Q4 collision (R3a), since no metric row actually
  mixes periods.
- `test_period_mixing_check_catches_leaked_period` — construct a metric row whose
  inputs_json contains a value that does not correspond to a real fact at that row's own
  period; category 5 must catch it.
- `test_alias_agreement_check` — two aliases resolving to the same value for the same
  period passes; a deliberately corrupted second alias fails.
- `test_unverified_yoy_drift_reported` — a YoY metric whose current/prior periods
  resolved via two aliases that were never co-tagged is flagged informationally; the
  real NVIDIA `pretax_income` case (co-tagged, confirmed equivalent) must NOT be flagged.
- `test_range_violations_grouped_by_metric_and_direction` — report formatting, not
  detection: multiple violations of the same metric/direction collapse to one group.
- `test_depreciation_falls_back_to_dep_amort` — `depreciation_rate`/`capex_to_depreciation`/
  `beneish_depi` use pure `depreciation` where tagged, fall back to `dep_amort` and
  disclose it in `formula` where not.
- `test_equity_and_net_income_single_alias` — confirms both were trimmed; a company
  tagging only the NCI-inclusive variant produces NULL, not a silently mismatched ratio.
- `test_alias_agreement_exception_reported_not_hard_failed` — `capex`'s registered
  exception shows up informationally, with its reason, and does not affect
  `hard_failure_count`.
- `test_alias_agreement_unregistered_canonical_still_hard_fails` — a disagreement on a
  canonical input *not* in `ALIAS_AGREEMENT_EXCEPTIONS` still counts as a hard failure.
- `test_total_debt_assumes_zero_for_untagged_finance_lease` — the real NVIDIA case:
  `total_debt`, `net_debt`, `invested_capital`, `roic`, and Beneish `LVGI` all compute;
  `formula` records the zero assumption.
- `test_total_debt_still_null_when_borrowings_missing` — confirms the refined NULL rule
  didn't loosen the *primary*-measure side: absent borrowings is still NULL, never $0.
- `test_debt_reconciliation_exception_reported_not_hard_failed` — the real AMZN
  2015-2016 ASU 2015-03 periods show up informationally, with their reason, and don't
  count toward `hard_failure_count`.
- `test_debt_reconciliation_unregistered_period_still_hard_fails` — a disagreement at a
  `(cik, period_end)` not in `DEBT_RECONCILIATION_EXCEPTIONS` still hard-fails.
- `test_range_exceptions_reported_not_hard_failed` — the real NVIDIA and Micron cases
  show up informationally, with their reasons, and don't count toward
  `hard_failure_count`.
- `test_range_exceptions_unregistered_violation_still_hard_fails` — a violation at a
  `(metric, cik, period_start, period_end)` not in `RANGE_EXCEPTIONS` still hard-fails.
- `test_validate_exits_zero_against_real_database` — the actual healthy-state assertion:
  run against the live `app.db`, `hard_failure_count == 0`.

---

## Likely Files Affected

```
edgar/config.py       (concept registry, metric registry, thresholds, CALC_VERSION)
edgar/db.py           (duration_days, filed_date)
edgar/xbrl.py         (main work)
edgar/metrics.py      (engine + primitives)
edgar/validate.py     (new)
edgar/edgar_client.py (companyfacts endpoint)
edgar/pipeline.py     (ingest-xbrl, compute-metrics, validate)
ARCHITECTURE.md       (§6 schema, §7 metric set, decision log)
tests/fixtures/companyfacts_trimmed_amzn.json  (main fixture: gross_profit stale tag, restatement)
tests/fixtures/companyfacts_trimmed_nvda.json  (2021 pretax_income drift, 52/53-week durations)
tests/fixtures/companyfacts_trimmed_mu.json    (debt reconciliation, 52/53-week durations, non-alias receivables)
tests/test_xbrl.py
tests/test_metrics.py
tests/test_validate.py
```

---

## Forward-Looking Concerns

Flagged now so they are not discovered late. **Do not implement any of these here.**

1. **LLM spend ledger.** The project has a hard $20 ceiling. Before the first API call in
   SPEC-006, there must be a spend ledger and a hard cap that refuses calls beyond it.
   Designing that after the first call is designing it too late.
2. **Segment data.** `companyfacts` exposes consolidated figures only — no AWS or Data
   Center segment breakdown. The data exists in the Segment Information note already
   extracted as text. Any future extraction is AI-derived and must not enter `xbrl_facts`.
3. **Market data isolation.** A future event-study feature adds a second, less reliable
   external source. It must be isolated so its failure cannot affect the filing pipeline.
4. **Quarterly working capital.** Days metrics computed on quarterly figures need
   annualising; the current spec computes them annually. Revisit before charting quarterly.
5. **Q4 derivation.** Q4 is not separately filed and must be derived as FY − (Q1+Q2+Q3).
   That is a calculation, not a fact, and belongs in `metrics` when implemented.

---

## Notes for the Implementer

- `companyfacts` shape: `facts.{taxonomy}.{concept}.units.{unit}` is a list of entries
  with `start`, `end`, `val`, `accn`, `fy`, `fp`, `form`, `filed`, sometimes `frame`.
  Instant facts have no `start`.
- Do **not** use the `frame` field for period selection. Compute duration from dates.
- Both Micron (ending on a Thursday) and NVIDIA (ending the last Sunday in January) run
  floating 52/53-week fiscal years, so annual durations vary by several days for *both*
  companies, not just Micron. Confirmed live: NVDA shows 363- and 370-day fiscal years
  and 97-day quarters from the same extra-week mechanism. This is why thresholds are
  ranges.
- `accession_no` on an `xbrl_facts` row must satisfy the existing foreign key to
  `filings(accession_no)`. `companyfacts` references accessions from every XBRL-tagged
  filing a company has ever made, including amendments and forms outside
  `TRACKED_FORMS` that are not necessarily present in `filings`. Look up the fact's `accn`
  against the real `filings` table per company before writing; store it only when it
  matches, otherwise leave `accession_no` NULL (the column already allows it) rather than
  violating the foreign key or silently dropping the fact.
- Range assertions are a data-quality technique: in a pipeline you cannot verify by eye,
  wide bounds on plausible values catch the errors that actually occur — wrong sign,
  wrong magnitude, wrong period — without becoming brittle as real data moves.
- Report any discrepancy between this spec and observed SEC behaviour rather than working
  around it silently. `ARCHITECTURE.md` must then be corrected.
