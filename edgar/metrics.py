"""Compute financial metrics from xbrl_facts (SPEC-004).

Declarative registry in config.py (name, inputs, basis, plausible range,
needs_prior) plus a small engine + primitives here. Concept alias resolution
happens per period, never once per company (SPEC-004 R1a, ARCHITECTURE.md §6).
Beneish/DuPont components are named exceptions per R6, each a small function
built from the same primitives as everything else.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import statistics
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Callable

from edgar import config

logger = logging.getLogger(__name__)


# --- startup consistency checks (fail loudly at import, not silently at runtime) ---

_ALIAS_TO_CANONICAL: dict[str, str] = {}
for _canonical, _ci in config.CONCEPT_REGISTRY.items():
    for _alias in _ci.aliases:
        if _alias in _ALIAS_TO_CANONICAL:
            raise RuntimeError(
                f"XBRL alias {_alias!r} claimed by both "
                f"{_ALIAS_TO_CANONICAL[_alias]!r} and {_canonical!r} -- "
                "every alias must belong to exactly one canonical input"
            )
        _ALIAS_TO_CANONICAL[_alias] = _canonical

for _name, _mdef in config.METRIC_REGISTRY.items():
    for _input in _mdef.inputs:
        if _input not in config.CONCEPT_REGISTRY:
            raise RuntimeError(
                f"Metric {_name!r} declares input {_input!r}, which is not in CONCEPT_REGISTRY"
            )


@dataclass(frozen=True)
class Fact:
    value: float
    concept: str
    filed_date: str | None
    duration_days: int | None


@dataclass(frozen=True)
class MetricResult:
    value: float | None
    inputs_used: dict[str, float] = field(default_factory=dict)
    formula: str = ""
    null_reason: str | None = None


def _record(inputs_used: dict[str, float], alias: str | None, value: float | None, suffix: str = "") -> None:
    if alias is not None and value is not None:
        inputs_used[f"{alias}{suffix}"] = value


def _classify_duration(days: int | None) -> str:
    if days is None:
        return config.PERIOD_CLASS_INSTANT
    for dc in config.PERIOD_CLASSES:
        if dc.min_days <= days <= dc.max_days:
            return dc.name
    return config.PERIOD_CLASS_OTHER


def _classes_for_basis(basis: str) -> tuple[str, ...]:
    if basis == "annual":
        return ("annual",)
    if basis == "quarterly":
        return ("quarterly",)
    if basis == "both":
        return ("annual", "quarterly")
    raise ValueError(f"Unknown basis {basis!r}")


# --- loading facts, restatement selection (SPEC-004 R4) ---


def _load_facts(conn: sqlite3.Connection, cik: str) -> dict[str, dict[tuple[str | None, str], Fact]]:
    """concept -> {(period_start, period_end): Fact}, latest filed_date wins per period."""
    rows = conn.execute(
        "SELECT concept, period_start, period_end, value, filed_date, duration_days "
        "FROM xbrl_facts WHERE cik = ?",
        (cik,),
    ).fetchall()
    by_concept: dict[str, dict[tuple[str | None, str], Fact]] = {}
    for row in rows:
        key = (row["period_start"], row["period_end"])
        bucket = by_concept.setdefault(row["concept"], {})
        existing = bucket.get(key)
        if existing is None or (row["filed_date"] or "") >= (existing.filed_date or ""):
            bucket[key] = Fact(row["value"], row["concept"], row["filed_date"], row["duration_days"])
    return by_concept


def _real_fiscal_period_ends(conn: sqlite3.Connection, cik: str) -> tuple[set[str], set[str]]:
    """(annual_ends, quarterly_ends) from filings.period_end -- the authoritative set.

    SPEC-004 R3a: a 350-380 day duration is not sufficient to identify a real
    fiscal year-end -- Amazon directly tags an implicit Q4 (a genuine 3-month
    fact ending on the fiscal year-end date) for several concepts, which is
    not itself a fiscal year-end. filings.period_end comes from the SEC
    submissions API, not from duration arithmetic, and is authoritative.
    """
    annual: set[str] = set()
    quarterly: set[str] = set()
    rows = conn.execute(
        "SELECT form_type, period_end FROM filings WHERE cik = ? AND period_end IS NOT NULL", (cik,)
    ).fetchall()
    for row in rows:
        if row["form_type"] == config.TENK_FORM_TYPE:
            annual.add(row["period_end"])
        elif row["form_type"] == config.TENQ_FORM_TYPE:
            quarterly.add(row["period_end"])
    return annual, quarterly


def _build_periods_by_class(
    facts_by_concept: dict[str, dict[tuple[str | None, str], Fact]],
    real_annual_ends: set[str],
    real_quarterly_ends: set[str],
) -> dict[str, set[tuple[str, str]]]:
    periods: dict[str, set[tuple[str, str]]] = {}
    for concept, bucket in facts_by_concept.items():
        canonical = _ALIAS_TO_CANONICAL.get(concept)
        if canonical is None or config.CONCEPT_REGISTRY[canonical].instant:
            continue
        for (start, end), fact in bucket.items():
            if start is None:
                continue
            cls = _classify_duration(fact.duration_days)
            if cls == "annual" and end not in real_annual_ends:
                continue
            if cls == "quarterly" and end not in real_quarterly_ends:
                continue
            periods.setdefault(cls, set()).add((start, end))
    return periods


# --- per-period alias resolution (SPEC-004 R1a) ---


def _resolve(
    canonical: str,
    facts_by_concept: dict[str, dict[tuple[str | None, str], Fact]],
    period_start: str | None,
    period_end: str,
) -> tuple[float | None, str | None]:
    """Try each alias in priority order; the first with a value for THIS exact
    period wins. Never resolved once for a company's whole history."""
    ci = config.CONCEPT_REGISTRY[canonical]
    key = (None, period_end) if ci.instant else (period_start, period_end)
    for alias in ci.aliases:
        bucket = facts_by_concept.get(alias)
        if bucket and key in bucket:
            return bucket[key].value, alias
    return None, None


def _resolve_at_end(
    facts_by_concept: dict[str, dict[tuple[str | None, str], Fact]],
    canonical: str,
    end: str,
    wanted_cls: str,
) -> tuple[float | None, str | None, str | None]:
    """Find a fact for `canonical` ending at `end` whose OWN duration
    classifies as `wanted_cls`, without knowing its period_start in advance
    -- unlike `_resolve`, which requires an exact (period_start, period_end)
    key. Used to discover a fiscal quarter's own start (SPEC-008 C4:
    `_fiscal_year_start` finds Q1's period_start this way, since Q1's start
    IS the fiscal year's start, whether the company tags cumulatively or
    not). Returns (value, alias, period_start)."""
    ci = config.CONCEPT_REGISTRY[canonical]
    for alias in ci.aliases:
        for (start, fact_end), fact in facts_by_concept.get(alias, {}).items():
            if fact_end == end and start is not None and _classify_duration(fact.duration_days) == wanted_cls:
                return fact.value, alias, start
    return None, None, None


def _resolve_gross_profit(
    facts_by_concept: dict, start: str, end: str
) -> tuple[float | None, str, dict[str, float]]:
    """(value, formula fragment, inputs_used) -- direct GrossProfit, else revenue - cogs."""
    inputs_used: dict[str, float] = {}
    val, alias = _resolve("gross_profit", facts_by_concept, start, end)
    if val is not None:
        _record(inputs_used, alias, val)
        return val, alias, inputs_used
    rev, rev_alias = _resolve("revenue", facts_by_concept, start, end)
    cogs, cogs_alias = _resolve("cogs", facts_by_concept, start, end)
    _record(inputs_used, rev_alias, rev)
    _record(inputs_used, cogs_alias, cogs)
    if rev is None or cogs is None:
        return None, "gross_profit", inputs_used
    return rev - cogs, f"({rev_alias} - {cogs_alias})", inputs_used


def _resolve_ppe_net(facts_by_concept: dict, end: str) -> tuple[float | None, str | None, dict[str, float]]:
    """(value, alias-or-note, inputs_used) -- pure ppe_net, else the broader
    ppe_and_lease_net (SPEC-004 R1d: a separate canonical input, not an alias;
    Micron folds finance-lease ROU assets into this line from FY2021)."""
    inputs_used: dict[str, float] = {}
    val, alias = _resolve("ppe_net", facts_by_concept, None, end)
    if val is not None:
        _record(inputs_used, alias, val)
        return val, alias, inputs_used
    broad, broad_alias = _resolve("ppe_and_lease_net", facts_by_concept, None, end)
    if broad is not None:
        _record(inputs_used, broad_alias, broad)
        return broad, f"{broad_alias} [ppe_net absent, fell back to ppe_and_lease_net]", inputs_used
    return None, None, inputs_used


def _resolve_depreciation(
    facts_by_concept: dict, start: str, end: str
) -> tuple[float | None, str | None, dict[str, float]]:
    """(value, alias-or-note, inputs_used) -- pure depreciation, else dep_amort (DD&A)
    (SPEC-004 R1f: a separate canonical input, not an alias; DD&A differs from pure
    depreciation by 20-40% -- it also includes amortization of intangibles)."""
    inputs_used: dict[str, float] = {}
    val, alias = _resolve("depreciation", facts_by_concept, start, end)
    if val is not None:
        _record(inputs_used, alias, val)
        return val, alias, inputs_used
    da, da_alias = _resolve("dep_amort", facts_by_concept, start, end)
    if da is not None:
        _record(inputs_used, da_alias, da)
        return da, f"{da_alias} [pure depreciation absent, fell back to dep_amort]", inputs_used
    return None, None, inputs_used


def _resolve_borrowings(facts_by_concept: dict, end: str) -> tuple[float | None, str, dict[str, float]]:
    """(value, formula fragment, inputs_used) -- combined tag preferred, else sum components.

    "Borrowings" only -- notes, term loans, bonds. Finance leases are a
    separate, additive component; see _resolve_total_debt.
    """
    inputs_used: dict[str, float] = {}
    val, alias = _resolve("total_debt", facts_by_concept, None, end)
    if val is not None:
        _record(inputs_used, alias, val)
        return val, alias, inputs_used
    dn, dn_alias = _resolve("debt_noncurrent", facts_by_concept, None, end)
    dc, dc_alias = _resolve("debt_current", facts_by_concept, None, end)
    _record(inputs_used, dn_alias, dn)
    _record(inputs_used, dc_alias, dc)
    if dn is not None and dc is not None:
        return dn + dc, f"({dn_alias} + {dc_alias}) [no combined tag for period]", inputs_used
    return None, "borrowings", inputs_used


