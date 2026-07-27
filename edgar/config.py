"""All configuration and constants for the edgar pipeline.

Per ARCHITECTURE.md §5: this is the only module permitted to contain SEC
URLs, CIKs, form-type literals, or numeric limits. Later specs will extend
this file with a note allow-list, concept aliases, and model names as those
modules (sections.py, xbrl.py/metrics.py, llm.py) are built — they are not
added yet because nothing uses them.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Company:
    ticker: str
    cik: str  # zero-padded 10 digits
    name: str
    fiscal_year_end: str  # 'MMDD'


# Adding a company is a one-line addition to this list.
WATCHLIST: list[Company] = [
    Company(ticker="AMZN", cik="0001018724", name="Amazon.com, Inc.", fiscal_year_end="1231"),
    Company(ticker="NVDA", cik="0001045810", name="NVIDIA Corporation", fiscal_year_end="0131"),
    Company(ticker="MU", cik="0000723125", name="Micron Technology, Inc.", fiscal_year_end="0903"),
]

TENK_FORM_TYPE: str = "10-K"
TENQ_FORM_TYPE: str = "10-Q"
EIGHTK_FORM_TYPE: str = "8-K"
TRACKED_FORMS: list[str] = [TENK_FORM_TYPE, TENQ_FORM_TYPE, EIGHTK_FORM_TYPE]

EIGHTK_REQUIRED_ITEM: str = "2.02"

# Section extraction (SPEC-002) covers these form types only; MD&A/Risk
# Factors/8-K exhibit extraction is SPEC-003.
SECTION_EXTRACTABLE_FORM_TYPES: list[str] = [TENK_FORM_TYPE, TENQ_FORM_TYPE]

# SEC Fair Access policy requires an identifying User-Agent on every request.
# Format: "Name email@example.com". Read from the environment so no personal
# contact info lives in source control.
_SEC_USER_AGENT_ENV_VAR = "SEC_USER_AGENT"


def get_sec_user_agent() -> str:
    value = os.environ.get(_SEC_USER_AGENT_ENV_VAR)
    if not value:
        raise RuntimeError(
            f"{_SEC_USER_AGENT_ENV_VAR} environment variable is not set. "
            "SEC blocks anonymous requests; set it to 'Your Name your.email@example.com' "
            "before running anything that talks to sec.gov."
        )
    return value


SEC_RATE_LIMIT_PER_SEC: int = 8
HTTP_MAX_RETRIES: int = 3
HTTP_BACKOFF_BASE_SECONDS: float = 1.0
HTTP_BACKOFF_MAX_SECONDS: float = 30.0
HTTP_TIMEOUT_SECONDS: float = 30.0

# Paths
PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent
DATA_DIR: Path = PROJECT_ROOT / "data"
DB_PATH: Path = DATA_DIR / "app.db"
RAW_ARCHIVE_DIR: Path = DATA_DIR / "raw"
SECTIONS_DIR: Path = DATA_DIR / "sections"

# --- SPEC-002: manifests and section extraction ---

FILING_SUMMARY_FILENAME: str = "FilingSummary.xml"
MANIFEST_FILENAME: str = "manifest.json"
FILING_INDEX_HTML_SUFFIX: str = "-index.html"

# FilingSummary.xml MenuCategory values.
MENUCATEGORY_COVER: str = "Cover"
MENUCATEGORY_STATEMENTS: str = "Statements"
MENUCATEGORY_NOTES: str = "Notes"
MENUCATEGORY_POLICIES: str = "Policies"
MENUCATEGORY_TABLES: str = "Tables"
MENUCATEGORY_DETAILS: str = "Details"

# Reports in these categories are extracted to `sections`; everything else
# (Cover, Tables, Details) is skipped -- see SPEC-002 R2.
SECTION_MENUCATEGORIES: list[str] = [
    MENUCATEGORY_STATEMENTS,
    MENUCATEGORY_NOTES,
    MENUCATEGORY_POLICIES,
]

# SEC-declared document type (from the filing index's Document Format Files
# table) identifying an earnings press release exhibit -- see ARCHITECTURE.md §3.6.
EXHIBIT_991_TYPE: str = "EX-99.1"

# --- SPEC-003: content-addressed section storage ---

# ALTER TABLE ... DROP COLUMN requires this SQLite version or newer.
MIN_SQLITE_VERSION_INFO: tuple[int, int, int] = (3, 35, 0)

SECTION_STORE_SUFFIX: str = ".txt.gz"
DB_BACKUP_SUFFIX: str = ".pre-migration.bak"

# --- SPEC-004: XBRL facts and financial metrics ---

COMPANYFACTS_TAXONOMY: str = "us-gaap"
CALC_VERSION: str = "v1"


@dataclass(frozen=True)
class ConceptInput:
    """A canonical financial input and the XBRL tags that may report it.

    Aliases are tried in priority order, resolved independently for EACH
    period -- never resolved once for a company's whole history. See
    ARCHITECTURE.md §6 (xbrl_facts note) and SPEC-004 R1a: Amazon's
    `GrossProfit` tag is real but only appears in filings covering
    FY2007-2008, so a company-wide "does this concept exist" resolution
    would wrongly treat it as available for every later period too.

    Every alias here must denote the same accounting quantity under a
    different name -- never a broader or narrower one. See ARCHITECTURE.md
    §2.1: `LongTermDebt` and `ReceivablesNetCurrent` were removed from
    other inputs' alias lists for violating this.
    """

    aliases: tuple[str, ...]
    unit: str
    # True for balance-sheet ("instant", as-of-a-date) facts, which have no
    # period_start. False for income-statement/cash-flow ("duration") facts,
    # which are reported over a start-end span. A real, stable property of
    # the underlying accounting concept, not something to infer from data.
    instant: bool = False


CONCEPT_REGISTRY: dict[str, ConceptInput] = {
    "revenue": ConceptInput(
        (
            "RevenueFromContractWithCustomerExcludingAssessedTax",
            "Revenues",
            "RevenueFromContractWithCustomerIncludingAssessedTax",
            "SalesRevenueNet",
        ),
        "USD",
    ),
    "cogs": ConceptInput(
        ("CostOfGoodsAndServicesSold", "CostOfRevenue", "CostOfGoodsSold"), "USD"
    ),
    "gross_profit": ConceptInput(("GrossProfit",), "USD"),
    "sga_expense": ConceptInput(
        ("SellingGeneralAndAdministrativeExpense", "GeneralAndAdministrativeExpense"), "USD"
    ),
    "rnd_expense": ConceptInput(("ResearchAndDevelopmentExpense",), "USD"),
    "operating_income": ConceptInput(("OperatingIncomeLoss",), "USD"),
    "pretax_income": ConceptInput(
        (
            "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
            "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments",
        ),
        "USD",
    ),
    "tax_expense": ConceptInput(("IncomeTaxExpenseBenefit",), "USD"),
    # NetIncomeLoss only -- ProfitLoss removed, see §2.1. It is NCI-inclusive, not a
    # synonym; mixing it with parent-level equity produces a wrong roe/net_margin.
    "net_income": ConceptInput(("NetIncomeLoss",), "USD"),
    # Separate canonical input, not a fallback alias of net_income. Stored, unused.
    "net_income_including_nci": ConceptInput(("ProfitLoss",), "USD"),
    "eps_diluted": ConceptInput(("EarningsPerShareDiluted",), "USD/shares"),
    "diluted_shares": ConceptInput(("WeightedAverageNumberOfDilutedSharesOutstanding",), "shares"),
    "total_assets": ConceptInput(("Assets",), "USD", instant=True),
    "current_assets": ConceptInput(("AssetsCurrent",), "USD", instant=True),
    "current_liabilities": ConceptInput(("LiabilitiesCurrent",), "USD", instant=True),
    # StockholdersEquity only -- the NCI-inclusive variant removed, see §2.1 and the
    # matching net_income split; same rationale, same fix.
    "equity": ConceptInput(("StockholdersEquity",), "USD", instant=True),
    # Separate canonical input, not a fallback alias of equity. Stored, unused.
    "equity_including_nci": ConceptInput(
        ("StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",), "USD", instant=True
    ),
    "cash": ConceptInput(("CashAndCashEquivalentsAtCarryingValue",), "USD", instant=True),
    "short_term_investments": ConceptInput(
        (
            "ShortTermInvestments",
            "MarketableSecuritiesCurrent",
            "AvailableForSaleSecuritiesDebtSecuritiesCurrent",
        ),
        "USD",
        instant=True,
    ),
    "inventory": ConceptInput(("InventoryNet",), "USD", instant=True),
    # AccountsReceivableNetCurrent only -- ReceivablesNetCurrent removed, see §2.1.
    "receivables": ConceptInput(("AccountsReceivableNetCurrent",), "USD", instant=True),
    "payables": ConceptInput(
        ("AccountsPayableCurrent", "AccountsPayableAndAccruedLiabilitiesCurrent"), "USD", instant=True
    ),
    "ppe_net": ConceptInput(("PropertyPlantAndEquipmentNet",), "USD", instant=True),
    # A separate, broader canonical input -- NOT an alias of ppe_net. Micron folds
    # finance-lease ROU assets into this combined line from FY2021 (ppe_net has no
    # entries after mid-2020 for Micron). See §2.1 / SPEC-004 R1d.
    "ppe_and_lease_net": ConceptInput(
        ("PropertyPlantAndEquipmentAndFinanceLeaseRightOfUseAssetAfterAccumulatedDepreciationAndAmortization",),
        "USD",
        instant=True,
    ),
    "ppe_gross": ConceptInput(("PropertyPlantAndEquipmentGross",), "USD", instant=True),
    # LongTermDebtNoncurrent only -- LongTermDebt removed, see §2.1 / total_debt below.
    "debt_noncurrent": ConceptInput(("LongTermDebtNoncurrent",), "USD", instant=True),
    "debt_current": ConceptInput(("LongTermDebtCurrent", "DebtCurrent"), "USD", instant=True),
    "finance_lease_liability_noncurrent": ConceptInput(("FinanceLeaseLiabilityNoncurrent",), "USD", instant=True),
    "finance_lease_liability_current": ConceptInput(("FinanceLeaseLiabilityCurrent",), "USD", instant=True),
    # Stored, not consumed by any metric yet -- future adjusted-leverage metric (R1e).
    "operating_lease_liability_noncurrent": ConceptInput(
        ("OperatingLeaseLiabilityNoncurrent",), "USD", instant=True
    ),
    "operating_lease_liability_current": ConceptInput(("OperatingLeaseLiabilityCurrent",), "USD", instant=True),
    # Computed: borrowings (combined tag, else debt_noncurrent + debt_current) PLUS
    # finance lease liabilities (SPEC-004 R1b) -- resolved by metrics.py's
    # _resolve_total_debt, never a plain alias lookup.
    "total_debt": ConceptInput(
        ("LongTermDebt", "DebtLongtermAndShorttermCombinedAmount"), "USD", instant=True
    ),
    # InterestExpenseDebt removed, see §2.1 -- differs from InterestExpense by up to
    # 100% for NVIDIA (total interest vs. debt-only interest; e.g. excludes lease interest).
    "interest_expense": ConceptInput(("InterestExpense", "InterestExpenseNonoperating"), "USD"),
    # Separate canonical input, not a fallback alias of interest_expense. Stored, unused.
    "interest_expense_debt": ConceptInput(("InterestExpenseDebt",), "USD"),
    # DD&A -- for EBITDA, which wants the full add-back. "Depreciation" removed, see §2.1:
    # it differs from DD&A by 20-40% (DD&A also includes amortization of intangibles).
    "dep_amort": ConceptInput(
        ("DepreciationDepletionAndAmortization", "DepreciationAmortizationAndAccretionNet"), "USD"
    ),
    # Separate canonical input, not a fallback alias of dep_amort. Used (with fallback to
    # dep_amort where untagged) by capex_to_depreciation, depreciation_rate, Beneish DEPI.
    "depreciation": ConceptInput(("Depreciation",), "USD"),
    # AllocatedShareBasedCompensationExpense removed, see §2.1 -- differs from
    # ShareBasedCompensation by ~1.4-3.8% for Micron (some SBC capitalized into
    # inventory rather than expensed). No replacement added; nothing here wants it.
    "sbc": ConceptInput(("ShareBasedCompensation",), "USD"),
    "cfo": ConceptInput(
        (
            "NetCashProvidedByUsedInOperatingActivities",
            "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
        ),
        "USD",
    ),
    # Disagree ~16% for one AMZN period (the real 2017 tag transition) -- a documented
    # alias-agreement exception (ALIAS_AGREEMENT_EXCEPTIONS below), not a synonym pair
    # nor a broader/narrower split. Kept as aliases on the strength of the AC9 hand
    # verification of free_cash_flow against Amazon's archived cash flow statement.
    "capex": ConceptInput(
        ("PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets"), "USD"
    ),
}

# Every raw XBRL concept name worth storing, across all canonical inputs.
ALL_CONFIGURED_CONCEPTS: frozenset[str] = frozenset(
    alias for c in CONCEPT_REGISTRY.values() for alias in c.aliases
)


@dataclass(frozen=True)
class AliasAgreementException:
    """A documented, accepted exception to R8 category 6 (alias agreement).

    Not every disagreement found is a broader/narrower split waiting to
    happen -- this is the register for the ones that are a real synonym pair
    despite disagreeing (a tag-transition artifact, restatement, etc.),
    with the reason written down. A canonical input NOT in this register
    still hard-fails on any disagreement; this register documents an
    exception, it does not silence a finding.
    """

    canonical: str
    reason: str


ALIAS_AGREEMENT_EXCEPTIONS: dict[str, AliasAgreementException] = {
    "capex": AliasAgreementException(
        canonical="capex",
        reason=(
            "Amazon's PaymentsToAcquirePropertyPlantAndEquipment -> "
            "PaymentsToAcquireProductiveAssets transition (~2017) disagrees ~16% for "
            "one period (2016-12-31). free_cash_flow computed from "
            "PaymentsToAcquireProductiveAssets was hand-verified against Amazon's "
            "archived FY2025 cash flow statement (SPEC-004 AC9); that alias is trusted."
        ),
    ),
}


@dataclass(frozen=True)
class DebtReconciliationException:
    """A documented, accepted exception to R8 category 4 (debt reconciliation).

    Keyed by (cik, period_end) -- unlike the alias-agreement register, a debt
    reconciliation exception is about a specific company's specific
    transition periods, not a standing property of a canonical input. A
    (cik, period_end) NOT in this register still hard-fails on any
    disagreement.
    """

    cik: str
    period_end: str
    reason: str


DEBT_RECONCILIATION_EXCEPTIONS: dict[tuple[str, str], DebtReconciliationException] = {
    ("0001018724", period_end): DebtReconciliationException(
        cik="0001018724",
        period_end=period_end,
        reason=(
            "ASU 2015-03 (effective 2016) moved debt issuance costs from an asset to a "
            "contra-liability. LongTermDebt (the combined tag) and "
            "debt_noncurrent + debt_current (the split components) legitimately differ "
            "~1.1% during the transition: one side reflects the reclassification before "
            "the other catches up. Real accounting-standard transition, not a tagging error."
        ),
    )
    for period_end in ("2015-12-31", "2016-03-31", "2016-06-30", "2016-09-30")
}


@dataclass(frozen=True)
class RangeException:
    """A documented, accepted exception to R8 category 1 (range violations).

    Keyed by (metric, cik, period_start, period_end) -- a specific finding,
    never a widened threshold. A range violation NOT in this register still
    hard-fails; this register documents a checked, explained real value, not
    a way to make a range wide enough to stop mattering.
    """

    metric: str
    cik: str
    period_start: str
    period_end: str
    reason: str


RANGE_EXCEPTIONS: dict[tuple[str, str, str, str], RangeException] = {
    ("incremental_gross_margin", "0001045810", "2022-05-02", "2022-07-31"): RangeException(
        metric="incremental_gross_margin",
        cik="0001045810",
        period_start="2022-05-02",
        period_end="2022-07-31",
        reason=(
            "NVIDIA's gaming-GPU inventory write-down quarter (Q2 FY2023): gross profit "
            "fell $1.3B on essentially flat revenue (Δrevenue = $197M, 2.94% of revenue -- "
            "above even the 2% guard floor, so this was never a denominator artifact). "
            "A real, disclosed, one-time write-down."
        ),
    ),
    ("effective_tax_rate", "0000723125", "2023-12-01", "2024-02-29"): RangeException(
        metric="effective_tax_rate",
        cik="0000723125",
        period_start="2023-12-01",
        period_end="2024-02-29",
        reason=(
            "Micron fiscal Q2 2024: pretax income of only $170M against a $622M discrete "
            "tax BENEFIT (net income $793M) -- consistent with a deferred-tax "
            "valuation-allowance release as Micron returned to marginal profitability "
            "coming out of the 2022-2023 memory downturn. A small real denominator plus a "
            "large real discrete item, not a data error."
        ),
    ),
}


@dataclass(frozen=True)
class DurationClass:
    name: str
    min_days: int
    max_days: int


# Ranges, not exact values -- both NVIDIA and Micron run floating 52/53-week
# fiscal years (ARCHITECTURE.md §1), so annual/quarterly durations vary by
# several days. Confirmed live: no real duration fact for any of the three
# companies fell outside these bands (SPEC-004 pre-implementation review).
PERIOD_CLASSES: tuple[DurationClass, ...] = (
    DurationClass("quarterly", 80, 100),
    DurationClass("half-year", 170, 190),
    DurationClass("three-quarter", 260, 285),
    DurationClass("annual", 350, 380),
)
PERIOD_CLASS_OTHER: str = "other"
PERIOD_CLASS_INSTANT: str = "instant"


@dataclass(frozen=True)
class MetricDef:
    """Declarative metadata for one metric. Computation lives in metrics.py.

    `inputs` lists every canonical input the metric's compute function may
    read, for the startup consistency check (a declared input not in
    CONCEPT_REGISTRY must fail loudly at import, never silently at runtime).
    `plausible_range` is None for raw dollar-amount metrics, where no
    cross-company bound is meaningful; ratios/percentages/indices get wide,
    deliberately generous bounds -- catching sign and magnitude errors, not
    precision.

    `extreme_informative` (SPEC-005 change, post-implementation) gates
    `metric_multi_year_extreme` (observations.py): False for compounding
    dollar-level metrics, where a "record" restates growth rather than
    reporting an event -- a growing company setting a revenue-scale record
    every few quarters says nothing an analyst doesn't already know from the
    growth-rate metrics themselves. A five-year low in a *ratio* (margin,
    return, days-outstanding) is information; a five-year high in a *level*
    (ebitda, free cash flow, invested capital, net debt) usually is not.
    True for everything else, including growth-RATE metrics (revenue_yoy,
    ...) and quality indices (Beneish) -- those are not compounding levels.
    """

    name: str
    inputs: tuple[str, ...]
    basis: str  # "annual" | "quarterly" | "both"
    plausible_range: tuple[float, float] | None
    needs_prior: bool = False
    category: str = ""
    extreme_informative: bool = True


METRIC_REGISTRY: dict[str, MetricDef] = {
    # --- Growth ---
    "revenue_yoy": MetricDef("revenue_yoy", ("revenue",), "both", (-0.9, 10.0), True, "growth"),
    "revenue_qoq": MetricDef("revenue_qoq", ("revenue",), "quarterly", (-0.9, 10.0), True, "growth"),
    # Range widened -5..5 -> -10..30 live: Micron's memory-cycle earnings swings are
    # genuinely this large, not errors (R6a).
    "operating_income_yoy": MetricDef(
        "operating_income_yoy", ("operating_income",), "both", (-10.0, 30.0), True, "growth"
    ),
    # Range widened -5..5 -> -10..20 live, same reason (R6a).
    "eps_diluted_yoy": MetricDef(
        "eps_diluted_yoy", ("eps_diluted",), "both", (-10.0, 20.0), True, "growth"
    ),
    # --- Margins ---
    "gross_margin": MetricDef(
        "gross_margin", ("gross_profit", "revenue", "cogs"), "both", (-1.0, 1.0), False, "margins"
    ),
    "operating_margin": MetricDef(
        "operating_margin", ("operating_income", "revenue"), "both", (-2.0, 1.0), False, "margins"
    ),
    "net_margin": MetricDef(
        "net_margin", ("net_income", "revenue"), "both", (-2.0, 1.0), False, "margins"
    ),
    "ebitda": MetricDef(
        "ebitda", ("operating_income", "dep_amort"), "both", None, False, "margins",
        extreme_informative=False,
    ),
    "ebitda_margin": MetricDef(
        "ebitda_margin", ("operating_income", "dep_amort", "revenue"), "both", (-2.0, 1.0), False, "margins"
    ),
    "rnd_intensity": MetricDef(
        "rnd_intensity", ("rnd_expense", "revenue"), "both", (0.0, 0.6), False, "margins"
    ),
    "sga_intensity": MetricDef(
        "sga_intensity", ("sga_expense", "revenue"), "both", (0.0, 0.6), False, "margins"
    ),
    # Range deliberately NOT widened for the live outlier (R6a) -- that outlier was a
    # near-zero Δrevenue amplifying noise, not a genuinely extreme value. Guarded via
    # INCREMENTAL_MARGIN_MIN_REVENUE_DELTA_PCT instead (raised 1% -> 2%).
    "incremental_gross_margin": MetricDef(
        "incremental_gross_margin",
        ("gross_profit", "revenue", "cogs"),
        "both",
        (-5.0, 5.0),
        True,
        "margins",
    ),
    # --- Returns ---
    # Range widened -0.5..1.0 -> -3.0..2.0 live: real large negative rates occur (R6a).
    "effective_tax_rate": MetricDef(
        "effective_tax_rate", ("tax_expense", "pretax_income"), "both", (-3.0, 2.0), False, "returns"
    ),
    "nopat": MetricDef(
        "nopat", ("operating_income", "tax_expense", "pretax_income"), "both", None, False, "returns",
        extreme_informative=False,
    ),
    "invested_capital": MetricDef(
        "invested_capital",
        (
            "total_debt", "debt_noncurrent", "debt_current",
            "finance_lease_liability_noncurrent", "finance_lease_liability_current",
            "equity", "cash",
        ),
        "both",
        None,
        False,
        "returns",
        extreme_informative=False,
    ),
    "roic": MetricDef(
        "roic",
        (
            "operating_income", "tax_expense", "pretax_income",
            "total_debt", "debt_noncurrent", "debt_current",
            "finance_lease_liability_noncurrent", "finance_lease_liability_current",
            "equity", "cash",
        ),
        "both",
        (-2.0, 2.0),
        False,
        "returns",
    ),
    "roe": MetricDef("roe", ("net_income", "equity"), "both", (-2.0, 2.0), False, "returns"),
    "asset_turnover": MetricDef(
        "asset_turnover", ("revenue", "total_assets"), "both", (0.0, 5.0), False, "returns"
    ),
    "equity_multiplier": MetricDef(
        "equity_multiplier", ("total_assets", "equity"), "both", (0.0, 20.0), False, "returns"
    ),
    "fixed_asset_turnover": MetricDef(
        "fixed_asset_turnover", ("revenue", "ppe_net", "ppe_and_lease_net"), "both", (0.0, 50.0), False, "returns"
    ),
    # --- Capital and cash ---
    "capex_to_revenue": MetricDef(
        "capex_to_revenue", ("capex", "revenue"), "both", (0.0, 1.0), False, "capital_cash"
    ),
    "capex_to_depreciation": MetricDef(
        "capex_to_depreciation", ("capex", "depreciation", "dep_amort"), "both", (0.0, 10.0), False, "capital_cash"
    ),
    "free_cash_flow": MetricDef(
        "free_cash_flow", ("cfo", "capex"), "both", None, False, "capital_cash",
        extreme_informative=False,
    ),
    "fcf_margin": MetricDef(
        "fcf_margin", ("cfo", "capex", "revenue"), "both", (-2.0, 1.0), False, "capital_cash"
    ),
    "fcf_conversion": MetricDef(
        "fcf_conversion", ("cfo", "capex", "net_income"), "both", (-10.0, 10.0), False, "capital_cash"
    ),
    "sbc_to_revenue": MetricDef(
        "sbc_to_revenue", ("sbc", "revenue"), "both", (0.0, 0.6), False, "capital_cash"
    ),
    "depreciation_rate": MetricDef(
        "depreciation_rate",
        ("depreciation", "dep_amort", "ppe_gross", "ppe_net", "ppe_and_lease_net"),
        "both",
        (0.0, 1.0),
        False,
        "capital_cash",
    ),
    # --- Working capital (annual basis only) ---
    "days_inventory": MetricDef(
        "days_inventory", ("inventory", "cogs"), "annual", (0.0, 730.0), False, "working_capital"
    ),
    "days_receivables": MetricDef(
        "days_receivables", ("receivables", "revenue"), "annual", (0.0, 365.0), False, "working_capital"
    ),
    "days_payables": MetricDef(
        "days_payables", ("payables", "cogs"), "annual", (0.0, 365.0), False, "working_capital"
    ),
    "cash_conversion_cycle": MetricDef(
        "cash_conversion_cycle",
        ("inventory", "cogs", "receivables", "revenue", "payables"),
        "annual",
        (-365.0, 730.0),
        False,
        "working_capital",
    ),
    "inventory_growth_less_revenue_growth": MetricDef(
        "inventory_growth_less_revenue_growth",
        ("inventory", "revenue"),
        "annual",
        (-5.0, 5.0),
        True,
        "working_capital",
    ),
    # --- Solvency ---
    "net_debt": MetricDef(
        "net_debt",
        (
            "total_debt", "debt_noncurrent", "debt_current",
            "finance_lease_liability_noncurrent", "finance_lease_liability_current",
            "cash", "short_term_investments",
        ),
        "both",
        None,
        False,
        "solvency",
        extreme_informative=False,
    ),
    "net_debt_to_ebitda": MetricDef(
        "net_debt_to_ebitda",
        (
            "total_debt", "debt_noncurrent", "debt_current",
            "finance_lease_liability_noncurrent", "finance_lease_liability_current",
            "cash", "short_term_investments", "operating_income", "dep_amort",
        ),
        "both",
        (-20.0, 20.0),
        False,
        "solvency",
    ),
    # Range widened -100..200 -> -200..5000 live: near-zero interest_expense for a
    # low-debt company/quarter makes this ratio legitimately huge (R6a).
    "interest_coverage": MetricDef(
        "interest_coverage", ("operating_income", "interest_expense"), "both", (-200.0, 5000.0), False, "solvency"
    ),
    "current_ratio": MetricDef(
        "current_ratio", ("current_assets", "current_liabilities"), "both", (0.0, 10.0), False, "solvency"
    ),
    # --- Quality: Beneish M-score and its 8 stored components ---
    "beneish_m_score": MetricDef(
        "beneish_m_score",
        (
            "receivables", "revenue", "gross_profit", "cogs", "current_assets", "ppe_net", "ppe_and_lease_net",
            "total_assets", "sga_expense", "total_debt", "debt_noncurrent", "debt_current",
            "finance_lease_liability_noncurrent", "finance_lease_liability_current",
            "current_liabilities", "depreciation", "dep_amort", "net_income", "cfo",
        ),
        "annual",
        (-10.0, 10.0),
        True,
        "quality",
    ),
    "beneish_dsri": MetricDef(
        "beneish_dsri", ("receivables", "revenue"), "annual", (0.0, 20.0), True, "quality"
    ),
    "beneish_gmi": MetricDef(
        "beneish_gmi", ("gross_profit", "revenue", "cogs"), "annual", (-20.0, 20.0), True, "quality"
    ),
    "beneish_aqi": MetricDef(
        "beneish_aqi",
        ("current_assets", "ppe_net", "ppe_and_lease_net", "total_assets"),
        "annual",
        (-20.0, 20.0),
        True,
        "quality",
    ),
    "beneish_sgi": MetricDef("beneish_sgi", ("revenue",), "annual", (0.0, 20.0), True, "quality"),
    "beneish_depi": MetricDef(
        "beneish_depi",
        ("depreciation", "dep_amort", "ppe_net", "ppe_and_lease_net"),
        "annual",
        (-20.0, 20.0),
        True,
        "quality",
    ),
    "beneish_sgai": MetricDef(
        "beneish_sgai", ("sga_expense", "revenue"), "annual", (-20.0, 20.0), True, "quality"
    ),
    "beneish_lvgi": MetricDef(
        "beneish_lvgi",
        (
            "total_debt", "debt_noncurrent", "debt_current",
            "finance_lease_liability_noncurrent", "finance_lease_liability_current",
            "current_liabilities", "total_assets",
        ),
        "annual",
        (-20.0, 20.0),
        True,
        "quality",
    ),
    "beneish_tata": MetricDef(
        # The one Beneish index computed from a single period -- no t-1 comparison.
        "beneish_tata", ("net_income", "cfo", "total_assets"), "annual", (-2.0, 2.0), False, "quality"
    ),
}

# Metrics that need a prior-year (annual) or prior-quarter (quarterly) period.
BENEISH_METRIC_NAMES: tuple[str, ...] = (
    "beneish_dsri", "beneish_gmi", "beneish_aqi", "beneish_sgi",
    "beneish_depi", "beneish_sgai", "beneish_lvgi", "beneish_tata",
)

BENEISH_INTERCEPT: float = -4.84
BENEISH_COEFFICIENTS: dict[str, float] = {
    "DSRI": 0.920,
    "GMI": 0.528,
    "AQI": 0.404,
    "SGI": 0.892,
    "DEPI": 0.115,
    "SGAI": -0.172,
    "TATA": 4.679,
    "LVGI": -0.327,
}
# Conventional flag threshold (Beneish 1999). Not applied as a hard-coded
# interpretation here -- SPEC-005's observation rules own thresholds.
BENEISH_FLAG_THRESHOLD: float = -1.78

# --- validate command tolerances (SPEC-004 R8) ---
DUPONT_RECONCILIATION_TOLERANCE: float = 0.01
GROSS_PROFIT_CROSSCHECK_TOLERANCE: float = 0.01
DEBT_RECONCILIATION_TOLERANCE: float = 0.01
ALIAS_AGREEMENT_TOLERANCE: float = 0.01
# Raised 1% -> 2% live (R6a): the 1% floor let a near-zero Δrevenue amplify noise into
# a range-shaped outlier. Guarding the denominator, not the output range.
INCREMENTAL_MARGIN_MIN_REVENUE_DELTA_PCT: float = 0.02

# --- SPEC-005: readability and observations ---

# R1: normalized_text_hash normalization. text_hash means content identity
# (any byte differs -> different hash); normalized_text_hash means wording
# identity (the prose reads the same, modulo the version stamp and rolling
# numeric disclosures within it).

# The XBRL viewer template prepends a bare version stamp (e.g. "v3.25.0.1")
# as the literal first line of every R-file's rendered body -- confirmed
# live, ARCHITECTURE.md §3.7. It is a rendering artifact, not content, and
# changes on every viewer upgrade regardless of whether the underlying text
# changed. Stripped entirely rather than number-masked: masking alone would
# still leave a different token shape across versions with a different
# number of dot-separated segments (v3.25.0.1 -> v3.25.4).
XBRL_VIEWER_VERSION_LINE_PATTERN: str = r"\Av\d+(?:\.\d+)+[ \t]*\n?"

# Masks every run of digits (with embedded commas/periods) to one placeholder
# token, so a rolling disclosure figure ("$1.3 billion, $1.4 billion, and
# $1.4 billion as of December 31, 2022, 2023, and 2024") does not register
# as a wording change when only the trailing figures roll forward a year.
# Confirmed necessary live: Amazon's FY2024->FY2025 Policies text differs in
# its normalized_text_hash-less form on essentially every rolling figure
# embedded in otherwise-unchanged policy paragraphs.
NUMERIC_TOKEN_PATTERN: str = r"\d[\d,]*\.?\d*"
NUMERIC_TOKEN_PLACEHOLDER: str = "<NUM>"

# R1: readability. Syllable heuristic is crude, pure-Python, and deliberately
# not a dependency -- the project relies on it only for *change over time*
# (a consistently-imperfect measure supports "this note got harder to read"
# even though it cannot support an absolute Gunning Fog score). See
# readability.py and ARCHITECTURE.md.
COMPLEX_WORD_MIN_SYLLABLES: int = 3

# Edge case: a one-sentence section produces a computable but meaningless fog
# index. No readability_change observation below this many words in EITHER
# the current or the prior-year section.
READABILITY_MIN_WORD_COUNT: int = 50
READABILITY_CHANGE_PCT: float = 0.15

# R5: rule registry.


@dataclass(frozen=True)
class RuleDef:
    """Declarative metadata for one observation rule. Firing logic lives in
    observations.py -- this dataclass holds only what a config entry should
    hold, matching MetricDef's split between config.py (data) and metrics.py
    (computation).

    `version` is this rule's own calc_version-equivalent: observations are
    keyed and filtered on it exactly as metrics are filtered on
    config.CALC_VERSION (SPEC-005 change 8), but per-rule rather than global
    -- bumping one rule's threshold must not invalidate every other rule's
    history.

    `severity` is None for metric_threshold_cross, whose severity varies per
    declared threshold (DECLARED_THRESHOLDS below) -- never assigned by
    judgement, always derived from the rule and its magnitude (R4).
    """

    name: str
    version: str
    severity: str | None
    subject_kind: str  # "metric" | "section" -- which table this rule reads


RULE_REGISTRY: dict[str, RuleDef] = {
    # v2: MetricDef.extreme_informative added post-implementation, excluding
    # compounding dollar-level metrics (ebitda, nopat, invested_capital,
    # free_cash_flow, net_debt) -- a real behavior change, so the version
    # bumps; v1's observations on those metrics remain in the table for
    # comparison but drop out of every current-version query (R4/R8).
    "metric_multi_year_extreme": RuleDef("metric_multi_year_extreme", "v2", "medium", "metric"),
    "metric_sigma_move": RuleDef("metric_sigma_move", "v1", "medium", "metric"),
    "metric_threshold_cross": RuleDef("metric_threshold_cross", "v1", None, "metric"),
    "metric_divergence": RuleDef("metric_divergence", "v1", "high", "metric"),
    # Renamed from accounting_policy_changed (SPEC-005 change 2): driven by
    # normalized_text_hash, not text_hash, and its firing threshold is a
    # measured calibration -- see SECTION_WORDING_SIMILARITY_THRESHOLD below
    # and SPEC-005 R5a / ARCHITECTURE.md.
    "section_wording_changed": RuleDef("section_wording_changed", "v1", "high", "section"),
    "section_length_change": RuleDef("section_length_change", "v1", "medium", "section"),
    "section_appeared": RuleDef("section_appeared", "v1", "high", "section"),
    "section_disappeared": RuleDef("section_disappeared", "v1", "high", "section"),
    "metric_stopped_computing": RuleDef("metric_stopped_computing", "v1", "medium", "metric"),
    "readability_change": RuleDef("readability_change", "v1", "low", "section"),
}


@dataclass(frozen=True)
class ThresholdRule:
    """One declared threshold for metric_threshold_cross (SPEC-005 R5)."""

    metric: str
    comparator: str  # "above" | "below" | "crosses_zero"
    value: float | None  # None for crosses_zero
    severity: str


DECLARED_THRESHOLDS: tuple[ThresholdRule, ...] = (
    ThresholdRule("beneish_m_score", "above", BENEISH_FLAG_THRESHOLD, "high"),
    ThresholdRule("current_ratio", "below", 1.0, "medium"),
    ThresholdRule("net_debt", "crosses_zero", None, "medium"),
    ThresholdRule("fcf_conversion", "below", 0.5, "medium"),
    ThresholdRule("interest_coverage", "below", 3.0, "high"),
)


@dataclass(frozen=True)
class DivergenceRule:
    """One declared divergence for metric_divergence (SPEC-005 R5). Severity
    is uniformly "high" for this rule -- see RULE_REGISTRY."""

    metric: str
    shape: str  # "above" | "yoy_decline"
    value: float


DECLARED_DIVERGENCES: tuple[DivergenceRule, ...] = (
    DivergenceRule("inventory_growth_less_revenue_growth", "above", 0.15),
    DivergenceRule("capex_to_depreciation", "above", 1.5),
    DivergenceRule("depreciation_rate", "yoy_decline", 0.15),
)

# SPEC-005 change 3 -- governing principle: a state-based rule (one whose
# condition can hold for many consecutive periods) fires only on the
# transition INTO the condition, never while it persists. Verified live
# against real data before this was adopted: without it, capex_to_depreciation
# fired on 71% of eligible periods (both AMZN and NVDA/MU are in sustained
# capex-expansion mode, so "capex > 1.5x depreciation" is close to their
# normal state, not an event) and fcf_conversion fired on 50% ("currently
# below 0.5" vs. "just crossed below 0.5" are very different claims). Applies
# to metric_threshold_cross and metric_divergence only -- NOT to
# metric_multi_year_extreme or metric_sigma_move, whose own mathematical
# definitions (a new record; a >2-sigma move) are already self-limiting.

# SPEC-005 change 4: minimum-history requirements split by basis for
# metric_multi_year_extreme. Quarterly cadence needs more periods before a
# "record" means anything than annual cadence does -- 5 years is 20 quarters
# but only 5 annual periods, and requiring 8 annual periods would make the
# rule permanently dead for every annual-only metric at current corpus depth
# (confirmed live: 0 eligible periods, ever, for any company, under a flat
# 8-prior-period rule applied to beneish_m_score or
# inventory_growth_less_revenue_growth). 4 is the smallest window in which
# "a new low" still means something for a metric reported once a year.
EXTREME_MIN_PRIOR_PERIODS: dict[str, int] = {"quarterly": 8, "annual": 4}
EXTREME_WINDOW_PERIODS: dict[str, int] = {"quarterly": 20, "annual": 5}  # "prior 5 years"

# metric_sigma_move remains quarterly-only, unlike metric_multi_year_extreme:
# ordering (is this the highest/lowest of N) is robust with few points; a
# standard deviation computed from 4-8 annual points is not a reliable
# dispersion estimate the same way. Never evaluated against annual-basis
# periods -- an intentional exclusion, not a miscalibration, and every
# annual-only metric (Beneish, working capital) will show zero
# metric_sigma_move observations by design.
SIGMA_MIN_PRIOR_PERIODS: int = 8
SIGMA_STD_DEV_THRESHOLD: float = 2.0

SECTION_LENGTH_CHANGE_PCT: float = 0.25

# SPEC-005 change 6: note-name aliases, same discipline as CONCEPT_REGISTRY
# aliases (ARCHITECTURE.md §2.1) -- an entry here must be a verified rename
# of the SAME note, never a broader/narrower relationship, and never a
# guess. Every entry below was confirmed two ways against live data: (1) the
# two names never co-occur in the same filing, anywhere in the company's
# history, and (2) the actual section text on either side of the transition
# describes the same underlying disclosure (same XBRL [Abstract] element,
# same opening sentence). Checked across all three watchlist companies;
# genuine renames were found only for Micron.


@dataclass(frozen=True)
class NoteNameAlias:
    alias: str
    canonical: str
    reason: str


_NOTE_NAME_ALIAS_ENTRIES: tuple[NoteNameAlias, ...] = (
    NoteNameAlias(
        "Basis of Presentation Basis of Presentation",
        "Basis of Presentation",
        "Micron 10-Q 2018-12-19 only: FilingSummary.xml renders this one filing's "
        "heading doubled ('Basis of Presentation Basis of Presentation'). Content "
        "confirmed identical topic (Organization, Consolidation and Presentation of "
        "Financial Statements) to every adjacent filing's plain 'Basis of "
        "Presentation' -- a rendering artifact for this one filing, not a second note.",
    ),
    NoteNameAlias(
        "Segment Information",
        "Segment and Other Information",
        "Micron's FilingSummary ShortName for its segment-reporting note flickered "
        "between 'Segment Information' and 'Segment and Other Information' from "
        "2018 to 2021 before settling permanently on the latter. Content confirmed "
        "identical (same 'Segment Reporting [Abstract]' XBRL element, same opening "
        "sentence) on both sides -- one note, two labels in use during the transition.",
    ),
    NoteNameAlias(
        "Equity Plans",
        "Equity Compensation Plans",
        "Micron renamed this note's FilingSummary ShortName in its FY2024 10-K "
        "(2024-10-04), permanently. Content confirmed identical topic (same "
        "'Share-Based Payment Arrangement [Abstract]' XBRL element) on both sides.",
    ),
)

NOTE_NAME_ALIASES: dict[str, str] = {e.alias: e.canonical for e in _NOTE_NAME_ALIAS_ENTRIES}

# SPEC-005 change 2, calibration finding: measured live against the real
# corpus, EXACT normalized_text_hash equality changes on 98.9% of
# fiscal-year-matched Policies comparisons and 98.3% of Notes comparisons --
# both far above the 33% ceiling (R8), and nearly identical to each other.
# Confirmed by direct diff, not a normalization bug: filers genuinely
# rewrite a sentence or two in nearly every note nearly every year (real
# examples found live: Amazon added a healthcare-services clause and a new
# "Derivative Instruments" policy; Micron's "Restructure and Asset
# Impairments" note, ~400 words, changed in 100% of the years compared).
# Byte-for-byte wording identity is therefore not a usable firing signal in
# EITHER category -- the fix is a materiality threshold, not a category
# choice. section_wording_changed uses normalized_text_hash as a fast exact
# match (skip trivially-identical wording, cheaply) and, for everything
# else, a real similarity ratio between the two normalized texts
# (difflib.SequenceMatcher.quick_ratio) against this threshold. Measured at
# 0.85: Policies fires on 14.6% of comparisons, Notes on 13.3% -- both
# comfortably under the ceiling and, again, nearly identical between
# categories. That similarity between categories is itself the calibration
# finding: Notes does not need excluding once real per-period noise
# (numbers, and now materiality) is controlled for.
SECTION_WORDING_SIMILARITY_THRESHOLD: float = 0.85

# Notes whose PRESENCE legitimately fluctuates period to period (e.g. a
# "pending ASU" housekeeping note that is only written when one is actually
# pending) -- excluded from section_appeared/section_disappeared specifically,
# because that rule means "a new or discontinued disclosure," not "nothing
# was pending this quarter." Confirmed live: this note toggled in and out of
# Micron's filings repeatedly across adjacent quarters, which is a recurring
# housekeeping pattern, not an event. section_length_change and
# section_wording_changed still apply normally whenever the note IS present
# in both compared periods -- only appeared/disappeared skips these names.
FLUCTUATING_NOTE_NAMES: frozenset[str] = frozenset({
    "Recently Issued Accounting Standards Not Yet Adopted",
})

# --- SPEC-005 R8: validate additions ---
FIRING_RATE_WARNING_PCT: float = 0.33

# --- SPEC-005 R10 (AC14 amendment): app.db size is a growth measure, not an
# absolute ceiling -- the absolute number was already relaxed twice during
# SPEC-004 (5 MB -> 6 MB) and needed relaxing again after SPEC-005 shipped.
# DB_SIZE_SOFT_CEILING_BYTES is informational only -- validate reports
# against it but never hard-fails on it; crossing it is a prompt to revisit
# the SQLite-in-git storage design (ARCHITECTURE.md §4.1), not a build break.
DB_SIZE_SOFT_CEILING_BYTES: int = 15 * 1024 * 1024

# Measured once, live, against a scratch copy of the real database: deleted
# one real Micron 10-Q's rows (filings/sections/xbrl_facts/metrics/
# observations), VACUUMed, then reprocessed it end to end through the real
# pipeline (extract -> ingest-xbrl -> compute-metrics -> backfill-readability
# -> compute-observations) and VACUUMed again. This is a point measurement,
# not something validate recomputes on every run -- doing that live would
# mean network calls and a scratch-copy side effect on every invocation,
# which contradicts validate's own zero-side-effects design. Re-measure by
# hand (see ARCHITECTURE.md §7a / decision log 32) if the marginal cost is
# ever suspected to have shifted materially (e.g. a new company added).
DB_SIZE_MEASURED_MARGINAL_BYTES_PER_FILING: int = 73_728
DB_SIZE_MEASURED_MARGINAL_FILING_DESCRIPTION: str = (
    "one MU 10-Q (0000723125-26-000015): 90 XBRL facts, 31 metric rows, several "
    "sections, 51 observations -- measured 2026-07-27"
)
