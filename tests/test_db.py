from __future__ import annotations

import sqlite3

import pytest

from edgar import config, db


def test_db_init_idempotent(tmp_path):
    db_path = tmp_path / "test.db"
    db.init_db(db_path)
    db.init_db(db_path)

    conn = db.get_connection(db_path)
    try:
        rows = conn.execute("SELECT cik FROM companies").fetchall()
        assert len(rows) == len(config.WATCHLIST)
    finally:
        conn.close()


def test_db_has_all_tables(tmp_path):
    db_path = tmp_path / "test.db"
    db.init_db(db_path)

    conn = db.get_connection(db_path)
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
        names = {row["name"] for row in rows}
        assert names == set(db.TABLE_NAMES)
        assert len(names) == len(db.TABLE_NAMES)
    finally:
        conn.close()


def test_init_db_seeds_without_duplicating_on_conflict(tmp_path):
    db_path = tmp_path / "test.db"
    db.init_db(db_path)
    db.init_db(db_path)  # seeds again -- must update, not duplicate

    conn = db.get_connection(db_path)
    try:
        rows = conn.execute(
            "SELECT cik, ticker, name, fiscal_year_end FROM companies ORDER BY cik"
        ).fetchall()
        assert len(rows) == len(config.WATCHLIST)
        by_cik = {row["cik"]: row for row in rows}
        for company in config.WATCHLIST:
            assert by_cik[company.cik]["ticker"] == company.ticker
            assert by_cik[company.cik]["name"] == company.name
            assert by_cik[company.cik]["fiscal_year_end"] == company.fiscal_year_end
    finally:
        conn.close()


def test_foreign_keys_enforced(tmp_path):
    db_path = tmp_path / "test.db"
    db.init_db(db_path)

    conn = db.get_connection(db_path)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO filings (
                    accession_no, cik, form_type, filing_date, discovered_at, status
                ) VALUES ('0000000000-00-000000', '9999999999', '10-K',
                          '2026-01-01', '2026-01-01T00:00:00', 'discovered')
                """
            )
    finally:
        conn.close()