def _resolve_total_debt(facts_by_concept: dict, end: str) -> tuple[float | None, str, dict[str, float]]:
    """(value, formula fragment, inputs_used) -- borrowings + finance lease liabilities.

    SPEC-004 R1b/R1h: Micron's real "Long-term debt" balance sheet line is
    borrowings (LongTermDebt) PLUS FinanceLeaseLiabilityNoncurrent, not
    borrowings alone. `borrowings` is a primary measure: NULL if absent,
    same as ever. Finance lease liabilities are different -- an additive
    component of a total the filer is REQUIRED to disclose under ASC 842,
    so an absent one is treated as $0, not NULL (confirmed live: NVIDIA
    tags OperatingLeaseLiability{Noncurrent,Current} but never
    FinanceLeaseLiability{Noncurrent,Current} -- it has none, this is not
    an ingestion gap). `formula` records whether each component was
    observed or assumed zero, so the distinction stays visible.
    """
    borrowings, borrowings_note, inputs_used = _resolve_borrowings(facts_by_concept, end)
    if borrowings is None:
        return None, borrowings_note, inputs_used
    fl_nc, fl_nc_alias = _resolve("finance_lease_liability_noncurrent", facts_by_concept, None, end)
    fl_c, fl_c_alias = _resolve("finance_lease_liability_current", facts_by_concept, None, end)
    if fl_nc is not None:
        _record(inputs_used, fl_nc_alias, fl_nc)
        fl_nc_term = fl_nc_alias
    else:
        fl_nc_term = "finance_lease_liability_noncurrent=$0 [assumed, ASC 842]"
    if fl_c is not None:
        _record(inputs_used, fl_c_alias, fl_c)
        fl_c_term = fl_c_alias
    else:
        fl_c_term = "finance_lease_liability_current=$0 [assumed, ASC 842]"
    total = borrowings + (fl_nc or 0.0) + (fl_c or 0.0)
    note = f"({borrowings_note} + {fl_nc_term} + {fl_c_term})"
    return total, note, inputs_used


# --- fiscal-period prior-period matching (SPEC-004 R9: fiscal, never calendar) ---

_YOY_OFFSET_DAYS = 365
_YOY_TOLERANCE_DAYS = 20
_QOQ_OFFSET_DAYS = 91
_QOQ_TOLERANCE_DAYS = 15


def _find_prior(
    candidates: set[tuple[str, str]],
    current_start: str,
    current_end: str,
    offset_days: int,
    tolerance_days: int,
) -> tuple[str, str] | None:
    """Closest same-duration-class period to (current_end - offset_days), within tolerance.

    Matches on fiscal alignment (same duration class, end date closest to the
    target offset) rather than calendar dates or the API's fy/fp fields --
    both NVIDIA and Micron have floating 52/53-week years, so a fixed
    365-day subtraction with a tight tolerance would miss real prior periods.
    """
    current_end_date = date.fromisoformat(current_end)
    target = current_end_date - timedelta(days=offset_days)
    best: tuple[str, str] | None = None
    best_diff: int | None = None
    for start, end in candidates:
        if (start, end) == (current_start, current_end):
            continue
        diff = abs((date.fromisoformat(end) - target).days)
        if diff <= tolerance_days and (best_diff is None or diff < best_diff):
            best, best_diff = (start, end), diff
    return best


def _find_prior_yoy(candidates: set[tuple[str, str]], start: str, end: str) -> tuple[str, str] | None:
    return _find_prior(candidates, start, end, _YOY_OFFSET_DAYS, _YOY_TOLERANCE_DAYS)


def _find_prior_qoq(candidates: set[tuple[str, str]], start: str, end: str) -> tuple[str, str] | None:
    return _find_prior(candidates, start, end, _QOQ_OFFSET_DAYS, _QOQ_TOLERANCE_DAYS)


# --- generic primitives for simple ratios and YoY-style growth metrics ---


def _simple_ratio(num_canonical: str, den_canonical: str, guard: Callable[[float, float], str | None] | None = None):
    def compute(facts, periods_by_class, start, end, cls) -> MetricResult:
        num_ci = config.CONCEPT_REGISTRY[num_canonical]
        den_ci = config.CONCEPT_REGISTRY[den_canonical]
        num, num_alias = _resolve(num_canonical, facts, None if num_ci.instant else start, end)
        den, den_alias = _resolve(den_canonical, facts, None if den_ci.instant else start, end)
        inputs_used: dict[str, float] = {}
        _record(inputs_used, num_alias, num)
        _record(inputs_used, den_alias, den)
        formula = f"{num_alias or num_canonical} / {den_alias or den_canonical}"
        if num is None:
            return MetricResult(None, inputs_used, formula, f"{num_canonical} missing")
        if den is None:
            return MetricResult(None, inputs_used, formula, f"{den_canonical} missing")
        if den == 0:
            return MetricResult(None, inputs_used, formula, f"{den_canonical} is zero")
        if guard is not None:
            reason = guard(num, den)
            if reason:
                return MetricResult(None, inputs_used, formula, reason)
        return MetricResult(num / den, inputs_used, formula, None)

    return compute


def _yoy_metric(canonical: str, prior_finder, extra_guard: Callable[[float], str | None] | None = None):
    def compute(facts, periods_by_class, start, end, cls) -> MetricResult:
        prior = prior_finder(periods_by_class.get(cls, set()), start, end)
        cur, cur_alias = _resolve(canonical, facts, start, end)
        inputs_used: dict[str, float] = {}
        _record(inputs_used, cur_alias, cur, "_t")
        base_formula = f"{cur_alias or canonical}_t / {canonical}_t-1 - 1"
        if prior is None:
            return MetricResult(None, inputs_used, base_formula, "prior period not found")
        p_start, p_end = prior
        prev, prev_alias = _resolve(canonical, facts, p_start, p_end)
        _record(inputs_used, prev_alias, prev, "_t-1")
        formula = f"{cur_alias or canonical}_t / {prev_alias or canonical}_t-1 - 1"
        if cur is None:
            return MetricResult(None, inputs_used, formula, f"{canonical} missing (current period)")
        if prev is None:
            return MetricResult(None, inputs_used, formula, f"{canonical} missing (prior period)")
        if extra_guard is not None:
            reason = extra_guard(prev)
            if reason:
                return MetricResult(None, inputs_used, formula, reason)
        if prev == 0:
            return MetricResult(None, inputs_used, formula, "prior value is zero")
        return MetricResult(cur / prev - 1, inputs_used, formula, None)

    return compute


def _ratio_of_ratios_index(num_canonical: str, den_canonical: str):
    """(num/den)_t / (num/den)_t-1 -- DSRI and SGAI both match this shape exactly."""

    def compute(facts, periods_by_class, start, end, cls) -> MetricResult:
        prior = _find_prior_yoy(periods_by_class.get("annual", set()), start, end)
        formula_stub = f"({num_canonical}/{den_canonical})_t / same_t-1"
        if prior is None:
            return MetricResult(None, {}, formula_stub, "prior period not found")
        p_start, p_end = prior
        num_ci = config.CONCEPT_REGISTRY[num_canonical]
        den_ci = config.CONCEPT_REGISTRY[den_canonical]
        n_t, n_t_alias = _resolve(num_canonical, facts, None if num_ci.instant else start, end)
        d_t, d_t_alias = _resolve(den_canonical, facts, None if den_ci.instant else start, end)
        n_p, n_p_alias = _resolve(num_canonical, facts, None if num_ci.instant else p_start, p_end)
        d_p, d_p_alias = _resolve(den_canonical, facts, None if den_ci.instant else p_start, p_end)
        inputs_used: dict[str, float] = {}
        _record(inputs_used, n_t_alias, n_t, "_t")
        _record(inputs_used, d_t_alias, d_t, "_t")
        _record(inputs_used, n_p_alias, n_p, "_t-1")
        _record(inputs_used, d_p_alias, d_p, "_t-1")
        formula = (
            f"({n_t_alias or num_canonical}_t/{d_t_alias or den_canonical}_t) / "
            f"({n_p_alias or num_canonical}_t-1/{d_p_alias or den_canonical}_t-1)"
        )
        if n_t is None or d_t is None or n_p is None or d_p is None:
            return MetricResult(None, inputs_used, formula, f"{num_canonical} or {den_canonical} missing in t or t-1")
        if d_t == 0 or d_p == 0:
            return MetricResult(None, inputs_used, formula, f"{den_canonical} is zero in t or t-1")
        ratio_t, ratio_p = n_t / d_t, n_p / d_p
        if ratio_p == 0:
            return MetricResult(None, inputs_used, formula, "prior-period ratio is zero")
        return MetricResult(ratio_t / ratio_p, inputs_used, formula, None)

    return compute


# --- Growth ---

_compute_revenue_yoy = _yoy_metric("revenue", _find_prior_yoy)
_compute_revenue_qoq = _yoy_metric("revenue", _find_prior_qoq)
_compute_operating_income_yoy = _yoy_metric(
    "operating_income", _find_prior_yoy, extra_guard=lambda prev: "prior period <= 0" if prev <= 0 else None
)
_compute_eps_diluted_yoy = _yoy_metric(
    "eps_diluted", _find_prior_yoy, extra_guard=lambda prev: "prior period <= 0" if prev <= 0 else None
)


def _compute_inventory_growth_less_revenue_growth(facts, periods_by_class, start, end, cls) -> MetricResult:
    inv = _yoy_metric("inventory", _find_prior_yoy)(facts, periods_by_class, start, end, cls)
    rev = _yoy_metric("revenue", _find_prior_yoy)(facts, periods_by_class, start, end, cls)
    inputs_used = {**inv.inputs_used, **rev.inputs_used}
    formula = f"({inv.formula}) - ({rev.formula})"
    if inv.value is None:
        return MetricResult(None, inputs_used, formula, f"inventory_yoy unavailable: {inv.null_reason}")
    if rev.value is None:
        return MetricResult(None, inputs_used, formula, f"revenue_yoy unavailable: {rev.null_reason}")
    return MetricResult(inv.value - rev.value, inputs_used, formula, None)


# --- Margins ---

_compute_operating_margin = _simple_ratio("operating_income", "revenue")
_compute_net_margin = _simple_ratio("net_income", "revenue")
_compute_rnd_intensity = _simple_ratio("rnd_expense", "revenue")
_compute_sga_intensity = _simple_ratio("sga_expense", "revenue")


def _compute_gross_margin(facts, periods_by_class, start, end, cls) -> MetricResult:
    gp, gp_note, inputs_used = _resolve_gross_profit(facts, start, end)
    rev, rev_alias = _resolve("revenue", facts, start, end)
    _record(inputs_used, rev_alias, rev)
    formula = f"({gp_note}) / {rev_alias or 'revenue'}"
    if gp is None:
        return MetricResult(None, inputs_used, formula, "gross_profit unresolved and revenue/cogs fallback unavailable")
    if rev is None:
        return MetricResult(None, inputs_used, formula, "revenue missing")
    if rev == 0:
        return MetricResult(None, inputs_used, formula, "revenue is zero")
    return MetricResult(gp / rev, inputs_used, formula, None)


def _compute_ebitda(facts, periods_by_class, start, end, cls) -> MetricResult:
    oi, oi_alias = _resolve("operating_income", facts, start, end)
    da, da_alias = _resolve("dep_amort", facts, start, end)
    inputs_used: dict[str, float] = {}
    _record(inputs_used, oi_alias, oi)
    _record(inputs_used, da_alias, da)
    formula = f"{oi_alias or 'operating_income'} + {da_alias or 'dep_amort'}"
    if oi is None:
        return MetricResult(None, inputs_used, formula, "operating_income missing")
    if da is None:
        return MetricResult(None, inputs_used, formula, "dep_amort missing")
    return MetricResult(oi + da, inputs_used, formula, None)


def _compute_ebitda_margin(facts, periods_by_class, start, end, cls) -> MetricResult:
    ebitda = _compute_ebitda(facts, periods_by_class, start, end, cls)
    rev, rev_alias = _resolve("revenue", facts, start, end)
    inputs_used = dict(ebitda.inputs_used)
    _record(inputs_used, rev_alias, rev)
    formula = f"({ebitda.formula}) / {rev_alias or 'revenue'}"
    if ebitda.value is None:
        return MetricResult(None, inputs_used, formula, f"ebitda unavailable: {ebitda.null_reason}")
    if rev is None:
        return MetricResult(None, inputs_used, formula, "revenue missing")
    if rev == 0:
        return MetricResult(None, inputs_used, formula, "revenue is zero")
    return MetricResult(ebitda.value / rev, inputs_used, formula, None)


