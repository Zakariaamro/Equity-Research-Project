"""Tests for edgar.pipeline's migrate-sections CLI command (SPEC-003 R4)."""

from __future__ import annotations

import argparse
import hashlib
import sqlite3

import pytest

from edgar import config, pipeline

ACCESSION = "0001018724-26-000004"


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@pytest.fixture
def pre_migration_db(tmp_path, sections_dir, monkeypatch):
    db_path = tmp_path / "app.db"
    monkeypatch.setattr(config, "DB_PATH", db_path)

    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE sections (
            id INTEGER PRIMARY KEY,
            accession_no TEXT NOT NULL,
            category TEXT NOT NULL,
            short_name TEXT NOT NULL,
            source_file TEXT,
            position INTEGER,
            text TEXT NOT NULL,
            text_hash TEXT NOT NULL,
            UNIQUE(accession_no, category, short_name, source_file)
        )
        """
    )
    text = "Income Taxes note text with a $7.1 billion figure."
    conn.execute(
        """
        INSERT INTO sections (accession_no, category, short_name, source_file, position, text, text_hash)
        VALUES (?, 'Notes', 'Income Taxes', 'R18.htm', 1, ?, ?)
        """,
        (ACCESSION, text, _hash(text)),
    )
    conn.commit()
    conn.close()
    return db_path, text


def test_migrate_sections_backs_up_before_dropping(pre_migration_db):
    db_path, text = pre_migration_db
    backup_path = db_path.with_name(db_path.name + config.DB_BACKUP_SUFFIX)
    assert not backup_path.exists()

    pipeline.cmd_migrate_sections(argparse.Namespace(dry_run=False))

    assert backup_path.exists()

    backup_conn = sqlite3.connect(backup_path)
    backup_columns = {row[1] for row in backup_conn.execute("PRAGMA table_info(sections)")}
    assert "text" in backup_columns  # backup preserves pre-migration schema
    backup_conn.close()

    conn = sqlite3.connect(db_path)
    live_columns = {row[1] for row in conn.execute("PRAGMA table_info(sections)")}
    assert "text" not in live_columns  # live db had the column dropped
    conn.close()


def test_migrate_sections_second_real_run_does_not_clobber_backup(pre_migration_db):
    """Regression: an earlier version backed up unconditionally, so a second
    no-op real run silently overwrote the one useful pre-migration backup
    with a copy of the already-migrated (text-less) db."""
    db_path, text = pre_migration_db
    backup_path = db_path.with_name(db_path.name + config.DB_BACKUP_SUFFIX)

    pipeline.cmd_migrate_sections(argparse.Namespace(dry_run=False))
    backup_mtime_after_first_run = backup_path.stat().st_mtime
    backup_conn = sqlite3.connect(backup_path)
    assert "text" in {row[1] for row in backup_conn.execute("PRAGMA table_info(sections)")}
    backup_conn.close()

    pipeline.cmd_migrate_sections(argparse.Namespace(dry_run=False))  # no-op, already migrated

    assert backup_path.stat().st_mtime == backup_mtime_after_first_run  # untouched
    backup_conn = sqlite3.connect(backup_path)
    assert "text" in {row[1] for row in backup_conn.execute("PRAGMA table_info(sections)")}
    backup_conn.close()


def test_migrate_sections_dry_run_creates_no_backup(pre_migration_db):
    db_path, _ = pre_migration_db
    backup_path = db_path.with_name(db_path.name + config.DB_BACKUP_SUFFIX)

    pipeline.cmd_migrate_sections(argparse.Namespace(dry_run=True))

    assert not backup_path.exists()
    conn = sqlite3.connect(db_path)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(sections)")}
    assert "text" in columns  # untouched
    conn.close()
