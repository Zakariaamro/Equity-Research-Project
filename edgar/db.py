"""Schema creation and connection handling.

Creates the complete seven-table schema from ARCHITECTURE.md §6 up front,
including tables unused until later specs (sections, xbrl_facts, metrics,
analyses, findings) — this avoids migrations mid-project.
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

CREATE TABLE IF NOT EXISTS analyses (
    id             INTEGER PRIMARY KEY,
    section_id     INTEGER NOT NULL REFERENCES sections(id),
    prompt_name    TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    model          TEXT NOT NULL,
    input_hash     TEXT NOT NULL,
    output_json    TEXT NOT NULL,
    input_tokens   INTEGER,
    output_tokens  INTEGER,
    cost_usd       REAL,
    created_at     TEXT NOT NULL,
    UNIQUE(input_hash)
);

CREATE TABLE IF NOT EXISTS findings (
    id           INTEGER PRIMARY KEY,
    analysis_id  INTEGER NOT NULL REFERENCES analyses(id),
    accession_no TEXT NOT NULL REFERENCES filings(accession_no),
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
"""

TABLE_NAMES = (
    "companies",
    "filings",
    "sections",
    "xbrl_facts",
    "metrics",
    "analyses",
    "findings",
    "observations",
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