def _compute_incremental_gross_margin(facts, periods_by_class, start, end, cls) -> MetricResult:
    prior = _find_prior_yoy(periods_by_class.get(cls, set()), start, end)
    if prior is None:
        return MetricResult(None, {}, "Δgross_profit / Δrevenue", "prior period not found")
    p_start, p_end = prior
    gp_t, gp_t_note, inputs_t = _resolve_gross_profit(facts, start, end)
    gp_p, gp_p_note, inputs_p = _resolve_gross_profit(facts, p_start, p_end)
    rev_t, rev_t_alias = _resolve("revenue", facts, start, end)
    rev_p, rev_p_alias = _resolve("revenue", facts, p_start, p_end)
    inputs_used = {f"{k}_t": v for k, v in inputs_t.items()}
    inputs_used.update({f"{k}_t-1": v for k, v in inputs_p.items()})
    _record(inputs_used, rev_t_alias, rev_t, "_t")
    _record(inputs_used, rev_p_alias, rev_p, "_t-1")
    formula = f"(({gp_t_note})_t - ({gp_p_note})_t-1) / ({rev_t_alias or 'revenue'}_t - {rev_p_alias or 'revenue'}_t-1)"
    if gp_t is None or gp_p is None or rev_t is None or rev_p is None:
        return MetricResult(None, inputs_used, formula, "gross_profit or revenue missing in t or t-1")
    delta_rev = rev_t - rev_p
    if rev_t != 0 and abs(delta_rev) < config.INCREMENTAL_MARGIN_MIN_REVENUE_DELTA_PCT * abs(rev_t):
        return MetricResult(None, inputs_used, formula, "|delta revenue| < 1% of revenue")
    if delta_rev == 0:
        return MetricResult(None, inputs_used, formula, "delta revenue is zero")
    return MetricResult((gp_t - gp_p) / delta_rev, inputs_used, formula, None)


# --- Returns ---

_compute_effective_tax_rate = _simple_ratio("tax_expense", "pretax_income")
_compute_roe = _simple_ratio("net_income", "equity", guard=lambda n, d: "equity is negative" if d < 0 else None)
_compute_asset_turnover = _simple_ratio("revenue", "total_assets")
_compute_equity_multiplier = _simple_ratio(
    "total_assets", "equity", guard=lambda n, d: "equity is negative" if d < 0 else None
)
def _compute_fixed_asset_turnover(facts, periods_by_class, start, end, cls) -> MetricResult:
    rev, rev_alias = _resolve("revenue", facts, start, end)
    ppe, ppe_note, inputs_used = _resolve_ppe_net(facts, end)
    _record(inputs_used, rev_alias, rev)
    formula = f"{rev_alias or 'revenue'} / {ppe_note or 'ppe_net'}"
    if rev is None:
        return MetricResult(None, inputs_used, formula, "revenue missing")
    if ppe is None:
        return MetricResult(None, inputs_used, formula, "ppe_net and ppe_and_lease_net both missing")
    if ppe == 0:
        return MetricResult(None, inputs_used, formula, "ppe_net is zero")
    return MetricResult(rev / ppe, inputs_used, formula, None)


def _compute_nopat(facts, periods_by_class, start, end, cls) -> MetricResult:
    oi, oi_alias = _resolve("operating_income", facts, start, end)
    tax, tax_alias = _resolve("tax_expense", facts, start, end)
    pretax, pretax_alias = _resolve("pretax_income", facts, start, end)
    inputs_used: dict[str, float] = {}
    _record(inputs_used, oi_alias, oi)
    _record(inputs_used, tax_alias, tax)
    _record(inputs_used, pretax_alias, pretax)
    formula = f"{oi_alias or 'operating_income'} * (1 - {tax_alias or 'tax_expense'}/{pretax_alias or 'pretax_income'})"
    if oi is None:
        return MetricResult(None, inputs_used, formula, "operating_income missing")
    if tax is None or pretax is None:
        return MetricResult(None, inputs_used, formula, "effective_tax_rate unavailable (tax_expense or pretax_income missing)")
    if pretax == 0:
        return MetricResult(None, inputs_used, formula, "pretax_income is zero")
    return MetricResult(oi * (1 - tax / pretax), inputs_used, formula, None)


def _compute_invested_capital(facts, periods_by_class, start, end, cls) -> MetricResult:
    debt, debt_note, inputs_used = _resolve_total_debt(facts, end)
    equity, equity_alias = _resolve("equity", facts, None, end)
    cash, cash_alias = _resolve("cash", facts, None, end)
    _record(inputs_used, equity_alias, equity)
    _record(inputs_used, cash_alias, cash)
    formula = f"({debt_note}) + {equity_alias or 'equity'} - {cash_alias or 'cash'}"
    if debt is None:
        return MetricResult(None, inputs_used, formula, "total_debt unresolved (no combined tag and components incomplete)")
    if equity is None:
        return MetricResult(None, inputs_used, formula, "equity missing")
    if cash is None:
        return MetricResult(None, inputs_used, formula, "cash missing")
    return MetricResult(debt + equity - cash, inputs_used, formula, None)


def _compute_roic(facts, periods_by_class, start, end, cls) -> MetricResult:
    nopat = _compute_nopat(facts, periods_by_class, start, end, cls)
    ic = _compute_invested_capital(facts, periods_by_class, start, end, cls)
    inputs_used = {**nopat.inputs_used, **ic.inputs_used}
    formula = f"({nopat.formula}) / ({ic.formula})"
    if nopat.value is None:
        return MetricResult(None, inputs_used, formula, f"nopat unavailable: {nopat.null_reason}")
    if ic.value is None:
        return MetricResult(None, inputs_used, formula, f"invested_capital unavailable: {ic.null_reason}")
    if ic.value == 0:
        return MetricResult(None, inputs_used, formula, "invested_capital is zero")
    return MetricResult(nopat.value / ic.value, inputs_used, formula, None)


# --- Capital and cash ---

_compute_capex_to_revenue = _simple_ratio("capex", "revenue")
def _compute_capex_to_depreciation(facts, periods_by_class, start, end, cls) -> MetricResult:
    capex, capex_alias = _resolve("capex", facts, start, end)
    dep, dep_note, inputs_used = _resolve_depreciation(facts, start, end)
    _record(inputs_used, capex_alias, capex)
    formula = f"{capex_alias or 'capex'} / {dep_note or 'depreciation'}"
    if capex is None:
        return MetricResult(None, inputs_used, formula, "capex missing")
    if dep is None:
        return MetricResult(None, inputs_used, formula, "depreciation and dep_amort both missing")
    if dep == 0:
        return MetricResult(None, inputs_used, formula, "depreciation is zero")
    return MetricResult(capex / dep, inputs_used, formula, None)
_compute_sbc_to_revenue = _simple_ratio("sbc", "revenue")


def _compute_free_cash_flow(facts, periods_by_class, start, end, cls) -> MetricResult:
    cfo, cfo_alias = _resolve("cfo", facts, start, end)
    capex, capex_alias = _resolve("capex", facts, start, end)
    inputs_used: dict[str, float] = {}
    _record(inputs_used, cfo_alias, cfo)
    _record(inputs_used, capex_alias, capex)
    formula = f"{cfo_alias or 'cfo'} - {capex_alias or 'capex'}"
    if cfo is None:
        return MetricResult(None, inputs_used, formula, "cfo missing")
    if capex is None:
        return MetricResult(None, inputs_used, formula, "capex missing")
    return MetricResult(cfo - capex, inputs_used, formula, None)


def _compute_fcfe(facts, periods_by_class, start, end, cls) -> MetricResult:
    """SPEC-008-batch-2 item 2: exact arithmetic on filed lines, same status
    as free_cash_flow -- no assumption involved."""
    cfo, cfo_alias = _resolve("cfo", facts, start, end)
    capex, capex_alias = _resolve("capex", facts, start, end)
    debt_issued, debt_issued_alias = _resolve("debt_issued", facts, start, end)
    debt_repaid, debt_repaid_alias = _resolve("debt_repaid", facts, start, end)
    inputs_used: dict[str, float] = {}
    _record(inputs_used, cfo_alias, cfo)
    _record(inputs_used, capex_alias, capex)
    _record(inputs_used, debt_issued_alias, debt_issued)
    _record(inputs_used, debt_repaid_alias, debt_repaid)
    formula = (
        f"{cfo_alias or 'cfo'} - {capex_alias or 'capex'} + "
        f"({debt_issued_alias or 'debt_issued'} - {debt_repaid_alias or 'debt_repaid'})"
    )
    if cfo is None:
        return MetricResult(None, inputs_used, formula, "cfo missing")
    if capex is None:
        return MetricResult(None, inputs_used, formula, "capex missing")
    if debt_issued is None:
        return MetricResult(None, inputs_used, formula, "debt_issued missing")
    if debt_repaid is None:
        return MetricResult(None, inputs_used, formula, "debt_repaid missing")
    return MetricResult(cfo - capex + (debt_issued - debt_repaid), inputs_used, formula, None)


# SPEC-008-batch-2 item 2: matches batch 1 item 2's own 10%-of-typical-
# magnitude threshold (_GROWTH_NEAR_ZERO_BASE_FRACTION in dashboard/data.py)
# -- same reasoning, reimplemented here rather than imported, since edgar/
# never imports from dashboard/ (dashboard reads, edgar writes -- ARCHITECTURE.md).
_FCFF_NEAR_ZERO_PRETAX_FRACTION = 0.10


def _median_abs_annual_pretax_income(facts, periods_by_class) -> float | None:
    """This company's own typical annual pre-tax income magnitude -- the
    reference `_compute_fcff_tax_rate` fails closed against when a given
    year's pre-tax income is technically positive but too small to divide
    by meaningfully. Computed from genuine annual-duration facts (the
    ANNUAL basis's own natural source); the quarterly/TTM path reconstructs
    the equivalent figure from four discrete quarters instead, see
    compute_discrete_quarter_metrics."""
    values = [
        v for (p_start, p_end) in periods_by_class.get("annual", set())
        if (v := _resolve("pretax_income", facts, p_start, p_end)[0]) is not None
    ]
    if not values:
        return None
    return statistics.median(abs(v) for v in values)


def _compute_fcff_tax_rate(facts, periods_by_class, start, end, cls) -> MetricResult:
    """SPEC-008-batch-2 item 2: annual only -- this fiscal year's own
    effective rate (Damodaran: historical FCF should use the actual
    realised rate, not a forecasting-style marginal one). The quarterly
    trailing-twelve-month counterpart lives entirely in
    compute_discrete_quarter_metrics (fcff_tax_rate_discrete), since it
    needs the trailing four DISCRETE quarters, not a single duration this
    per-period engine ever sees.

    Fails closed, never substitutes a statutory rate or clamps into a
    band: pre-tax income <= 0, or positive but near zero relative to this
    company's own typical annual pre-tax income (the same fractional test
    batch 1 item 2 established for growth -- a meaningless denominator is
    a meaningless denominator whether the numerator is a growth base or a
    tax rate's own base)."""
    tax, tax_alias = _resolve("tax_expense", facts, start, end)
    pretax, pretax_alias = _resolve("pretax_income", facts, start, end)
    inputs_used: dict[str, float] = {}
    _record(inputs_used, tax_alias, tax)
    _record(inputs_used, pretax_alias, pretax)
    formula = f"{tax_alias or 'tax_expense'} / {pretax_alias or 'pretax_income'} (this fiscal year)"
    if tax is None or pretax is None:
        return MetricResult(None, inputs_used, formula, "tax_expense or pretax_income missing")
    if pretax <= 0:
        return MetricResult(
            None, inputs_used, formula,
            f"pre-tax income is not positive ({pretax:,.0f}) -- effective tax rate not meaningful",
        )
    typical = _median_abs_annual_pretax_income(facts, periods_by_class)
    if typical and pretax < _FCFF_NEAR_ZERO_PRETAX_FRACTION * typical:
        return MetricResult(
            None, inputs_used, formula,
            f"pre-tax income ({pretax:,.0f}) is near zero relative to this company's typical annual "
            f"pre-tax income ({typical:,.0f}) -- effective tax rate not meaningful",
        )
    return MetricResult(tax / pretax, inputs_used, formula, None)


