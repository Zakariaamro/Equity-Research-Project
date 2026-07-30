"""Schema creation and connection handling.

Creates the complete nine-table schema from ARCHITECTURE.md §6 up front,
including tables unused until later specs (sections, xbrl_facts, metrics,
llm_calls, analyses, findings, observations) — this avoids migrations
mid-project, aside from the rare in-place schema change to a table that
turns out to still be empty (see `_migrate_analyses_table`, SPEC-006).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from edgar import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS companies (
    cik              TEXT PRIMARY KEY,
    ticker           TEXT NOT NULL,
    name             TEXT NOT NULL,
    fiscal_year_end  TEXT
);

CREATE TABLE IF NOT EXISTS filings (
    accession_no   TEXT PRIMARY KEY,
    cik            TEXT NOT NULL REFERENCES companies(cik),
    form_type      TEXT NOT NULL,
    filing_date    TEXT NOT NULL,
    period_end     TEXT,
    -- SPEC-005: from companyfacts fy/fp labels, populated at XBRL ingest
    -- time for 10-K/10-Q only (NULL for 8-K, which has no companyfacts
    -- entry). Used for fiscal-period prior-year matching in observations.py
    -- instead of date arithmetic -- robust to NVDA/MU's floating 52/53-week
    -- years by construction, since it is not derived from any date at all.
    fiscal_year    INTEGER,
    fiscal_period  TEXT,
    items          TEXT,
    primary_doc    TEXT,
    raw_path       TEXT,
    discovered_at  TEXT NOT NULL,
    status         TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sections (
    id                  INTEGER PRIMARY KEY,
    accession_no        TEXT NOT NULL REFERENCES filings(accession_no),
    category            TEXT NOT NULL,
    short_name          TEXT NOT NULL,
    source_file         TEXT,
    position            INTEGER,
    text_hash           TEXT NOT NULL,
    -- SPEC-005: text_hash means content identity (any byte differs -> a new
    -- hash); normalized_text_hash means wording identity (version stamp
    -- stripped, numeric tokens masked -- see config.py). Both are pure
    -- functions of the immutable section text, same precedent as
    -- duration_days on xbrl_facts.
    normalized_text_hash TEXT,
    word_count          INTEGER,
    sentence_count      INTEGER,
    complex_word_count  INTEGER,
    UNIQUE(accession_no, category, short_name, source_file)
);

CREATE TABLE IF NOT EXISTS xbrl_facts (
    id            INTEGER PRIMARY KEY,
    cik           TEXT NOT NULL REFERENCES companies(cik),
    taxonomy      TEXT NOT NULL,
    concept       TEXT NOT NULL,
    unit          TEXT NOT NULL,
    period_start  TEXT,
    period_end    TEXT NOT NULL,
    fiscal_year   INTEGER,
    fiscal_period TEXT,
    value         REAL NOT NULL,
    accession_no  TEXT,
    form_type     TEXT,
    duration_days INTEGER,
    filed_date    TEXT,
    UNIQUE(cik, concept, unit, period_start, period_end, accession_no)
);

CREATE TABLE IF NOT EXISTS metrics (
    id           INTEGER PRIMARY KEY,
    cik          TEXT NOT NULL REFERENCES companies(cik),
    accession_no TEXT REFERENCES filings(accession_no),
    period_start TEXT NOT NULL,
    period_end   TEXT NOT NULL,
    name         TEXT NOT NULL,
    value        REAL,
    formula      TEXT NOT NULL,
    inputs_json  TEXT NOT NULL,
    calc_version TEXT NOT NULL,
    computed_at  TEXT NOT NULL,
    UNIQUE(cik, period_start, period_end, name, calc_version)
);

-- ============ SPEC-006: LLM SPEND LEDGER ============
-- Sole source of spend. Every call attempt appends a row, including
-- failures and refusals -- a ledger with gaps is not a ledger. Checked
-- before every call so the lifetime cap (config.LLM_BUDGET_USD) cannot be
-- exceeded by construction (SPEC-006 R1). Defined before `analyses`, which
-- references it. This ledger only ever sees spend that routes through it --
-- see SPEC-006A / ARCHITECTURE.md §4.2 for the layers that exist because
-- that is not the same thing as "all spend."
CREATE TABLE IF NOT EXISTS llm_calls (
    id             INTEGER PRIMARY KEY,
    created_at     TEXT NOT NULL,
    model          TEXT NOT NULL,
    prompt_name    TEXT,
    prompt_version TEXT,
    input_tokens   INTEGER NOT NULL,
    output_tokens  INTEGER NOT NULL,
    cost_usd       REAL NOT NULL,
    status         TEXT NOT NULL,        -- ok | error | refused | reconciliation
    -- 'reconciliation': a manual, one-off adjustment row for real spend that a
    -- since-fixed ledger bug failed to capture at the time (2026-07-28: the
    -- truncation-retry gap, see scripts/backfill_2026_07_28_truncation_ledger_gap.py).
    -- Never written by application code -- only by a reviewed, committed one-off
    -- script -- and its note always states plainly what is measured versus assumed.
    note           TEXT
);

-- SPEC-006: analyses.call_id is the sole link to spend (llm_calls); analyses
-- itself no longer carries input_tokens/output_tokens/cost_usd.
CREATE TABLE IF NOT EXISTS analyses (
    id             INTEGER PRIMARY KEY,
    section_id     INTEGER NOT NULL REFERENCES sections(id),
    prompt_name    TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    model          TEXT NOT NULL,
    -- sha256 of the FULLY RENDERED prompt (every interpolated value --
    -- note text, company, ticker, form type, fiscal period, note name --
    -- included), never just the static template (SPEC-006 R2).
    input_hash     TEXT NOT NULL,
    output_json    TEXT NOT NULL,
    call_id        INTEGER REFERENCES llm_calls(id),
    created_at     TEXT NOT NULL,
    UNIQUE(input_hash)
);

CREATE TABLE IF NOT EXISTS findings (
    id           INTEGER PRIMARY KEY,
    analysis_id  INTEGER NOT NULL REFERENCES analyses(id),
    accession_no TEXT NOT NULL REFERENCES filings(accession_no),
    -- red_flag | accounting_change | litigation | concentration | liquidity | note_item
    category     TEXT NOT NULL,
    severity     TEXT,
    headline     TEXT NOT NULL,
    detail       TEXT,
    quote        TEXT,
    created_at   TEXT NOT NULL
);

-- ============ SPEC-005: OBSERVATIONS ============
-- A layer between CALCULATIONS and INTERPRETATION (ARCHITECTURE.md §2):
-- deterministic, rule-based, verified by Python -- never LLM output.
CREATE TABLE IF NOT EXISTS observations (
    id            INTEGER PRIMARY KEY,
    cik           TEXT NOT NULL REFERENCES companies(cik),
    -- The CURRENT period's filing (SPEC-005 change 7) -- the filing this
    -- observation is FOR. Prior-period rows referenced by a comparison rule
    -- live in refs_json, not here.
    accession_no  TEXT REFERENCES filings(accession_no),
    period_end    TEXT NOT NULL,
    rule_name     TEXT NOT NULL,
    rule_version  TEXT NOT NULL,
    subject       TEXT NOT NULL,
    severity      TEXT NOT NULL,
    statement     TEXT NOT NULL,
    -- JSON list of {"table": "metrics"|"sections", "id": <int>} -- every row
    -- behind this observation, BOTH sides of any comparison (current and
    -- prior). An observation without references is a bug (SPEC-005 R2).
    refs_json     TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    UNIQUE(cik, period_end, rule_name, rule_version, subject)
);

-- ============ SPEC-007: THE GROUNDED BRIEF ============
-- One short narrative per filing, written only from observations and
-- findings already verified (ARCHITECTURE.md §2). References llm_calls
-- (SPEC-006's ledger) for the GENERATOR call only -- the verifier pass is a
-- second real, billed call, recorded in llm_calls under its own prompt_name
-- (config.BRIEF_VERIFIER_PROMPT_NAME) but not linked here by a second FK,
-- matching this table's schema as specified (SPEC-007 R1).
CREATE TABLE IF NOT EXISTS briefs (
    id               INTEGER PRIMARY KEY,
    accession_no     TEXT NOT NULL REFERENCES filings(accession_no),
    cik              TEXT NOT NULL REFERENCES companies(cik),
    prompt_name      TEXT NOT NULL,
    prompt_version   TEXT NOT NULL,
    verifier_version TEXT NOT NULL,
    model            TEXT NOT NULL,
    -- sha256 of (model|prompt_version|verifier_version|rendered_prompt) --
    -- includes verifier_version because a verifier change can change which
    -- sentences survive, so it changes the output (SPEC-007 R1).
    input_hash       TEXT NOT NULL,
    call_id          INTEGER REFERENCES llm_calls(id),
    -- COUNTS only, never the rejected text itself -- R1 warns against
    -- storing rejected SENTENCES ("invites it being displayed by
    -- accident"), which a bare integer cannot be. Needed for R7's drop-rate
    -- check (generator-side and verifier-side, per prompt version):
    -- dropped sentences are never persisted, so without these two counts
    -- validate.py would have no way to recompute a drop rate after the
    -- fact -- unlike findings, where analyses.output_json retains every
    -- returned finding (dropped or kept) for exactly this reason.
    generator_dropped INTEGER NOT NULL DEFAULT 0,
    verifier_dropped   INTEGER NOT NULL DEFAULT 0,
    created_at       TEXT NOT NULL,
    UNIQUE(input_hash)
);

-- Dropped sentences (failed a type check or the verifier pass) are NEVER
-- stored here -- only counted and reported (SPEC-007 R1). Storing rejected
-- output invites it being displayed by accident.
CREATE TABLE IF NOT EXISTS brief_sentences (
    id            INTEGER PRIMARY KEY,
    brief_id      INTEGER NOT NULL REFERENCES briefs(id),
    position      INTEGER NOT NULL,
    -- restatement | juxtaposition | aggregation | grouping | sourced_causal
    sentence_type TEXT NOT NULL,
    text          TEXT NOT NULL,
    -- JSON list of ref strings, e.g. ["obs:1234", "finding:567"] -- every
    -- one already verified to resolve to an observation or finding
    -- belonging to this filing and supplied in this brief's input (R4).
    refs_json     TEXT NOT NULL,
    UNIQUE(brief_id, position)
);
"""

