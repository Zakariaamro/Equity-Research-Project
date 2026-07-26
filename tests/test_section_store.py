"""Tests for edgar.section_store (SPEC-003 R3/R4).

`sections_dir` (isolating config.SECTIONS_DIR to a tmp_path) is autouse in
tests/conftest.py -- no test here should ever touch the real data/sections/.
"""

from __future__ import annotations

import gzip
import hashlib
import sqlite3

import pytest

from edgar import config, section_store

ACCESSION = "0001018724-26-000004"


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _make_pre_migration_conn(tmp_path, rows):
    """rows: list of (accession_no, short_name, text, text_hash)."""
    db_path = tmp_path / "premigration.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
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
    for i, (accession_no, short_name, text, text_hash) in enumerate(rows):
        conn.execute(
            """
            INSERT INTO sections (accession_no, category, short_name, source_file, position, text, text_hash)
            VALUES (?, 'Notes', ?, ?, ?, ?, ?)
            """,
            (accession_no, short_name, f"R{i}.htm", i, text, text_hash),
        )
    conn.commit()
    return conn


# --- write_section_text / read_section_text / section_path ---


def test_write_then_read_roundtrip():
    text = "Numbers survive: $7.1 billion, (1,234), 5.6%, en-dash –."
    text_hash = section_store.write_section_text(text)
    assert section_store.read_section_text(text_hash) == text


def test_write_is_idempotent(sections_dir):
    text = "Repeated content, written twice."
    h1 = section_store.write_section_text(text)
    h2 = section_store.write_section_text(text)

    assert h1 == h2
    files = list(sections_dir.rglob("*.txt.gz"))
    assert len(files) == 1


def test_path_sharding():
    text_hash = "ab" + "0" * 62
    path = section_store.section_path(text_hash)

    assert path.parent.name == "ab"
    assert path.name == f"{text_hash}.txt.gz"
    assert path.parent.parent == config.SECTIONS_DIR


def test_read_missing_hash_raises():
    with pytest.raises(section_store.SectionContentMissingError):
        section_store.read_section_text("0" * 64)


def test_write_raises_on_content_mismatch_at_existing_path(sections_dir):
    original = "original content"
    text_hash = section_store.write_section_text(original)
    path = section_store.section_path(text_hash)
    path.write_bytes(gzip.compress(b"tampered content, same path"))

    with pytest.raises(section_store.SectionContentMismatchError):
        section_store.write_section_text(original)


# --- migrate_sections ---


def test_migration_verifies_hashes(tmp_path):
    good_text = "Income Taxes note."
    bad_text = "Segment Information note."
    rows = [
        (ACCESSION, "Income Taxes", good_text, _hash(good_text)),
        (ACCESSION, "Segment Information", bad_text, "0" * 64),  # corrupted stored hash
    ]
    conn = _make_pre_migration_conn(tmp_path, rows)

    with pytest.raises(section_store.MigrationAbortedError) as exc_info:
        section_store.migrate_sections(conn)

    assert ACCESSION in str(exc_info.value)
    assert "Segment Information" in str(exc_info.value)
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(sections)")}
    assert "text" in columns  # column not dropped


def test_migration_aborts_on_empty_text_hash(tmp_path):
    text = "Some note."
    rows = [(ACCESSION, "Income Taxes", text, "")]
    conn = _make_pre_migration_conn(tmp_path, rows)

    with pytest.raises(section_store.MigrationAbortedError) as exc_info:
        section_store.migrate_sections(conn)

    assert "empty text_hash" in str(exc_info.value)


def test_migration_migrates_all_rows(tmp_path, sections_dir):
    text = "Clean note text."
    rows = [(ACCESSION, "Income Taxes", text, _hash(text))]
    conn = _make_pre_migration_conn(tmp_path, rows)

    report = section_store.migrate_sections(conn)

    assert report.rows_migrated == 1
    assert report.files_written == 1
    assert report.files_already_present == 0
    assert report.db_size_before is not None
    assert report.db_size_after is not None

    columns = {row["name"] for row in conn.execute("PRAGMA table_info(sections)")}
    assert "text" not in columns
    stored_hash = conn.execute("SELECT text_hash FROM sections").fetchone()["text_hash"]
    assert section_store.read_section_text(stored_hash) == text


def test_migration_idempotent(tmp_path):
    text = "Clean note text."
    rows = [(ACCESSION, "Income Taxes", text, _hash(text))]
    conn = _make_pre_migration_conn(tmp_path, rows)

    section_store.migrate_sections(conn)
    report2 = section_store.migrate_sections(conn)

    assert report2.already_migrated


def test_migration_dry_run_writes_nothing(tmp_path, sections_dir):
    text = "Clean note text."
    rows = [(ACCESSION, "Income Taxes", text, _hash(text))]
    conn = _make_pre_migration_conn(tmp_path, rows)

    report = section_store.migrate_sections(conn, dry_run=True)

    assert report.dry_run is True
    assert report.rows_migrated == 1
    assert not list(sections_dir.rglob("*.txt.gz"))  # nothing written
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(sections)")}
    assert "text" in columns  # schema untouched
    # still has its original text column intact, nothing consumed
    assert conn.execute("SELECT text FROM sections").fetchone()["text"] == text


def test_migration_dry_run_idempotent_reporting(tmp_path):
    rows = []
    conn = _make_pre_migration_conn(tmp_path, rows)
    section_store.migrate_sections(conn)  # real run, drops the column

    report = section_store.migrate_sections(conn, dry_run=True)

    assert report.already_migrated