def _compute_fcff(facts, periods_by_class, start, end, cls) -> MetricResult:
    """SPEC-008-batch-2 item 2: NOT exact arithmetic -- rests on
    _compute_fcff_tax_rate's constructed rate. Fails closed whenever that
    rate is unavailable, same as any other missing input; never falls back
    to a statutory or clamped rate."""
    cfo, cfo_alias = _resolve("cfo", facts, start, end)
    capex, capex_alias = _resolve("capex", facts, start, end)
    interest, interest_alias = _resolve("interest_expense", facts, start, end)
    rate_result = _compute_fcff_tax_rate(facts, periods_by_class, start, end, cls)
    inputs_used: dict[str, float] = {}
    _record(inputs_used, cfo_alias, cfo)
    _record(inputs_used, capex_alias, capex)
    _record(inputs_used, interest_alias, interest)
    inputs_used.update(rate_result.inputs_used)
    if rate_result.value is not None:
        inputs_used["fcff_tax_rate"] = rate_result.value
    rate_str = f"{rate_result.value:.4f}" if rate_result.value is not None else "unavailable"
    formula = f"{cfo_alias or 'cfo'} + {interest_alias or 'interest_expense'}*(1-{rate_str}) - {capex_alias or 'capex'}"
    if cfo is None:
        return MetricResult(None, inputs_used, formula, "cfo missing")
    if capex is None:
        return MetricResult(None, inputs_used, formula, "capex missing")
    if interest is None:
        return MetricResult(None, inputs_used, formula, "interest_expense missing")
    if rate_result.value is None:
        return MetricResult(None, inputs_used, formula, f"effective tax rate unavailable: {rate_result.null_reason}")
    return MetricResult(cfo + interest * (1 - rate_result.value) - capex, inputs_used, formula, None)


def _compute_fcf_margin(facts, periods_by_class, start, end, cls) -> MetricResult:
    fcf = _compute_free_cash_flow(facts, periods_by_class, start, end, cls)
    rev, rev_alias = _resolve("revenue", facts, start, end)
    inputs_used = dict(fcf.inputs_used)
    _record(inputs_used, rev_alias, rev)
    formula = f"({fcf.formula}) / {rev_alias or 'revenue'}"
    if fcf.value is None:
        return MetricResult(None, inputs_used, formula, f"free_cash_flow unavailable: {fcf.null_reason}")
    if rev is None:
        return MetricResult(None, inputs_used, formula, "revenue missing")
    if rev == 0:
        return MetricResult(None, inputs_used, formula, "revenue is zero")
    return MetricResult(fcf.value / rev, inputs_used, formula, None)


def _compute_fcf_conversion(facts, periods_by_class, start, end, cls) -> MetricResult:
    fcf = _compute_free_cash_flow(facts, periods_by_class, start, end, cls)
    ni, ni_alias = _resolve("net_income", facts, start, end)
    inputs_used = dict(fcf.inputs_used)
    _record(inputs_used, ni_alias, ni)
    formula = f"({fcf.formula}) / {ni_alias or 'net_income'}"
    if fcf.value is None:
        return MetricResult(None, inputs_used, formula, f"free_cash_flow unavailable: {fcf.null_reason}")
    if ni is None:
        return MetricResult(None, inputs_used, formula, "net_income missing")
    if ni <= 0:
        return MetricResult(None, inputs_used, formula, "net_income <= 0")
    return MetricResult(fcf.value / ni, inputs_used, formula, None)


def _compute_depreciation_rate(facts, periods_by_class, start, end, cls) -> MetricResult:
    dep, dep_note, inputs_used = _resolve_depreciation(facts, start, end)
    ppe_g, ppe_g_alias = _resolve("ppe_gross", facts, None, end)
    if ppe_g is not None:
        denom_alias, denom_val, note = ppe_g_alias, ppe_g, ""
        _record(inputs_used, ppe_g_alias, ppe_g)
    else:
        denom_val, denom_note, ppe_inputs = _resolve_ppe_net(facts, end)
        inputs_used.update(ppe_inputs)
        denom_alias = denom_note
        note = " [ppe_gross absent, fell back to ppe_net/ppe_and_lease_net]" if denom_val is not None else ""
    formula = f"{dep_note or 'depreciation'} / {denom_alias or 'ppe_gross/ppe_net'}{note}"
    if dep is None:
        return MetricResult(None, inputs_used, formula, "depreciation and dep_amort both missing")
    if denom_val is None:
        return MetricResult(None, inputs_used, formula, "ppe_gross, ppe_net, and ppe_and_lease_net all missing")
    if denom_val == 0:
        return MetricResult(None, inputs_used, formula, "denominator is zero")
    return MetricResult(dep / denom_val, inputs_used, formula, None)


# --- Working capital (annual only) ---


def _compute_days_inventory(facts, periods_by_class, start, end, cls) -> MetricResult:
    inv, inv_alias = _resolve("inventory", facts, None, end)
    cogs, cogs_alias = _resolve("cogs", facts, start, end)
    inputs_used: dict[str, float] = {}
    _record(inputs_used, inv_alias, inv)
    _record(inputs_used, cogs_alias, cogs)
    formula = f"{inv_alias or 'inventory'} / {cogs_alias or 'cogs'} * 365"
    if inv is None:
        return MetricResult(None, inputs_used, formula, "inventory missing")
    if cogs is None:
        return MetricResult(None, inputs_used, formula, "cogs missing")
    if cogs == 0:
        return MetricResult(None, inputs_used, formula, "cogs is zero")
    return MetricResult(inv / cogs * 365, inputs_used, formula, None)


def _compute_days_receivables(facts, periods_by_class, start, end, cls) -> MetricResult:
    rec, rec_alias = _resolve("receivables", facts, None, end)
    rev, rev_alias = _resolve("revenue", facts, start, end)
    inputs_used: dict[str, float] = {}
    _record(inputs_used, rec_alias, rec)
    _record(inputs_used, rev_alias, rev)
    formula = f"{rec_alias or 'receivables'} / {rev_alias or 'revenue'} * 365"
    if rec is None:
        return MetricResult(None, inputs_used, formula, "receivables missing")
    if rev is None:
        return MetricResult(None, inputs_used, formula, "revenue missing")
    if rev == 0:
        return MetricResult(None, inputs_used, formula, "revenue is zero")
    return MetricResult(rec / rev * 365, inputs_used, formula, None)


def _compute_days_payables(facts, periods_by_class, start, end, cls) -> MetricResult:
    pay, pay_alias = _resolve("payables", facts, None, end)
    cogs, cogs_alias = _resolve("cogs", facts, start, end)
    inputs_used: dict[str, float] = {}
    _record(inputs_used, pay_alias, pay)
    _record(inputs_used, cogs_alias, cogs)
    formula = f"{pay_alias or 'payables'} / {cogs_alias or 'cogs'} * 365"
    if pay is None:
        return MetricResult(None, inputs_used, formula, "payables missing")
    if cogs is None:
        return MetricResult(None, inputs_used, formula, "cogs missing")
    if cogs == 0:
        return MetricResult(None, inputs_used, formula, "cogs is zero")
    return MetricResult(pay / cogs * 365, inputs_used, formula, None)


def _compute_cash_conversion_cycle(facts, periods_by_class, start, end, cls) -> MetricResult:
    di = _compute_days_inventory(facts, periods_by_class, start, end, cls)
    dr = _compute_days_receivables(facts, periods_by_class, start, end, cls)
    dp = _compute_days_payables(facts, periods_by_class, start, end, cls)
    inputs_used = {**di.inputs_used, **dr.inputs_used, **dp.inputs_used}
    formula = f"({di.formula}) + ({dr.formula}) - ({dp.formula})"
    if di.value is None:
        return MetricResult(None, inputs_used, formula, f"days_inventory unavailable: {di.null_reason}")
    if dr.value is None:
        return MetricResult(None, inputs_used, formula, f"days_receivables unavailable: {dr.null_reason}")
    if dp.value is None:
        return MetricResult(None, inputs_used, formula, f"days_payables unavailable: {dp.null_reason}")
    return MetricResult(di.value + dr.value - dp.value, inputs_used, formula, None)


# --- Solvency ---


def _compute_net_debt(facts, periods_by_class, start, end, cls) -> MetricResult:
    debt, debt_note, inputs_used = _resolve_total_debt(facts, end)
    cash, cash_alias = _resolve("cash", facts, None, end)
    sti, sti_alias = _resolve("short_term_investments", facts, None, end)
    _record(inputs_used, cash_alias, cash)
    _record(inputs_used, sti_alias, sti)
    formula = f"({debt_note}) - {cash_alias or 'cash'} - {sti_alias or 'short_term_investments'}"
    if debt is None:
        return MetricResult(None, inputs_used, formula, "total_debt unresolved (no combined tag and components incomplete)")
    if cash is None:
        return MetricResult(None, inputs_used, formula, "cash missing")
    if sti is None:
        return MetricResult(None, inputs_used, formula, "short_term_investments missing")
    return MetricResult(debt - cash - sti, inputs_used, formula, None)


def _compute_net_debt_to_ebitda(facts, periods_by_class, start, end, cls) -> MetricResult:
    nd = _compute_net_debt(facts, periods_by_class, start, end, cls)
    ebitda = _compute_ebitda(facts, periods_by_class, start, end, cls)
    inputs_used = {**nd.inputs_used, **ebitda.inputs_used}
    formula = f"({nd.formula}) / ({ebitda.formula})"
    if nd.value is None:
        return MetricResult(None, inputs_used, formula, f"net_debt unavailable: {nd.null_reason}")
    if ebitda.value is None:
        return MetricResult(None, inputs_used, formula, f"ebitda unavailable: {ebitda.null_reason}")
    if ebitda.value <= 0:
        return MetricResult(None, inputs_used, formula, "ebitda <= 0")
    return MetricResult(nd.value / ebitda.value, inputs_used, formula, None)


_compute_interest_coverage = _simple_ratio("operating_income", "interest_expense")
_compute_current_ratio = _simple_ratio("current_assets", "current_liabilities")


# --- Quality: Beneish M-score and its 8 stored components ---

_compute_beneish_dsri = _ratio_of_ratios_index("receivables", "revenue")
_compute_beneish_sgai = _ratio_of_ratios_index("sga_expense", "revenue")


