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
    items          TEXT,
    primary_doc    TEXT,
    raw_path       TEXT,
    discovered_at  TEXT NOT NULL,
    status         TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sections (
    id            INTEGER PRIMARY KEY,
    accession_no  TEXT NOT NULL REFERENCES filings(accession_no),
    category      TEXT NOT NULL,
    short_name    TEXT NOT NULL,
    source_file   TEXT,
    position      INTEGER,
    text_hash     TEXT NOT NULL,
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
"""

TABLE_NAMES = (
    "companies",
    "filings",
    "sections",
    "xbrl_facts",
    "metrics",
    "analyses",
    "findings",
)


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
