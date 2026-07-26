"""Content-addressed storage for section text (SPEC-003).

Text lives at data/sections/{hash[:2]}/{hash}.txt.gz, gzipped UTF-8, keyed by
the sha256 hash `sections.py` already computes. Nothing outside this module
may construct a section path (SPEC-003 R3).
"""

from __future__ import annotations

import gzip
import hashlib
import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from edgar import config


class SectionContentMissingError(Exception):
    """A row's text_hash has no corresponding file on disk. Corruption, not an empty section."""


class SectionContentMismatchError(Exception):
    """A hash's file already exists but its decompressed content differs. Never overwritten."""


class SqliteTooOldError(Exception):
    """The running SQLite predates ALTER TABLE ... DROP COLUMN support."""


class MigrationAbortedError(Exception):
    """Hash verification failed during migration. No file was skipped and no column was dropped."""


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def section_path(text_hash: str) -> Path:
    return config.SECTIONS_DIR / text_hash[:2] / f"{text_hash}{config.SECTION_STORE_SUFFIX}"


def read_section_text(text_hash: str) -> str:
    path = section_path(text_hash)
    if not path.exists():
        raise SectionContentMissingError(f"No section content on disk for hash {text_hash!r} ({path})")
    return gzip.decompress(path.read_bytes()).decode("utf-8")


def write_section_text(text: str) -> str:
    """Write gzipped text to its content-addressed path if absent. Returns the hash.

    Files are immutable. If the path already exists, its content is verified to
    match before skipping the write; a mismatch raises rather than overwriting.
    """
    text_hash = _hash_text(text)
    path = section_path(text_hash)

    if path.exists():
        existing = read_section_text(text_hash)
        if existing != text:
            raise SectionContentMismatchError(
                f"{path} exists with content that does not match hash {text_hash!r}"
            )
        return text_hash

    path.parent.mkdir(parents=True, exist_ok=True)
    payload = gzip.compress(text.encode("utf-8"))
    tmp_path = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp_path.write_bytes(payload)
    os.replace(tmp_path, path)  # atomic on the same filesystem
    return text_hash


# --- Migration (SPEC-003 R4) ---


@dataclass(frozen=True)
class MigrationReport:
    dry_run: bool
    already_migrated: bool
    rows_migrated: int = 0
    files_written: int = 0
    files_already_present: int = 0
    db_size_before: int | None = None
    db_size_after: int | None = None

    def summary(self) -> str:
        if self.already_migrated:
            return "sections.text already dropped -- nothing to do."
        lines = [
            f"{'[dry-run] ' if self.dry_run else ''}rows verified: {self.rows_migrated}",
            f"files written: {self.files_written}",
            f"files already present: {self.files_already_present}",
        ]
        if self.db_size_before is not None:
            lines.append(f"app.db before: {self.db_size_before:,} bytes")
        if self.db_size_after is not None:
            lines.append(f"app.db after: {self.db_size_after:,} bytes")
        return "\n".join(lines)


def _check_sqlite_version() -> None:
    if sqlite3.sqlite_version_info < config.MIN_SQLITE_VERSION_INFO:
        required = ".".join(str(p) for p in config.MIN_SQLITE_VERSION_INFO)
        raise SqliteTooOldError(
            f"SQLite {sqlite3.sqlite_version} does not support ALTER TABLE ... DROP COLUMN "
            f"(requires {required}+). Upgrade the Python/SQLite runtime before migrating."
        )


def _has_text_column(conn: sqlite3.Connection) -> bool:
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(sections)")}
    return "text" in columns


def needs_migration(conn: sqlite3.Connection) -> bool:
    """True if sections.text is still present and migrate_sections() has work to do.

    Callers use this to decide whether a backup is worth taking before running
    the real migration -- there is nothing to protect against once the column
    is already gone.
    """
    return _has_text_column(conn)


def _db_file_path(conn: sqlite3.Connection) -> Path:
    for row in conn.execute("PRAGMA database_list"):
        if row["name"] == "main":
            return Path(row["file"])
    raise RuntimeError("Could not resolve the main database file path")


def migrate_sections(conn: sqlite3.Connection, dry_run: bool = False) -> MigrationReport:
    """Move every sections.text value to the content-addressed store, then drop the column.

    Idempotent: if `text` is already gone, returns immediately with a no-op report.
    Verify-before-drop: every row's text is hashed and compared to its stored
    text_hash before anything about the schema changes. Any mismatch raises
    MigrationAbortedError naming every offending (accession_no, short_name) --
    the column is never dropped partially.
    """
    _check_sqlite_version()

    if not _has_text_column(conn):
        return MigrationReport(dry_run=dry_run, already_migrated=True)

    rows = conn.execute("SELECT accession_no, short_name, text, text_hash FROM sections").fetchall()

    mismatches: list[str] = []
    files_written = 0
    files_already_present = 0

    for row in rows:
        text = row["text"]
        stored_hash = row["text_hash"]
        if not stored_hash:
            mismatches.append(f"{row['accession_no']} / {row['short_name']}: empty text_hash")
            continue

        computed_hash = _hash_text(text)
        if computed_hash != stored_hash:
            mismatches.append(
                f"{row['accession_no']} / {row['short_name']}: "
                f"stored text_hash={stored_hash!r} does not match recomputed={computed_hash!r}"
            )
            continue

        existed_before = section_path(stored_hash).exists()
        if not dry_run:
            write_section_text(text)
        if existed_before:
            files_already_present += 1
        else:
            files_written += 1

    if mismatches:
        raise MigrationAbortedError(
            "Migration aborted -- hash verification failed for "
            f"{len(mismatches)} row(s), no column was dropped:\n" + "\n".join(mismatches)
        )

    if dry_run:
        return MigrationReport(
            dry_run=True,
            already_migrated=False,
            rows_migrated=len(rows),
            files_written=files_written,
            files_already_present=files_already_present,
        )

    db_path = _db_file_path(conn)
    db_size_before = db_path.stat().st_size

    conn.commit()
    conn.execute("ALTER TABLE sections DROP COLUMN text")
    conn.commit()
    conn.execute("VACUUM")

    db_size_after = db_path.stat().st_size

    return MigrationReport(
        dry_run=False,
        already_migrated=False,
        rows_migrated=len(rows),
        files_written=files_written,
        files_already_present=files_already_present,
        db_size_before=db_size_before,
        db_size_after=db_size_after,
    )