def _compute_beneish_sgi(facts, periods_by_class, start, end, cls) -> MetricResult:
    prior = _find_prior_yoy(periods_by_class.get("annual", set()), start, end)
    if prior is None:
        return MetricResult(None, {}, "revenue_t / revenue_t-1", "prior period not found")
    p_start, p_end = prior
    rev_t, rev_t_alias = _resolve("revenue", facts, start, end)
    rev_p, rev_p_alias = _resolve("revenue", facts, p_start, p_end)
    inputs_used: dict[str, float] = {}
    _record(inputs_used, rev_t_alias, rev_t, "_t")
    _record(inputs_used, rev_p_alias, rev_p, "_t-1")
    formula = f"{rev_t_alias or 'revenue'}_t / {rev_p_alias or 'revenue'}_t-1"
    if rev_t is None or rev_p is None:
        return MetricResult(None, inputs_used, formula, "revenue missing in t or t-1")
    if rev_p == 0:
        return MetricResult(None, inputs_used, formula, "prior revenue is zero")
    return MetricResult(rev_t / rev_p, inputs_used, formula, None)


def _compute_beneish_gmi(facts, periods_by_class, start, end, cls) -> MetricResult:
    prior = _find_prior_yoy(periods_by_class.get("annual", set()), start, end)
    if prior is None:
        return MetricResult(None, {}, "gross_margin_t-1 / gross_margin_t", "prior period not found")
    p_start, p_end = prior
    gp_t, gp_t_note, inputs_t = _resolve_gross_profit(facts, start, end)
    gp_p, gp_p_note, inputs_p = _resolve_gross_profit(facts, p_start, p_end)
    rev_t, rev_t_alias = _resolve("revenue", facts, start, end)
    rev_p, rev_p_alias = _resolve("revenue", facts, p_start, p_end)
    inputs_used = {f"{k}_t": v for k, v in inputs_t.items()}
    inputs_used.update({f"{k}_t-1": v for k, v in inputs_p.items()})
    _record(inputs_used, rev_t_alias, rev_t, "_t")
    _record(inputs_used, rev_p_alias, rev_p, "_t-1")
    formula = f"(({gp_p_note})_t-1/{rev_p_alias or 'revenue'}_t-1) / (({gp_t_note})_t/{rev_t_alias or 'revenue'}_t)"
    if gp_t is None or gp_p is None or rev_t is None or rev_p is None:
        return MetricResult(None, inputs_used, formula, "gross_profit or revenue missing in t or t-1")
    if rev_t == 0 or rev_p == 0:
        return MetricResult(None, inputs_used, formula, "revenue is zero in t or t-1")
    margin_t, margin_p = gp_t / rev_t, gp_p / rev_p
    if margin_t == 0:
        return MetricResult(None, inputs_used, formula, "current gross margin is zero")
    return MetricResult(margin_p / margin_t, inputs_used, formula, None)


def _compute_beneish_aqi(facts, periods_by_class, start, end, cls) -> MetricResult:
    prior = _find_prior_yoy(periods_by_class.get("annual", set()), start, end)
    if prior is None:
        return MetricResult(
            None, {}, "(1-(current_assets+ppe_net)/total_assets)_t / same_t-1", "prior period not found"
        )
    p_start, p_end = prior

    def _term(period_end: str):
        ca, ca_alias = _resolve("current_assets", facts, None, period_end)
        ppe, ppe_note, ppe_inputs = _resolve_ppe_net(facts, period_end)
        ta, ta_alias = _resolve("total_assets", facts, None, period_end)
        used: dict[str, float] = {}
        _record(used, ca_alias, ca)
        used.update(ppe_inputs)
        _record(used, ta_alias, ta)
        aliases = (ca_alias, ppe_note, ta_alias)
        if ca is None or ppe is None or ta is None or ta == 0:
            return None, used, aliases
        return 1 - (ca + ppe) / ta, used, aliases

    term_t, used_t, (ca_a, ppe_a, ta_a) = _term(end)
    term_p, used_p, _aliases_p = _term(p_end)
    inputs_used = {f"{k}_t": v for k, v in used_t.items()}
    inputs_used.update({f"{k}_t-1": v for k, v in used_p.items()})
    formula = (
        f"(1-({ca_a or 'current_assets'}+{ppe_a or 'ppe_net'})/{ta_a or 'total_assets'})_t / same_t-1 "
        "[omits 'other securities' term, not reliably tagged in XBRL]"
    )
    if term_t is None or term_p is None:
        return MetricResult(None, inputs_used, formula, "current_assets, ppe_net, or total_assets missing/zero in t or t-1")
    if term_p == 0:
        return MetricResult(None, inputs_used, formula, "prior-period term is zero")
    return MetricResult(term_t / term_p, inputs_used, formula, None)


def _compute_beneish_depi(facts, periods_by_class, start, end, cls) -> MetricResult:
    prior = _find_prior_yoy(periods_by_class.get("annual", set()), start, end)
    if prior is None:
        return MetricResult(
            None, {}, "(depreciation/(depreciation+ppe_net))_t-1 / same_t", "prior period not found"
        )
    p_start, p_end = prior

    def _rate(period_start: str, period_end: str):
        dep, dep_note, dep_inputs = _resolve_depreciation(facts, period_start, period_end)
        ppe, ppe_note, ppe_inputs = _resolve_ppe_net(facts, period_end)
        used: dict[str, float] = dict(dep_inputs)
        used.update(ppe_inputs)
        aliases = (dep_note, ppe_note)
        if dep is None or ppe is None or (dep + ppe) == 0:
            return None, used, aliases
        return dep / (dep + ppe), used, aliases

    rate_t, used_t, (dep_a, ppe_a) = _rate(start, end)
    rate_p, used_p, _aliases_p = _rate(p_start, p_end)
    inputs_used = {f"{k}_t": v for k, v in used_t.items()}
    inputs_used.update({f"{k}_t-1": v for k, v in used_p.items()})
    dep_term = f"{dep_a or 'depreciation'}/({dep_a or 'depreciation'}+{ppe_a or 'ppe_net'})"
    formula = f"({dep_term})_t-1 / ({dep_term})_t"
    if rate_t is None or rate_p is None:
        return MetricResult(None, inputs_used, formula, "depreciation/dep_amort or ppe_net missing/zero-sum in t or t-1")
    if rate_t == 0:
        return MetricResult(None, inputs_used, formula, "current-period depreciation rate is zero")
    return MetricResult(rate_p / rate_t, inputs_used, formula, None)


def _compute_beneish_lvgi(facts, periods_by_class, start, end, cls) -> MetricResult:
    prior = _find_prior_yoy(periods_by_class.get("annual", set()), start, end)
    if prior is None:
        return MetricResult(
            None, {}, "((total_debt+current_liabilities)/total_assets)_t / same_t-1", "prior period not found"
        )
    p_start, p_end = prior

    def _term(period_end: str):
        debt, debt_note, used = _resolve_total_debt(facts, period_end)
        cl, cl_alias = _resolve("current_liabilities", facts, None, period_end)
        ta, ta_alias = _resolve("total_assets", facts, None, period_end)
        used = dict(used)
        _record(used, cl_alias, cl)
        _record(used, ta_alias, ta)
        if debt is None or cl is None or ta is None or ta == 0:
            return None, used, debt_note, cl_alias, ta_alias
        return (debt + cl) / ta, used, debt_note, cl_alias, ta_alias

    term_t, used_t, note_t, cl_alias_t, ta_alias_t = _term(end)
    term_p, used_p, note_p, _cl_alias_p, _ta_alias_p = _term(p_end)
    inputs_used = {f"{k}_t": v for k, v in used_t.items()}
    inputs_used.update({f"{k}_t-1": v for k, v in used_p.items()})
    formula = (
        f"(({note_t}+{cl_alias_t or 'current_liabilities'})/{ta_alias_t or 'total_assets'})_t / "
        f"(({note_p}+{cl_alias_t or 'current_liabilities'})/{ta_alias_t or 'total_assets'})_t-1"
    )
    if term_t is None or term_p is None:
        return MetricResult(None, inputs_used, formula, "total_debt, current_liabilities, or total_assets missing/zero in t or t-1")
    if term_p == 0:
        return MetricResult(None, inputs_used, formula, "prior-period term is zero")
    return MetricResult(term_t / term_p, inputs_used, formula, None)


def _compute_beneish_tata(facts, periods_by_class, start, end, cls) -> MetricResult:
    # No prior period: TATA is the one Beneish index computed from a single period.
    ni, ni_alias = _resolve("net_income", facts, start, end)
    cfo, cfo_alias = _resolve("cfo", facts, start, end)
    ta, ta_alias = _resolve("total_assets", facts, None, end)
    inputs_used: dict[str, float] = {}
    _record(inputs_used, ni_alias, ni)
    _record(inputs_used, cfo_alias, cfo)
    _record(inputs_used, ta_alias, ta)
    formula = (
        f"({ni_alias or 'net_income'} - {cfo_alias or 'cfo'}) / {ta_alias or 'total_assets'} "
        "[net income substituted for income from continuing operations]"
    )
    if ni is None or cfo is None:
        return MetricResult(None, inputs_used, formula, "net_income or cfo missing")
    if ta is None:
        return MetricResult(None, inputs_used, formula, "total_assets missing")
    if ta == 0:
        return MetricResult(None, inputs_used, formula, "total_assets is zero")
    return MetricResult((ni - cfo) / ta, inputs_used, formula, None)


_BENEISH_COMPONENT_FUNCS: dict[str, Callable] = {
    "DSRI": _compute_beneish_dsri,
    "GMI": _compute_beneish_gmi,
    "AQI": _compute_beneish_aqi,
    "SGI": _compute_beneish_sgi,
    "DEPI": _compute_beneish_depi,
    "SGAI": _compute_beneish_sgai,
    "TATA": _compute_beneish_tata,
    "LVGI": _compute_beneish_lvgi,
}


def _compute_beneish_m_score(facts, periods_by_class, start, end, cls) -> MetricResult:
    # M-score's own inputs are the 8 index values -- the raw XBRL concepts
    # behind each index are already recorded on that index's own metric row
    # (beneish_dsri, beneish_gmi, ...); re-flattening them here would just
    # duplicate that data on every M-score row.
    components = {
        idx: fn(facts, periods_by_class, start, end, cls) for idx, fn in _BENEISH_COMPONENT_FUNCS.items()
    }
    inputs_used: dict[str, float] = {idx: r.value for idx, r in components.items() if r.value is not None}
    coeff_terms = " + ".join(f"({config.BENEISH_COEFFICIENTS[idx]})*{idx}" for idx in components)
    formula = f"{config.BENEISH_INTERCEPT} + {coeff_terms}"
    missing = [idx for idx, r in components.items() if r.value is None]
    if missing:
        return MetricResult(None, inputs_used, formula, f"components missing: {', '.join(missing)}")
    score = config.BENEISH_INTERCEPT + sum(
        config.BENEISH_COEFFICIENTS[idx] * components[idx].value for idx in components
    )
    return MetricResult(score, inputs_used, formula, None)


