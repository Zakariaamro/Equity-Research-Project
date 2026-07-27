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
    """

    name: str
    inputs: tuple[str, ...]
    basis: str  # "annual" | "quarterly" | "both"
    plausible_range: tuple[float, float] | None
    needs_prior: bool = False
    category: str = ""


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
    "ebitda": MetricDef("ebitda", ("operating_income", "dep_amort"), "both", None, False, "margins"),
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
        "nopat", ("operating_income", "tax_expense", "pretax_income"), "both", None, False, "returns"
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
        "free_cash_flow", ("cfo", "capex"), "both", None, False, "capital_cash"
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
