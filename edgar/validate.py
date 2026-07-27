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

from edgar import config
from edgar import metrics as metrics_mod


@dataclass
class ValidationReport:
    range_violations: list[dict] = field(default_factory=list)
    dupont_violations: list[dict] = field(default_factory=list)
    gross_profit_violations: list[dict] = field(default_factory=list)
    debt_reconciliation_violations: list[dict] = field(default_factory=list)
    period_mixing_violations: list[dict] = field(default_factory=list)
    alias_agreement_violations: list[dict] = field(default_factory=list)
    alias_agreement_exceptions: list[dict] = field(default_factory=list)
    concept_drift: list[dict] = field(default_factory=list)
    unverified_yoy_drift: list[dict] = field(default_factory=list)
    coverage: list[dict] = field(default_factory=list)
    unresolved_concepts: list[dict] = field(default_factory=list)
    finance_lease_zero_assumptions: list[dict] = field(default_factory=list)

    @property
    def hard_failure_count(self) -> int:
        return (
            len(self.range_violations)
            + len(self.dupont_violations)
            + len(self.gross_profit_violations)
            + len(self.debt_reconciliation_violations)
            + len(self.period_mixing_violations)
            + len(self.alias_agreement_violations)
        )


def _companies(tickers: list[str] | None) -> list[tuple[str, str]]:
    return [(c.cik, c.ticker) for c in config.WATCHLIST if tickers is None or c.ticker in tickers]


def _ciks_for_tickers(tickers: list[str] | None) -> list[str]:
    return [c.cik for c in config.WATCHLIST if tickers is None or c.ticker in tickers]


# --- category 1: range violations ---


def _check_range_violations(conn: sqlite3.Connection, tickers: list[str] | None) -> list[dict]:
    ciks = _ciks_for_tickers(tickers)
    if not ciks:
        return []
    placeholders = ",".join("?" for _ in ciks)
    rows = conn.execute(
        f"SELECT m.cik, c.ticker, m.period_end, m.name, m.value FROM metrics m "
        f"JOIN companies c ON c.cik = m.cik "
        f"WHERE m.cik IN ({placeholders}) AND m.value IS NOT NULL AND m.calc_version = ?",
        (*ciks, config.CALC_VERSION),
    ).fetchall()
    violations = []
    for row in rows:
        mdef = config.METRIC_REGISTRY.get(row["name"])
        if mdef is None or mdef.plausible_range is None:
            continue
        lo, hi = mdef.plausible_range
        if not (lo <= row["value"] <= hi):
            violations.append(
                {
                    "ticker": row["ticker"],
                    "period_end": row["period_end"],
                    "metric": row["name"],
                    "value": row["value"],
                    "range": (lo, hi),
                }
            )
    return violations


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


def _check_debt_reconciliation(conn: sqlite3.Connection, tickers: list[str] | None) -> list[dict]:
    violations = []
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
                violations.append(
                    {
                        "ticker": ticker,
                        "period_end": end,
                        "combined_concept": combined_alias,
                        "combined": combined,
                        "component_sum": component_sum,
                        "rel_diff": rel_diff,
                    }
                )
    return violations


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


def run_validate(conn: sqlite3.Connection, tickers: list[str] | None = None) -> ValidationReport:
    return ValidationReport(
        range_violations=_check_range_violations(conn, tickers),
        dupont_violations=_check_dupont(conn, tickers),
        gross_profit_violations=_check_gross_profit_crosscheck(conn, tickers),
        debt_reconciliation_violations=_check_debt_reconciliation(conn, tickers),
        period_mixing_violations=_check_period_mixing(conn, tickers),
        alias_agreement_violations=_check_alias_agreement(conn, tickers),
        alias_agreement_exceptions=_check_alias_agreement_exceptions(conn, tickers),
        concept_drift=_check_concept_drift(conn, tickers),
        unverified_yoy_drift=_check_unverified_yoy_drift(conn, tickers),
        coverage=_check_coverage(conn, tickers),
        unresolved_concepts=_check_unresolved_concepts(conn, tickers),
        finance_lease_zero_assumptions=_check_finance_lease_zero_assumptions(conn, tickers),
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

    lines.append(
        f"\n{report.hard_failure_count} finding(s) in categories 1-6 (would exit non-zero)."
        if report.hard_failure_count
        else "\nCategories 1-6 clean."
    )
    return "\n".join(lines)
