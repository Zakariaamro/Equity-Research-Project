"""Validation command (SPEC-004 R8).

A first-class deliverable, not a test helper. Runs against the real database
and reports 11 categories without modifying anything. Categories 1-6 indicate
incorrect numbers and cause a non-zero exit; 7-11 are informational.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from edgar import analyze as analyze_mod
from edgar import brief as brief_mod
from edgar import config
from edgar import llm as llm_mod
from edgar import metrics as metrics_mod
from edgar import observations as observations_mod
from edgar import section_store


@dataclass
class ValidationReport:
    range_violations: list[dict] = field(default_factory=list)
    range_exceptions: list[dict] = field(default_factory=list)
    dupont_violations: list[dict] = field(default_factory=list)
    gross_profit_violations: list[dict] = field(default_factory=list)
    debt_reconciliation_violations: list[dict] = field(default_factory=list)
    debt_reconciliation_exceptions: list[dict] = field(default_factory=list)
    period_mixing_violations: list[dict] = field(default_factory=list)
    alias_agreement_violations: list[dict] = field(default_factory=list)
    alias_agreement_exceptions: list[dict] = field(default_factory=list)
    concept_drift: list[dict] = field(default_factory=list)
    unverified_yoy_drift: list[dict] = field(default_factory=list)
    coverage: list[dict] = field(default_factory=list)
    unresolved_concepts: list[dict] = field(default_factory=list)
    finance_lease_zero_assumptions: list[dict] = field(default_factory=list)
    discrete_quarter_sum_violations: list[dict] = field(default_factory=list)
    discrete_quarter_sum_exceptions: list[dict] = field(default_factory=list)
    observation_per_filing_contribution: list[dict] = field(default_factory=list)
    observation_dead_rules: list[dict] = field(default_factory=list)
    observation_lookahead_violations: list[dict] = field(default_factory=list)
    observation_determinism_violations: list[dict] = field(default_factory=list)
    observation_orphan_refs: list[dict] = field(default_factory=list)
    db_size: dict = field(default_factory=dict)
    llm_pricing_staleness: list[dict] = field(default_factory=list)
    llm_ledger_mismatches: list[dict] = field(default_factory=list)
    llm_cost_recomputation_mismatches: list[dict] = field(default_factory=list)
    llm_pricing_rate_review: list[dict] = field(default_factory=list)
    llm_budget: dict = field(default_factory=dict)
    llm_orphan_findings: list[dict] = field(default_factory=list)
    llm_quote_integrity_violations: list[dict] = field(default_factory=list)
    llm_discard_rate: list[dict] = field(default_factory=list)
    llm_numeric_support: list[dict] = field(default_factory=list)
    brief_orphan_refs: list[dict] = field(default_factory=list)
    brief_cross_filing_refs: list[dict] = field(default_factory=list)
    brief_invalid_types: list[dict] = field(default_factory=list)
    brief_reverification_failures: list[dict] = field(default_factory=list)
    brief_empty: list[dict] = field(default_factory=list)
    brief_drop_rates: list[dict] = field(default_factory=list)

    @property
    def hard_failure_count(self) -> int:
        return (
            len(self.range_violations)
            + len(self.dupont_violations)
            + len(self.gross_profit_violations)
            + len(self.debt_reconciliation_violations)
            + len(self.discrete_quarter_sum_violations)
            + len(self.period_mixing_violations)
            + len(self.alias_agreement_violations)
            + len(self.observation_lookahead_violations)
            + len(self.observation_determinism_violations)
            + len(self.observation_orphan_refs)
            + len(self.llm_ledger_mismatches)
            + (1 if self.llm_budget.get("over_budget") else 0)
            + len(self.llm_orphan_findings)
            + len(self.llm_quote_integrity_violations)
            + len(self.brief_orphan_refs)
            + len(self.brief_cross_filing_refs)
            + len(self.brief_invalid_types)
            + len(self.brief_reverification_failures)
        )


def _companies(tickers: list[str] | None) -> list[tuple[str, str]]:
    return [(c.cik, c.ticker) for c in config.WATCHLIST if tickers is None or c.ticker in tickers]


def _ciks_for_tickers(tickers: list[str] | None) -> list[str]:
    return [c.cik for c in config.WATCHLIST if tickers is None or c.ticker in tickers]


# --- category 1: range violations ---


def _find_range_violations(conn: sqlite3.Connection, tickers: list[str] | None) -> list[dict]:
    """Every metric row outside its declared plausible range -- regardless of
    whether that (metric, cik, period) is in RANGE_EXCEPTIONS. Partitioned
    into hard failures vs. exceptions by the caller."""
    ciks = _ciks_for_tickers(tickers)
    if not ciks:
        return []
    placeholders = ",".join("?" for _ in ciks)
    rows = conn.execute(
        f"SELECT m.cik, c.ticker, m.period_start, m.period_end, m.name, m.value FROM metrics m "
        f"JOIN companies c ON c.cik = m.cik "
        f"WHERE m.cik IN ({placeholders}) AND m.value IS NOT NULL AND m.calc_version = ?",
        (*ciks, config.CALC_VERSION),
    ).fetchall()
    findings = []
    for row in rows:
        mdef = config.METRIC_REGISTRY.get(row["name"])
        if mdef is None or mdef.plausible_range is None:
            continue
        lo, hi = mdef.plausible_range
        if not (lo <= row["value"] <= hi):
            findings.append(
                {
                    "ticker": row["ticker"],
                    "cik": row["cik"],
                    "period_start": row["period_start"],
                    "period_end": row["period_end"],
                    "metric": row["name"],
                    "value": row["value"],
                    "range": (lo, hi),
                }
            )
    return findings


def _range_exception_key(v: dict) -> tuple[str, str, str, str]:
    return (v["metric"], v["cik"], v["period_start"], v["period_end"])


def _check_range_violations(conn: sqlite3.Connection, tickers: list[str] | None) -> list[dict]:
    """Hard-failing range violations: (metric, cik, period) NOT in RANGE_EXCEPTIONS."""
    return [f for f in _find_range_violations(conn, tickers) if _range_exception_key(f) not in config.RANGE_EXCEPTIONS]


def _check_range_exceptions(conn: sqlite3.Connection, tickers: list[str] | None) -> list[dict]:
    """Range violations covered by the register -- informational, with the
    written reason attached."""
    findings = [f for f in _find_range_violations(conn, tickers) if _range_exception_key(f) in config.RANGE_EXCEPTIONS]
    for f in findings:
        f["reason"] = config.RANGE_EXCEPTIONS[_range_exception_key(f)].reason
    return findings


def _group_range_violations(violations: list[dict]) -> dict[tuple[str, str], dict]:
    """Group by (metric, direction) so 58 individual lines become a handful of
    groups with counts and observed min/max -- evidence to widen a range once,
    not a scroll of occurrences (SPEC-004 R8 category 1)."""
    groups: dict[tuple[str, str], dict] = {}
    for v in violations:
        lo, hi = v["range"]
        direction = "above" if v["value"] > hi else "below"
        key = (v["metric"], direction)
        g = groups.setdefault(key, {"range": (lo, hi), "values": [], "tickers": set()})
        g["values"].append(v["value"])
        g["tickers"].add(v["ticker"])
    return groups


# --- category 2: DuPont reconciliation ---


def _metric_values_by_period(conn: sqlite3.Connection, cik: str, names: tuple[str, ...]) -> dict[str, dict[str, float]]:
    placeholders = ",".join("?" for _ in names)
    rows = conn.execute(
        f"SELECT period_end, name, value FROM metrics WHERE cik = ? AND calc_version = ? AND name IN ({placeholders})",
        (cik, config.CALC_VERSION, *names),
    ).fetchall()
    result: dict[str, dict[str, float]] = {}
    for row in rows:
        result.setdefault(row["period_end"], {})[row["name"]] = row["value"]
    return result


def _check_dupont(conn: sqlite3.Connection, tickers: list[str] | None) -> list[dict]:
    violations = []
    names = ("net_margin", "asset_turnover", "equity_multiplier", "roe")
    for cik, ticker in _companies(tickers):
        by_period = _metric_values_by_period(conn, cik, names)
        for period_end, vals in by_period.items():
            if any(vals.get(n) is None for n in names):
                continue
            product = vals["net_margin"] * vals["asset_turnover"] * vals["equity_multiplier"]
            roe = vals["roe"]
            denom = abs(roe) if roe != 0 else 1e-9
            rel_diff = abs(product - roe) / denom
            if rel_diff > config.DUPONT_RECONCILIATION_TOLERANCE:
                violations.append(
                    {"ticker": ticker, "period_end": period_end, "product": product, "roe": roe, "rel_diff": rel_diff}
                )
    return violations


# --- category 3: gross profit cross-check ---


def _check_gross_profit_crosscheck(conn: sqlite3.Connection, tickers: list[str] | None) -> list[dict]:
    violations = []
    for cik, ticker in _companies(tickers):
        facts = metrics_mod._load_facts(conn, cik)
        annual_ends, quarterly_ends = metrics_mod._real_fiscal_period_ends(conn, cik)
        periods_by_class = metrics_mod._build_periods_by_class(facts, annual_ends, quarterly_ends)
        seen: set[tuple[str, str]] = set()
        for cls, periods in periods_by_class.items():
            if cls == config.PERIOD_CLASS_OTHER:
                continue
            for start, end in periods:
                if (start, end) in seen:
                    continue
                seen.add((start, end))
                gp, _gp_alias = metrics_mod._resolve("gross_profit", facts, start, end)
                if gp is None:
                    continue
                rev, _rev_alias = metrics_mod._resolve("revenue", facts, start, end)
                cogs, _cogs_alias = metrics_mod._resolve("cogs", facts, start, end)
                if rev is None or cogs is None:
                    continue
                computed = rev - cogs
                denom = abs(gp) if gp != 0 else 1e-9
                rel_diff = abs(computed - gp) / denom
                if rel_diff > config.GROSS_PROFIT_CROSSCHECK_TOLERANCE:
                    violations.append(
                        {
                            "ticker": ticker,
                            "period_end": end,
                            "reported_gross_profit": gp,
                            "computed_revenue_minus_cogs": computed,
                            "rel_diff": rel_diff,
                        }
                    )
    return violations


# --- category 4: debt reconciliation ---


def _find_debt_reconciliation_findings(conn: sqlite3.Connection, tickers: list[str] | None) -> list[dict]:
    """Every (cik, period_end) where the combined debt tag and summed
    components disagree beyond tolerance -- regardless of whether it's in
    DEBT_RECONCILIATION_EXCEPTIONS. Partitioned by the caller."""
    findings = []
    for cik, ticker in _companies(tickers):
        facts = metrics_mod._load_facts(conn, cik)
        period_ends: set[str] = set()
        for canonical in ("total_debt", "debt_noncurrent", "debt_current"):
            for alias in config.CONCEPT_REGISTRY[canonical].aliases:
                for (_start, end) in facts.get(alias, {}):
                    period_ends.add(end)
        for end in sorted(period_ends):
            combined, combined_alias = metrics_mod._resolve("total_debt", facts, None, end)
            if combined is None:
                continue
            dn, _dn_alias = metrics_mod._resolve("debt_noncurrent", facts, None, end)
            dc, _dc_alias = metrics_mod._resolve("debt_current", facts, None, end)
            if dn is None or dc is None:
                continue
            component_sum = dn + dc
            denom = abs(combined) if combined != 0 else 1e-9
            rel_diff = abs(component_sum - combined) / denom
            if rel_diff > config.DEBT_RECONCILIATION_TOLERANCE:
                findings.append(
                    {
                        "ticker": ticker,
                        "cik": cik,
                        "period_end": end,
                        "combined_concept": combined_alias,
                        "combined": combined,
                        "component_sum": component_sum,
                        "rel_diff": rel_diff,
                    }
                )
    return findings


def _check_debt_reconciliation(conn: sqlite3.Connection, tickers: list[str] | None) -> list[dict]:
    """Hard-failing debt reconciliation findings: (cik, period_end) NOT in
    DEBT_RECONCILIATION_EXCEPTIONS."""
    return [
        f
        for f in _find_debt_reconciliation_findings(conn, tickers)
        if (f["cik"], f["period_end"]) not in config.DEBT_RECONCILIATION_EXCEPTIONS
    ]


def _check_debt_reconciliation_exceptions(conn: sqlite3.Connection, tickers: list[str] | None) -> list[dict]:
    """Debt reconciliation findings covered by the register -- informational,
    with the written reason attached."""
    findings = [
        f
        for f in _find_debt_reconciliation_findings(conn, tickers)
        if (f["cik"], f["period_end"]) in config.DEBT_RECONCILIATION_EXCEPTIONS
    ]
    for f in findings:
        f["reason"] = config.DEBT_RECONCILIATION_EXCEPTIONS[(f["cik"], f["period_end"])].reason
    return findings


# --- category 5: period-mixing check (redefined, SPEC-004 R8) ---


def _split_suffix(key: str) -> tuple[str, bool]:
    """(concept_or_component_name, is_prior_period)."""
    if key.endswith("_t-1"):
        return key[:-4], True
    if key.endswith("_t"):
        return key[:-2], False
    return key, False


def _check_period_mixing(conn: sqlite3.Connection, tickers: list[str] | None) -> list[dict]:
    """For every stored metric row, every current-period input value must match
    a real xbrl_facts row at that row's own exact (period_start, period_end);
    every prior-period input value must match a real fact of the same duration
    class as the row's own period.

    Redefined from the original "no period_end may appear in more than one
    duration class" check, which flagged 158 real but benign cases live --
    Amazon directly tags an implicit Q4 sharing an end date with the annual
    figure (SPEC-004 R3a), which is not period-mixing since the engine keys on
    the full (period_start, period_end) pair. This version asserts the actual
    property R8 describes and reads zero on correct data, because the engine
    guarantees it by construction; a non-zero result means a real bug let a
    value from a different period leak into a computation.
    """
    violations = []
    for cik, ticker in _companies(tickers):
        rows = conn.execute(
            "SELECT name, period_start, period_end, inputs_json FROM metrics "
            "WHERE cik = ? AND calc_version = ? AND value IS NOT NULL",
            (cik, config.CALC_VERSION),
        ).fetchall()
        for row in rows:
            inputs = json.loads(row["inputs_json"])
            row_start, row_end = row["period_start"], row["period_end"]
            row_days = (date.fromisoformat(row_end) - date.fromisoformat(row_start)).days
            row_class = metrics_mod._classify_duration(row_days)
            for key, value in inputs.items():
                if key == "_null_reason":
                    continue
                concept, is_prior = _split_suffix(key)
                canonical = metrics_mod._ALIAS_TO_CANONICAL.get(concept)
                if canonical is None:
                    continue  # not a raw XBRL concept (e.g. a Beneish component name)
                instant = config.CONCEPT_REGISTRY[canonical].instant

                if not is_prior:
                    if instant:
                        match = conn.execute(
                            "SELECT 1 FROM xbrl_facts WHERE cik = ? AND concept = ? AND value = ? "
                            "AND period_start IS NULL AND period_end = ? LIMIT 1",
                            (cik, concept, value, row_end),
                        ).fetchone()
                    else:
                        match = conn.execute(
                            "SELECT 1 FROM xbrl_facts WHERE cik = ? AND concept = ? AND value = ? "
                            "AND period_start = ? AND period_end = ? LIMIT 1",
                            (cik, concept, value, row_start, row_end),
                        ).fetchone()
                    if match is None:
                        violations.append(
                            {
                                "ticker": ticker,
                                "metric": row["name"],
                                "period_end": row_end,
                                "concept": concept,
                                "value": value,
                                "reason": "current-period value does not match a fact at this row's own period",
                            }
                        )
                else:
                    if instant:
                        ok = (
                            conn.execute(
                                "SELECT 1 FROM xbrl_facts WHERE cik = ? AND concept = ? AND value = ? "
                                "AND period_start IS NULL LIMIT 1",
                                (cik, concept, value),
                            ).fetchone()
                            is not None
                        )
                    else:
                        fact_rows = conn.execute(
                            "SELECT duration_days FROM xbrl_facts WHERE cik = ? AND concept = ? AND value = ? "
                            "AND period_start IS NOT NULL",
                            (cik, concept, value),
                        ).fetchall()
                        ok = any(metrics_mod._classify_duration(fr["duration_days"]) == row_class for fr in fact_rows)
                    if not ok:
                        violations.append(
                            {
                                "ticker": ticker,
                                "metric": row["name"],
                                "period_end": row_end,
                                "concept": concept,
                                "value": value,
                                "reason": "prior-period value does not match a same-class fact for this company",
                            }
                        )
    return violations


# --- category 6: alias agreement (new) ---


def _find_alias_disagreements(conn: sqlite3.Connection, tickers: list[str] | None) -> list[dict]:
    """Every pair of aliases of the same canonical input that both resolve for
    the exact same period and disagree beyond tolerance -- regardless of
    whether that canonical is in ALIAS_AGREEMENT_EXCEPTIONS. Partitioned into
    hard failures vs. exceptions by the caller."""
    findings = []
    for cik, ticker in _companies(tickers):
        facts = metrics_mod._load_facts(conn, cik)
        for canonical, ci in config.CONCEPT_REGISTRY.items():
            if len(ci.aliases) < 2:
                continue
            all_keys: set[tuple[str | None, str]] = set()
            for alias in ci.aliases:
                all_keys |= set(facts.get(alias, {}).keys())
            for key in all_keys:
                resolved = [(alias, facts[alias][key].value) for alias in ci.aliases if key in facts.get(alias, {})]
                if len(resolved) < 2:
                    continue
                for i in range(len(resolved)):
                    for j in range(i + 1, len(resolved)):
                        a_name, a_val = resolved[i]
                        b_name, b_val = resolved[j]
                        denom = abs(a_val) if a_val != 0 else 1e-9
                        rel_diff = abs(a_val - b_val) / denom
                        if rel_diff > config.ALIAS_AGREEMENT_TOLERANCE:
                            findings.append(
                                {
                                    "ticker": ticker,
                                    "canonical": canonical,
                                    "period_start": key[0],
                                    "period_end": key[1],
                                    "alias_a": a_name,
                                    "value_a": a_val,
                                    "alias_b": b_name,
                                    "value_b": b_val,
                                    "rel_diff": rel_diff,
                                }
                            )
    return findings


def _check_alias_agreement(conn: sqlite3.Connection, tickers: list[str] | None) -> list[dict]:
    """Hard-failing disagreements: canonical input NOT in ALIAS_AGREEMENT_EXCEPTIONS.
    Automates the alias-purity rule (ARCHITECTURE.md §2.1) across the whole registry."""
    return [
        f for f in _find_alias_disagreements(conn, tickers) if f["canonical"] not in config.ALIAS_AGREEMENT_EXCEPTIONS
    ]


def _check_alias_agreement_exceptions(conn: sqlite3.Connection, tickers: list[str] | None) -> list[dict]:
    """Disagreements for canonicals IN the register -- informational, with the
    written reason attached (R1g)."""
    findings = [
        f for f in _find_alias_disagreements(conn, tickers) if f["canonical"] in config.ALIAS_AGREEMENT_EXCEPTIONS
    ]
    for f in findings:
        f["reason"] = config.ALIAS_AGREEMENT_EXCEPTIONS[f["canonical"]].reason
    return findings


# --- category 7: concept drift (R5) ---


def _check_concept_drift(conn: sqlite3.Connection, tickers: list[str] | None) -> list[dict]:
    events = []
    for cik, ticker in _companies(tickers):
        facts = metrics_mod._load_facts(conn, cik)
        annual_ends, quarterly_ends = metrics_mod._real_fiscal_period_ends(conn, cik)
        periods_by_class = metrics_mod._build_periods_by_class(facts, annual_ends, quarterly_ends)
        all_periods = sorted(set().union(*periods_by_class.values())) if periods_by_class else []
        for canonical, ci in config.CONCEPT_REGISTRY.items():
            if len(ci.aliases) < 2:
                continue
            prev_alias: str | None = None
            for start, end in all_periods:
                lookup_start = None if ci.instant else start
                _val, alias = metrics_mod._resolve(canonical, facts, lookup_start, end)
                if alias is None:
                    continue
                if prev_alias is not None and alias != prev_alias:
                    events.append(
                        {
                            "ticker": ticker,
                            "canonical": canonical,
                            "period_end": end,
                            "from_concept": prev_alias,
                            "to_concept": alias,
                        }
                    )
                prev_alias = alias
    return events


# --- category 8: unverified YoY drift (new, informational) ---


def _check_unverified_yoy_drift(conn: sqlite3.Connection, tickers: list[str] | None) -> list[dict]:
    """A YoY-style metric whose current/prior periods resolved via two
    different aliases of the same canonical input is spanning a concept-drift
    boundary. Flag it if those two aliases were never co-tagged for this
    company -- equivalence is being assumed, not demonstrated (unlike the
    real NVIDIA pretax_income case, confirmed equivalent by 5 co-tagged
    periods with identical values -- R5)."""
    findings = []
    for cik, ticker in _companies(tickers):
        facts = metrics_mod._load_facts(conn, cik)
        rows = conn.execute(
            "SELECT name, period_end, inputs_json FROM metrics WHERE cik = ? AND calc_version = ? AND value IS NOT NULL",
            (cik, config.CALC_VERSION),
        ).fetchall()
        for row in rows:
            mdef = config.METRIC_REGISTRY.get(row["name"])
            if mdef is None or not mdef.needs_prior:
                continue
            inputs = json.loads(row["inputs_json"])
            by_canonical: dict[str, dict[str, str]] = {}
            for key in inputs:
                concept, is_prior = _split_suffix(key)
                if concept == key and not key.endswith("_t"):
                    continue  # no suffix at all -- not a two-period comparison
                canonical = metrics_mod._ALIAS_TO_CANONICAL.get(concept)
                if canonical is None:
                    continue
                by_canonical.setdefault(canonical, {})["prior" if is_prior else "current"] = concept
            for canonical, whenmap in by_canonical.items():
                cur_alias = whenmap.get("current")
                prior_alias = whenmap.get("prior")
                if cur_alias is None or prior_alias is None or cur_alias == prior_alias:
                    continue
                cur_periods = set(facts.get(cur_alias, {}).keys())
                prior_periods = set(facts.get(prior_alias, {}).keys())
                if not (cur_periods & prior_periods):
                    findings.append(
                        {
                            "ticker": ticker,
                            "metric": row["name"],
                            "period_end": row["period_end"],
                            "canonical": canonical,
                            "from_concept": prior_alias,
                            "to_concept": cur_alias,
                        }
                    )
    return findings


# --- category 9: coverage ---


def _check_coverage(conn: sqlite3.Connection, tickers: list[str] | None) -> list[dict]:
    coverage = []
    for cik, ticker in _companies(tickers):
        rows = conn.execute(
            "SELECT name, value FROM metrics WHERE cik = ? AND calc_version = ?", (cik, config.CALC_VERSION)
        ).fetchall()
        by_name: dict[str, list[bool]] = {}
        for row in rows:
            by_name.setdefault(row["name"], []).append(row["value"] is not None)
        for name, flags in sorted(by_name.items()):
            total = len(flags)
            computed = sum(flags)
            coverage.append(
                {
                    "ticker": ticker,
                    "metric": name,
                    "computed": computed,
                    "total": total,
                    "pct": computed / total if total else 0.0,
                }
            )
    return coverage


# --- category 10: unresolved concepts (global, not per-company) ---


def _check_unresolved_concepts(conn: sqlite3.Connection, tickers: list[str] | None) -> list[dict]:
    ciks = _ciks_for_tickers(tickers)
    if not ciks:
        return []
    placeholders = ",".join("?" for _ in ciks)
    present = {
        row["concept"]
        for row in conn.execute(f"SELECT DISTINCT concept FROM xbrl_facts WHERE cik IN ({placeholders})", ciks).fetchall()
    }
    unresolved = []
    for canonical, ci in config.CONCEPT_REGISTRY.items():
        if not any(alias in present for alias in ci.aliases):
            unresolved.append({"canonical": canonical, "aliases_tried": list(ci.aliases)})
    return unresolved


# --- category 11: finance lease zero-assumption disclosure (new, informational) ---


def _check_finance_lease_zero_assumptions(conn: sqlite3.Connection, tickers: list[str] | None) -> list[dict]:
    """One line per company where total_debt assumed $0 for an absent finance
    lease component (SPEC-004 R1h) -- keeps the assumption visible at the
    portfolio level, not just inside individual formula strings."""
    findings = []
    for cik, ticker in _companies(tickers):
        facts = metrics_mod._load_facts(conn, cik)
        period_ends: set[str] = set()
        for canonical in ("total_debt", "debt_noncurrent", "debt_current"):
            for alias in config.CONCEPT_REGISTRY[canonical].aliases:
                for (_start, end) in facts.get(alias, {}):
                    period_ends.add(end)
        affected = 0
        for end in period_ends:
            borrowings, _note, _inputs = metrics_mod._resolve_borrowings(facts, end)
            if borrowings is None:
                continue
            fl_nc, _ = metrics_mod._resolve("finance_lease_liability_noncurrent", facts, None, end)
            fl_c, _ = metrics_mod._resolve("finance_lease_liability_current", facts, None, end)
            if fl_nc is None or fl_c is None:
                affected += 1
        if affected:
            findings.append({"ticker": ticker, "periods_affected": affected})
    return findings


# --- category 12: discrete quarter sum-back (SPEC-008 C4, approved 2026-08-08) ---


def _find_discrete_quarter_sum_violations(conn: sqlite3.Connection, tickers: list[str] | None) -> list[dict]:
    """Every (cik, canonical, fiscal year) where the four stored discrete
    quarters (Q1 + Q2 + Q3 + Q4-discrete) don't sum to a FRESH read of the
    filed FY figure -- regardless of whether it's in
    DISCRETE_QUARTER_SUM_EXCEPTIONS. Partitioned by the caller.

    This is an algebraic identity, not a business-tolerance check (contrast
    category 3/4's relative tolerances for genuinely approximate cross-
    checks). By construction, Q4-discrete = FY_filed - 9mo, so
    Q1+Q2+Q3+Q4 telescopes to EXACTLY FY_filed whenever every input was
    drawn from one consistent vintage:

        Q1 + Q2 + Q3 + Q4
      = V1 + (V2-V1) + (V3-V2) + (FY-V3)
      = FY

    The FY figure here is read FRESH from `xbrl_facts` (`metrics_mod.
    _resolve`, not the `metrics` table) rather than reused from whatever
    `compute_discrete_quarter_metrics` embedded in Q4-discrete's own stored
    value -- deliberately, so this check catches STALENESS: if a
    restatement changes the filed FY figure (or any quarterly YTD figure)
    after `compute-metrics` last ran, the stored discrete quarters still
    reflect the OLD vintage and the sum stops tying to the CURRENT filed
    figure. A pure re-derivation compared against itself would trivially
    always tie by the algebra above and catch nothing; comparing STORED
    against FRESH is what makes this a real check."""
    findings = []
    for cik, ticker in _companies(tickers):
        facts = metrics_mod._load_facts(conn, cik)
        quarter_map = metrics_mod._fiscal_quarter_map(conn, cik)
        fiscal_years = sorted({fy for (fy, _fp) in quarter_map})
        for canonical in metrics_mod._DISCRETE_QUARTER_CANONICALS:
            name = f"{canonical}_discrete"
            for fy in fiscal_years:
                fy_start = metrics_mod._fiscal_year_start(facts, quarter_map, fy, canonical)
                fy_end = quarter_map.get((fy, "FY"))
                if fy_start is None or fy_end is None:
                    continue
                fy_filed, _fy_alias = metrics_mod._resolve(canonical, facts, fy_start, fy_end)
                if fy_filed is None:
                    continue
                quarter_values: list[float] = []
                complete = True
                for fp in ("Q1", "Q2", "Q3", "FY"):
                    end = quarter_map.get((fy, fp))
                    if end is None:
                        complete = False
                        break
                    row = conn.execute(
                        "SELECT value FROM metrics WHERE cik = ? AND period_end = ? AND name = ? AND calc_version = ?",
                        (cik, end, name, config.CALC_VERSION),
                    ).fetchone()
                    if row is None or row["value"] is None:
                        complete = False
                        break
                    quarter_values.append(row["value"])
                if not complete:
                    continue  # incomplete fiscal year -- nothing to check yet, not a violation
                component_sum = sum(quarter_values)
                diff = abs(component_sum - fy_filed)
                if diff > config.DISCRETE_QUARTER_SUM_TOLERANCE_USD:
                    findings.append(
                        {
                            "ticker": ticker,
                            "cik": cik,
                            "canonical": canonical,
                            "fiscal_year": fy,
                            "fy_period_end": fy_end,
                            "filed_fy": fy_filed,
                            "quarter_sum": component_sum,
                            "diff": diff,
                        }
                    )
    return findings


def _check_discrete_quarter_sum(conn: sqlite3.Connection, tickers: list[str] | None) -> list[dict]:
    """Hard-failing sum-back findings: (cik, canonical, fy_period_end) NOT in
    DISCRETE_QUARTER_SUM_EXCEPTIONS."""
    return [
        f
        for f in _find_discrete_quarter_sum_violations(conn, tickers)
        if (f["cik"], f["canonical"], f["fy_period_end"]) not in config.DISCRETE_QUARTER_SUM_EXCEPTIONS
    ]


def _check_discrete_quarter_sum_exceptions(conn: sqlite3.Connection, tickers: list[str] | None) -> list[dict]:
    """Sum-back findings covered by the register -- informational, with the
    written reason attached."""
    findings = [
        f
        for f in _find_discrete_quarter_sum_violations(conn, tickers)
        if (f["cik"], f["canonical"], f["fy_period_end"]) in config.DISCRETE_QUARTER_SUM_EXCEPTIONS
    ]
    for f in findings:
        f["reason"] = config.DISCRETE_QUARTER_SUM_EXCEPTIONS[(f["cik"], f["canonical"], f["fy_period_end"])].reason
    return findings


# --- SPEC-005 R8: observation checks. All queries filter on each rule's own
# CURRENT rule_version (config.RULE_REGISTRY[name].version) -- exactly as
# metrics checks filter on config.CALC_VERSION (SPEC-005 change 8) -- so a
# stale prior-version row never counts toward firing-rate or coverage stats.


def _observations_for_rule(conn: sqlite3.Connection, tickers: list[str] | None, rule_name: str) -> list[dict]:
    ciks = _ciks_for_tickers(tickers)
    if not ciks:
        return []
    version = config.RULE_REGISTRY[rule_name].version
    placeholders = ",".join("?" for _ in ciks)
    rows = conn.execute(
        f"SELECT id, cik, period_end, subject, severity, statement, refs_json FROM observations "
        f"WHERE cik IN ({placeholders}) AND rule_name = ? AND rule_version = ?",
        (*ciks, rule_name, version),
    ).fetchall()
    return [dict(r) for r in rows]


def _check_observation_per_filing_contribution(conn: sqlite3.Connection, tickers: list[str] | None) -> list[dict]:
    """Per rule, the mean and maximum number of observations it contributes
    to a single filing (R8, replacing the original eligible-periods-fired
    percentage). Informational, always reported (not just when high) --
    there is no longer a fixed ceiling to flag against; see the constant's
    removal note and SPEC-005 R8b for why.

    The original measure counted firings per (subject, period) -- which
    structurally penalises a rule that evaluates 30 metrics per filing
    (metric_multi_year_extreme) against one that evaluates a single
    condition (metric_threshold_cross), regardless of how much of any ONE
    filing's brief either actually occupies. What matters for a dashboard or
    an LLM narration budget is per-filing occupancy, not per-subject-period
    frequency -- a rule that fires rarely but produces 14 observations on
    the one filing where it does fire crowds out everything else in that
    filing's list exactly as much as a rule that fires constantly at 1
    observation each time.
    """
    ciks = _ciks_for_tickers(tickers)
    if not ciks:
        return []
    placeholders = ",".join("?" for _ in ciks)
    findings = []
    for rule_name in config.RULE_REGISTRY:
        version = config.RULE_REGISTRY[rule_name].version
        rows = conn.execute(
            f"SELECT cik, accession_no, COUNT(*) AS n FROM observations "
            f"WHERE cik IN ({placeholders}) AND rule_name = ? AND rule_version = ? AND accession_no IS NOT NULL "
            f"GROUP BY cik, accession_no",
            (*ciks, rule_name, version),
        ).fetchall()
        if not rows:
            continue
        counts = [row["n"] for row in rows]
        findings.append(
            {
                "rule_name": rule_name,
                "filings_contributed_to": len(counts),
                "mean_per_filing": sum(counts) / len(counts),
                "max_per_filing": max(counts),
            }
        )
    return findings


def _check_observation_dead_rules(conn: sqlite3.Connection, tickers: list[str] | None) -> list[dict]:
    """A rule that never fires across the whole corpus, informational (R8) --
    either miscalibrated or dead code, reported either way."""
    findings = []
    for rule_name in config.RULE_REGISTRY:
        fired = len(_observations_for_rule(conn, tickers, rule_name))
        eligible = sum(
            observations_mod.ELIGIBLE_COUNT_FUNCS[rule_name](conn, cik) for cik in _ciks_for_tickers(tickers)
        )
        if fired == 0:
            findings.append({"rule_name": rule_name, "eligible": eligible})
    return findings


def _resolve_ref(conn: sqlite3.Connection, ref: dict) -> sqlite3.Row | None:
    if ref["table"] == "metrics":
        return conn.execute("SELECT period_end FROM metrics WHERE id = ?", (ref["id"],)).fetchone()
    if ref["table"] == "sections":
        return conn.execute(
            "SELECT f.period_end FROM sections s JOIN filings f ON f.accession_no = s.accession_no WHERE s.id = ?",
            (ref["id"],),
        ).fetchone()
    return None


def _check_observation_lookahead(conn: sqlite3.Connection, tickers: list[str] | None) -> list[dict]:
    """Hard failure (R3/R8): no observation may reference data with
    period_end later than its own -- re-verified against the PERSISTED
    table, independently of the write-time assertion in compute_observations."""
    violations = []
    for rule_name in config.RULE_REGISTRY:
        for row in _observations_for_rule(conn, tickers, rule_name):
            for ref in json.loads(row["refs_json"]):
                resolved = _resolve_ref(conn, ref)
                if resolved is not None and resolved["period_end"] > row["period_end"]:
                    violations.append(
                        {
                            "rule_name": rule_name, "cik": row["cik"], "period_end": row["period_end"],
                            "subject": row["subject"], "ref": ref, "ref_period_end": resolved["period_end"],
                        }
                    )
    return violations


def _check_observation_determinism(conn: sqlite3.Connection, tickers: list[str] | None) -> list[dict]:
    """Hard failure (R8): recompute every rule in memory (no write) and
    assert the statement is byte-identical to what is persisted.

    Iterates over PERSISTED rows, not over every possible recomputed
    observation -- an observations table that simply hasn't been computed
    yet (e.g. right after compute-metrics, before compute-observations ever
    ran) has nothing persisted to compare against and must read as zero
    violations, not "everything mismatches." Determinism means "recomputing
    something already written reproduces it," not "everything computable
    has been computed."
    """
    violations = []
    for cik, ticker in _companies(tickers):
        recomputed_by_key = {
            (obs.rule_name, obs.period_end, obs.subject): obs.statement
            for obs in observations_mod._observations_for_company(conn, cik, None)
        }
        persisted_rows = conn.execute(
            "SELECT rule_name, period_end, subject, statement, rule_version FROM observations WHERE cik = ?",
            (cik,),
        ).fetchall()
        for row in persisted_rows:
            if row["rule_version"] != config.RULE_REGISTRY[row["rule_name"]].version:
                continue  # stale version -- not part of the current determinism check
            key = (row["rule_name"], row["period_end"], row["subject"])
            recomputed = recomputed_by_key.get(key)
            if recomputed is None or recomputed != row["statement"]:
                violations.append(
                    {
                        "ticker": ticker, "rule_name": row["rule_name"], "period_end": row["period_end"],
                        "subject": row["subject"], "recomputed": recomputed, "persisted": row["statement"],
                    }
                )
    return violations


def _check_observation_orphan_refs(conn: sqlite3.Connection, tickers: list[str] | None) -> list[dict]:
    """Hard failure (R2/R8): every id in refs_json must resolve to an existing row."""
    violations = []
    for rule_name in config.RULE_REGISTRY:
        for row in _observations_for_rule(conn, tickers, rule_name):
            for ref in json.loads(row["refs_json"]):
                if _resolve_ref(conn, ref) is None:
                    violations.append(
                        {
                            "rule_name": rule_name, "cik": row["cik"], "period_end": row["period_end"],
                            "subject": row["subject"], "ref": ref,
                        }
                    )
    return violations


# --- SPEC-005 R10 (AC14 amendment): database size, a growth measure ---


def _db_file_path(conn: sqlite3.Connection) -> Path | None:
    for row in conn.execute("PRAGMA database_list"):
        if row["name"] == "main" and row["file"]:
            return Path(row["file"])
    return None


def _check_db_size(conn: sqlite3.Connection) -> dict:
    """Informational, never hard-failing (R10): current app.db size against
    the 15 MB soft ceiling, plus the last hand-measured marginal cost of one
    additional filing (config.DB_SIZE_MEASURED_MARGINAL_BYTES_PER_FILING --
    a point measurement, not recomputed live here; see that constant's
    docstring for why)."""
    path = _db_file_path(conn)
    size_bytes = path.stat().st_size if path is not None and path.exists() else None
    return {
        "size_bytes": size_bytes,
        "soft_ceiling_bytes": config.DB_SIZE_SOFT_CEILING_BYTES,
        "over_soft_ceiling": size_bytes is not None and size_bytes > config.DB_SIZE_SOFT_CEILING_BYTES,
        "measured_marginal_bytes_per_filing": config.DB_SIZE_MEASURED_MARGINAL_BYTES_PER_FILING,
        "measured_marginal_filing_description": config.DB_SIZE_MEASURED_MARGINAL_FILING_DESCRIPTION,
    }


# --- SPEC-006 R10: LLM infrastructure checks ---


def _check_llm_pricing_staleness(conn: sqlite3.Connection) -> list[dict]:
    """Informational (R1a/R10): a stale price doesn't corrupt persisted data,
    but the budget cap's entire meaning depends on LLM_PRICING being current,
    so a verification date older than LLM_PRICING_STALENESS_WARNING_DAYS is
    surfaced here rather than only discoverable by reading config.py."""
    today = date.today()
    findings = []
    for model, pricing in config.LLM_PRICING.items():
        verified = date.fromisoformat(pricing.verified_date)
        age_days = (today - verified).days
        if age_days > config.LLM_PRICING_STALENESS_WARNING_DAYS:
            findings.append(
                {
                    "model": model,
                    "verified_date": pricing.verified_date,
                    "age_days": age_days,
                    "source_url": pricing.source_url,
                }
            )
    return findings


def _check_llm_ledger_reconciliation(conn: sqlite3.Connection) -> list[dict]:
    """Hard failure (R10): llm.total_spent's SUM(cost_usd) must match a raw
    SQL sum computed independently here -- guards against total_spent ever
    drifting from what the ledger literally contains (e.g. a future filter
    added to one but not the other)."""
    reported = llm_mod.total_spent(conn)
    raw = conn.execute("SELECT COALESCE(SUM(cost_usd), 0.0) AS total FROM llm_calls").fetchone()["total"]
    if abs(reported - raw) > 1e-9:
        return [{"reported_total": reported, "raw_sum": raw}]
    return []


def _check_llm_cost_recomputation(conn: sqlite3.Connection) -> list[dict]:
    """SPEC-006A (L10, "reconciliation against the ledger"): every 'ok'/
    'error' row's stored cost_usd must equal compute_cost(model,
    input_tokens, output_tokens) recomputed under the CURRENT
    config.LLM_PRICING. This is exactly the check that would have caught
    the 2026-07-27 mispricing automatically (a row recorded at the $3/$15
    rate while LLM_PRICING said $2/$10, or vice versa).

    Informational, not a hard failure: a REAL pricing change (see
    LLM_SONNET5_RATE_REVIEW_DATE) makes old rows mismatch on purpose, since
    they were genuinely billed at the rate in effect when they were made --
    that is expected drift, not corruption, and must be judged by a human
    at that point (backfill the old rows' known rate, same as the
    2026-07-27 fix), not silenced or hard-failed here automatically.
    """
    rows = conn.execute(
        "SELECT id, model, input_tokens, output_tokens, cost_usd FROM llm_calls WHERE status IN ('ok', 'error')"
    ).fetchall()
    mismatches = []
    for row in rows:
        try:
            expected = llm_mod.compute_cost(row["model"], row["input_tokens"], row["output_tokens"])
        except ValueError:
            continue  # unpriced model -- not this check's concern
        if abs(expected - row["cost_usd"]) > 1e-6:
            mismatches.append(
                {
                    "call_id": row["id"],
                    "model": row["model"],
                    "stored_cost_usd": row["cost_usd"],
                    "expected_cost_usd_at_current_pricing": expected,
                }
            )
    return mismatches


def _check_llm_pricing_rate_review(conn: sqlite3.Connection) -> list[dict]:
    """SPEC-006A: claude-sonnet-5's introductory rate in LLM_PRICING expires
    on config.LLM_SONNET5_RATE_REVIEW_DATE (2026-09-01). Once today reaches
    that date, LLM_PRICING must be hand-bumped to the standard $3.00/$15.00
    rate or every call from that day forward is silently mispriced again --
    the forcing function for the exact mistake this spec exists to stop
    repeating."""
    today = date.today()
    review_date = date.fromisoformat(config.LLM_SONNET5_RATE_REVIEW_DATE)
    if today < review_date:
        return []
    pricing = config.LLM_PRICING.get("claude-sonnet-5")
    if pricing is not None and pricing.input_per_mtok >= 3.00:
        return []  # already bumped to the standard rate
    return [
        {
            "model": "claude-sonnet-5",
            "review_date": config.LLM_SONNET5_RATE_REVIEW_DATE,
            "today": today.isoformat(),
            "current_input_per_mtok": pricing.input_per_mtok if pricing is not None else None,
        }
    ]


def _check_llm_budget_headroom(conn: sqlite3.Connection) -> dict:
    """Report spent/remaining always; a hard failure only if spend has
    somehow exceeded the budget -- that would mean the cap leaked (R10)."""
    spent = llm_mod.total_spent(conn)
    return {
        "spent": spent,
        "budget": config.LLM_BUDGET_USD,
        "remaining": config.LLM_BUDGET_USD - spent,
        "over_budget": spent > config.LLM_BUDGET_USD,
    }


def _check_llm_orphan_findings(conn: sqlite3.Connection) -> list[dict]:
    """Hard failure (R10): every finding must resolve to an analysis, and that
    analysis must resolve to a section."""
    rows = conn.execute(
        """
        SELECT fi.id AS finding_id, fi.analysis_id, an.id AS resolved_analysis_id, an.section_id,
               se.id AS resolved_section_id
        FROM findings fi
        LEFT JOIN analyses an ON an.id = fi.analysis_id
        LEFT JOIN sections se ON se.id = an.section_id
        """
    ).fetchall()
    violations = []
    for row in rows:
        if row["resolved_analysis_id"] is None:
            violations.append({"finding_id": row["finding_id"], "problem": f"analysis_id {row['analysis_id']} does not resolve"})
        elif row["resolved_section_id"] is None:
            violations.append({"finding_id": row["finding_id"], "problem": f"analysis's section_id {row['section_id']} does not resolve"})
    return violations


def _check_llm_quote_integrity(conn: sqlite3.Connection) -> list[dict]:
    """Hard failure (R10): re-verify every stored finding's quote against its
    section's CURRENT text, independently of the write-time verification in
    analyze.py -- a stored finding that no longer matches its source is
    corruption, whether from a bug or from the section text having been
    re-written under the same hash key."""
    rows = conn.execute(
        """
        SELECT fi.id AS finding_id, fi.quote, se.text_hash
        FROM findings fi
        JOIN analyses an ON an.id = fi.analysis_id
        JOIN sections se ON se.id = an.section_id
        """
    ).fetchall()
    violations = []
    for row in rows:
        source_text = section_store.read_section_text(row["text_hash"])
        if not analyze_mod.verify_quote(row["quote"], source_text):
            violations.append({"finding_id": row["finding_id"], "quote": row["quote"]})
    return violations


def _check_llm_numeric_support(conn: sqlite3.Connection) -> list[dict]:
    """Informational, per prompt version: of the numeric tokens appearing in
    kept findings' `headline`/`detail`, how many appear in the source note.

    Recomputed from stored findings and their section text rather than stored
    at write time -- the same choice as quote integrity above, and for the
    same reason: a derived figure is a property of (finding, source), so
    deriving it on demand cannot drift out of step with either. Warning
    metric only while config.NUMERIC_SUPPORT_ENFORCE is False; unsupported
    numbers include legitimately computed ones.
    """
    rows = conn.execute(
        """
        SELECT fi.id AS finding_id, fi.headline, fi.detail, fi.quote, an.prompt_version, se.text_hash
        FROM findings fi
        JOIN analyses an ON an.id = fi.analysis_id
        JOIN sections se ON se.id = an.section_id
        """
    ).fetchall()
    totals: dict[str, dict] = {}
    for row in rows:
        source_text = section_store.read_section_text(row["text_hash"])
        numeric = analyze_mod.check_numeric_support(
            row["headline"], row["detail"], row["quote"], source_text
        )
        bucket = totals.setdefault(
            row["prompt_version"],
            {
                "checked": 0, "supported_in_quote": 0, "supported_in_note_only": 0,
                "derived_verified": 0, "findings_with_unsupported": 0, "examples": [],
            },
        )
        bucket["checked"] += numeric.checked
        bucket["supported_in_quote"] += numeric.supported_in_quote
        bucket["supported_in_note_only"] += numeric.supported_in_note_only
        bucket["derived_verified"] += numeric.derived_verified_count
        if numeric.unsupported:
            bucket["findings_with_unsupported"] += 1
            if len(bucket["examples"]) < 5:
                bucket["examples"].append({"finding_id": row["finding_id"], "unsupported": numeric.unsupported})
    results = []
    for version, d in sorted(totals.items()):
        supported = d["supported_in_quote"] + d["supported_in_note_only"]
        results.append(
            {
                "prompt_version": version,
                "checked": d["checked"],
                "supported": supported,
                "supported_in_quote": d["supported_in_quote"],
                "supported_in_note_only": d["supported_in_note_only"],
                "derived_verified": d["derived_verified"],
                "support_rate": (supported / d["checked"]) if d["checked"] else None,
                "findings_with_unsupported": d["findings_with_unsupported"],
                "examples": d["examples"],
            }
        )
    return results


def _check_llm_discard_rate(conn: sqlite3.Connection) -> list[dict]:
    """Informational, per prompt version (R10): how many of the findings the
    model returned survived quote verification. output_json on `analyses`
    holds every finding the model RETURNED (verified or not); `findings` only
    holds what SURVIVED -- the difference between the two, per analysis, is
    the discard count."""
    kept_by_analysis = dict(
        conn.execute("SELECT analysis_id, COUNT(*) AS n FROM findings GROUP BY analysis_id").fetchall()
    )
    totals: dict[str, dict[str, int]] = {}
    for row in conn.execute("SELECT id, prompt_version, output_json FROM analyses"):
        returned = len(json.loads(row["output_json"]).get("findings", []))
        kept = kept_by_analysis.get(row["id"], 0)
        bucket = totals.setdefault(row["prompt_version"], {"returned": 0, "kept": 0})
        bucket["returned"] += returned
        bucket["kept"] += kept
    findings = []
    for version, d in sorted(totals.items()):
        discarded = d["returned"] - d["kept"]
        rate = discarded / d["returned"] if d["returned"] else 0.0
        findings.append(
            {"prompt_version": version, "returned": d["returned"], "kept": d["kept"], "discarded": discarded, "discard_rate": rate}
        )
    return findings


# --- SPEC-007: The Grounded Brief (R7) ---


def _check_brief_orphan_references(conn: sqlite3.Connection) -> list[dict]:
    """Hard failure (R7): every reference in every stored sentence must
    resolve to a live observation or finding row. A model cannot cite what
    does not exist, and this must still hold true after the fact -- R4's
    write-time check is not assumed to be the only thing keeping this true."""
    violations = []
    for row in conn.execute("SELECT id, brief_id, refs_json FROM brief_sentences"):
        refs = json.loads(row["refs_json"])
        for ref in refs:
            parsed = brief_mod.parse_ref(ref)
            if parsed is None:
                violations.append({"sentence_id": row["id"], "brief_id": row["brief_id"], "ref": ref, "problem": "malformed ref"})
                continue
            kind, id_ = parsed
            table = "observations" if kind == "obs" else "findings"
            exists = conn.execute(f"SELECT 1 FROM {table} WHERE id = ?", (id_,)).fetchone()
            if not exists:
                violations.append(
                    {"sentence_id": row["id"], "brief_id": row["brief_id"], "ref": ref, "problem": f"no such {table} row"}
                )
    return violations


def _check_brief_cross_filing_references(conn: sqlite3.Connection) -> list[dict]:
    """Hard failure (R7): no sentence may cite a source from a DIFFERENT
    filing than the brief it belongs to -- R2's whole selection is scoped to
    one filing's accession_no; a cross-filing reference means either a
    selection bug or a resolved-but-wrong reference slipped through."""
    violations = []
    rows = conn.execute(
        "SELECT bs.id AS sentence_id, bs.brief_id, bs.refs_json, b.accession_no "
        "FROM brief_sentences bs JOIN briefs b ON b.id = bs.brief_id"
    ).fetchall()
    for row in rows:
        refs = json.loads(row["refs_json"])
        for ref in refs:
            parsed = brief_mod.parse_ref(ref)
            if parsed is None:
                continue  # already caught by the orphan-reference check
            kind, id_ = parsed
            table = "observations" if kind == "obs" else "findings"
            source_row = conn.execute(f"SELECT accession_no FROM {table} WHERE id = ?", (id_,)).fetchone()
            if source_row is not None and source_row["accession_no"] != row["accession_no"]:
                violations.append(
                    {
                        "sentence_id": row["sentence_id"], "brief_id": row["brief_id"], "ref": ref,
                        "brief_accession_no": row["accession_no"], "source_accession_no": source_row["accession_no"],
                    }
                )
    return violations


def _check_brief_sentence_type_validity(conn: sqlite3.Connection) -> list[dict]:
    """Hard failure (R7): every stored sentence_type must be one of the five
    permitted values -- R4 drops an unrecognised type before it is ever
    written, so any row failing this means corruption, not a live model
    response."""
    violations = []
    for row in conn.execute("SELECT id, brief_id, sentence_type FROM brief_sentences"):
        if row["sentence_type"] not in config.BRIEF_SENTENCE_TYPES:
            violations.append({"sentence_id": row["id"], "brief_id": row["brief_id"], "sentence_type": row["sentence_type"]})
    return violations


def _source_text_for_row(kind: str, row: dict) -> str:
    """Same construction as brief._source_text -- duplicated rather than
    imported (that name is private to brief.py), matching this project's
    established convention of small per-module formatting helpers."""
    if kind == "obs":
        return row["statement"]
    return f"{row['headline']} {row['detail'] or ''} {row['quote'] or ''}"


def _check_brief_reverification(conn: sqlite3.Connection) -> list[dict]:
    """Hard failure (R7): re-run R4's per-type mechanical checks against
    every STORED sentence, resolving each cited ref to its CURRENT text
    (not re-doing R2's selection, which could have legitimately changed
    since generation -- only whether the stored sentence itself still
    passes its own declared type's rules against its own cited sources).
    Any failure means corruption: a bad sentence was persisted despite R4,
    or an underlying observation/finding's text changed underneath it."""
    violations = []
    for row in conn.execute("SELECT id, brief_id, sentence_type, text, refs_json FROM brief_sentences"):
        refs = json.loads(row["refs_json"])
        supplied_index: dict[str, dict] = {}
        unresolved = False
        for ref in refs:
            parsed = brief_mod.parse_ref(ref)
            if parsed is None:
                unresolved = True
                break
            kind, id_ = parsed
            table = "observations" if kind == "obs" else "findings"
            source_row = conn.execute(f"SELECT * FROM {table} WHERE id = ?", (id_,)).fetchone()
            if source_row is None:
                unresolved = True
                break
            supplied_index[ref] = {"kind": kind, "row": dict(source_row), "text": _source_text_for_row(kind, dict(source_row))}
        if unresolved:
            continue  # already caught by the orphan-reference check
        sentence = {"type": row["sentence_type"], "text": row["text"], "refs": refs}
        kept, reason = brief_mod.verify_sentence(sentence, supplied_index)
        if not kept:
            violations.append({"sentence_id": row["id"], "brief_id": row["brief_id"], "reason": reason})
    return violations


def _check_brief_empty(conn: sqlite3.Connection) -> list[dict]:
    """Informational (R7): every brief with zero stored sentences, and why --
    "Do not pay to be told nothing happened" made no call at all (no
    observations/findings supplied); a real call producing zero surviving
    sentences means the generator, R4, or the verifier discarded everything.
    A quiet quarter is legitimate; this is reported, never a failure."""
    rows = conn.execute(
        """
        SELECT b.id AS brief_id, b.accession_no, b.prompt_version, b.verifier_version,
               b.generator_dropped, b.verifier_dropped, b.call_id,
               (SELECT COUNT(*) FROM brief_sentences bs WHERE bs.brief_id = b.id) AS n_sentences
        FROM briefs b
        """
    ).fetchall()
    empties = []
    for row in rows:
        if row["n_sentences"] > 0:
            continue
        reason = "no observations or findings supplied" if row["call_id"] is None else (
            f"all sentences dropped (generator={row['generator_dropped']}, verifier={row['verifier_dropped']})"
        )
        empties.append(
            {
                "brief_id": row["brief_id"], "accession_no": row["accession_no"],
                "prompt_version": row["prompt_version"], "verifier_version": row["verifier_version"], "reason": reason,
            }
        )
    return empties


def _check_brief_drop_rates(conn: sqlite3.Connection) -> list[dict]:
    """Informational (R7): generator-side and verifier-side drop rates, per
    (prompt_version, verifier_version). A rising generator rate means the
    generator got looser; a persistently zero verifier rate over many
    briefs means the verifier is not actually adversarial and should be
    checked with a deliberately bad sentence (R5)."""
    rows = conn.execute(
        """
        SELECT b.prompt_version, b.verifier_version, b.generator_dropped, b.verifier_dropped,
               (SELECT COUNT(*) FROM brief_sentences bs WHERE bs.brief_id = b.id) AS kept_count
        FROM briefs b WHERE b.call_id IS NOT NULL
        """
    ).fetchall()
    totals: dict[tuple[str, str], dict[str, int]] = {}
    for row in rows:
        key = (row["prompt_version"], row["verifier_version"])
        bucket = totals.setdefault(key, {"generator_dropped": 0, "verifier_dropped": 0, "kept": 0, "briefs": 0})
        bucket["generator_dropped"] += row["generator_dropped"]
        bucket["verifier_dropped"] += row["verifier_dropped"]
        bucket["kept"] += row["kept_count"]
        bucket["briefs"] += 1
    results = []
    for (prompt_version, verifier_version), d in sorted(totals.items()):
        survived_r4 = d["kept"] + d["verifier_dropped"]
        raw_total = survived_r4 + d["generator_dropped"]
        results.append(
            {
                "prompt_version": prompt_version, "verifier_version": verifier_version, "briefs": d["briefs"],
                "raw_sentences": raw_total, "generator_dropped": d["generator_dropped"],
                "generator_drop_rate": (d["generator_dropped"] / raw_total) if raw_total else None,
                "survived_r4": survived_r4, "verifier_dropped": d["verifier_dropped"],
                "verifier_drop_rate": (d["verifier_dropped"] / survived_r4) if survived_r4 else None,
                "kept": d["kept"],
            }
        )
    return results


def run_validate(conn: sqlite3.Connection, tickers: list[str] | None = None) -> ValidationReport:
    return ValidationReport(
        range_violations=_check_range_violations(conn, tickers),
        range_exceptions=_check_range_exceptions(conn, tickers),
        dupont_violations=_check_dupont(conn, tickers),
        gross_profit_violations=_check_gross_profit_crosscheck(conn, tickers),
        debt_reconciliation_violations=_check_debt_reconciliation(conn, tickers),
        debt_reconciliation_exceptions=_check_debt_reconciliation_exceptions(conn, tickers),
        period_mixing_violations=_check_period_mixing(conn, tickers),
        alias_agreement_violations=_check_alias_agreement(conn, tickers),
        alias_agreement_exceptions=_check_alias_agreement_exceptions(conn, tickers),
        concept_drift=_check_concept_drift(conn, tickers),
        unverified_yoy_drift=_check_unverified_yoy_drift(conn, tickers),
        coverage=_check_coverage(conn, tickers),
        unresolved_concepts=_check_unresolved_concepts(conn, tickers),
        finance_lease_zero_assumptions=_check_finance_lease_zero_assumptions(conn, tickers),
        discrete_quarter_sum_violations=_check_discrete_quarter_sum(conn, tickers),
        discrete_quarter_sum_exceptions=_check_discrete_quarter_sum_exceptions(conn, tickers),
        observation_per_filing_contribution=_check_observation_per_filing_contribution(conn, tickers),
        observation_dead_rules=_check_observation_dead_rules(conn, tickers),
        observation_lookahead_violations=_check_observation_lookahead(conn, tickers),
        observation_determinism_violations=_check_observation_determinism(conn, tickers),
        observation_orphan_refs=_check_observation_orphan_refs(conn, tickers),
        db_size=_check_db_size(conn),
        llm_pricing_staleness=_check_llm_pricing_staleness(conn),
        llm_ledger_mismatches=_check_llm_ledger_reconciliation(conn),
        llm_cost_recomputation_mismatches=_check_llm_cost_recomputation(conn),
        llm_pricing_rate_review=_check_llm_pricing_rate_review(conn),
        llm_budget=_check_llm_budget_headroom(conn),
        llm_orphan_findings=_check_llm_orphan_findings(conn),
        llm_quote_integrity_violations=_check_llm_quote_integrity(conn),
        llm_discard_rate=_check_llm_discard_rate(conn),
        llm_numeric_support=_check_llm_numeric_support(conn),
        brief_orphan_refs=_check_brief_orphan_references(conn),
        brief_cross_filing_refs=_check_brief_cross_filing_references(conn),
        brief_invalid_types=_check_brief_sentence_type_validity(conn),
        brief_reverification_failures=_check_brief_reverification(conn),
        brief_empty=_check_brief_empty(conn),
        brief_drop_rates=_check_brief_drop_rates(conn),
    )


def format_report(report: ValidationReport) -> str:
    lines: list[str] = []

    def _section(title: str, items: list[dict], formatter) -> None:
        lines.append(f"\n=== {title} ({len(items)}) ===")
        if not items:
            lines.append("  (none)")
            return
        for item in items:
            lines.append(f"  {formatter(item)}")

    groups = _group_range_violations(report.range_violations)
    lines.append(f"\n=== 1. Range violations ({len(report.range_violations)} total, {len(groups)} group(s)) ===")
    if not groups:
        lines.append("  (none)")
    else:
        for (metric, direction), g in sorted(groups.items()):
            lines.append(
                f"  {metric} {direction} range {g['range']}: {len(g['values'])} occurrence(s), "
                f"values {min(g['values']):.4g} to {max(g['values']):.4g}, tickers: {sorted(g['tickers'])}"
            )
    _section(
        "1a. Range exceptions (informational -- registered, not hard-failed)",
        report.range_exceptions,
        lambda v: f"{v['ticker']} {v['metric']} {v['period_end']}: value={v['value']:.4g} outside {v['range']} -- {v['reason']}",
    )

    _section(
        "2. DuPont reconciliation",
        report.dupont_violations,
        lambda v: f"{v['ticker']} {v['period_end']}: net_margin*asset_turnover*equity_multiplier="
        f"{v['product']:.4g} vs roe={v['roe']:.4g} (rel diff {v['rel_diff']:.2%})",
    )
    _section(
        "3. Gross profit cross-check",
        report.gross_profit_violations,
        lambda v: f"{v['ticker']} {v['period_end']}: reported={v['reported_gross_profit']:.4g} "
        f"vs revenue-cogs={v['computed_revenue_minus_cogs']:.4g} (rel diff {v['rel_diff']:.2%})",
    )
    _section(
        "4. Debt reconciliation",
        report.debt_reconciliation_violations,
        lambda v: f"{v['ticker']} {v['period_end']}: {v['combined_concept']}={v['combined']:.4g} "
        f"vs debt_noncurrent+debt_current={v['component_sum']:.4g} (rel diff {v['rel_diff']:.2%})",
    )
    _section(
        "4a. Debt reconciliation exceptions (informational -- registered, not hard-failed)",
        report.debt_reconciliation_exceptions,
        lambda v: f"{v['ticker']} {v['period_end']}: {v['combined_concept']}={v['combined']:.4g} "
        f"vs debt_noncurrent+debt_current={v['component_sum']:.4g} (rel diff {v['rel_diff']:.2%}) -- {v['reason']}",
    )
    _section(
        "5. Period-mixing check",
        report.period_mixing_violations,
        lambda v: f"{v['ticker']} {v['metric']} {v['period_end']} concept={v['concept']} value={v['value']}: {v['reason']}",
    )
    _section(
        "6. Alias agreement",
        report.alias_agreement_violations,
        lambda v: f"{v['ticker']} {v['canonical']} {v['period_end']}: {v['alias_a']}={v['value_a']:.4g} "
        f"vs {v['alias_b']}={v['value_b']:.4g} (rel diff {v['rel_diff']:.2%})",
    )
    _section(
        "6a. Alias agreement exceptions (informational -- registered, not hard-failed)",
        report.alias_agreement_exceptions,
        lambda v: f"{v['ticker']} {v['canonical']} {v['period_end']}: {v['alias_a']}={v['value_a']:.4g} "
        f"vs {v['alias_b']}={v['value_b']:.4g} (rel diff {v['rel_diff']:.2%}) -- {v['reason']}",
    )
    _section(
        "7. Concept drift (informational)",
        report.concept_drift,
        lambda v: f"{v['ticker']} {v['canonical']}: {v['from_concept']} -> {v['to_concept']} at {v['period_end']}",
    )
    _section(
        "8. Unverified YoY drift (informational)",
        report.unverified_yoy_drift,
        lambda v: f"{v['ticker']} {v['metric']} {v['period_end']} ({v['canonical']}): "
        f"{v['from_concept']} -> {v['to_concept']}, never co-tagged",
    )
    _section(
        "9. Coverage (informational)",
        report.coverage,
        lambda v: f"{v['ticker']} {v['metric']}: {v['computed']}/{v['total']} ({v['pct']:.0%})",
    )
    _section(
        "10. Unresolved concepts (informational)",
        report.unresolved_concepts,
        lambda v: f"{v['canonical']}: no data for any alias {v['aliases_tried']}",
    )
    _section(
        "11. Finance lease zero-assumption (informational)",
        report.finance_lease_zero_assumptions,
        lambda v: f"{v['ticker']}: total_debt assumed $0 finance lease in {v['periods_affected']} period(s)",
    )
    _section(
        "12. Observation per-filing contribution (informational -- mean/max observations per filing)",
        report.observation_per_filing_contribution,
        lambda v: f"{v['rule_name']}: mean {v['mean_per_filing']:.1f}, max {v['max_per_filing']} "
        f"(across {v['filings_contributed_to']} filing(s) it contributed to)",
    )
    _section(
        "13. Dead observation rules (informational)",
        report.observation_dead_rules,
        lambda v: f"{v['rule_name']}: never fired ({v['eligible']} eligible period(s))",
    )
    _section(
        "14. Observation lookahead violations",
        report.observation_lookahead_violations,
        lambda v: f"{v['rule_name']} cik={v['cik']} {v['period_end']} subject={v['subject']}: "
        f"ref {v['ref']} has period_end {v['ref_period_end']}",
    )
    _section(
        "15. Observation determinism violations",
        report.observation_determinism_violations,
        lambda v: f"{v['ticker']} {v['rule_name']} {v['period_end']} subject={v['subject']}: "
        f"recomputed={v['recomputed']!r} vs persisted={v['persisted']!r}",
    )
    _section(
        "16. Observation orphan references",
        report.observation_orphan_refs,
        lambda v: f"{v['rule_name']} cik={v['cik']} {v['period_end']} subject={v['subject']}: "
        f"unresolved ref {v['ref']}",
    )
    lines.append("\n=== 17. Database size (informational -- growth measure, not a hard ceiling) ===")
    d = report.db_size
    if d.get("size_bytes") is None:
        lines.append("  (could not determine app.db file size)")
    else:
        mb = d["size_bytes"] / (1024 * 1024)
        ceiling_mb = d["soft_ceiling_bytes"] / (1024 * 1024)
        flag = "  ** OVER SOFT CEILING **" if d["over_soft_ceiling"] else ""
        lines.append(f"  app.db: {mb:.2f} MB (soft ceiling {ceiling_mb:.0f} MB){flag}")
        lines.append(
            f"  measured marginal cost of one filing: {d['measured_marginal_bytes_per_filing']:,} bytes "
            f"({d['measured_marginal_filing_description']})"
        )

    _section(
        "18. LLM pricing staleness (informational -- SPEC-006 R1a/R10)",
        report.llm_pricing_staleness,
        lambda v: f"{v['model']}: verified {v['verified_date']} ({v['age_days']} days ago, source {v['source_url']})",
    )
    _section(
        "19. LLM ledger reconciliation",
        report.llm_ledger_mismatches,
        lambda v: f"total_spent()={v['reported_total']:.4f} vs raw SUM(cost_usd)={v['raw_sum']:.4f}",
    )
    lines.append("\n=== 20. LLM budget headroom ===")
    b = report.llm_budget
    if b:
        flag = "  ** OVER BUDGET **" if b["over_budget"] else ""
        lines.append(f"  spent ${b['spent']:.4f} of ${b['budget']:.2f} budget, ${b['remaining']:.4f} remaining{flag}")
    _section(
        "21. LLM orphan findings",
        report.llm_orphan_findings,
        lambda v: f"finding_id={v['finding_id']}: {v['problem']}",
    )
    _section(
        "22. LLM quote integrity violations",
        report.llm_quote_integrity_violations,
        lambda v: f"finding_id={v['finding_id']}: quote no longer verifies against section text: {v['quote']!r}",
    )
    _section(
        "23. LLM discard rate (informational, by prompt version)",
        report.llm_discard_rate,
        lambda v: f"{v['prompt_version']}: {v['kept']}/{v['returned']} kept "
        f"({v['discard_rate']:.0%} discarded)",
    )
    _section(
        "24. LLM numeric support in headline/detail (informational, warning only, by prompt version)",
        report.llm_numeric_support,
        lambda v: f"{v['prompt_version']}: {v['supported']}/{v['checked']} numeric tokens found in source "
        + (f"({v['support_rate']:.0%} supported)" if v["support_rate"] is not None else "(no numbers)")
        + f" [in quote: {v['supported_in_quote']}, in note only: {v['supported_in_note_only']}, "
        + f"derived-verified: {v['derived_verified']}]"
        + f", {v['findings_with_unsupported']} finding(s) with >=1 unsupported number"
        + (f"; e.g. {v['examples'][0]['unsupported']} in finding_id={v['examples'][0]['finding_id']}" if v["examples"] else ""),
    )
    _section(
        "25. LLM cost recomputation (informational -- SPEC-006A L10; would have caught the 2026-07-27 mispricing)",
        report.llm_cost_recomputation_mismatches,
        lambda v: f"call_id={v['call_id']} {v['model']}: stored cost_usd={v['stored_cost_usd']:.6f} vs "
        f"compute_cost() at current pricing={v['expected_cost_usd_at_current_pricing']:.6f}",
    )
    _section(
        "26. LLM pricing rate review (SPEC-006A -- claude-sonnet-5's introductory rate)",
        report.llm_pricing_rate_review,
        lambda v: f"{v['model']}: introductory-rate review date {v['review_date']} has passed (today {v['today']}) "
        f"but LLM_PRICING still shows input_per_mtok={v['current_input_per_mtok']} -- bump to the standard "
        "$3.00/$15.00 rate",
    )
    _section(
        "27. Brief orphan references (every ref must resolve to a live observation/finding)",
        report.brief_orphan_refs,
        lambda v: f"sentence_id={v['sentence_id']} brief_id={v['brief_id']} ref={v['ref']!r}: {v['problem']}",
    )
    _section(
        "28. Brief cross-filing references (no sentence may cite another filing's source)",
        report.brief_cross_filing_refs,
        lambda v: f"sentence_id={v['sentence_id']} brief_id={v['brief_id']} ref={v['ref']!r}: "
        f"brief is for {v['brief_accession_no']}, source belongs to {v['source_accession_no']}",
    )
    _section(
        "29. Brief sentence type validity (every stored type must be one of the five permitted)",
        report.brief_invalid_types,
        lambda v: f"sentence_id={v['sentence_id']} brief_id={v['brief_id']}: unrecognised type {v['sentence_type']!r}",
    )
    _section(
        "30. Brief re-verification (R4's checks re-run against stored sentences)",
        report.brief_reverification_failures,
        lambda v: f"sentence_id={v['sentence_id']} brief_id={v['brief_id']}: {v['reason']}",
    )
    _section(
        "31. Empty briefs (informational -- a quiet quarter is legitimate)",
        report.brief_empty,
        lambda v: f"accession_no={v['accession_no']} brief_id={v['brief_id']} ({v['prompt_version']}/{v['verifier_version']}): {v['reason']}",
    )
    _section(
        "32. Brief drop rates (informational, generator-side and verifier-side, by prompt/verifier version)",
        report.brief_drop_rates,
        lambda v: f"{v['prompt_version']}/{v['verifier_version']} ({v['briefs']} brief(s)): "
        f"generator dropped {v['generator_dropped']}/{v['raw_sentences']} "
        + (f"({v['generator_drop_rate']:.0%})" if v["generator_drop_rate"] is not None else "(no sentences)")
        + f"; verifier dropped {v['verifier_dropped']}/{v['survived_r4']} "
        + (f"({v['verifier_drop_rate']:.0%})" if v["verifier_drop_rate"] is not None else "(none survived R4)")
        + f"; kept {v['kept']}",
    )
    _section(
        "33. Discrete quarter sum-back (SPEC-008 C4)",
        report.discrete_quarter_sum_violations,
        lambda v: f"{v['ticker']} {v['canonical']} FY ending {v['fy_period_end']}: "
        f"Q1+Q2+Q3+Q4={v['quarter_sum']:,.0f} vs filed FY={v['filed_fy']:,.0f} (diff {v['diff']:,.0f})",
    )
    _section(
        "33a. Discrete quarter sum-back exceptions (informational -- registered, not hard-failed)",
        report.discrete_quarter_sum_exceptions,
        lambda v: f"{v['ticker']} {v['canonical']} FY ending {v['fy_period_end']}: "
        f"Q1+Q2+Q3+Q4={v['quarter_sum']:,.0f} vs filed FY={v['filed_fy']:,.0f} (diff {v['diff']:,.0f}) -- {v['reason']}",
    )

    lines.append(
        f"\n{report.hard_failure_count} finding(s) in hard-failing categories "
        "(1, 2, 3, 4, 5, 6, 14, 15, 16, 19, 20, 21, 22, 27, 28, 29, 30, 33) -- would exit non-zero."
        if report.hard_failure_count
        else "\nAll hard-failing categories clean."
    )
    return "\n".join(lines)