# --- discrete fiscal quarters (SPEC-008 C4 2026-08-08, generalized to the
# income statement for D12 2026-08-08) ---
#
# Two distinct problems, same fix. Some companies (Micron, NVIDIA) tag
# cash-flow-statement concepts cumulatively -- a Q2 10-Q's "cash from
# operations" is six months, not three, growing to nine months at Q3,
# resetting at the next fiscal year. Separately, EVERY company's Q4 is
# missing on the income statement, for a structural reason, not a tagging
# quirk: there is no "Q4 10-Q" -- the fourth quarter is only ever reported
# via the 10-K, which tags the ANNUAL cumulative figure, never a discrete
# 3-month Q4 one (confirmed against real data: AMZN's Q1/Q2/Q3 income-
# statement lines carry a redundant discrete tag alongside their
# cumulative one in the same filing; Q4/FY never does, for any company).
# Both are the same shape of problem -- a real filed number for the wrong
# window, or no window at all -- and the same derivation solves both:
# derive the discrete quarter by subtraction wherever it isn't filed
# directly: Q2 = 6mo - Q1, Q3 = 9mo - 6mo, Q4 = FY - 9mo. Q1 needs no
# subtraction -- its own filed figure already covers exactly one quarter,
# since a fiscal year's Q1 period_start IS the fiscal year's own start.
#
# Balance sheet is deliberately untouched: instant (as-of-a-date) facts
# have no duration to mismatch and nothing to subtract.
#
# A SEPARATE pass from `compute_metrics`'s generic per-period loop below,
# not folded into it: every other metric is computed at (period_start,
# period_end) pairs that already exist as real fact durations
# (`periods_by_class`, built from what's actually in `xbrl_facts`). A
# discrete quarter's own (period_start, period_end) is SYNTHETIC when the
# company tags cumulatively -- Q2's true 3-month window doesn't exist as
# any single filed fact, by definition of the problem being solved. It has
# to be constructed from the fiscal calendar (`filings.fiscal_year`/
# `fiscal_period`, SEC's own dei:DocumentFiscalYearFocus/PeriodFocus tags),
# never from a fixed MMDD calendar assumption -- Micron's fiscal year-end
# floats (confirmed: 2025-08-28, not a fixed date), so `config.Company.
# fiscal_year_end` (a nominal MMDD, unused anywhere else in this codebase)
# cannot anchor this; the authoritative source is what SEC says THIS filing
# covers, not a calendar guess.
#
# Restatement handling: both subtraction endpoints are resolved through the
# SAME `_load_facts`/`_resolve` machinery every other metric in this file
# uses, which already implements this project's one restatement policy
# (SPEC-004 R4, `_load_facts`'s own docstring: "latest filed_date wins per
# period"). Both endpoints are drawn from the same vintage by construction
# -- a second, bespoke restatement policy was deliberately NOT invented for
# this one case. The residual risk (one endpoint restated, its neighbour
# not yet re-filed) is caught downstream, not here: `validate`'s discrete-
# quarter sum-back check (edgar/validate.py) re-derives the same numbers
# fresh from `xbrl_facts` and compares their sum to the filed FY figure,
# which is an algebraic identity that only fails to tie exactly when the
# inputs are inconsistent -- see that check's docstring for the proof.
#
# Plausibility: METRIC_REGISTRY's existing `plausible_range` is checked
# HERE, at compute time, not left to a post-hoc `validate` pass -- an
# implausible subtraction result (e.g. a restated endpoint producing
# negative capex, which cannot legitimately happen) becomes a null with a
# reason, never a written, later-displayed figure.

_DISCRETE_QUARTER_CANONICALS: tuple[str, ...] = (
    # Cash flow (SPEC-008 C4, approved 2026-08-08).
    "cfo", "capex", "sbc", "dep_amort",
    # Income statement (SPEC-008 D12, approved 2026-08-08): Q4 has no
    # discrete filed fact anywhere in this project's corpus, for any
    # company, in any year -- only the FY 10-K's cumulative figure exists.
    # Q1/Q2/Q3 are typically ALREADY tagged at the true discrete duration
    # directly (unlike cash flow's MU/NVDA problem, confirmed against real
    # AMZN data: revenue's Q2/Q3 facts exist as BOTH a discrete tag and a
    # redundant cumulative one in the same filing) -- their own `_discrete`
    # rows mostly just reconfirm the filed figure by subtraction, which is
    # harmless and needed anyway so the sum-back check has all four
    # quarters to check against.
    "revenue", "cogs", "gross_profit", "rnd_expense", "sga_expense",
    "operating_income", "interest_expense", "pretax_income", "tax_expense", "net_income",
    # Cash flow completeness (SPEC-008-batch-1 item 5, approved 2026-08-09):
    # the three-section statement's new lines get the identical treatment.
    "receivables_change", "inventory_change", "payables_change", "deferred_tax", "other_noncash",
    "acquisitions", "investment_purchases", "investment_maturities", "net_cash_investing",
    "buybacks", "dividends_paid", "debt_issued", "debt_repaid", "finance_lease_principal_paid",
    "net_cash_financing",
    "fx_effect_on_cash", "net_change_in_cash",
)
_PRIOR_FISCAL_PERIOD: dict[str, str] = {"Q2": "Q1", "Q3": "Q2", "FY": "Q3"}

# SPEC-008-batch-2 item 2: the standard Q1/Q2/Q3/FY(=Q4) cycle as an
# ordinal sequence -- pure integer arithmetic on fiscal labels, not date
# arithmetic, matching this project's own standing rule for period lookups.
_FISCAL_QUARTER_ORDER: tuple[str, ...] = ("Q1", "Q2", "Q3", "FY")


def _trailing_four_quarters(fy: int, fp: str) -> list[tuple[int, str]]:
    """The 4 fiscal (year, period) keys ending at (fy, fp) inclusive, in
    chronological order -- used by fcff_tax_rate_discrete's trailing-
    twelve-month sum. Whether all 4 actually exist for this company is the
    caller's problem (a real fail-closed case for a company's own earliest
    few quarters, not an error here)."""
    ordinal = fy * 4 + _FISCAL_QUARTER_ORDER.index(fp)
    keys = []
    for o in range(ordinal - 3, ordinal + 1):
        year, pos = divmod(o, 4)
        keys.append((year, _FISCAL_QUARTER_ORDER[pos]))
    return keys


def _fiscal_quarter_map(conn: sqlite3.Connection, cik: str) -> dict[tuple[int, str], str]:
    """(fiscal_year, fiscal_period) -> period_end, from `filings` -- the
    authoritative SEC-declared fiscal calendar, not date arithmetic."""
    rows = conn.execute(
        "SELECT DISTINCT fiscal_year, fiscal_period, period_end FROM filings "
        "WHERE cik = ? AND fiscal_period IS NOT NULL AND form_type IN (?, ?)",
        (cik, config.TENQ_FORM_TYPE, config.TENK_FORM_TYPE),
    ).fetchall()
    return {(row["fiscal_year"], row["fiscal_period"]): row["period_end"] for row in rows}


def _fiscal_year_start(
    facts_by_concept: dict, quarter_map: dict[tuple[int, str], str], fiscal_year: int, canonical: str
) -> str | None:
    """FY start = THIS canonical's own Q1 fact's period_start.

    Deliberately not a shared "representative concept" borrowed across
    every line (an earlier version used `cfo` for this, reasoning that one
    filing's cash-flow statement shares a single duration across every
    line in it) -- generalizing to the income statement (SPEC-008 D12,
    approved 2026-08-08) removed that justification: nothing guarantees a
    company tags `revenue`'s Q1 the same day as `cfo`'s. Each canonical
    resolving its OWN Q1 anchor is strictly more correct and costs nothing
    extra -- if a line's own Q1 isn't tagged, that line's derivation fails
    closed for that fiscal year rather than silently borrowing a different
    line's window."""
    q1_end = quarter_map.get((fiscal_year, "Q1"))
    if q1_end is None:
        return None
    _value, _alias, start = _resolve_at_end(facts_by_concept, canonical, q1_end, "quarterly")
    return start


def _compute_one_discrete_quarter(
    canonical: str, facts_by_concept: dict, fy_start: str | None, this_end: str, prior_end: str | None
) -> MetricResult:
    """One (canonical, fiscal year, fiscal quarter)'s discrete value.
    `prior_end` is None for Q1 (no subtraction -- the filed figure already
    IS the discrete quarter)."""
    if prior_end is None:
        value, alias, _start = _resolve_at_end(facts_by_concept, canonical, this_end, "quarterly")
        inputs_used: dict[str, float] = {}
        _record(inputs_used, alias, value)
        formula = f"{alias or canonical} (Q1, already discrete)"
        if value is None:
            return MetricResult(None, inputs_used, formula, "Q1 not tagged at the quarterly duration")
        return MetricResult(value, inputs_used, formula, None)

    if fy_start is None:
        return MetricResult(None, {}, "", "fiscal year start not resolvable (Q1 not tagged at the quarterly duration)")

    this_value, this_alias = _resolve(canonical, facts_by_concept, fy_start, this_end)
    prior_value, prior_alias = _resolve(canonical, facts_by_concept, fy_start, prior_end)
    inputs_used = {}
    _record(inputs_used, this_alias, this_value, suffix="_ytd_this")
    _record(inputs_used, prior_alias, prior_value, suffix="_ytd_prior")
    formula = f"{this_alias or canonical}(YTD to {this_end}) - {prior_alias or canonical}(YTD to {prior_end})"
    if this_value is None:
        return MetricResult(None, inputs_used, formula, f"cumulative figure through {this_end} not tagged")
    if prior_value is None:
        return MetricResult(None, inputs_used, formula, f"cumulative figure through {prior_end} not tagged")
    return MetricResult(this_value - prior_value, inputs_used, formula, None)


def _discrete_quarter_period_start(
    canonical: str, facts_by_concept: dict, fy_start: str | None, prior_end: str | None
) -> str | None:
    """The DISCRETE quarter's own period_start for storage -- distinct from
    `fy_start`, which is the CUMULATIVE fact's period_start used to look up
    subtraction inputs. Q1's discrete start is fy_start itself (trivially,
    since Q1 IS the fiscal year's first quarter). Q2/Q3/Q4's discrete start
    is the immediately prior REAL filed quarter's period_end + 1 day --
    plain date arithmetic off an actual filed date, not a calendar
    assumption, so it is correct regardless of when the fiscal year floats
    to."""
    if prior_end is None:
        return fy_start
    return (date.fromisoformat(prior_end) + timedelta(days=1)).isoformat()


def _refused_computed_separately(facts, periods_by_class, start, end, cls) -> MetricResult:
    """`COMPUTE_FUNCS` placeholder for `computed_separately` metrics
    (discrete-quarter metrics). Must never actually run -- `compute_metrics`
    skips these by the `computed_separately` flag before ever calling a
    compute function, same as the generic loop skips no other metric today.
    Raises rather than silently computing a value at the wrong period if
    that skip is ever accidentally removed."""
    raise RuntimeError(
        "a computed_separately metric reached the generic per-period loop -- "
        "its own compute_discrete_quarter_metrics pass should have handled it, "
        "and compute_metrics should have skipped it"
    )


