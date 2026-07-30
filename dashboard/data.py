"""SPEC-008: ALL database access. Read-only, always -- the dashboard's first
hard rule. Every public function is cached (`st.cache_data`) and takes an
explicit `db_path`, defaulting to `config.DB_PATH`, so tests can point at a
throwaway database without touching global state.

Cache invalidation: every cached query is keyed in part on the database
file's own modification time (`_mtime`), so an update to `app.db` (a new
pipeline run, a fresh `git pull`) invalidates every cached result the next
time a page reads -- never a stale figure surviving a real data change.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import streamlit as st

from edgar import config

DEFAULT_DB_PATH: Path = config.DB_PATH


# --- connection + cache-invalidation plumbing ---


def _mtime(db_path: Path) -> float:
    if not db_path.exists():
        raise FileNotFoundError(
            f"No database at {db_path} -- run `python -m edgar.pipeline init-db` first."
        )
    return db_path.stat().st_mtime


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


@st.cache_data
def _cached_query(db_path_str: str, mtime_value: float, sql: str, params: tuple) -> list[dict]:
    # NOTE: `mtime_value` deliberately does NOT start with an underscore.
    # st.cache_data excludes underscore-prefixed parameters from the cache
    # key entirely (its convention for passing unhashable objects, e.g. a
    # live connection) -- naming this `_mtime_value` would silently make the
    # cache never invalidate on a real mtime change, defeating the entire
    # point of passing it (found live, while writing this module's own
    # tests: `test_cache_invalidates_when_db_mtime_changes` failed with the
    # underscore-prefixed name and passed once renamed).
    conn = _connect(Path(db_path_str))
    try:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def _run(db_path: Path, sql: str, params: tuple = ()) -> list[dict]:
    """Every query in this module goes through here -- the single point
    where the mtime-based cache key is attached (constraint: 'the cache must
    be invalidated when the database file's modification time changes')."""
    return _cached_query(str(db_path), _mtime(db_path), sql, params)


def _one(db_path: Path, sql: str, params: tuple = ()) -> dict | None:
    rows = _run(db_path, sql, params)
    return rows[0] if rows else None


def _current_version_observations(rows: list[dict]) -> list[dict]:
    """Filter to each rule's CURRENT rule_version only -- `observations`
    deliberately retains superseded-version rows for historical comparison
    (SPEC-005), and those can carry a different severity than the current
    version assigns (found live, SPEC-007 pre-implementation review).
    Duplicated from brief.py's identical helper rather than imported -- the
    dashboard has zero dependency on any module whose purpose is spending
    money, by design (SPEC-008's second hard rule), not just by accident."""
    return [r for r in rows if r["rule_version"] == config.RULE_REGISTRY[r["rule_name"]].version]


# --- companies, filings ---


def get_companies(db_path: Path = DEFAULT_DB_PATH) -> list[dict]:
    return _run(db_path, "SELECT cik, ticker, name, fiscal_year_end FROM companies ORDER BY ticker")


def get_anchor_filing(cik: str, db_path: Path = DEFAULT_DB_PATH) -> dict | None:
    """The Overview page's header filing: the latest 10-K or 10-Q, NEVER an
    8-K (SPEC-008 v1.1) -- an 8-K has no sections, metrics, findings, or
    brief, and a page built around those cannot be "about" one."""
    return _one(
        db_path,
        """
        SELECT accession_no, form_type, filing_date, period_end, fiscal_year, fiscal_period, cik
        FROM filings
        WHERE cik = ? AND form_type IN (?, ?)
        ORDER BY filing_date DESC, accession_no DESC LIMIT 1
        """,
        (cik, config.TENK_FORM_TYPE, config.TENQ_FORM_TYPE),
    )


def get_more_recent_8k(cik: str, anchor_filing_date: str, db_path: Path = DEFAULT_DB_PATH) -> dict | None:
    """A same-day-or-later 8-K relative to the anchor 10-K/10-Q -- noted on
    the Overview page with a link to the Filings page, never used as the
    anchor and never silently dropped (SPEC-008 v1.1). Confirmed live:
    NVIDIA's most recent 10-Q and an 8-K share a filing date."""
    return _one(
        db_path,
        """
        SELECT accession_no, form_type, filing_date
        FROM filings
        WHERE cik = ? AND form_type = ? AND filing_date >= ?
        ORDER BY filing_date DESC LIMIT 1
        """,
        (cik, config.EIGHTK_FORM_TYPE, anchor_filing_date),
    )


def get_all_filings(db_path: Path = DEFAULT_DB_PATH) -> list[dict]:
    """Every filing in the database (SPEC-008 R6: "all 171, not just the
    analysed window")."""
    return _run(
        db_path,
        """
        SELECT f.accession_no, f.form_type, f.filing_date, f.period_end, f.fiscal_year, f.fiscal_period,
               c.ticker, c.name AS company_name, c.cik
        FROM filings f JOIN companies c ON c.cik = f.cik
        ORDER BY f.filing_date DESC
        """,
    )


def get_filing(accession_no: str, db_path: Path = DEFAULT_DB_PATH) -> dict | None:
    return _one(
        db_path,
        """
        SELECT f.accession_no, f.form_type, f.filing_date, f.period_end, f.fiscal_year, f.fiscal_period,
               c.ticker, c.name AS company_name, c.cik
        FROM filings f JOIN companies c ON c.cik = f.cik
        WHERE f.accession_no = ?
        """,
        (accession_no,),
    )


# --- brief (Overview: "the brief") ---


def get_brief_sentences(accession_no: str, db_path: Path = DEFAULT_DB_PATH) -> list[dict]:
    """Every kept sentence of a filing's most recent brief, each carrying
    its own resolved sources (for adjacent-display, never behind a click --
    binding, SPEC-007 Residual Risk) and `max_source_severity` (0=high,
    1=medium, 2=low) for the top-6 ranking (SPEC-008 R3)."""
    brief_row = _one(
        db_path, "SELECT id FROM briefs WHERE accession_no = ? ORDER BY id DESC LIMIT 1", (accession_no,)
    )
    if brief_row is None:
        return []
    sentence_rows = _run(
        db_path,
        "SELECT id, position, sentence_type, text, refs_json FROM brief_sentences WHERE brief_id = ? ORDER BY position",
        (brief_row["id"],),
    )
    severity_rank = {"high": 0, "medium": 1, "low": 2}
    out = []
    for row in sentence_rows:
        refs = json.loads(row["refs_json"])
        sources = [_resolve_ref(ref, db_path) for ref in refs]
        sources = [s for s in sources if s is not None]
        worst = min((severity_rank.get(s["severity"], 2) for s in sources), default=2)
        out.append({**row, "refs": refs, "sources": sources, "max_source_severity": worst})
    return out


def _resolve_ref(ref: str, db_path: Path) -> dict | None:
    if ":" not in ref:
        return None
    kind, _, id_str = ref.partition(":")
    if kind not in ("obs", "finding") or not id_str.isdigit():
        return None
    if kind == "obs":
        row = _one(db_path, "SELECT * FROM observations WHERE id = ?", (int(id_str),))
        if row is None:
            return None
        return {"kind": "observation", "severity": row["severity"], "text": row["statement"], "row": row}
    row = _one(db_path, "SELECT * FROM findings WHERE id = ?", (int(id_str),))
    if row is None:
        return None
    return {"kind": "finding", "severity": row["severity"], "text": row["headline"], "row": row}


# --- observations ("What changed?") ---


def get_top_observations(
    accession_no: str, db_path: Path = DEFAULT_DB_PATH, max_total: int = 8, max_per_rule: int = 2
) -> list[dict]:
    """SPEC-008 R3, Q1: top observations for the latest filing, severity-
    ranked, max 2 per rule -- current rule_version only (v1.1: a stale
    superseded-version row can carry a different, sometimes higher,
    severity than the current version assigns)."""
    rows = _run(
        db_path,
        "SELECT * FROM observations WHERE accession_no = ? ORDER BY "
        "CASE severity WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END, id",
        (accession_no,),
    )
    rows = _current_version_observations(rows)
    selected: list[dict] = []
    per_rule: dict[str, int] = {}
    for row in rows:
        if per_rule.get(row["rule_name"], 0) >= max_per_rule:
            continue
        selected.append(row)
        per_rule[row["rule_name"]] = per_rule.get(row["rule_name"], 0) + 1
        if len(selected) >= max_total:
            break
    return selected


# --- metrics ---


def _classify_duration(period_start: str | None, period_end: str) -> str:
    """Duplicated from metrics.py's identical helper (same reasoning as
    `_current_version_observations` above) -- `metrics` has no stored
    "annual"/"quarterly" column; the class is a pure function of
    (period_start, period_end), same as metrics.py computes it."""
    if period_start is None:
        return config.PERIOD_CLASS_INSTANT
    from datetime import date

    days = (date.fromisoformat(period_end) - date.fromisoformat(period_start)).days
    for dc in config.PERIOD_CLASSES:
        if dc.min_days <= days <= dc.max_days:
            return dc.name
    return config.PERIOD_CLASS_OTHER


_CLASSES_FOR_BASIS = {"annual": ("annual",), "quarterly": ("quarterly",), "both": ("annual", "quarterly")}


def get_metric_series(cik: str, name: str, basis: str, db_path: Path = DEFAULT_DB_PATH) -> list[dict]:
    """Full history for one metric/company/basis -- for sparklines and the
    Metrics page's charts. Each row carries `null_reason` (parsed out of
    `inputs_json`'s `_null_reason` key, metrics.py's own convention) so a
    NULL point never has to be guessed at by the caller. Filtered to the
    duration classes `basis` actually calls for (metrics.py's own
    `_classes_for_basis`) -- a "both"-basis metric's `annual` rows and
    `quarterly` rows share one `name`; without this filter a chart would mix
    them on one axis."""
    allowed_classes = _CLASSES_FOR_BASIS[basis]
    rows = _run(
        db_path,
        "SELECT period_start, period_end, value, formula, inputs_json FROM metrics "
        "WHERE cik = ? AND name = ? AND calc_version = ? ORDER BY period_end",
        (cik, name, config.CALC_VERSION),
    )
    out = []
    for row in rows:
        if _classify_duration(row["period_start"], row["period_end"]) not in allowed_classes:
            continue
        inputs = json.loads(row["inputs_json"])
        null_reason = inputs.pop("_null_reason", None) if row["value"] is None else None
        out.append(
            {
                "period_start": row["period_start"], "period_end": row["period_end"], "value": row["value"],
                "formula": row["formula"], "inputs_used": inputs, "null_reason": null_reason,
            }
        )
    return out


def get_latest_metric(cik: str, name: str, basis: str, db_path: Path = DEFAULT_DB_PATH) -> dict | None:
    """The single latest value for one metric/company -- for Overview
    tiles. Carries its own `period_end` explicitly (v1.1: every displayed
    value states its own period, since an annual-basis metric's latest
    value can genuinely lag a quarterly anchor filing)."""
    series = get_metric_series(cik, name, basis, db_path)
    return series[-1] if series else None


def get_metric_evidence(
    cik: str, name: str, period_start: str | None, period_end: str, db_path: Path = DEFAULT_DB_PATH
) -> dict | None:
    """R5: formula, input values, and the source filing for one chart
    point's evidence panel. Keyed on (period_start, period_end), not
    period_end alone -- a real, documented case in this project (decision
    log #19) has an annual period share an end date with an implicit
    quarter; period_end alone would be ambiguous for it."""
    row = _one(
        db_path,
        "SELECT accession_no, formula, inputs_json FROM metrics "
        "WHERE cik = ? AND name = ? AND period_start IS ? AND period_end = ? AND calc_version = ?",
        (cik, name, period_start, period_end, config.CALC_VERSION),
    )
    if row is None:
        return None
    inputs = json.loads(row["inputs_json"])
    inputs.pop("_null_reason", None)
    return {"formula": row["formula"], "inputs_used": inputs, "accession_no": row["accession_no"]}


# --- findings ("Should I be suspicious?", Filings page) ---


def get_red_flag_findings(accession_no: str, db_path: Path = DEFAULT_DB_PATH) -> list[dict]:
    return _run(
        db_path,
        "SELECT * FROM findings WHERE accession_no = ? AND category = 'red_flag' ORDER BY id",
        (accession_no,),
    )


def get_findings_for_filing(accession_no: str, db_path: Path = DEFAULT_DB_PATH) -> list[dict]:
    return _run(db_path, "SELECT * FROM findings WHERE accession_no = ? ORDER BY id", (accession_no,))


def get_observations_for_filing(accession_no: str, db_path: Path = DEFAULT_DB_PATH) -> list[dict]:
    rows = _run(
        db_path,
        "SELECT * FROM observations WHERE accession_no = ? ORDER BY "
        "CASE severity WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END, id",
        (accession_no,),
    )
    return _current_version_observations(rows)


def get_sections_for_filing(accession_no: str, db_path: Path = DEFAULT_DB_PATH) -> list[dict]:
    return _run(
        db_path,
        "SELECT id, category, short_name, position, text_hash FROM sections WHERE accession_no = ? ORDER BY category, position",
        (accession_no,),
    )


def get_filing_detail(accession_no: str, db_path: Path = DEFAULT_DB_PATH) -> dict:
    """SPEC-008 R6: everything the Filings page shows for one selected
    filing -- metadata, its brief, observations, findings (with quotes),
    section list. `brief` is None (not an empty list) when no brief exists
    at all for this filing, distinct from a brief that exists but is empty."""
    filing = get_filing(accession_no, db_path)
    brief_row = _one(db_path, "SELECT id, prompt_version, verifier_version FROM briefs WHERE accession_no = ?", (accession_no,))
    sentences = get_brief_sentences(accession_no, db_path) if brief_row is not None else None
    return {
        "filing": filing,
        "brief": {"meta": brief_row, "sentences": sentences} if brief_row is not None else None,
        "observations": get_observations_for_filing(accession_no, db_path),
        "findings": get_findings_for_filing(accession_no, db_path),
        "sections": get_sections_for_filing(accession_no, db_path),
    }


# --- financials (curated statement lines) ---

# R4: "Curated lines, not full GAAP presentation." Every entry is an
# EXISTING canonical input already in config.CONCEPT_REGISTRY -- no new
# concepts, no new database columns (Constraints).
INCOME_STATEMENT_LINES: tuple[tuple[str, str], ...] = (
    ("revenue", "Revenue"),
    ("cogs", "Cost of goods sold"),
    ("gross_profit", "Gross profit"),
    ("rnd_expense", "Research and development"),
    ("sga_expense", "Selling, general and administrative"),
    ("operating_income", "Operating income"),
    ("interest_expense", "Interest expense"),
    ("pretax_income", "Pre-tax income"),
    ("tax_expense", "Income tax expense"),
    ("net_income", "Net income"),
)
BALANCE_SHEET_LINES: tuple[tuple[str, str], ...] = (
    ("cash", "Cash and cash equivalents"),
    ("short_term_investments", "Short-term investments"),
    ("receivables", "Accounts receivable"),
    ("inventory", "Inventory"),
    ("current_assets", "Total current assets"),
    ("ppe_net", "Property, plant and equipment, net"),
    ("total_assets", "Total assets"),
    ("payables", "Accounts payable"),
    ("current_liabilities", "Total current liabilities"),
    ("debt_noncurrent", "Long-term debt"),
    ("equity", "Total stockholders' equity"),
)
CASH_FLOW_LINES: tuple[tuple[str, str], ...] = (
    ("cfo", "Cash from operations"),
    ("capex", "Capital expenditure"),
    ("free_cash_flow", "Free cash flow"),
    ("sbc", "Stock-based compensation"),
    ("dep_amort", "Depreciation and amortization"),
)


def get_statement_period_ends(cik: str, db_path: Path = DEFAULT_DB_PATH) -> list[dict]:
    """Every distinct (period_start, period_end) with metrics computed for
    this company -- the period selector's options for the Financials page."""
    return _run(
        db_path,
        "SELECT DISTINCT period_start, period_end FROM metrics WHERE cik = ? AND calc_version = ? "
        "ORDER BY period_end DESC",
        (cik, config.CALC_VERSION),
    )


def get_statement_line_values(
    cik: str, period_end: str, lines: tuple[tuple[str, str], ...], db_path: Path = DEFAULT_DB_PATH
) -> list[dict]:
    """One curated statement's lines for one period. `free_cash_flow` and
    other computed metrics resolve from `metrics`; raw canonical inputs
    (revenue, cogs, ...) resolve from `xbrl_facts` directly via
    CONCEPT_REGISTRY's alias list -- both are "already held" data (R4), just
    in different tables."""
    out = []
    for canonical, label in lines:
        value = None
        if canonical in config.METRIC_REGISTRY:
            row = _one(
                db_path,
                "SELECT value FROM metrics WHERE cik = ? AND name = ? AND period_end = ? AND calc_version = ?",
                (cik, canonical, period_end, config.CALC_VERSION),
            )
            value = row["value"] if row else None
        elif canonical in config.CONCEPT_REGISTRY:
            aliases = config.CONCEPT_REGISTRY[canonical].aliases
            placeholders = ",".join("?" for _ in aliases)
            row = _one(
                db_path,
                f"SELECT value FROM xbrl_facts WHERE cik = ? AND concept IN ({placeholders}) AND period_end = ? "
                "ORDER BY filed_date DESC LIMIT 1",
                (cik, *aliases, period_end),
            )
            value = row["value"] if row else None
        out.append({"canonical": canonical, "label": label, "value": value})
    return out


def get_as_filed_sections(cik: str, db_path: Path = DEFAULT_DB_PATH) -> list[dict]:
    """R4 'As filed': the Statements-category section text, exactly as SEC
    rendered it, across every filing for this company."""
    return _run(
        db_path,
        """
        SELECT s.id, s.accession_no, s.short_name, s.text_hash, f.filing_date, f.form_type
        FROM sections s JOIN filings f ON f.accession_no = s.accession_no
        WHERE f.cik = ? AND s.category = ?
        ORDER BY f.filing_date DESC, s.position
        """,
        (cik, config.MENUCATEGORY_STATEMENTS),
    )
