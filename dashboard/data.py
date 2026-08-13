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
import statistics
from datetime import date, timedelta
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
    brief, and a page built around those cannot be "about" one.

    SPEC-008 review D2 (found live): this query used to select only
    `filings` columns, with the caller (`overview.py`) patching in `ticker`
    by hand afterward -- `company_name` was never patched in anywhere, so
    every Overview header rendered "(MU)"/"(AMZN)" with an empty name before
    the ticker. Joined to `companies` here, like `get_filing`/
    `get_all_filings` already do, so the caller doesn't need to patch
    anything in."""
    return _one(
        db_path,
        """
        SELECT f.accession_no, f.form_type, f.filing_date, f.period_end, f.fiscal_year, f.fiscal_period, f.cik,
               c.ticker, c.name AS company_name
        FROM filings f JOIN companies c ON c.cik = f.cik
        WHERE f.cik = ? AND f.form_type IN (?, ?)
        ORDER BY f.filing_date DESC, f.accession_no DESC LIMIT 1
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
    its own resolved sources (SPEC-007 Residual Risk's binding mitigation,
    amended by SPEC-008 C2 -- 2026-07-31: sources now collapse behind a
    click, but this resolution is still what makes the always-visible
    source count possible, since the count is computed from these same
    resolved sources) and `max_source_severity` (0=high, 1=medium, 2=low)
    for the top-6 ranking (SPEC-008 R3)."""
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
#
# Each entry is (canonical, label, fallback_canonical, fallback_label).
# `fallback_canonical`/`fallback_label` are None for the common case (no
# fallback). Where set (SPEC-008 review, PP&E follow-up, found live): the
# PRIMARY canonical is a narrower fact than the fallback (e.g. `ppe_net`
# excludes finance-lease ROU assets; `ppe_and_lease_net` includes them) --
# never the same fact under a different name, so the fallback is never
# added as an alias of the primary in CONCEPT_REGISTRY (that would be the
# alias-purity violation ARCHITECTURE.md §2.1 warns against, the same one
# already refused for Micron's combined debt tag). When the fallback
# resolves, the row's `label` becomes `fallback_label`, not the primary
# label with a parenthetical note -- a reader scanning the row must see a
# label that accurately names the number next to it, not one that has to
# be read alongside a note to avoid being misled.
# SPEC-008 D12 (approved 2026-08-08): every duration-based line declares a
# `_discrete` fallback_canonical -- Q4 has no discrete filed fact anywhere
# in this project's corpus, for any company, in any year (only the 10-K's
# ANNUAL cumulative figure exists; there is no "Q4 10-Q"). `fallback_label
# == label` on every one, exactly the cash-flow pattern (SPEC-008 C4):
# merges into ONE row, filed where tagged directly (Q1/Q2/Q3, usually),
# derived by subtraction where not (Q4, always).
INCOME_STATEMENT_LINES: tuple[tuple[str, str, str | None, str | None], ...] = (
    ("revenue", "Revenue", "revenue_discrete", "Revenue"),
    ("cogs", "Cost of goods sold", "cogs_discrete", "Cost of goods sold"),
    ("gross_profit", "Gross profit", "gross_profit_discrete", "Gross profit"),
    ("rnd_expense", "Research and development", "rnd_expense_discrete", "Research and development"),
    (
        "sga_expense", "Selling, general and administrative",
        "sga_expense_discrete", "Selling, general and administrative",
    ),
    ("operating_income", "Operating income", "operating_income_discrete", "Operating income"),
    ("interest_expense", "Interest expense", "interest_expense_discrete", "Interest expense"),
    ("pretax_income", "Pre-tax income", "pretax_income_discrete", "Pre-tax income"),
    ("tax_expense", "Income tax expense", "tax_expense_discrete", "Income tax expense"),
    ("net_income", "Net income", "net_income_discrete", "Net income"),
)

# SPEC-008-batch-1 item 6 (approved 2026-08-09), renamed and widened by
# SPEC-008-batch-2 item 2 (approved 2026-08-13): the figures an analyst
# wants that aren't line items on any of the three statements.
#
# Per-share lines deliberately NOT added to INCOME_STATEMENT_LINES and
# deliberately given NO fallback_canonical (unlike every other line in this
# file) -- EarningsPerShareDiluted/Basic and the two WeightedAverageNumberOf...
# concepts are WEIGHTED AVERAGES over their period, not summable flows.
# The Q4 = FY - 9M discrete-quarter mechanism this project uses everywhere
# else is exact arithmetic for a SUM (revenue, cfo, ...); for an AVERAGE it
# is simply wrong -- confirmed against real data before this was written:
# AMZN's 6-month-YTD diluted share count (10,889M) sits BETWEEN its two
# discrete quarters' own counts (10,874M, 10,903M), the way an average of
# two numbers always does, not the way a running SUM does. Q4 is therefore
# a genuine, unfillable gap here where a company doesn't tag it directly.
#
# Cash flow lines: free_cash_flow and fcfe are exact arithmetic on filed
# lines (same status as batch 1 item 4's gross profit); fcff is NOT --
# it rests on a constructed effective tax rate (edgar/metrics.py's
# _compute_fcff_tax_rate), fails closed rather than substituting a
# statutory rate, and its cells are marked distinctly (`rate_assumption`,
# set in _mark_fcff_as_a_rate_assumption below) even where they aren't
# `is_derived_quarter` -- an annual, directly-filed-duration FCFF figure
# still rests on that year's own constructed rate, a different fact from
# "this project subtracted two filed cumulatives". fcff_tax_rate is shown
# as its own row (its label states which basis produced it -- see
# _label_fcff_tax_rate_row_by_basis) so the number driving FCFF is
# inspectable, not buried.
#
# Render-layer note (out of scope for this batch): EPS is dollars-per-share
# at 2 decimal places and share counts are in millions -- neither should be
# formatted through `fmt.format_usd`'s $-millions convention (already wired
# in dashboard/components.py's `_CELL_FORMATTERS`, the render-batch follow-
# up to batch 1). The rate row is a percent, also not yet wired to a
# percent formatter -- this function returns raw, correctly-resolved
# values; the render batch owns display.
KEY_METRICS_LINES: tuple[tuple[str, str, str | None, str | None], ...] = (
    ("eps_basic", "Basic EPS", None, None),
    ("eps_diluted", "Diluted EPS", None, None),
    ("basic_shares", "Basic shares outstanding", None, None),
    ("diluted_shares", "Diluted shares outstanding", None, None),
    ("free_cash_flow", "Free cash flow", "free_cash_flow_discrete", "Free cash flow"),
    ("fcff", "Free cash flow to the firm", "fcff_discrete", "Free cash flow to the firm"),
    ("fcfe", "Free cash flow to equity", "fcfe_discrete", "Free cash flow to equity"),
    ("fcff_tax_rate", "Effective tax rate (FCFF)", "fcff_tax_rate_discrete", "Effective tax rate (FCFF)"),
)


BALANCE_SHEET_LINES: tuple[tuple[str, str, str | None, str | None], ...] = (
    ("cash", "Cash and cash equivalents", None, None),
    ("short_term_investments", "Short-term investments", None, None),
    ("receivables", "Accounts receivable", None, None),
    ("inventory", "Inventory", None, None),
    ("current_assets", "Total current assets", None, None),
    (
        "ppe_net", "Property, plant and equipment, net",
        "ppe_and_lease_net", "Property, plant and equipment and finance-lease ROU assets, net",
    ),
    ("total_assets", "Total assets", None, None),
    ("goodwill", "Goodwill", None, None),
    ("intangibles", "Intangible assets, net", None, None),
    ("payables", "Accounts payable", None, None),
    ("current_liabilities", "Total current liabilities", None, None),
    ("debt_current", "Short-term debt and current portion of long-term debt", None, None),
    ("debt_noncurrent", "Long-term debt", None, None),
    ("operating_lease_liabilities", "Operating lease liabilities", None, None),
    ("total_liabilities", "Total liabilities", None, None),
    ("equity", "Total stockholders' equity", None, None),
    ("retained_earnings", "Retained earnings (accumulated deficit)", None, None),
)
CASH_FLOW_LINES: tuple[tuple[str, str, str | None, str | None], ...] = (
    # SPEC-008-batch-2 item 1 (approved 2026-08-13): TRADITIONAL statement
    # order -- a real filed cash flow statement builds UP to each section's
    # subtotal, it doesn't open with the result. `cfo` used to sit first
    # (SPEC-008 C4); it moves to the end of the operating section here,
    # relabelled to match what it actually is ("Net cash provided by
    # operating activities"), same canonical and same filed/derived
    # resolution, only its position and label change.
    #
    # `fallback_label == label` on every one of these still means MERGE
    # (SPEC-008 C4 item 3): the SAME accounting concept, filed directly or
    # derived by subtraction, never two different things shown side by
    # side. `net_income` reuses the income statement's OWN canonical and
    # `_discrete` fallback -- no new registry work, same discipline as
    # every line below it.
    ("net_income", "Net income", "net_income_discrete", "Net income"),
    ("dep_amort", "Depreciation and amortization", "dep_amort_discrete", "Depreciation and amortization"),
    ("sbc", "Stock-based compensation", "sbc_discrete", "Stock-based compensation"),
    ("deferred_tax", "Deferred income tax", "deferred_tax_discrete", "Deferred income tax"),
    ("other_noncash", "Other non-cash adjustments", "other_noncash_discrete", "Other non-cash adjustments"),
    ("receivables_change", "Change in receivables", "receivables_change_discrete", "Change in receivables"),
    ("inventory_change", "Change in inventory", "inventory_change_discrete", "Change in inventory"),
    ("payables_change", "Change in payables", "payables_change_discrete", "Change in payables"),
    (
        "cfo", "Net cash provided by operating activities",
        "cfo_discrete", "Net cash provided by operating activities",
    ),
    ("capex", "Capital expenditure", "capex_discrete", "Capital expenditure"),
    ("acquisitions", "Acquisitions, net of cash acquired", "acquisitions_discrete", "Acquisitions, net of cash acquired"),
    (
        "investment_purchases", "Purchases of investments",
        "investment_purchases_discrete", "Purchases of investments",
    ),
    (
        "investment_maturities", "Maturities and sales of investments",
        "investment_maturities_discrete", "Maturities and sales of investments",
    ),
    # Checked against the real corpus and REJECTED, per the spec's own
    # explicit warning: summing this section's own four lines above
    # reconstructed Micron's filed investing total 0 times out of 7 (see
    # the render-batch follow-up commit) -- this subtotal resolves ONLY
    # filed-or-discrete-subtraction, same as every line above it, never a
    # sum of this project's own tracked components.
    (
        "net_cash_investing", "Net cash used in investing activities",
        "net_cash_investing_discrete", "Net cash used in investing activities",
    ),
    ("buybacks", "Share repurchases", "buybacks_discrete", "Share repurchases"),
    ("dividends_paid", "Dividends paid", "dividends_paid_discrete", "Dividends paid"),
    ("debt_issued", "Debt issued", "debt_issued_discrete", "Debt issued"),
    ("debt_repaid", "Debt repaid", "debt_repaid_discrete", "Debt repaid"),
    (
        "finance_lease_principal_paid", "Finance lease principal paid",
        "finance_lease_principal_paid_discrete", "Finance lease principal paid",
    ),
    (
        "net_cash_financing", "Net cash provided by (used in) financing activities",
        "net_cash_financing_discrete", "Net cash provided by (used in) financing activities",
    ),
    ("fx_effect_on_cash", "Effect of exchange rates on cash", "fx_effect_on_cash_discrete", "Effect of exchange rates on cash"),
    ("net_change_in_cash", "Net change in cash", "net_change_in_cash_discrete", "Net change in cash"),
    # New (item 1). SPEC-008-batch-2 cash-reconciliation follow-up
    # (approved 2026-08-13, found live): this originally reused the
    # BALANCE SHEET's own `cash` canonical (CashAndCashEquivalentsAt
    # CarryingValue, EXCLUDES restricted cash) -- wrong. A real filed cash
    # flow statement reconciles on the BROADER post-ASU-2016-18 concept,
    # the SAME one net_change_in_cash/fx_effect_on_cash already use
    # (`cash_and_restricted_cash`, restricted cash included since 2018);
    # mixing the narrow balance-sheet concept in here is exactly why the
    # statement didn't internally reconcile (confirmed live: AMZN's real
    # cash and equivalents run 86,810 -> 78,213, but its filed cash flow
    # statement runs 90,106 -> 80,927 -- the ~3.3B/2.7B gap is restricted
    # cash). "Cash at end of period" now resolves `cash_and_restricted_
    # cash` directly at this column's period_end -- a different concept
    # from the balance sheet's own `cash` row, correctly (two statements,
    # two concepts, as the filings do). No fallback: filed figure or
    # nothing, never derived.
    #
    # "Cash at beginning of period" has no CONCEPT_REGISTRY entry at all --
    # it is patched by _derive_cash_beginning_from_prior_instant below,
    # which resolves the SAME `cash_and_restricted_cash` canonical one
    # calendar day before this column's period_start (confirmed against
    # the real corpus: a duration fact's `start` is consistently one day
    # after the prior instant's `end`, for all three companies -- an XBRL
    # filing convention, not a fiscal-calendar guess).
    ("cash_beginning", "Cash at beginning of period", None, None),
    ("cash_and_restricted_cash", "Cash at end of period", None, None),
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


def concept_never_tagged(cik: str, canonical: str, db_path: Path = DEFAULT_DB_PATH) -> bool:
    """Whether `canonical`'s CONCEPT_REGISTRY aliases have no row within
    this company's ANALYZED window -- distinguishes "this specific period
    has a gap" from "this company simply does not use this XBRL concept"
    (SPEC-008 review, debt-line follow-up, found live: Micron has never
    tagged `LongTermDebtNoncurrent` anywhere in the analyzed history --
    100% of periods, not one).

    Scoped to `period_end >= MIN(metrics.period_end)` for this company, not
    all of SEC history -- found live, a naive "any row, ever" check was a
    false negative here: Micron DID tag `LongTermDebtNoncurrent` in
    2012-2013, years before this project's analyzed window begins, which
    made the concept look "tagged at some point" when it has never once
    been tagged in any period this dashboard actually shows.

    Only meaningful for CONCEPT_REGISTRY (raw `xbrl_facts`) entries; a
    METRIC_REGISTRY entry, an unrecognised name, or a company with no
    `metrics` rows at all (no established analyzed window to scope
    against) returns False (not "structurally absent" -- just not this
    function's concern)."""
    if canonical not in config.CONCEPT_REGISTRY:
        return False
    earliest = _one(db_path, "SELECT MIN(period_end) AS earliest FROM metrics WHERE cik = ?", (cik,))
    if earliest is None or earliest["earliest"] is None:
        return False
    aliases = config.CONCEPT_REGISTRY[canonical].aliases
    placeholders = ",".join("?" for _ in aliases)
    row = _one(
        db_path,
        f"SELECT 1 AS present FROM xbrl_facts WHERE cik = ? AND concept IN ({placeholders}) "
        "AND period_end >= ? LIMIT 1",
        (cik, *aliases, earliest["earliest"]),
    )
    return row is None


# Concepts with an actual written diagnosis to point to -- the pointer is
# included only here, never invented generically for a structurally-absent
# concept that has no such note (SPEC-008 review, debt-line follow-up).
_STRUCTURAL_ABSENCE_DIAGNOSIS: dict[str, str] = {
    "debt_noncurrent": "see the standing debt-tag diagnosis in SPEC-008",
}


def get_statement_line_null_reason(
    cik: str, canonical: str, ticker: str, db_path: Path = DEFAULT_DB_PATH
) -> str:
    """The reason shown for a statement line with no value. Distinguishes a
    genuine period-specific gap (AMZN's gross profit/R&D at some periods --
    "not tagged for this period" is accurate for those) from a company
    that has never tagged the concept at all across the analyzed history
    (Micron's `debt_noncurrent` -- accurate would say so, not imply this
    one period is the exception). This decides nothing about which concept
    would be a safe fallback to DISPLAY instead -- it is a change to the
    reason string only."""
    if concept_never_tagged(cik, canonical, db_path):
        base = f"{ticker} has not tagged this concept in any filing on record"
        pointer = _STRUCTURAL_ABSENCE_DIAGNOSIS.get(canonical)
        return f"{base} -- {pointer}" if pointer else base
    return "not tagged for this period"


def _resolve_statement_line_value(
    cik: str, canonical: str, period_start: str | None, period_end: str, db_path: Path
) -> float | None:
    """Resolve ONE canonical input's value for an exact period -- the
    single-canonical logic `get_statement_line_values` uses for both a
    line's primary canonical and, when the primary is absent, its
    fallback. `free_cash_flow` and other computed metrics resolve from
    `metrics`; raw canonical inputs (revenue, cogs, ...) resolve from
    `xbrl_facts` directly via CONCEPT_REGISTRY's alias list.

    Duration (non-instant) concepts require an EXACT (period_start,
    period_end) match (SPEC-008 review D11, found live: a 10-Q routinely
    tags a duration concept MORE THAN ONCE for the same period_end -- the
    three-month quarter and the nine-month year-to-date cumulative --
    sharing a filed_date, with no principled way to prefer one by
    period_end alone). Instant (balance-sheet, as-of-a-date) concepts have
    no period_start at all and are queried by period_end alone."""
    if canonical in config.METRIC_REGISTRY:
        row = _one(
            db_path,
            "SELECT value FROM metrics WHERE cik = ? AND name = ? AND period_start = ? AND period_end = ? "
            "AND calc_version = ?",
            (cik, canonical, period_start, period_end, config.CALC_VERSION),
        )
        return row["value"] if row else None
    if canonical in config.CONCEPT_REGISTRY:
        concept_def = config.CONCEPT_REGISTRY[canonical]
        aliases = concept_def.aliases
        placeholders = ",".join("?" for _ in aliases)
        if concept_def.instant:
            row = _one(
                db_path,
                f"SELECT value FROM xbrl_facts WHERE cik = ? AND concept IN ({placeholders}) "
                "AND period_end = ? ORDER BY filed_date DESC LIMIT 1",
                (cik, *aliases, period_end),
            )
        else:
            row = _one(
                db_path,
                f"SELECT value FROM xbrl_facts WHERE cik = ? AND concept IN ({placeholders}) "
                "AND period_start = ? AND period_end = ? ORDER BY filed_date DESC LIMIT 1",
                (cik, *aliases, period_start, period_end),
            )
        return row["value"] if row else None
    return None


def get_statement_line_values(
    cik: str, period_start: str | None, period_end: str,
    lines: tuple[tuple[str, str, str | None, str | None], ...],
    db_path: Path = DEFAULT_DB_PATH,
) -> list[dict]:
    """One curated statement's lines for one period.

    Each line tries its primary canonical first; if that resolves to
    nothing AND a `fallback_canonical` is declared, tries the fallback --
    same exact-period-match discipline, never a guess among candidates.
    When the fallback resolves, the row's `label` is `fallback_label`, NOT
    the primary label (SPEC-008 review, PP&E follow-up: a label must
    accurately name the number next to it -- "Property, plant and
    equipment, net: 397,463" would pair a pure-PP&E label with a figure
    that also includes finance-lease ROU assets, misleading a reader
    scanning the row without reading a note)."""
    out = []
    for canonical, label, fallback_canonical, fallback_label in lines:
        value = _resolve_statement_line_value(cik, canonical, period_start, period_end, db_path)
        used_label = label
        if value is None and fallback_canonical is not None:
            value = _resolve_statement_line_value(cik, fallback_canonical, period_start, period_end, db_path)
            if value is not None:
                used_label = fallback_label
        out.append({"canonical": canonical, "label": used_label, "value": value})
    return out


def get_period_duration_class(period_start: str | None, period_end: str) -> str:
    """Public wrapper around `_classify_duration` for display callers
    outside this module (SPEC-008 review D11 -- financials.py needs to
    state each duration-based statement's own period length, not just its
    end date). The private helper stays private for its original purpose
    (basis filtering inside this module); this is a deliberate, separate
    exposure."""
    return _classify_duration(period_start, period_end)


def get_cash_flow_period(
    cik: str, quarterly_period_start: str, period_end: str, db_path: Path = DEFAULT_DB_PATH
) -> tuple[str, str]:
    """SPEC-008 review D11 follow-up (found live, confirmed against the
    real corpus): a 10-Q's cash-flow statement is routinely tagged
    year-to-date only, not incrementally per quarter -- confirmed for
    Micron and NVIDIA, where most non-Q1 quarters have NO three-month cash-
    flow facts tagged at all (Q1 always matches, since a fiscal year's
    first quarter's three-month and year-to-date figures are the same
    number). The exact-match fix in `get_statement_line_values` is correct
    to refuse a guess when it can't tell which duration a caller wants --
    but for the cash-flow section specifically, forcing the selector's
    quarterly period_start when the filing itself never tagged that
    duration turns a real, differently-shaped fact into "not tagged for
    this period," which reads as a data gap rather than what it is: the
    right number, at a different, itself-unambiguous duration.

    Uses `cfo` as the representative concept -- one 10-Q's cash-flow
    statement covers a single consistent duration across every line item
    in it, so what's true for `cfo` is true for the whole section. Returns
    `(period_start_to_use, note)`: the quarterly `period_start` unchanged
    with an empty note when it already has data (the common, correct
    case); otherwise, the ONE other period_start actually tagged for this
    period_end, with an explicit note -- never a guess among multiple
    candidates, matching `get_statement_line_values`'s own refusal to pick
    an arbitrary one."""
    aliases = config.CONCEPT_REGISTRY["cfo"].aliases
    placeholders = ",".join("?" for _ in aliases)
    quarterly_match = _one(
        db_path,
        f"SELECT 1 AS present FROM xbrl_facts WHERE cik = ? AND concept IN ({placeholders}) "
        "AND period_start = ? AND period_end = ?",
        (cik, *aliases, quarterly_period_start, period_end),
    )
    if quarterly_match is not None:
        return quarterly_period_start, ""
    candidates = _run(
        db_path,
        f"SELECT DISTINCT period_start FROM xbrl_facts WHERE cik = ? AND concept IN ({placeholders}) "
        "AND period_end = ? AND period_start IS NOT NULL",
        (cik, *aliases, period_end),
    )
    if len(candidates) == 1:
        return candidates[0]["period_start"], "not tagged separately for this quarter -- showing the year-to-date figure instead"
    return quarterly_period_start, ""


# --- C4: the multi-period statement table ---


def get_statement_periods(cik: str, basis: str, db_path: Path = DEFAULT_DB_PATH) -> list[dict]:
    """Every distinct (period_start, period_end) for this company at this
    basis, OLDEST TO NEWEST -- C4's columns, left to right. Filters
    `get_statement_period_ends` (all periods with metrics computed, newest
    first, the period-selector's own list) by duration class, the same
    `_CLASSES_FOR_BASIS` mapping `get_metric_series` already uses, then
    reverses it -- a table reads oldest-to-newest; a selector dropdown
    reads newest-first. One source of periods, two orderings for two
    different UIs, not two queries."""
    all_periods = get_statement_period_ends(cik, db_path)
    allowed_classes = _CLASSES_FOR_BASIS[basis]
    filtered = [p for p in all_periods if _classify_duration(p["period_start"], p["period_end"]) in allowed_classes]
    return list(reversed(filtered))


def _growth_pct(prior_value: float, value: float) -> float | None:
    if prior_value == 0:
        return None
    return (value - prior_value) / abs(prior_value)


# SPEC-008-batch-1 item 2 (D14, approved 2026-08-08): a growth percentage is
# mathematically defined but analytically meaningless in two cases -- the
# base's sign differs from the new value's (a move from profit to loss
# cannot be expressed as a percentage change of the old figure), or the
# base is a small fraction of this LINE's own typical size (Micron's real
# free cash flow moving from $72M to $3,022M prints "+4097.2%", which
# conveys nothing and crowds the column; the $72M quarter was itself real,
# just atypically small for this line).
#
# Threshold: 10% of the row's own median absolute value across every
# period currently in `cells` -- a property of THIS line, THIS company,
# not a global constant, matching the review's own phrasing ("relative to
# the line's own typical magnitude"). Chosen empirically, not picked
# blind: the review's own cited example (MU free cash flow, $72M base) has
# a ratio of 72M / 786M (MU's real FCF median) = 0.092 -- a 5% threshold
# would MISS it; 10% is the smallest round number that catches it. Checked
# against the real corpus at three candidate thresholds before choosing:
#
#   5%  -> 55 cells flip from a number to n/m (misses the review's own example)
#   10% -> 72 cells flip (catches it; chosen)
#   15% -> 79 cells flip (7 more than 10%, diminishing returns)
#
# Of the 72 cells at 10%: 49 are sign-crossing (independent of the
# threshold), 23 are near-zero-base, 0 are exactly zero (a zero base
# already yields no growth_pct at all -- see `_growth_pct` -- so there is
# nothing to "flip" for that case; it was already blank before this item,
# still blank after). Per company: AMZN 23, NVDA 15, MU 34.
_GROWTH_NEAR_ZERO_BASE_FRACTION = 0.10


def _classify_growth(prior_value: float, value: float, typical_magnitude: float | None) -> tuple[float | None, str | None]:
    """Returns (growth_pct, not_meaningful_reason). `growth_pct` is the raw
    computed value (still attached even when flagged -- the number itself
    isn't wrong, just not worth presenting as a rate); `not_meaningful_reason`
    is None when the growth IS meaningful, else one of "zero_base",
    "sign_crossing", "near_zero_base" -- the caller (components.py) renders
    "n/m" for any non-None reason, never the raw percentage."""
    growth_pct = _growth_pct(prior_value, value)
    if prior_value == 0:
        return growth_pct, "zero_base"
    if (prior_value > 0) != (value > 0) and value != 0:
        return growth_pct, "sign_crossing"
    if typical_magnitude and abs(prior_value) < _GROWTH_NEAR_ZERO_BASE_FRACTION * typical_magnitude:
        return growth_pct, "near_zero_base"
    return growth_pct, None


def _classify_blank_cell(
    other_label: str | None, other_value: float | None, gap_reason_fn,
) -> tuple[str, str]:
    """SPEC-008 C4 constraint 4: a blank cell means one of two different
    things, and the table must say which -- the single-period Summary tab
    already distinguished this; the multi-period table dropped the
    distinction entirely until this pass restored it. `other_value` is this
    SAME period's value in the paired primary/fallback row for this line,
    or None if no such pairing applies to this canonical at all (or the
    pairing is a MERGED single row, which by construction has no blank
    cells left to explain this way -- see `_resolve_line_across_periods`).

    A third cause this function used to classify -- "not computed at this
    fallback DURATION" -- is gone, not merely unreachable: the cash-flow
    duration-fallback mechanism (`is_duration_fallback`, the old † marker)
    was retired for this table (SPEC-008 C4, approved 2026-08-08) in favour
    of deriving the true discrete quarter via `fallback_canonical` merged
    into one row (`is_derived_quarter`, the new † marker) -- there is no
    longer a case where a cell is blank BECAUSE of a duration mismatch;
    either the discrete value was found (filed or derived) or it wasn't,
    which is a plain gap."""
    if other_value is not None:
        return "split", f"this period's figure is in the '{other_label}' row instead -- a presentation split, not a gap"
    return "gap", gap_reason_fn()


# --- SPEC-008-batch-1 item 1 (D13, approved 2026-08-09): year-over-year growth ---


def _fiscal_labels(cik: str, db_path: Path) -> dict[str, tuple[int, str]]:
    """period_end -> (fiscal_year, fiscal_period), from `filings` -- the
    authoritative SEC-declared fiscal calendar (dei:DocumentFiscalYearFocus/
    PeriodFocus), same source `edgar.metrics`'s discrete-quarter derivation
    uses for the same reason (SPEC-008 C4/D12): no date arithmetic, robust
    to MU/NVDA's floating fiscal year-end by construction. A dashboard-owned
    read-only copy of the query shape, not a reach into `edgar.metrics` (a
    pipeline module) -- this module owns every SQL query the dashboard
    makes (SPEC-008's hard rule), full stop, no exceptions for convenience.

    `ORDER BY filing_date` so that if two accessions somehow share a
    period_end (unobserved in this project's real data, but not
    structurally impossible), the LATEST filing's label wins building the
    dict -- the same "latest wins" convention SPEC-004 R4 already commits
    to elsewhere, not a new policy invented here."""
    rows = _run(
        db_path,
        "SELECT period_end, fiscal_year, fiscal_period FROM filings "
        "WHERE cik = ? AND fiscal_period IS NOT NULL ORDER BY filing_date",
        (cik,),
    )
    return {row["period_end"]: (row["fiscal_year"], row["fiscal_period"]) for row in rows}


def _year_ago_period_end(period_end: str, fiscal_labels: dict[str, tuple[int, str]]) -> str | None:
    """The period_end of the SAME fiscal quarter one fiscal year earlier --
    None if this period_end has no fiscal label at all, or if no filing
    carries the target (fiscal_year - 1, SAME fiscal_period) label. Never a
    calendar guess (e.g. "365 days earlier"): fails closed to None exactly
    like every other discrete-quarter lookup in this project when the
    authoritative fiscal calendar doesn't have an answer."""
    label = fiscal_labels.get(period_end)
    if label is None:
        return None
    target = (label[0] - 1, label[1])
    for candidate_end, candidate_label in fiscal_labels.items():
        if candidate_label == target:
            return candidate_end
    return None


def _annotate_blanks(
    cells: list[dict], other_cells: list[dict] | None, other_label: str | None, gap_reason_fn,
) -> None:
    """Attaches `blank_cause`/`blank_reason` to every None-valued cell, in
    place. `other_cells` is the paired primary/fallback row's cells (same
    columns, same order), or None when this line has no fallback pairing
    at all (or the pairing was merged into one row already)."""
    for i, cell in enumerate(cells):
        if cell["value"] is not None:
            continue
        other_value = other_cells[i]["value"] if other_cells is not None else None
        cause, reason = _classify_blank_cell(other_label, other_value, gap_reason_fn)
        cell["blank_cause"] = cause
        cell["blank_reason"] = reason


def _resolve_line_across_periods(
    cik: str,
    canonical: str,
    label: str,
    fallback_canonical: str | None,
    fallback_label: str | None,
    effective_periods: list[tuple[str | None, str]],
    ticker: str,
    db_path: Path,
    fiscal_labels: dict[str, tuple[int, str]],
) -> list[dict]:
    """One line item's row(s) across every column. `effective_periods` is
    `(period_start, period_end)` per column -- every statement's own
    nominal period boundaries, uniformly (SPEC-008 C4, approved
    2026-08-08: cash flow used to substitute a possibly-wrong-duration
    period_start here, `is_duration_fallback` marking when it did; that
    mechanism is retired in favour of resolving the true discrete figure
    directly, filed or derived, below -- `get_cash_flow_period` itself is
    unchanged and still serves the Summary tab, which still needs to state
    a single period's own duration in words).

    THREE resolution patterns share this one mechanism (SPEC-008 C4 item
    3, approved 2026-08-08: reuse or unify, never invent a third).
    `fallback_label == label` is what distinguishes them -- inferred from
    the caller's own data, not a separate flag a caller could forget to
    set:

    - No fallback at all (`fallback_canonical is None`): every other
      line item. One row, ordinary blanks.
    - Fallback with a DIFFERENT label (`ppe_net` -> `ppe_and_lease_net`):
      the concept itself changed, so the label must too -- SPEC-008 C4
      constraint 3, TWO rows when the fallback is used anywhere in the
      displayed range (primary-labelled, populated only where primary
      resolved; fallback-labelled, populated only where the fallback
      did), never one row whose meaning changes between columns.
    - Fallback with the SAME label (every cash-flow line's `_discrete`
      pair): the SAME accounting concept, sourced two ways for
      continuity -- filed directly (AMZN) or derived by subtraction
      (MU, NVDA). MERGED into ONE row: each cell takes the primary value
      if filed, else the fallback value, with `is_derived_quarter`
      marking which cells used it (the new † marker, replacing the old
      duration-fallback one it retired).

    SPEC-008 C4 constraint 2, the REAL rule: growth% is only valid between
    two cells covering the SAME duration. For the merged case this is now
    true by construction -- every cell is the TRUE discrete quarter,
    whichever way it was obtained -- so growth is no longer suppressed for
    these lines the way it always was under the old duration-fallback
    proxy."""
    merge_fallback = fallback_canonical is not None and fallback_label == label

    primary_cells: list[dict] = []
    fallback_cells: list[dict] = []
    fallback_used = False
    for period_start, period_end in effective_periods:
        primary_value = _resolve_statement_line_value(cik, canonical, period_start, period_end, db_path)
        fallback_value = None
        if primary_value is None and fallback_canonical is not None:
            fallback_value = _resolve_statement_line_value(cik, fallback_canonical, period_start, period_end, db_path)
            if fallback_value is not None:
                fallback_used = True
        primary_cells.append({"period_end": period_end, "value": primary_value})
        fallback_cells.append({"period_end": period_end, "value": fallback_value})

    # SPEC-008 C4 constraint 4: the "never tagged at all" vs. "not tagged
    # for this period" distinction (`get_statement_line_null_reason`)
    # depends only on (cik, canonical, ticker), never on which period a
    # given blank cell is -- computed at most once per row, not once per
    # blank cell, and only if a blank cell actually needs it (a row with no
    # plain gaps -- e.g. every blank is a split-row complement -- never
    # pays for the query at all).
    _gap_reason_cache: list[str] = []

    def _gap_reason() -> str:
        if not _gap_reason_cache:
            _gap_reason_cache.append(get_statement_line_null_reason(cik, canonical, ticker, db_path))
        return _gap_reason_cache[0]

    if merge_fallback:
        merged_cells = []
        for p_cell, f_cell in zip(primary_cells, fallback_cells):
            is_derived = p_cell["value"] is None and f_cell["value"] is not None
            merged_cells.append(
                {
                    "period_end": p_cell["period_end"],
                    "value": f_cell["value"] if is_derived else p_cell["value"],
                    "is_derived_quarter": is_derived,
                }
            )
        _annotate_blanks(merged_cells, None, None, _gap_reason)
        return [_finalize_statement_row(label, canonical, merged_cells, fiscal_labels)]

    primary_used = any(c["value"] is not None for c in primary_cells)
    rows = []
    if primary_used or not fallback_used:
        # Always show the primary row unless the fallback is what's
        # actually carrying every populated cell -- a line with NO data at
        # all anywhere (neither primary nor fallback resolves) still shows
        # its normal, single row of blanks, same as any other "not tagged"
        # line; only a fallback-only line drops the permanently-empty
        # primary row.
        _annotate_blanks(primary_cells, fallback_cells if fallback_used else None, fallback_label, _gap_reason)
        rows.append(_finalize_statement_row(label, canonical, primary_cells, fiscal_labels))
    if fallback_used:
        _annotate_blanks(fallback_cells, primary_cells, label, _gap_reason)
        rows.append(_finalize_statement_row(fallback_label, canonical, fallback_cells, fiscal_labels))
    return rows


def _finalize_statement_row(
    label: str, canonical: str, cells: list[dict], fiscal_labels: dict[str, tuple[int, str]]
) -> dict:
    """Attaches each cell's growth_pct -- R8: derived here, in the data
    layer, never computed inline in a page. `growth_pct` is None whenever
    there is no valid prior cell to compare against (no immediately
    preceding cell, or either side missing a value) -- SPEC-008 C4
    constraint 2's real rule is that growth is only valid between two
    cells of the SAME duration, which every cell in `cells` now IS by
    construction (the true discrete quarter, filed or derived; see
    `_resolve_line_across_periods`'s docstring). The old duration-fallback
    proxy that used to also gate this is gone with the mechanism it was
    guarding against.

    SPEC-008-batch-1 item 2 (D14): also attaches `growth_not_meaningful`
    (`_classify_growth`) -- a zero or sign-crossing base, or a base under
    10% of this row's own typical (median) magnitude. `growth_pct` stays
    populated even when flagged; the reason is what tells the caller to
    render "n/m" instead of the number, not the absence of a number.

    SPEC-008-batch-1 item 1 (D13): also attaches `yoy_growth_pct`/
    `yoy_growth_not_meaningful` -- the SAME cell compared against the SAME
    fiscal quarter one fiscal year earlier (`_year_ago_period_end`,
    `filings.fiscal_year`/`fiscal_period`, no date arithmetic), fails
    closed to None when that quarter isn't among `cells` at all or its own
    value is missing, and shares item 2's n/m classification -- a
    meaningless base is exactly as meaningless one year apart as one
    quarter apart. On the annual basis this is numerically identical to
    the sequential value by construction (a fiscal year's "one year
    earlier, same fiscal period" IS its immediately preceding annual
    cell) -- deliberately not special-cased here; which of the two
    (if either) to SHOW is a display decision, out of scope for this
    batch."""
    typical_magnitude = None
    row_values = [cell["value"] for cell in cells if cell["value"] is not None]
    if row_values:
        typical_magnitude = statistics.median(abs(v) for v in row_values)
    cells_by_period_end = {cell["period_end"]: cell for cell in cells}

    prior: dict | None = None
    for cell in cells:
        growth_pct = None
        not_meaningful = None
        if prior is not None and prior["value"] is not None and cell["value"] is not None:
            growth_pct, not_meaningful = _classify_growth(prior["value"], cell["value"], typical_magnitude)
        cell["growth_pct"] = growth_pct
        cell["growth_not_meaningful"] = not_meaningful

        yoy_growth_pct = None
        yoy_not_meaningful = None
        year_ago_end = _year_ago_period_end(cell["period_end"], fiscal_labels)
        year_ago_cell = cells_by_period_end.get(year_ago_end) if year_ago_end is not None else None
        if year_ago_cell is not None and year_ago_cell["value"] is not None and cell["value"] is not None:
            yoy_growth_pct, yoy_not_meaningful = _classify_growth(
                year_ago_cell["value"], cell["value"], typical_magnitude
            )
        cell["yoy_growth_pct"] = yoy_growth_pct
        cell["yoy_growth_not_meaningful"] = yoy_not_meaningful

        prior = cell
    return {"label": label, "canonical": canonical, "cells": cells}


def _statement_table(
    cik: str,
    lines: tuple[tuple[str, str, str | None, str | None], ...],
    periods: list[dict],
    ticker: str,
    db_path: Path,
) -> list[dict]:
    # SPEC-008 C4 (approved 2026-08-08): cash flow no longer needs its own
    # branch here. It used to substitute `get_cash_flow_period`'s (possibly
    # wrong-duration) period_start for the whole column; now every line
    # resolves at its column's own TRUE discrete period_start uniformly,
    # same as every other statement -- filed directly (AMZN) or via
    # `fallback_canonical` to a `_discrete` metric (MU, NVDA), inside
    # `_resolve_line_across_periods` itself. `get_cash_flow_period` is
    # unchanged and still used by the Summary tab.
    effective_periods = [(p["period_start"], p["period_end"]) for p in periods]
    # SPEC-008-batch-1 item 1 (D13): computed once per table, not once per
    # line -- every line's YoY lookup shares the same (cik-wide) fiscal
    # calendar.
    fiscal_labels = _fiscal_labels(cik, db_path)

    rows: list[dict] = []
    for canonical, label, fallback_canonical, fallback_label in lines:
        rows.extend(
            _resolve_line_across_periods(
                cik, canonical, label, fallback_canonical, fallback_label, effective_periods, ticker, db_path,
                fiscal_labels,
            )
        )
    _derive_gross_profit_from_components(rows, fiscal_labels)
    _derive_total_liabilities_from_components(rows, fiscal_labels)
    _derive_cash_beginning_from_prior_instant(rows, effective_periods, cik, db_path, fiscal_labels)
    _mark_fcff_as_a_rate_assumption(rows)
    _label_fcff_tax_rate_row_by_basis(rows, periods)
    return rows


def _derive_gross_profit_from_components(rows: list[dict], fiscal_labels: dict[str, tuple[int, str]]) -> None:
    """SPEC-008-batch-1 item 4 (approved 2026-08-09): gross_profit =
    revenue - cogs, applied only where gross_profit itself (filed directly
    or via its own `_discrete` fallback) is still blank at a period where
    BOTH revenue and cogs resolve. A no-op for any table without all three
    canonicals (balance sheet, cash flow) -- cheap to call unconditionally
    rather than threading an income-statement-only flag through
    `_statement_table`.

    STRICT LIMIT (the item's own words): exact arithmetic on lines already
    on the SAME statement, nothing requiring judgement about what belongs
    in a line. Checked against the real corpus before this was written,
    not assumed: revenue - cogs equals the FILED gross_profit in all 236
    periods across the three companies where all three are filed
    simultaneously -- zero mismatches. Two other candidates were checked
    and REJECTED by the same test, not implemented: net_income = pretax_
    income - tax_expense holds for NVDA (102/103) but not AMZN (5/123) or
    MU (1/103) -- both companies have other components (minority
    interest, equity-method income, ...) this project doesn't carry, so
    the equation is not exact and R&D-style judgement would be needed to
    fix it, which the item explicitly rules out. operating_income and
    pretax_income were not even numerically checked -- both are known
    from the review itself (Part 2, "Non-operating income share") to have
    real components (AMZN's Anthropic/OpenAI marks) this dashboard
    doesn't carry at all, so the equation can't be exact by construction.

    A DIFFERENT kind of derivation than the discrete-quarter mechanism
    (arithmetic across LINES within one period, not subtraction across
    TIME for one line) -- deliberately not forced into `fallback_canonical`,
    which only ever does a single-concept lookup, not arithmetic on two.
    The reader-facing claim is identical though ("not filed, this project
    computed it"), so it reuses the SAME marker, `is_derived_quarter`, one
    visual language for that claim rather than two. Cells are re-
    `_finalize_statement_row`'d after patching so growth/YoY reflect the
    now-filled values instead of the stale blanks they were computed
    against the first time."""
    revenue_row = next((r for r in rows if r["canonical"] == "revenue"), None)
    cogs_row = next((r for r in rows if r["canonical"] == "cogs"), None)
    gross_profit_row = next((r for r in rows if r["canonical"] == "gross_profit"), None)
    if revenue_row is None or cogs_row is None or gross_profit_row is None:
        return

    revenue_by_end = {cell["period_end"]: cell["value"] for cell in revenue_row["cells"]}
    cogs_by_end = {cell["period_end"]: cell["value"] for cell in cogs_row["cells"]}
    patched = False
    for cell in gross_profit_row["cells"]:
        if cell["value"] is not None:
            continue
        revenue_value = revenue_by_end.get(cell["period_end"])
        cogs_value = cogs_by_end.get(cell["period_end"])
        if revenue_value is None or cogs_value is None:
            continue
        cell["value"] = revenue_value - cogs_value
        cell["is_derived_quarter"] = True
        cell.pop("blank_cause", None)
        cell.pop("blank_reason", None)
        patched = True

    if patched:
        _finalize_statement_row(
            gross_profit_row["label"], gross_profit_row["canonical"], gross_profit_row["cells"], fiscal_labels
        )


def _derive_total_liabilities_from_components(rows: list[dict], fiscal_labels: dict[str, tuple[int, str]]) -> None:
    """SPEC-008-batch-1 item 7 (approved 2026-08-11): total_liabilities =
    total_assets - equity, applied only where total_liabilities itself
    (filed directly as `Liabilities` -- NVDA and MU do; AMZN never files a
    standalone total) is still blank at a period where BOTH total_assets
    and equity resolve. Same pattern and same STRICT LIMIT as item 4's
    _derive_gross_profit_from_components: exact arithmetic on lines
    already on this statement, checked against the real corpus first --
    computed == filed Liabilities in all 16/16 recent NVDA and MU quarters,
    zero mismatches (see the total_liabilities ConceptInput comment in
    edgar/config.py). A no-op for tables without all three canonicals
    (income statement, cash flow, EPS/shares)."""
    assets_row = next((r for r in rows if r["canonical"] == "total_assets"), None)
    equity_row = next((r for r in rows if r["canonical"] == "equity"), None)
    liabilities_row = next((r for r in rows if r["canonical"] == "total_liabilities"), None)
    if assets_row is None or equity_row is None or liabilities_row is None:
        return

    assets_by_end = {cell["period_end"]: cell["value"] for cell in assets_row["cells"]}
    equity_by_end = {cell["period_end"]: cell["value"] for cell in equity_row["cells"]}
    patched = False
    for cell in liabilities_row["cells"]:
        if cell["value"] is not None:
            continue
        assets_value = assets_by_end.get(cell["period_end"])
        equity_value = equity_by_end.get(cell["period_end"])
        if assets_value is None or equity_value is None:
            continue
        cell["value"] = assets_value - equity_value
        cell["is_derived_quarter"] = True
        cell.pop("blank_cause", None)
        cell.pop("blank_reason", None)
        patched = True

    if patched:
        _finalize_statement_row(
            liabilities_row["label"], liabilities_row["canonical"], liabilities_row["cells"], fiscal_labels
        )


def _derive_cash_beginning_from_prior_instant(
    rows: list[dict],
    effective_periods: list[tuple[str | None, str]],
    cik: str,
    db_path: Path,
    fiscal_labels: dict[str, tuple[int, str]],
) -> None:
    """SPEC-008-batch-2 item 1 (approved 2026-08-13), corrected by the
    cash-reconciliation follow-up (same date, found live by independent
    review): "cash at beginning of period" is not a new duration concept
    to curate -- it is the SAME instant fact `cash_and_restricted_cash`
    already carries (this table's own "Cash at end of period" row, the
    BROADER post-ASU-2016-18 concept -- NOT the balance sheet's narrower
    `cash`, whose reuse here was the original bug: it excludes restricted
    cash, so beginning + net_change never equalled ending), read at the
    date ONE CALENDAR DAY before this column's period_start.

    Checked against the real corpus before writing this, not assumed: a
    duration fact's `start` date is consistently one day after the PRIOR
    instant's `end` date, for all three companies (AMZN Q2 2025:
    start=2025-04-01, prior instant end=2025-03-31; NVDA and MU confirmed
    the same pattern across six consecutive fiscal years each) -- an XBRL
    filing convention (duration contexts are start-inclusive, instant
    contexts are point-in-time), not a fiscal-calendar guess. This is a
    DIFFERENT kind of lookup than `_derive_gross_profit_from_components`/
    `_derive_total_liabilities_from_components` above: no arithmetic runs
    on the value at all, so a patched cell here does NOT get
    `is_derived_quarter` -- that marker means "this project computed a
    number the filer didn't file"; this number is the filer's own, at the
    exact instant XBRL itself uses to represent it. A no-op for any table
    without a `cash_beginning` row (income statement, balance sheet,
    EPS/shares, key metrics)."""
    beginning_row = next((r for r in rows if r["canonical"] == "cash_beginning"), None)
    if beginning_row is None:
        return

    patched = False
    for cell, (period_start, _period_end) in zip(beginning_row["cells"], effective_periods):
        if cell["value"] is not None or period_start is None:
            continue
        prior_instant = (date.fromisoformat(period_start) - timedelta(days=1)).isoformat()
        value = _resolve_statement_line_value(cik, "cash_and_restricted_cash", None, prior_instant, db_path)
        if value is None:
            continue
        cell["value"] = value
        cell.pop("blank_cause", None)
        cell.pop("blank_reason", None)
        patched = True

    if patched:
        _finalize_statement_row(
            beginning_row["label"], beginning_row["canonical"], beginning_row["cells"], fiscal_labels
        )


def _mark_fcff_as_a_rate_assumption(rows: list[dict]) -> None:
    """SPEC-008-batch-2 item 2, requirement 2: "Mark FCFF distinctly from
    FCF and FCFE. [...] That difference should be visible." `is_derived_
    quarter` already marks "this project subtracted two filed cumulatives"
    -- a DIFFERENT fact from "this number rests on a constructed tax rate",
    which is true of every populated FCFF cell, including an annual,
    directly-filed-duration one that never went through the discrete-
    quarter mechanism at all. A no-op for any table without an `fcff` row
    (every statement except key metrics)."""
    fcff_row = next((r for r in rows if r["canonical"] == "fcff"), None)
    if fcff_row is None:
        return
    for cell in fcff_row["cells"]:
        cell["rate_assumption"] = cell["value"] is not None


def _label_fcff_tax_rate_row_by_basis(rows: list[dict], periods: list[dict]) -> None:
    """SPEC-008-batch-2 item 2, requirement 4: "Add the effective tax rate
    used as its own row [...], labelled with which basis produced it
    (year's own, or TTM)." Every column in one `_statement_table` call
    shares the same basis (the page's own annual/quarterly toggle), so this
    reads it once from the first period rather than per cell. A no-op for
    any table without an `fcff_tax_rate` row."""
    rate_row = next((r for r in rows if r["canonical"] == "fcff_tax_rate"), None)
    if rate_row is None or not periods:
        return
    duration_class = _classify_duration(periods[0]["period_start"], periods[0]["period_end"])
    suffix = " -- this year's own rate" if duration_class == "annual" else " -- trailing twelve months"
    rate_row["label"] = rate_row["label"] + suffix


def get_income_statement_table(
    cik: str, periods: list[dict], ticker: str, db_path: Path = DEFAULT_DB_PATH
) -> list[dict]:
    return _statement_table(cik, INCOME_STATEMENT_LINES, periods, ticker, db_path=db_path)


def get_key_metrics_table(
    cik: str, periods: list[dict], ticker: str, db_path: Path = DEFAULT_DB_PATH
) -> list[dict]:
    """SPEC-008-batch-1 item 6 / SPEC-008-batch-2 item 2: EPS, share counts,
    and derived per-share/cash-flow metrics, resolved through the SAME
    `_statement_table` machinery as every other statement (growth%, YoY,
    blank-cause classification all apply identically). The per-share lines
    have NO fallback_canonical -- see `KEY_METRICS_LINES`'s own docstring
    for why subtraction is invalid for a weighted average; a blank Q4 there
    is a genuine, correctly-unfilled gap, not a bug."""
    return _statement_table(cik, KEY_METRICS_LINES, periods, ticker, db_path=db_path)


def get_balance_sheet_table(
    cik: str, periods: list[dict], ticker: str, db_path: Path = DEFAULT_DB_PATH
) -> list[dict]:
    return _statement_table(cik, BALANCE_SHEET_LINES, periods, ticker, db_path=db_path)


def get_cash_flow_table(cik: str, periods: list[dict], ticker: str, db_path: Path = DEFAULT_DB_PATH) -> list[dict]:
    """SPEC-008 C4 (approved 2026-08-08): every cash-flow line declares a
    `_discrete` `fallback_canonical` in `CASH_FLOW_LINES` -- filed directly
    where the company tags the true quarter (AMZN), derived by subtraction
    where it tags year-to-date instead (MU, NVDA; `edgar.metrics.
    compute_discrete_quarter_metrics`). `is_derived_quarter` marks which
    cells used the derived path, replacing the old duration-fallback
    marker this table used to show instead of the true quarterly figure."""
    return _statement_table(cik, CASH_FLOW_LINES, periods, ticker, db_path=db_path)


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