def compute_discrete_quarter_metrics(
    conn: sqlite3.Connection, tickers: list[str] | None = None, metric_names: list[str] | None = None
) -> list[dict]:
    """Compute every `computed_separately` discrete-quarter metric for every
    fiscal quarter on record. Idempotent, same as `compute_metrics`; call
    both from the pipeline's `compute-metrics` command."""
    companies = [c for c in config.WATCHLIST if tickers is None or c.ticker in tickers]
    written: list[dict] = []
    null_count = 0
    computed_count = 0
    for company in companies:
        facts_by_concept = _load_facts(conn, company.cik)
        quarter_map = _fiscal_quarter_map(conn, company.cik)
        fiscal_years = sorted({fy for (fy, _fp) in quarter_map})

        # canonical -> fiscal_year -> fiscal_period -> (value, period_start, period_end)
        # -- kept in memory this pass so free_cash_flow_discrete can compose
        # cfo_discrete/capex_discrete without a DB round-trip.
        resolved: dict[str, dict[tuple[int, str], tuple[float | None, str, str]]] = {}

        for canonical in _DISCRETE_QUARTER_CANONICALS:
            name = f"{canonical}_discrete"
            if metric_names is not None and name not in metric_names:
                continue
            mdef = config.METRIC_REGISTRY[name]
            resolved[canonical] = {}
            for fy in fiscal_years:
                fy_start = _fiscal_year_start(facts_by_concept, quarter_map, fy, canonical)
                for fp in ("Q1", "Q2", "Q3", "FY"):
                    end = quarter_map.get((fy, fp))
                    if end is None:
                        continue
                    prior_fp = _PRIOR_FISCAL_PERIOD.get(fp)
                    prior_end = quarter_map.get((fy, prior_fp)) if prior_fp else None
                    if fp != "Q1" and prior_fp is not None and prior_end is None:
                        result = MetricResult(None, {}, "", f"prior fiscal quarter ({prior_fp}) not filed yet")
                    else:
                        result = _compute_one_discrete_quarter(
                            canonical, facts_by_concept, fy_start, end, prior_end if fp != "Q1" else None
                        )

                    # SPEC-008-batch-1 item 5 (found live against the real
                    # corpus, 2026-08-09): the plausibility floor must
                    # apply ONLY to a genuinely DERIVED (subtracted) value,
                    # never to Q1's direct pass-through of a filed fact --
                    # AMZN really did file -$48M for Q1 2025 acquisitions
                    # (a real, negative, directly filed number, formula
                    # "Q1, already discrete", no subtraction involved at
                    # all), and the gate was nulling it out on the
                    # assumption that "Payments*" concepts are always
                    # positive, which is the norm but not a guarantee this
                    # project gets to override a real filed fact with.
                    # This project's own rule, stated for D15 (Micron's
                    # zero interest expense): if the filing really says so,
                    # the display is correct and the finding is about the
                    # filing, not the code -- the same rule applies here.
                    value = result.value
                    if value is not None and fp != "Q1" and mdef.plausible_range is not None:
                        lo, hi = mdef.plausible_range
                        if not (lo <= value <= hi):
                            result = MetricResult(
                                None, result.inputs_used, result.formula,
                                f"implausible: {value:,.0f} outside [{lo}, {hi}] "
                                "-- likely a restated endpoint the subtraction hasn't caught up with",
                            )
                            value = None

                    period_start = _discrete_quarter_period_start(
                        canonical, facts_by_concept, fy_start, prior_end if fp != "Q1" else None
                    )
                    if period_start is None:
                        continue  # can't establish the window at all -- no row to write

                    resolved[canonical][(fy, fp)] = (value, period_start, end)
                    if value is None:
                        inputs_json = json.dumps({**result.inputs_used, "_null_reason": result.null_reason}, sort_keys=True)
                        null_count += 1
                    else:
                        inputs_json = json.dumps(result.inputs_used, sort_keys=True)
                        computed_count += 1
                    changed = _write_metric(
                        conn, company.cik, period_start, end, name, value, result.formula, inputs_json
                    )
                    if changed:
                        written.append(
                            {"ticker": company.ticker, "name": name, "period_end": end, "class": "quarterly",
                             "value": value, "null_reason": result.null_reason}
                        )

        # free_cash_flow_discrete = cfo_discrete - capex_discrete, purely
        # local arithmetic on values already resolved above -- no new
        # cross-filing subtraction, same composition free_cash_flow itself
        # already uses for its own (non-discrete) inputs.
        if metric_names is None or "free_cash_flow_discrete" in metric_names:
            for key in set(resolved.get("cfo", {})) & set(resolved.get("capex", {})):
                cfo_value, period_start, end = resolved["cfo"][key]
                capex_value, capex_start, _capex_end = resolved["capex"][key]
                if period_start != capex_start:
                    continue  # D11 discipline: refuse unless both windows genuinely agree
                formula = "cfo_discrete - capex_discrete"
                if cfo_value is None or capex_value is None:
                    value, null_reason = None, "cfo_discrete or capex_discrete unavailable"
                    null_count += 1
                else:
                    value, null_reason = cfo_value - capex_value, None
                    computed_count += 1
                inputs_used = {}
                if cfo_value is not None:
                    inputs_used["cfo_discrete"] = cfo_value
                if capex_value is not None:
                    inputs_used["capex_discrete"] = capex_value
                inputs_json = json.dumps(
                    {**inputs_used, "_null_reason": null_reason} if value is None else inputs_used, sort_keys=True
                )
                changed = _write_metric(
                    conn, company.cik, period_start, end, "free_cash_flow_discrete", value, formula, inputs_json
                )
                if changed:
                    written.append(
                        {"ticker": company.ticker, "name": "free_cash_flow_discrete", "period_end": end,
                         "class": "quarterly", "value": value, "null_reason": null_reason}
                    )

        # fcfe_discrete = cfo_discrete - capex_discrete + (debt_issued_discrete
        # - debt_repaid_discrete) -- SPEC-008-batch-2 item 2, same composition
        # pattern as free_cash_flow_discrete above: exact arithmetic, no
        # assumption involved.
        if metric_names is None or "fcfe_discrete" in metric_names:
            keys = (
                set(resolved.get("cfo", {})) & set(resolved.get("capex", {}))
                & set(resolved.get("debt_issued", {})) & set(resolved.get("debt_repaid", {}))
            )
            for key in keys:
                cfo_value, period_start, end = resolved["cfo"][key]
                capex_value, capex_start, _e = resolved["capex"][key]
                debt_issued_value, di_start, _e2 = resolved["debt_issued"][key]
                debt_repaid_value, dr_start, _e3 = resolved["debt_repaid"][key]
                if not (period_start == capex_start == di_start == dr_start):
                    continue  # D11 discipline: refuse unless every window genuinely agrees
                formula = "cfo_discrete - capex_discrete + (debt_issued_discrete - debt_repaid_discrete)"
                inputs_used = {}
                if cfo_value is not None:
                    inputs_used["cfo_discrete"] = cfo_value
                if capex_value is not None:
                    inputs_used["capex_discrete"] = capex_value
                if debt_issued_value is not None:
                    inputs_used["debt_issued_discrete"] = debt_issued_value
                if debt_repaid_value is not None:
                    inputs_used["debt_repaid_discrete"] = debt_repaid_value
                if None in (cfo_value, capex_value, debt_issued_value, debt_repaid_value):
                    value, null_reason = None, (
                        "cfo_discrete, capex_discrete, debt_issued_discrete, or debt_repaid_discrete unavailable"
                    )
                    null_count += 1
                else:
                    value = cfo_value - capex_value + (debt_issued_value - debt_repaid_value)
                    null_reason = None
                    computed_count += 1
                inputs_json = json.dumps(
                    {**inputs_used, "_null_reason": null_reason} if value is None else inputs_used, sort_keys=True
                )
                changed = _write_metric(
                    conn, company.cik, period_start, end, "fcfe_discrete", value, formula, inputs_json
                )
                if changed:
                    written.append(
                        {"ticker": company.ticker, "name": "fcfe_discrete", "period_end": end,
                         "class": "quarterly", "value": value, "null_reason": null_reason}
                    )

        # fcff_tax_rate_discrete: trailing-twelve-month effective tax rate
        # (SPEC-008-batch-2 item 2) -- GAAP requires discrete tax items
        # excluded from the ANNUAL rate and recognised in full in the
        # quarter they arise, so a single quarter's own ratio is
        # contaminated by construction (Amazon's $15.9B Anthropic-
        # revaluation discrete tax expense is exactly this problem in the
        # real corpus). TTM sums the trailing 4 DISCRETE quarters already
        # resolved above -- needs all 4 present, fails closed (not a
        # shorter window) when fewer exist, which is every company's own
        # earliest few quarters. `typical_pretax` reconstructs each fiscal
        # year's annual pre-tax income from its own 4 discrete quarters
        # (rather than a separate genuine-annual-duration lookup, the
        # regular engine's own _median_abs_annual_pretax_income uses) --
        # the two are the same quantity, this is just the version already
        # available from this pass's own `resolved` dict.
        fcff_tax_rate_resolved: dict[tuple[int, str], tuple[float | None, str, str]] = {}
        if metric_names is None or "fcff_tax_rate_discrete" in metric_names:
            annual_pretax_reconstructed = []
            for fy in fiscal_years:
                parts = [resolved.get("pretax_income", {}).get((fy, fp)) for fp in _FISCAL_QUARTER_ORDER]
                if all(p is not None and p[0] is not None for p in parts):
                    annual_pretax_reconstructed.append(sum(p[0] for p in parts))
            typical_pretax = (
                statistics.median(abs(v) for v in annual_pretax_reconstructed)
                if annual_pretax_reconstructed else None
            )

            for fy in fiscal_years:
                for fp in _FISCAL_QUARTER_ORDER:
                    own_entry = (
                        resolved.get("tax_expense", {}).get((fy, fp))
                        or resolved.get("pretax_income", {}).get((fy, fp))
                    )
                    if own_entry is None:
                        continue  # this quarter's own window is unknown -- no row to write
                    _own_value, period_start, end = own_entry
                    trailing = _trailing_four_quarters(fy, fp)
                    tax_entries = [resolved.get("tax_expense", {}).get(k) for k in trailing]
                    pretax_entries = [resolved.get("pretax_income", {}).get(k) for k in trailing]
                    formula = "sum(tax_expense_discrete, 4q) / sum(pretax_income_discrete, 4q) [TTM]"
                    have_four = (
                        all(e is not None and e[0] is not None for e in tax_entries)
                        and all(e is not None and e[0] is not None for e in pretax_entries)
                    )
                    if not have_four:
                        value = None
                        null_reason = "fewer than four consecutive discrete quarters on record"
                        inputs_used = {}
                        null_count += 1
                    else:
                        ttm_tax = sum(e[0] for e in tax_entries)
                        ttm_pretax = sum(e[0] for e in pretax_entries)
                        inputs_used = {"ttm_tax_expense": ttm_tax, "ttm_pretax_income": ttm_pretax}
                        if ttm_pretax <= 0:
                            value = None
                            null_reason = f"TTM pre-tax income is not positive ({ttm_pretax:,.0f})"
                            null_count += 1
                        elif typical_pretax and ttm_pretax < _FCFF_NEAR_ZERO_PRETAX_FRACTION * typical_pretax:
                            value = None
                            null_reason = (
                                f"TTM pre-tax income ({ttm_pretax:,.0f}) is near zero relative to this "
                                f"company's typical annual pre-tax income ({typical_pretax:,.0f})"
                            )
                            null_count += 1
                        else:
                            value = ttm_tax / ttm_pretax
                            null_reason = None
                            computed_count += 1
                    fcff_tax_rate_resolved[(fy, fp)] = (value, period_start, end)
                    inputs_json = json.dumps(
                        {**inputs_used, "_null_reason": null_reason} if value is None else inputs_used,
                        sort_keys=True,
                    )
                    changed = _write_metric(
                        conn, company.cik, period_start, end, "fcff_tax_rate_discrete", value, formula, inputs_json
                    )
                    if changed:
                        written.append(
                            {"ticker": company.ticker, "name": "fcff_tax_rate_discrete", "period_end": end,
                             "class": "quarterly", "value": value, "null_reason": null_reason}
                        )

        # fcff_discrete = cfo_discrete + interest_expense_discrete*(1 -
        # fcff_tax_rate_discrete) - capex_discrete (SPEC-008-batch-2 item 2)
        # -- fails closed whenever the rate itself is unavailable, same as
        # any other missing input; never substitutes a statutory rate.
        if metric_names is None or "fcff_discrete" in metric_names:
            keys = (
                set(resolved.get("cfo", {})) & set(resolved.get("capex", {}))
                & set(resolved.get("interest_expense", {})) & set(fcff_tax_rate_resolved)
            )
            for key in keys:
                cfo_value, period_start, end = resolved["cfo"][key]
                capex_value, capex_start, _e = resolved["capex"][key]
                interest_value, interest_start, _e2 = resolved["interest_expense"][key]
                rate_value, rate_start, _e3 = fcff_tax_rate_resolved[key]
                if not (period_start == capex_start == interest_start == rate_start):
                    continue  # D11 discipline: refuse unless every window genuinely agrees
                inputs_used = {}
                if cfo_value is not None:
                    inputs_used["cfo_discrete"] = cfo_value
                if capex_value is not None:
                    inputs_used["capex_discrete"] = capex_value
                if interest_value is not None:
                    inputs_used["interest_expense_discrete"] = interest_value
                if rate_value is not None:
                    inputs_used["fcff_tax_rate_discrete"] = rate_value
                rate_str = f"{rate_value:.4f}" if rate_value is not None else "unavailable"
                formula = f"cfo_discrete + interest_expense_discrete*(1-{rate_str}) - capex_discrete"
                if cfo_value is None or capex_value is None or interest_value is None:
                    value = None
                    null_reason = "cfo_discrete, capex_discrete, or interest_expense_discrete unavailable"
                    null_count += 1
                elif rate_value is None:
                    value = None
                    null_reason = "fcff_tax_rate_discrete unavailable"
                    null_count += 1
                else:
                    value = cfo_value + interest_value * (1 - rate_value) - capex_value
                    null_reason = None
                    computed_count += 1
                inputs_json = json.dumps(
                    {**inputs_used, "_null_reason": null_reason} if value is None else inputs_used, sort_keys=True
                )
                changed = _write_metric(
                    conn, company.cik, period_start, end, "fcff_discrete", value, formula, inputs_json
                )
                if changed:
                    written.append(
                        {"ticker": company.ticker, "name": "fcff_discrete", "period_end": end,
                         "class": "quarterly", "value": value, "null_reason": null_reason}
                    )

    conn.commit()
    logger.info("compute_discrete_quarter_metrics: %d computed, %d NULL", computed_count, null_count)
    return written


