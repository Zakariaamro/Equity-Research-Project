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


# SPEC-006: Anthropic API key, same discipline as SEC_USER_AGENT above --
# read from the environment, never from source, fail fast and by name
# rather than attempting an anonymous call (R2, edge case table).
#
# SPEC-006A incident fix (2026-07-27): deliberately NOT named ANTHROPIC_API_KEY.
# That is the Anthropic SDK's own default variable name -- Claude Code (and
# anthropic-sdk-based tooling generally) reads it automatically. The
# original $10 balance was exhausted by $6.28 of Claude Code spend billing
# to the same account because this project's key happened to share that
# name in the shell environment; the two were never actually connected in
# code, just accidentally co-located by name. A project-specific name makes
# that particular confusion structurally impossible, and it is what makes
# the L9 environment canary (llm.check_environment_canary) meaningful: it
# warns specifically because this project does NOT read ANTHROPIC_API_KEY.
_ANTHROPIC_API_KEY_ENV_VAR = "EQUITY_RESEARCH_ANTHROPIC_API_KEY"


def get_anthropic_api_key() -> str:
    value = os.environ.get(_ANTHROPIC_API_KEY_ENV_VAR)
    if not value:
        raise RuntimeError(
            f"{_ANTHROPIC_API_KEY_ENV_VAR} environment variable is not set. "
            "Set it before running anything that calls the Anthropic API."
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

    `headline` (SPEC-005 change, post-implementation round 2) gates
    `metric_multi_year_extreme` and `metric_sigma_move`: only headline
    metrics generate standalone "is this metric's own value notable"
    observations. False for: the 8 individual Beneish component indices
    (DSRI, GMI, ...) -- only the composite `beneish_m_score` is a headline
    quality signal, an analyst reads the components as its inputs, not as
    findings in their own right; and for `nopat`, `invested_capital`,
    `effective_tax_rate`, `ebitda` -- intermediates that feed other metrics
    (`roic`, `net_debt_to_ebitda`, ...) rather than headline figures
    themselves. Does NOT gate `metric_threshold_cross`/`metric_divergence`,
    which already only ever run against a small, curated, always-headline
    set (DECLARED_THRESHOLDS/DECLARED_DIVERGENCES below) -- nor
    `metric_stopped_computing`, where a silently-broken intermediate is
    still diagnostically useful even though it is not itself headline.
    """

    name: str
    inputs: tuple[str, ...]
    basis: str  # "annual" | "quarterly" | "both"
    plausible_range: tuple[float, float] | None
    needs_prior: bool = False
    category: str = ""
    extreme_informative: bool = True
    headline: bool = True


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
        extreme_informative=False, headline=False,
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
        "effective_tax_rate", ("tax_expense", "pretax_income"), "both", (-3.0, 2.0), False, "returns",
        headline=False,
    ),
    "nopat": MetricDef(
        "nopat", ("operating_income", "tax_expense", "pretax_income"), "both", None, False, "returns",
        extreme_informative=False, headline=False,
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
        headline=False,
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
    # All 8 components: headline=False -- an analyst reads these as
    # beneish_m_score's inputs, not as findings in their own right. Only the
    # composite score is a headline quality signal.
    "beneish_dsri": MetricDef(
        "beneish_dsri", ("receivables", "revenue"), "annual", (0.0, 20.0), True, "quality", headline=False
    ),
    "beneish_gmi": MetricDef(
        "beneish_gmi", ("gross_profit", "revenue", "cogs"), "annual", (-20.0, 20.0), True, "quality",
        headline=False,
    ),
    "beneish_aqi": MetricDef(
        "beneish_aqi",
        ("current_assets", "ppe_net", "ppe_and_lease_net", "total_assets"),
        "annual",
        (-20.0, 20.0),
        True,
        "quality",
        headline=False,
    ),
    "beneish_sgi": MetricDef(
        "beneish_sgi", ("revenue",), "annual", (0.0, 20.0), True, "quality", headline=False
    ),
    "beneish_depi": MetricDef(
        "beneish_depi",
        ("depreciation", "dep_amort", "ppe_net", "ppe_and_lease_net"),
        "annual",
        (-20.0, 20.0),
        True,
        "quality",
        headline=False,
    ),
    "beneish_sgai": MetricDef(
        "beneish_sgai", ("sga_expense", "revenue"), "annual", (-20.0, 20.0), True, "quality", headline=False
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
        headline=False,
    ),
    "beneish_tata": MetricDef(
        # The one Beneish index computed from a single period -- no t-1 comparison.
        "beneish_tata", ("net_income", "cfo", "total_assets"), "annual", (-2.0, 2.0), False, "quality",
        headline=False,
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


# SPEC-005 post-implementation, round 2 -- severity reassigned by analytical
# materiality, not by detection method. The original assignment (every
# section_appeared/disappeared "high", every metric extreme "medium"
# regardless of which metric) let presentation artifacts (a boilerplate SEC
# disclosure item showing up "new" this year) outrank real economic signal
# (a five-year low in a margin). See RULE_REGISTRY comments per rule and
# ARCHITECTURE.md §2.4 for the full rationale.
#
# Every rule's version bumps this round: the new severity-override layer
# (BOILERPLATE_NOTE_NAMES, cross-company simultaneity demotion, R5c/R5d
# below) changes what every rule can produce, even where the base severity
# assignment for a given rule is otherwise unchanged, so v1 rows are no
# longer comparable to current output for any rule.
RULE_REGISTRY: dict[str, RuleDef] = {
    # Severity now varies by metric category -- "high" for margins/returns/
    # working_capital (a five-year low in a ratio is information), "medium"
    # otherwise (including growth-rate and capital_cash/solvency/quality
    # metrics) -- computed in observations.py, not a fixed value here. Also
    # gated on MetricDef.headline (R5d) as well as extreme_informative.
    "metric_multi_year_extreme": RuleDef("metric_multi_year_extreme", "v3", None, "metric"),
    "metric_sigma_move": RuleDef("metric_sigma_move", "v2", "medium", "metric"),
    # Uniformly "high": crossing any of these five declared hard thresholds
    # (a solvency ratio, a cash-burn signal, a leverage flip, a fraud-risk
    # flag) is analytically material regardless of which one it is -- see
    # DECLARED_THRESHOLDS below, where the per-threshold severity field is
    # now uniform rather than varying.
    "metric_threshold_cross": RuleDef("metric_threshold_cross", "v2", "high", "metric"),
    "metric_divergence": RuleDef("metric_divergence", "v2", "high", "metric"),
    # Renamed from accounting_policy_changed (SPEC-005 change 2): driven by
    # normalized_text_hash, not text_hash, and its firing threshold is a
    # measured calibration -- see SECTION_WORDING_SIMILARITY_THRESHOLD below
    # and SPEC-005 R5a / ARCHITECTURE.md. "High" here means "for a
    # substantive note" -- BOILERPLATE_NOTE_NAMES overrides to "low"
    # regardless of rule (R5d).
    # v3: SECTION_WORDING_SIMILARITY_THRESHOLD recalibrated after the
    # quick_ratio -> ratio() fix (see the constant's docstring) -- a real
    # behavior change to the similarity computation itself, not just severity.
    "section_wording_changed": RuleDef("section_wording_changed", "v3", "high", "section"),
    "section_length_change": RuleDef("section_length_change", "v2", "medium", "section"),
    # Downgraded from "high": a note appearing or disappearing is a
    # presentation change, not itself a number moving -- see R5d. Automatic
    # rename detection (R5e) also removes the single biggest source of
    # spurious appeared+disappeared pairs before this rule ever sees them.
    # v3: quick_ratio -> ratio() fix changed which appeared/disappeared names
    # get pulled into a section_renamed pair instead (see section_renamed's
    # own version-bump comment) -- the output SET changes even though these
    # two rules' own logic didn't. v4: BOILERPLATE_NOTE_NAMES excluded from
    # rename-pairing (R5f) -- boilerplate names that used to get (wrongly)
    # paired into a rename now correctly fall back to being reported here
    # (and are then forced "low" anyway by the boilerplate override).
    "section_appeared": RuleDef("section_appeared", "v4", "low", "section"),
    "section_disappeared": RuleDef("section_disappeared", "v4", "low", "section"),
    "metric_stopped_computing": RuleDef("metric_stopped_computing", "v2", "medium", "metric"),
    "readability_change": RuleDef("readability_change", "v2", "low", "section"),
    # New (R5e): an automatically detected note rename (disappeared/appeared
    # pair whose actual text is similar above SECTION_RENAME_SIMILARITY_THRESHOLD)
    # emitted as one observation instead of a disappeared+appeared pair.
    # Always "low" -- a rename is a presentation change, lower-confidence
    # than a manually verified NOTE_NAME_ALIASES entry (which is never
    # reported as an observation at all, since it's treated as one
    # continuous note from the start).
    # v2: quick_ratio -> ratio() fix (see SECTION_WORDING_SIMILARITY_THRESHOLD's
    # docstring) -- a real change to which pairs match, not just a value tweak.
    # v3: BOILERPLATE_NOTE_NAMES excluded from rename-pairing (R5f) -- fixed
    # a real false positive ("Pay vs Performance Disclosure" repeatedly
    # paired with an unrelated accounting-standards note at the threshold).
    "section_renamed": RuleDef("section_renamed", "v3", "low", "section"),
}


@dataclass(frozen=True)
class ThresholdRule:
    """One declared threshold for metric_threshold_cross (SPEC-005 R5)."""

    metric: str
    comparator: str  # "above" | "below" | "crosses_zero"
    value: float | None  # None for crosses_zero
    severity: str


# All five uniformly "high" (post-implementation round 2) -- see
# RULE_REGISTRY's metric_threshold_cross entry for why severity no longer
# varies by which threshold crossed.
DECLARED_THRESHOLDS: tuple[ThresholdRule, ...] = (
    ThresholdRule("beneish_m_score", "above", BENEISH_FLAG_THRESHOLD, "high"),
    ThresholdRule("current_ratio", "below", 1.0, "high"),
    ThresholdRule("net_debt", "crosses_zero", None, "high"),
    ThresholdRule("fcf_conversion", "below", 0.5, "high"),
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
# else, a real similarity ratio between the two normalized texts against
# this threshold.
#
# CORRECTED post-implementation (round 2): the original measurement used
# difflib.SequenceMatcher.quick_ratio(), which is a fast UPPER-BOUND
# heuristic (character-multiset overlap), not real sequence matching --
# checked live while debugging round 2's rename detector, two genuinely
# unrelated Micron notes scored quick_ratio=0.87 against a true ratio() of
# 0.03. Every use of this threshold now uses the real .ratio(). Re-measured
# with the corrected metric: the whole distribution shifts down hard
# (median similarity 0.96 under quick_ratio -> 0.73-0.80 under real ratio,
# since ordinary annual-editing noise is not actually 96% textually stable,
# quick_ratio just said so). At 0.60: Policies fires on 27.0% of
# comparisons, Notes on 19.8% -- both comfortably under the 33% ceiling.
# The old 0.85 threshold, re-measured with the corrected metric, would fire
# on 67%/61% -- nowhere close to usable; it only looked calibrated because
# it was calibrated against the wrong number.
SECTION_WORDING_SIMILARITY_THRESHOLD: float = 0.60

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

# --- SPEC-005 post-implementation round 2: severity by materiality ---

# R5d: any observation about one of these notes is "low" regardless of
# which rule produced it. All four are boilerplate SEC-mandated disclosure
# items confirmed live to roll out industry-wide around the same real-world
# date across the whole watchlist (Pay vs Performance Disclosure, Item 402;
# Cybersecurity Risk Management and Strategy Disclosure, Item 106; Insider
# Trading Arrangements and Insider Trading Policies and Procedures, Item
# 408) -- their appearance/disappearance/wording is a regulatory-calendar
# event, not company-specific information, however the detection rule
# happened to notice it.
BOILERPLATE_NOTE_NAMES: frozenset[str] = frozenset({
    "Pay vs Performance Disclosure",
    "Cybersecurity Risk Management and Strategy Disclosure",
    "Insider Trading Arrangements",
    "Insider Trading Policies and Procedures",
})

# R5d: metric_multi_year_extreme severity by MetricDef.category -- "high"
# for the categories where a multi-year record is itself the analytical
# claim (a five-year low margin, a five-year low return, a working-capital
# days figure at an extreme); "medium" for every other eligible category
# (growth rates, capex ratios, solvency multiples, the Beneish M-score).
EXTREME_HIGH_SEVERITY_CATEGORIES: frozenset[str] = frozenset({"margins", "returns", "working_capital"})

# R5d: an observation is demoted to "low" (with a reason appended to the
# statement) when the SAME rule fires on the SAME subject for more than one
# watchlist company within this many days of each other. Verified live
# against all four current BOILERPLATE_NOTE_NAMES cases before picking this
# value: cross-company gaps ranged 214-303 days (companies with different
# fiscal year ends reach "first annual report after the same regulatory
# effective date" at different calendar times) -- 365 days comfortably
# covers a same real-world event across the whole watchlist without being
# so wide it would treat two genuinely unrelated same-named events years
# apart as one event.
CROSS_COMPANY_SIMULTANEITY_WINDOW_DAYS: int = 365

# R5e: automatic rename detection for section_appeared/section_disappeared.
# A disappeared name and an appeared name within the SAME year-over-year
# transition, whose actual section text similarity (same function as
# section_wording_changed -- difflib.SequenceMatcher.ratio on
# normalize_for_wording_hash'd text) exceeds this, are reported as one
# section_renamed observation instead of a separate disappeared+appeared
# pair. User-specified value. Higher than SECTION_WORDING_SIMILARITY_THRESHOLD
# (0.60) -- deliberate: a rename is inferred indirectly (two different
# titles, matched only by content), so it needs higher confidence than
# "does this same-titled note's content still match itself."
SECTION_RENAME_SIMILARITY_THRESHOLD: float = 0.70

# --- SPEC-005 R8: validate additions ---
# FIRING_RATE_WARNING_PCT (a flat 33%-of-eligible-periods ceiling) removed,
# post-implementation round 3 (R8b): it counted firings per (subject,
# period), which structurally penalises a rule evaluating many metrics per
# filing against one evaluating a single condition, regardless of how much
# of any ONE filing's brief either actually occupies. Replaced by
# validate.py's per-filing contribution report (mean/max observations
# contributed to a single filing, per rule) -- informational, no fixed
# ceiling, always reported.

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

# --- SPEC-006: LLM infrastructure and section analysis ---

# R2/R2a: model selection is a config entry, not a literal in llm.py. Sonnet 5
# chosen on quality, not cost -- see ARCHITECTURE.md decision log 39 and
# SPEC-006 R2a for the full reasoning (a weaker model is likelier to
# fabricate a finding rather than correctly return empty, the exact failure
# mode R6's quote verification exists to catch).
LLM_MODEL: str = "claude-sonnet-5"

# SPEC-006A (L2): tracks money ACTUALLY AVAILABLE, not the original project
# intent. On 2026-07-27 the real $10 prepaid balance was exhausted -- ~$3.79
# of it this project's own pipeline (working as designed), ~$6.28 of it
# Claude Code billing to the same account (see _ANTHROPIC_API_KEY_ENV_VAR
# above).
#
# Re-aligned 2026-07-28 (10.00 -> 8.50): `llm_calls`'s recorded lifetime total
# ($5.99 at the time) and the real Console prepaid balance (~$2.80) are NOT
# the same number and were never going to be -- the ORIGINAL $10 balance
# absorbed both this project's ledgered spend AND the Claude Code leak
# (decision log #40), but LLM_BUDGET_USD only ever tracked the former. A cap
# set to what the ledger shows, rather than to what Console shows, is a
# rubber stamp on money that may already be gone, not an early-warning layer.
# 8.50 was chosen as comfortably BELOW the real remaining balance at
# re-alignment time, the same "money actually available, not a round number"
# principle L2 was already built on (see the original 2026-07-27 note this
# replaces) -- not derived from the ledger total at all.
#
# These two numbers -- this cap and the real Console balance -- measure
# DIFFERENT things (recorded pipeline spend vs. actual prepaid balance after
# every process that bills to the same key) and will drift apart again the
# moment anything spends against the account outside this ledger's view,
# exactly as it did before. No code in this project reads the Console
# balance -- L1 (ARCHITECTURE.md §4.2) is already explicit that this is an
# operator responsibility no code enforces. Re-aligning LLM_BUDGET_USD
# against the real Console figure, periodically, is that same responsibility
# applied on an ongoing basis, not a one-time correction.
LLM_BUDGET_USD: float = 8.50
LLM_WARN_FRACTION: float = 0.75

# --- SPEC-006A: budget guardrails (L3-L7) ---
# Every constant below is a SEPARATE, independently-failing layer -- see
# SPEC-006A-budget-guardrails.md's threat model table. Do not remove one
# because another looks like it covers the same case; that overlap (L3/L6
# especially) is deliberate.

# L3: per-run cost ceiling. Tracked in-process (llm.RunGuard) against REAL
# recorded spend for calls made so far THIS run, independent of the lifetime
# ledger total (L2). Overridable per invocation via --max-run-cost, never by
# editing this value mid-run.
LLM_MAX_RUN_COST_USD: float = 2.00

# L4: any dry-run estimate above this requires `--confirm-cost N` naming the
# estimate. Deliberately not a bare `--yes` -- see LLM_CONFIRM_COST_TOLERANCE_USD.
LLM_CONFIRM_THRESHOLD_USD: float = 1.00

# L4: how close --confirm-cost's N must be to the dry-run estimate. 1% of
# the estimate, floored at $0.01 -- tight enough that pasting a stale or
# guessed number fails, loose enough that reading the estimate off the
# terminal and retyping it (rounding included) succeeds. Not specified by
# SPEC-006A itself ("a small tolerance"); this is the concrete choice.
LLM_CONFIRM_COST_TOLERANCE_FRACTION: float = 0.01
LLM_CONFIRM_COST_TOLERANCE_FLOOR_USD: float = 0.01

# L5: a prompt-version bump invalidating more than this many previously-
# cached analyses (within the currently selected run, not the whole corpus)
# requires --acknowledge-cache-invalidation before an execute run proceeds.
LLM_CACHE_INVALIDATION_WARN: int = 50

# L6: deliberately redundant with L3 (see module comment above). Holds even
# if compute_cost/LLM_PRICING is itself wrong -- exactly the class of bug
# SPEC-006A exists to catch (see LLM_PRICING's 2026-07-27 fix note below).
# At this project's real per-call cost (~$0.03-0.12, measured from the live
# ledger), L3's $2.00 run cap binds well before 300 calls are ever reached
# in normal operation -- that is correct, not a sign this ceiling is loose;
# it is the backstop for when L3's arithmetic cannot be trusted, not the
# primary limit for a healthy run.
LLM_MAX_CALLS_PER_RUN: int = 300

# L7: unattended (scheduled) runs are held to a much lower ceiling than L3's
# interactive default -- nobody is watching, and a loop can run all night.
# A normal incremental run (one new filing, 10-15 sections) costs roughly
# $0.25 (SPEC-006A), so this is generous for correct behaviour and tight
# against runaway behaviour. Binding on the GitHub Actions spec when it is
# written; analyze-sections' own --scheduled flag enforces it today.
LLM_SCHEDULED_RUN_MAX_COST_USD: float = 0.50


@dataclass(frozen=True)
class ModelPricing:
    """One model's verified $/million-token pricing (SPEC-006 R1/R1a).

    `source_url` and `verified_date` exist so a future reviewer (or
    `validate`'s staleness check) can tell at a glance whether this is still
    current without having to re-derive it -- a price is a fact with an
    expiration date, not a constant.
    """

    input_per_mtok: float
    output_per_mtok: float
    source_url: str
    verified_date: str  # ISO 8601 date the figures were checked, not guessed


_ANTHROPIC_PRICING_SOURCE = "https://platform.claude.com/docs/en/docs/about-claude/pricing"
_ANTHROPIC_PRICING_VERIFIED_DATE = "2026-07-27"

# Claude Sonnet 5 has TWO published rates: an introductory $2/$10 per MTok
# through 2026-08-31, reverting to standard $3/$15 from 2026-09-01.
#
# SPEC-006A incident fix (2026-07-27): this table PREVIOUSLY used the
# post-September standard rate ($3/$15) throughout, deliberately, on the
# reasoning that overstating cost was "the conservative direction" for a
# budget cap. That reasoning was wrong in practice: `llm_calls` is also the
# LEDGER, and it exists to reconcile against Console's actual balance, not
# only to underspend. Recorded spend was $5.6859 for the 197 real+error
# calls made so far; recomputing the SAME stored token counts at the
# introductory $2/$10 rate gives $3.7906 -- exactly the $3/$15 : $2/$10
# ratio (3:2) applied to the real number, and exactly the "~$3.79" figure
# this project's own share of the 2026-07-27 incident was measured at. The
# ledger was silently overstating its own numbers by 50%, which is worse
# than a smaller cap: a ledger an operator cannot trust against Console is
# not doing its one job. LLM_BUDGET_USD=10.00 (L2, since re-aligned to 8.50
# on 2026-07-28 -- see LLM_BUDGET_USD's own comment) was the real, harder-
# money conservatism at the time; this table tells the truth about what a
# call actually costs today.
#
# This table now uses the rate ACTUALLY IN EFFECT (introductory, through
# 2026-08-31). It will go stale again on 2026-09-01 -- see
# LLM_SONNET5_RATE_REVIEW_DATE below, which makes that transition a forced,
# visible `validate` warning instead of a second silent drift. Existing
# `llm_calls` rows recorded under the old (wrong) rate were backfilled once,
# by hand, to this table's values (SPEC-006A) -- see ARCHITECTURE.md's
# incident entry; this is not something `validate`/init_db redoes on every
# run, since a REAL future rate change must NOT retroactively reprice calls
# that were genuinely billed at a now-superseded rate.
LLM_PRICING: dict[str, ModelPricing] = {
    "claude-opus-5": ModelPricing(
        input_per_mtok=5.00, output_per_mtok=25.00,
        source_url=_ANTHROPIC_PRICING_SOURCE, verified_date=_ANTHROPIC_PRICING_VERIFIED_DATE,
    ),
    "claude-sonnet-5": ModelPricing(
        input_per_mtok=2.00, output_per_mtok=10.00,  # introductory rate, in effect through 2026-08-31
        source_url=_ANTHROPIC_PRICING_SOURCE, verified_date=_ANTHROPIC_PRICING_VERIFIED_DATE,
    ),
    "claude-haiku-4-5-20251001": ModelPricing(
        input_per_mtok=1.00, output_per_mtok=5.00,
        source_url=_ANTHROPIC_PRICING_SOURCE, verified_date=_ANTHROPIC_PRICING_VERIFIED_DATE,
    ),
}

# SPEC-006A: the day claude-sonnet-5's introductory rate above stops being
# true. validate.py hard-warns once date.today() reaches this date, until a
# human bumps the table above to the standard $3.00/$15.00 rate -- the
# forcing function that makes the 2026-09-01 transition a visible event
# instead of a second silent mispricing.
LLM_SONNET5_RATE_REVIEW_DATE: str = "2026-09-01"

# R8b (validate, SPEC-006 R10): informational warning when a pricing entry's
# verification date is older than this many days -- prices change, and a
# stale table makes the cap meaningless (R1's own words). Not a hard
# failure: a stale price doesn't corrupt existing data the way a broken FK
# or a hash mismatch would.
LLM_PRICING_STALENESS_WARNING_DAYS: int = 60

# R4: only the paid stage is bounded. Archiving and extraction (SPEC-001/002)
# stay unbounded on purpose (ARCHITECTURE.md §4.1).
ANALYSIS_START_DATE: str = "2025-01-01"

# R4: Notes only for V1 -- Policies are excluded (observations already
# detects policy wording changes; including them roughly doubles cost).
ANALYSIS_CATEGORIES: tuple[str, ...] = (MENUCATEGORY_NOTES,)

# R5: output schema categories/severities -- the only vocabulary
# `analyze.py` may write to `findings.category`/`findings.severity`.
# Corrected in ARCHITECTURE.md v2.4: the schema comment previously said
# red_flag|guidance|management_language|note_item, which predated this spec.
FINDING_CATEGORIES: frozenset[str] = frozenset({
    "red_flag", "accounting_change", "litigation", "concentration", "liquidity", "note_item",
})
FINDING_SEVERITIES: frozenset[str] = frozenset({"high", "medium", "low"})

# R5: prompt file identity, versioned per R3 -- a prompt change is a new
# version file, never an edit to an existing one.
SECTION_ANALYSIS_PROMPT_NAME: str = "section_analysis"
SECTION_ANALYSIS_PROMPT_VERSION: str = "v3"
PROMPTS_DIR: Path = PROJECT_ROOT / "prompts"

# R6: minimum verbatim quote length. A three-word quote matches everything
# and proves nothing.
QUOTE_MIN_LENGTH: int = 40

# Numeric support: whether a finding whose headline/detail contains a number
# absent from its source note is DISCARDED, or merely counted.
#
# False, deliberately. Quote verification grounds the quote and nothing else;
# `headline` and `detail` are ungrounded prose, and a number appearing there
# but not in the note is the most legible symptom of that. But the check
# cannot distinguish a fabricated figure from a legitimately derived one (a
# sum, a difference, a percentage the model computed correctly), so enforcing
# it immediately would discard correct findings to catch incorrect ones at an
# unknown exchange rate. The rate is measured first, per prompt version,
# reported alongside the discard rate; enforcement is a decision to take with
# the numbers in hand rather than on principle.
NUMERIC_SUPPORT_ENFORCE: bool = False

# R2: approximate token-count method for estimate_cost() and --dry-run,
# stated per R2's own requirement ("approximate token counts are fine;
# state the method"). ~4 characters per token in English is Anthropic's own
# stated rule of thumb (pricing FAQ: "1 token is approximately 4 characters
# or 0.75 words"). Not exact -- the real count comes back in the API
# response and is what actually gets recorded in llm_calls -- this is only
# for pre-call estimates.
CHARS_PER_TOKEN_ESTIMATE: float = 4.0

# R2: structured-output retry -- a response failing to parse as the
# declared JSON schema is retried once, then recorded as an error. Distinct
# from HTTP-transport retries (config.HTTP_MAX_RETRIES, reused from SEC
# client backoff config per R2) -- this is a content-validation retry, not a
# network one.
LLM_JSON_PARSE_MAX_RETRIES: int = 1

# R8: prompt-development sampling. Deterministic and reported, never a
# silent random draw -- "Iterate on 5-10 sections, never the full set."
SAMPLE_MAX_SECTIONS_FOR_DEVELOPMENT: int = 10
DEFAULT_SAMPLE_SEED: int = 20260727

# R5 output schema: the maximum output size requested per call. Raised from
# 1,024 after the v1 sampled development run (seed 20260727) truncated a
# real response mid-string: that call returned output_tokens=1024 exactly
# and failed to parse as "Unterminated string." The original 1,024 estimate
# reasoned from headline and detail length and overlooked the verbatim
# quotes, which are the long part -- a single quote can run 300+ characters,
# and a note with several findings carries several of them. Truncation is a
# particularly bad failure here because it is silent at the API level and
# surfaces only as a JSON parse error, which is indistinguishable from a
# model that simply emitted bad JSON.
LLM_MAX_OUTPUT_TOKENS: int = 4096

# 2026-07-27 live-error-analysis fix: the API's own `stop_reason` field, not
# a JSON-parse failure, is now what identifies truncation (llm.py,
# get_or_create_analysis). Prior to this fix, three of the six real ledger
# errors so far were truncation (stop_reason would have been "max_tokens")
# misfiled as generic "invalid JSON/schema" errors -- one at the OLD
# output_tokens=1024 cap (the incident LLM_MAX_OUTPUT_TOKENS's own comment
# above describes), two at the CURRENT output_tokens=4096 cap, meaning
# raising the cap once already did not fully fix truncation; it only moved
# where it binds. This is the cap a truncated call is retried at, once, per
# section, before being recorded as a distinct "truncated" outcome --
# doubled rather than raised a little, since a call that filled 4096 tokens
# with a still-incomplete response (long verbatim quotes, several findings)
# needs meaningful headroom, not a marginal bump.
LLM_TRUNCATION_RETRY_OUTPUT_TOKENS: int = 8192

# What --dry-run assumes a call will actually EMIT, as distinct from the
# ceiling above. Measured over the 26 real v2/v3 calls of the sampled
# development runs: mean 334 output tokens, p95 840, max 972 -- empty
# responses cost 14 tokens and a three-finding response under 1,000. Using
# LLM_MAX_OUTPUT_TOKENS for estimation instead put the full-set estimate at
# $20.06, over the $20 cap, for a run whose real cost is under $6: an
# estimate 12x high is not conservative, it is uninformative, and it would
# have argued against a run that was always affordable.
#
# The BUDGET CAP deliberately keeps using LLM_MAX_OUTPUT_TOKENS, not this
# value. The asymmetry is intentional: over-estimating a dry run misleads,
# whereas over-estimating at the cap only ever refuses a call slightly too
# early, which is the safe direction for a hard spending limit.
LLM_ESTIMATED_OUTPUT_TOKENS: int = 1024

# Edge case (R2/R6): "Section text exceeds the context limit -> skip, log
# with the section identity. Do not silently truncate." Every candidate
# model's real context window is far larger than this (100K+ tokens), and
# every real in-window note is a few thousand tokens (SPEC-006 pre-
# implementation review) -- this exists as a defensive, conservative
# tripwire for a pathological outlier, not a limit expected to bind in
# practice. Estimated via CHARS_PER_TOKEN_ESTIMATE, same method as
# estimate_cost.
LLM_MAX_INPUT_TOKENS_ESTIMATE: int = 150_000

# --- SPEC-007: The Grounded Brief ---

# R2: input selection caps. Ranking and slot/cap logic itself lives in
# brief.py -- these are the numeric limits only, matching the
# config.py/engine split already established (CONCEPT_REGISTRY/xbrl.py,
# RULE_REGISTRY/observations.py).
BRIEF_MAX_OBSERVATIONS: int = 8
BRIEF_MAX_FINDINGS: int = 8

# R2 v2.1: for a metric-subject rule, the slot is (rule_name, metric_category)
# -- config.METRIC_REGISTRY[subject].category, SPEC-005's existing field --
# capped at this many; for a section-subject rule the slot is (rule_name,)
# alone (SPEC-005 R11's original form, unchanged -- no metric-category
# concept for a note name). Found live, pre-implementation: a flat "2 per
# rule_name" treated metric_multi_year_extreme's ~30 metrics across 7
# categories as one thing, and silently dropped whole categories
# (working_capital, returns) from a real Amazon 10-K's selection.
BRIEF_OBSERVATION_SLOT_CAP: int = 2

# R2 v2.1: regardless of how many distinct (rule, category) slots a single
# rule_name contributes across, it may never supply more than this many of
# BRIEF_MAX_OBSERVATIONS. Binding only for a metric-subject rule touching 3+
# categories in one filing -- a section-subject rule's slot cap of 2 already
# satisfies this trivially.
BRIEF_OBSERVATION_RULE_CEILING: int = 4

# R2 v2.1: mirrors the observation slot cap, one layer up -- findings have no
# rule_name (they come from the LLM, not a deterministic rule), so the
# diversity dimension is `category` directly. Found live: uncapped, Amazon's
# real most-recent 10-K selected 8 of 8 findings from category="litigation",
# excluding the filing's only red_flag finding entirely -- the second time in
# this project a ranking with no diversity constraint has collapsed onto
# whichever dimension happened to be over-represented (the first was SPEC-005
# R11, for observations, for the identical structural reason).
BRIEF_MAX_FINDINGS_PER_CATEGORY: int = 2

# R2 v2.1: severity descending, then id ascending -- expressed directly as a
# SQL CASE expression in brief.py's ORDER BY clauses, not as a Python dict
# here (a literal SQL CASE is the actual mechanism; nothing in brief.py
# consumes this ranking as a Python value). A reproducibility rule, not a
# claim that a lower id is more analytically important -- stated explicitly
# so the SAME filing selects an IDENTICAL set on every run (R1's input_hash
# is computed over the rendered prompt, which embeds whichever set was
# selected; a non-deterministic tie-break would hash the same logical
# question differently run to run, and the cache would stop meaning
# anything).

# R3/R4: the only sentence types a stored brief_sentences.sentence_type may
# hold -- an unrecognised type is dropped in code (R4), never persisted.
BRIEF_SENTENCE_TYPES: frozenset[str] = frozenset(
    {"restatement", "juxtaposition", "aggregation", "grouping", "sourced_causal"}
)
BRIEF_MIN_SENTENCES: int = 3
BRIEF_MAX_SENTENCES: int = 6

# R4: causal connectives -- a cited SOURCE must contain one of these for a
# `sourced_causal` sentence to survive; a sentence of any OTHER type
# containing one is dropped (stating a cause outside the one type built to
# carry it). Deliberately a fixed list, not a model judgement call.
BRIEF_CAUSAL_CONNECTIVES: tuple[str, ...] = (
    "because", "due to", "driven by", "as a result of", "caused by",
    "owing to", "reflecting", "attributable to", "stemming from",
)

# R4: predictive/modal constructions -- any sentence containing one is
# dropped regardless of declared type. "No fabricated narrative is worth
# more than having a narrative at all" (Objective) applies hardest here.
BRIEF_PREDICTIVE_TERMS: tuple[str, ...] = (
    "will", "expects", "likely", "suggests", "indicates", "points to", "implies",
)

# R3: prompt file identity, versioned per the same convention as SPEC-006
# (a prompt change is a new version file, never an edit to an existing one).
BRIEF_GENERATOR_PROMPT_NAME: str = "filing_brief"
BRIEF_GENERATOR_PROMPT_VERSION: str = "v1"

# R5: the adversarial verifier pass has its OWN version, independent of the
# generator's prompt_version -- R1: "a change to the verifier can change
# which sentences survive, so it changes the output," and `input_hash`
# includes it for exactly that reason.
BRIEF_VERIFIER_PROMPT_NAME: str = "brief_verifier"
BRIEF_VERIFIER_VERSION: str = "v1"

# Output caps. Deliberately much smaller than SPEC-006's LLM_MAX_OUTPUT_TOKENS
# (4096, sized for section-analysis's long verbatim quotes) -- a brief is
# 3-6 short sentences of JSON, and the verifier's response is a compact
# per-sentence verdict list. Real-data pre-implementation review (2026-07-30)
# measured generator output ~400-600 tokens and verifier output ~150-300
# tokens for Amazon's and Micron's real most-recent 10-Ks; these caps keep
# generous headroom above that without inheriting SPEC-006's truncation
# history at a much larger size.
BRIEF_MAX_OUTPUT_TOKENS: int = 2048
BRIEF_VERIFIER_MAX_OUTPUT_TOKENS: int = 1024

# What --dry-run assumes a call will actually EMIT, as distinct from the caps
# above -- same asymmetry as LLM_ESTIMATED_OUTPUT_TOKENS vs
# LLM_MAX_OUTPUT_TOKENS (SPEC-006): a dry-run estimate should reflect real
# expected usage, not the defensive ceiling. From the same real-data
# measurement above.
BRIEF_ESTIMATED_GENERATOR_OUTPUT_TOKENS: int = 500
BRIEF_ESTIMATED_VERIFIER_OUTPUT_TOKENS: int = 250