TABLE_NAMES = (
    "companies",
    "filings",
    "sections",
    "xbrl_facts",
    "metrics",
    "llm_calls",
    "analyses",
    "findings",
    "observations",
    "briefs",
    "brief_sentences",
)

# SPEC-005: columns added to tables that already existed (and, for the real
# app.db, already have rows) before this spec. CREATE TABLE IF NOT EXISTS
# above is a no-op against an existing table, so these need an explicit,
# idempotent ALTER TABLE ADD COLUMN -- safe because ADD COLUMN (unlike DROP
# COLUMN, see section_store.py) never touches existing data and works on any
# SQLite version this project supports.
_NEW_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("filings", "fiscal_year", "INTEGER"),
    ("filings", "fiscal_period", "TEXT"),
    ("sections", "normalized_text_hash", "TEXT"),
    ("sections", "word_count", "INTEGER"),
    ("sections", "sentence_count", "INTEGER"),
    ("sections", "complex_word_count", "INTEGER"),
)


def _add_column_if_missing(conn: sqlite3.Connection, table: str, column: str, coltype: str) -> None:
    existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")


def _migrate_analyses_table(conn: sqlite3.Connection) -> None:
    """SPEC-006: analyses gains call_id, drops input_tokens/output_tokens/cost_usd.

    The table has been empty since it was created in SPEC-001 -- nothing has
    called the LLM yet -- so this is a drop-and-recreate, not a real
    column-preserving migration, and does not need SQLite's ALTER TABLE
    DROP COLUMN support (see section_store.py's MIN_SQLITE_VERSION_INFO for
    why that matters elsewhere). If the table is ever non-empty when this
    runs, it refuses rather than silently discarding rows -- a real
    migration would be needed at that point, and this is not it.
    """
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(analyses)")}
    if not existing or "call_id" in existing:
        return  # table doesn't exist yet, or already migrated
    count = conn.execute("SELECT COUNT(*) AS n FROM analyses").fetchone()["n"]
    if count:
        raise RuntimeError(
            f"analyses has {count} row(s) but predates SPEC-006's schema (no call_id) -- "
            "refusing to drop a non-empty table. A real migration is needed here, not this one."
        )
    conn.execute("DROP TABLE analyses")


def get_connection(db_path: Path | None = None) -> sqlite3.Connection:
    """Open a connection with foreign keys enforced.

    Caller owns the connection's lifecycle (close it, or use as a context manager).
    """
    path = db_path if db_path is not None else config.DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: Path | None = None) -> None:
    """Create the schema if missing and seed companies from config.WATCHLIST.

    Idempotent: safe to call any number of times. Existing companies are
    updated in place (never duplicated) if their CIK already exists.
    """
    conn = get_connection(db_path)
    try:
        _migrate_analyses_table(conn)
        conn.executescript(SCHEMA)
        for table, column, coltype in _NEW_COLUMNS:
            _add_column_if_missing(conn, table, column, coltype)
        for company in config.WATCHLIST:
            conn.execute(
                """
                INSERT INTO companies (cik, ticker, name, fiscal_year_end)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(cik) DO UPDATE SET
                    ticker = excluded.ticker,
                    name = excluded.name,
                    fiscal_year_end = excluded.fiscal_year_end
                """,
                (company.cik, company.ticker, company.name, company.fiscal_year_end),
            )
        conn.commit()
    finally:
        conn.close()