# --- registry dispatch ---

COMPUTE_FUNCS: dict[str, Callable] = {
    "revenue_yoy": _compute_revenue_yoy,
    "revenue_qoq": _compute_revenue_qoq,
    "operating_income_yoy": _compute_operating_income_yoy,
    "eps_diluted_yoy": _compute_eps_diluted_yoy,
    "gross_margin": _compute_gross_margin,
    "operating_margin": _compute_operating_margin,
    "net_margin": _compute_net_margin,
    "ebitda": _compute_ebitda,
    "ebitda_margin": _compute_ebitda_margin,
    "rnd_intensity": _compute_rnd_intensity,
    "sga_intensity": _compute_sga_intensity,
    "incremental_gross_margin": _compute_incremental_gross_margin,
    "effective_tax_rate": _compute_effective_tax_rate,
    "nopat": _compute_nopat,
    "invested_capital": _compute_invested_capital,
    "roic": _compute_roic,
    "roe": _compute_roe,
    "asset_turnover": _compute_asset_turnover,
    "equity_multiplier": _compute_equity_multiplier,
    "fixed_asset_turnover": _compute_fixed_asset_turnover,
    "capex_to_revenue": _compute_capex_to_revenue,
    "capex_to_depreciation": _compute_capex_to_depreciation,
    "free_cash_flow": _compute_free_cash_flow,
    "fcf_margin": _compute_fcf_margin,
    "fcf_conversion": _compute_fcf_conversion,
    "fcfe": _compute_fcfe,
    "fcff_tax_rate": _compute_fcff_tax_rate,
    "fcff": _compute_fcff,
    "sbc_to_revenue": _compute_sbc_to_revenue,
    "depreciation_rate": _compute_depreciation_rate,
    "days_inventory": _compute_days_inventory,
    "days_receivables": _compute_days_receivables,
    "days_payables": _compute_days_payables,
    "cash_conversion_cycle": _compute_cash_conversion_cycle,
    "inventory_growth_less_revenue_growth": _compute_inventory_growth_less_revenue_growth,
    "net_debt": _compute_net_debt,
    "net_debt_to_ebitda": _compute_net_debt_to_ebitda,
    "interest_coverage": _compute_interest_coverage,
    "current_ratio": _compute_current_ratio,
    "beneish_m_score": _compute_beneish_m_score,
    "beneish_dsri": _compute_beneish_dsri,
    "beneish_gmi": _compute_beneish_gmi,
    "beneish_aqi": _compute_beneish_aqi,
    "beneish_sgi": _compute_beneish_sgi,
    "beneish_depi": _compute_beneish_depi,
    "beneish_sgai": _compute_beneish_sgai,
    "beneish_lvgi": _compute_beneish_lvgi,
    "beneish_tata": _compute_beneish_tata,
    # computed_separately -- see _refused_computed_separately's own docstring.
    "cfo_discrete": _refused_computed_separately,
    "capex_discrete": _refused_computed_separately,
    "sbc_discrete": _refused_computed_separately,
    "dep_amort_discrete": _refused_computed_separately,
    "free_cash_flow_discrete": _refused_computed_separately,
    "fcfe_discrete": _refused_computed_separately,
    "fcff_tax_rate_discrete": _refused_computed_separately,
    "fcff_discrete": _refused_computed_separately,
    "revenue_discrete": _refused_computed_separately,
    "cogs_discrete": _refused_computed_separately,
    "gross_profit_discrete": _refused_computed_separately,
    "rnd_expense_discrete": _refused_computed_separately,
    "sga_expense_discrete": _refused_computed_separately,
    "operating_income_discrete": _refused_computed_separately,
    "interest_expense_discrete": _refused_computed_separately,
    "pretax_income_discrete": _refused_computed_separately,
    "tax_expense_discrete": _refused_computed_separately,
    "net_income_discrete": _refused_computed_separately,
    "receivables_change_discrete": _refused_computed_separately,
    "inventory_change_discrete": _refused_computed_separately,
    "payables_change_discrete": _refused_computed_separately,
    "deferred_tax_discrete": _refused_computed_separately,
    "other_noncash_discrete": _refused_computed_separately,
    "acquisitions_discrete": _refused_computed_separately,
    "investment_purchases_discrete": _refused_computed_separately,
    "investment_maturities_discrete": _refused_computed_separately,
    "net_cash_investing_discrete": _refused_computed_separately,
    "buybacks_discrete": _refused_computed_separately,
    "dividends_paid_discrete": _refused_computed_separately,
    "debt_issued_discrete": _refused_computed_separately,
    "debt_repaid_discrete": _refused_computed_separately,
    "net_cash_financing_discrete": _refused_computed_separately,
    "finance_lease_principal_paid_discrete": _refused_computed_separately,
    "fx_effect_on_cash_discrete": _refused_computed_separately,
    "net_change_in_cash_discrete": _refused_computed_separately,
}

assert set(COMPUTE_FUNCS) == set(config.METRIC_REGISTRY), (
    f"COMPUTE_FUNCS / METRIC_REGISTRY mismatch: "
    f"{set(config.METRIC_REGISTRY) ^ set(COMPUTE_FUNCS)}"
)


# --- persistence ---


def _now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0, tzinfo=None).isoformat()


def _write_metric(
    conn: sqlite3.Connection,
    cik: str,
    period_start: str,
    period_end: str,
    name: str,
    value: float | None,
    formula: str,
    inputs_json: str,
) -> bool:
    """Insert or update one metric row. Returns True if a row changed.

    Keyed on (cik, period_start, period_end, name, calc_version) -- period_end
    alone is not sufficient. Real data confirms this: Amazon has a
    365-day-duration fact and a 90-day-duration fact that both end on
    2025-06-30 (a trailing-twelve-month figure happens to close on the same
    date as an ordinary quarter). A basis="both" metric computed for both the
    annual and quarterly instance of that end date would silently overwrite
    one with the other on every run if period_start weren't part of the key.
    """
    existing = conn.execute(
        "SELECT value, formula, inputs_json FROM metrics "
        "WHERE cik = ? AND period_start = ? AND period_end = ? AND name = ? AND calc_version = ?",
        (cik, period_start, period_end, name, config.CALC_VERSION),
    ).fetchone()
    if existing is not None:
        if existing["value"] == value and existing["formula"] == formula and existing["inputs_json"] == inputs_json:
            return False
        conn.execute(
            "UPDATE metrics SET value = ?, formula = ?, inputs_json = ?, computed_at = ? "
            "WHERE cik = ? AND period_start = ? AND period_end = ? AND name = ? AND calc_version = ?",
            (value, formula, inputs_json, _now_iso(), cik, period_start, period_end, name, config.CALC_VERSION),
        )
        return True
    conn.execute(
        "INSERT INTO metrics (cik, accession_no, period_start, period_end, name, value, formula, inputs_json, calc_version, computed_at) "
        "VALUES (?, NULL, ?, ?, ?, ?, ?, ?, ?, ?)",
        (cik, period_start, period_end, name, value, formula, inputs_json, config.CALC_VERSION, _now_iso()),
    )
    return True


def compute_metrics(
    conn: sqlite3.Connection, tickers: list[str] | None = None, metric_names: list[str] | None = None
) -> list[dict]:
    """Compute every registered metric for every applicable period. Idempotent."""
    companies = [c for c in config.WATCHLIST if tickers is None or c.ticker in tickers]
    written: list[dict] = []
    null_count = 0
    computed_count = 0
    for company in companies:
        facts_by_concept = _load_facts(conn, company.cik)
        real_annual_ends, real_quarterly_ends = _real_fiscal_period_ends(conn, company.cik)
        periods_by_class = _build_periods_by_class(facts_by_concept, real_annual_ends, real_quarterly_ends)
        for name, mdef in config.METRIC_REGISTRY.items():
            if metric_names is not None and name not in metric_names:
                continue
            if mdef.computed_separately:
                continue  # SPEC-008 C4: handled by compute_discrete_quarter_metrics instead
            fn = COMPUTE_FUNCS[name]
            for cls in _classes_for_basis(mdef.basis):
                for start, end in sorted(periods_by_class.get(cls, set())):
                    result = fn(facts_by_concept, periods_by_class, start, end, cls)
                    if result.value is None:
                        inputs_json = json.dumps(
                            {**result.inputs_used, "_null_reason": result.null_reason}, sort_keys=True
                        )
                        null_count += 1
                    else:
                        inputs_json = json.dumps(result.inputs_used, sort_keys=True)
                        computed_count += 1
                    changed = _write_metric(
                        conn, company.cik, start, end, name, result.value, result.formula, inputs_json
                    )
                    if changed:
                        written.append(
                            {
                                "ticker": company.ticker,
                                "name": name,
                                "period_end": end,
                                "class": cls,
                                "value": result.value,
                                "null_reason": result.null_reason,
                            }
                        )
    conn.commit()
    logger.info("compute_metrics: %d computed, %d NULL", computed_count, null_count)
    return written
