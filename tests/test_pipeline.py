"""Tests for edgar.pipeline's CLI commands (SPEC-003 R4, SPEC-006A)."""

from __future__ import annotations

import argparse
import hashlib
import sqlite3

import pytest

from edgar import analyze, config, db, pipeline, section_store

ACCESSION = "0001018724-26-000004"
AMZN_CIK = "0001018724"

NOTE_TEXT = (
    "Income Taxes. In 2025, we recorded a net tax provision of $19.1 billion. The 2025 "
    "Tax Act increased our income tax provision, primarily due to a decrease in the "
    "foreign income deduction, and significantly decreased our cash taxes."
)


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


# --- SPEC-006A L4: --confirm-cost gate ---


@pytest.fixture
def analyze_ready_db(tmp_path, sections_dir, monkeypatch):
    db_path = tmp_path / "app.db"
    monkeypatch.setattr(config, "DB_PATH", db_path)
    db.init_db(db_path)

    conn = db.get_connection(db_path)
    conn.execute(
        "INSERT INTO filings (accession_no, cik, form_type, filing_date, period_end, fiscal_year, fiscal_period, "
        "discovered_at, status) VALUES ('acc1', ?, '10-K', '2026-02-06', '2025-12-31', 2025, 'FY', "
        "'2026-02-06T00:00:00', 'sectioned')",
        (AMZN_CIK,),
    )
    text_hash = section_store.write_section_text(NOTE_TEXT)
    conn.execute(
        "INSERT INTO sections (accession_no, category, short_name, source_file, position, text_hash) "
        "VALUES ('acc1', 'Notes', 'Income Taxes', 'R1.htm', 1, ?)",
        (text_hash,),
    )
    conn.commit()
    conn.close()
    return db_path


def _analyze_ns(**overrides) -> argparse.Namespace:
    base = dict(
        ticker="AMZN", accession=None, sample=None, seed=None, limit=None,
        execute=True, confirm_cost=None, acknowledge_cache_invalidation=False,
        max_run_cost=None, max_calls=None, scheduled=False,
    )
    base.update(overrides)
    return argparse.Namespace(**base)


def test_confirm_cost_required_above_threshold(analyze_ready_db, monkeypatch):
    # Any positive estimate exceeds a threshold of 0.0 -- isolates the "is
    # --confirm-cost required at all" behaviour from needing a huge note.
    monkeypatch.setattr(config, "LLM_CONFIRM_THRESHOLD_USD", 0.0)

    with pytest.raises(SystemExit):
        pipeline.cmd_analyze_sections(_analyze_ns())  # no --confirm-cost given

    conn = db.get_connection(analyze_ready_db)
    refused = conn.execute("SELECT * FROM llm_calls WHERE status = 'refused'").fetchall()
    conn.close()
    assert len(refused) == 1
    assert "L4" in refused[0]["note"] and "confirm-cost" in refused[0]["note"]


def test_confirm_cost_rejects_mismatched_figure(analyze_ready_db, monkeypatch):
    monkeypatch.setattr(config, "LLM_CONFIRM_THRESHOLD_USD", 0.0)

    conn = db.get_connection(analyze_ready_db)
    dry_stats = analyze.run_analysis(conn, tickers=["AMZN"], execute=False)
    conn.close()
    wrong_value = dry_stats.estimated_cost_usd + 5.0  # far outside any reasonable tolerance

    with pytest.raises(SystemExit):
        pipeline.cmd_analyze_sections(_analyze_ns(confirm_cost=wrong_value))

    conn = db.get_connection(analyze_ready_db)
    refused = conn.execute("SELECT * FROM llm_calls WHERE status = 'refused'").fetchall()
    conn.close()
    assert len(refused) == 1
    assert "L4" in refused[0]["note"] and "does not match" in refused[0]["note"]


def test_confirm_cost_matching_figure_proceeds(analyze_ready_db, monkeypatch):
    """The converse of the two refusal tests: a correctly-read-and-typed
    --confirm-cost lets an otherwise-refusing-nothing run actually execute."""
    monkeypatch.setattr(config, "LLM_CONFIRM_THRESHOLD_USD", 0.0)
    monkeypatch.setenv("EQUITY_RESEARCH_ANTHROPIC_API_KEY", "sk-ant-fake-for-test")

    conn = db.get_connection(analyze_ready_db)
    dry_stats = analyze.run_analysis(conn, tickers=["AMZN"], execute=False)
    conn.close()

    class _FakeRawClient:
        def messages_create(self, model, max_tokens, prompt):
            return "not valid json", 100, 20, "end_turn"  # invalid, but still a real (billed) attempt

    fake_client = pipeline.llm.LLMClient(raw_client=_FakeRawClient())
    monkeypatch.setattr(pipeline.llm, "LLMClient", lambda: fake_client)

    pipeline.cmd_analyze_sections(_analyze_ns(confirm_cost=dry_stats.estimated_cost_usd))

    conn = db.get_connection(analyze_ready_db)
    # Reached the real execute path (real, if invalid-JSON, calls were made) --
    # proof the confirmation gate let a correctly-confirmed run through. Two
    # rows, not one: the fake always returns invalid JSON, so both the
    # initial attempt and its one retry are real (billed) attempts, each
    # with its own ledger row (2026-07-28 live-error-analysis fix).
    made_calls = conn.execute("SELECT * FROM llm_calls WHERE status != 'refused'").fetchall()
    conn.close()
    assert len(made_calls) == 2
