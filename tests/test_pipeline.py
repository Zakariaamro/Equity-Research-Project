"""Tests for edgar.pipeline's CLI commands (SPEC-003 R4, SPEC-006A)."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path

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


# --- SPEC-009 P2 follow-up (approved 2026-08-24): scheduled-llm-run's shared ceiling ---


@pytest.fixture
def scheduled_llm_ready_db(tmp_path, sections_dir, monkeypatch):
    """Same filing/section as analyze_ready_db, PLUS an observation on the
    same filing -- makes it a candidate for BOTH stages run_scheduled_llm_
    stages chains, in one small fixture rather than two disconnected ones."""
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
    conn.execute(
        "INSERT INTO observations (cik, accession_no, period_end, rule_name, rule_version, subject, severity, "
        "statement, refs_json, created_at) VALUES (?, 'acc1', '2025-12-31', 'metric_multi_year_extreme', "
        f"'{config.RULE_REGISTRY['metric_multi_year_extreme'].version}', 'gross_margin', 'low', "
        "'gross_margin is unremarkable this period.', '[]', '2026-01-01T00:00:00')",
        (AMZN_CIK,),
    )
    conn.commit()
    conn.close()
    return db_path


def test_run_scheduled_llm_stages_gives_the_second_stage_only_what_remains(monkeypatch):
    # Unit-level: proves the ARITHMETIC and WIRING -- stage 2's own ceiling
    # is exactly what stage 1 left behind, not its own separate $0.50 --
    # without needing a full two-stage LLM fixture for this specific claim.
    monkeypatch.setattr(config, "LLM_SCHEDULED_RUN_MAX_COST_USD", 0.50)

    fake_analysis_stats = analyze.RunStats(dry_run=False)
    fake_analysis_stats.run_cost_usd = 0.30
    monkeypatch.setattr(pipeline.analyze, "run_analysis", lambda conn, **kwargs: fake_analysis_stats)

    captured = {}

    def fake_run_brief_generation(conn, **kwargs):
        captured.update(kwargs)
        stats = pipeline.brief.BriefRunStats(dry_run=False)
        stats.run_cost_usd = 0.0
        return stats

    monkeypatch.setattr(pipeline.brief, "run_brief_generation", fake_run_brief_generation)

    pipeline.run_scheduled_llm_stages(conn=None)

    assert captured["scheduled"] is True
    assert captured["execute"] is True
    assert captured["max_run_cost_usd"] == pytest.approx(0.20)  # 0.50 - 0.30, not a fresh 0.50


def test_run_scheduled_llm_stages_skips_brief_stage_when_ceiling_exhausted(monkeypatch):
    monkeypatch.setattr(config, "LLM_SCHEDULED_RUN_MAX_COST_USD", 0.50)

    fake_analysis_stats = analyze.RunStats(dry_run=False)
    fake_analysis_stats.run_cost_usd = 0.50  # the whole combined ceiling, spent by stage 1 alone
    monkeypatch.setattr(pipeline.analyze, "run_analysis", lambda conn, **kwargs: fake_analysis_stats)

    captured = {}

    def fake_run_brief_generation(conn, **kwargs):
        captured.update(kwargs)
        return pipeline.brief.BriefRunStats(dry_run=not kwargs.get("execute", False))

    monkeypatch.setattr(pipeline.brief, "run_brief_generation", fake_run_brief_generation)

    analysis_stats, brief_stats = pipeline.run_scheduled_llm_stages(conn=None)

    assert captured.get("execute") in (False, None)  # never asked to spend -- dry run only, or not called with execute=True
    assert brief_stats.stopped_reason is not None
    assert "no budget left" in brief_stats.stopped_reason


def test_run_scheduled_llm_stages_end_to_end_real_functions_stay_under_the_combined_ceiling(
    scheduled_llm_ready_db, monkeypatch,
):
    # Integration-level: the two REAL functions (not monkeypatched), fake
    # LLM clients only, proving the composition actually works -- catches
    # any signature/wiring mismatch the arg-capturing unit tests above
    # would miss.
    monkeypatch.setattr(config, "LLM_MAX_OUTPUT_TOKENS", 200)
    monkeypatch.setattr(config, "LLM_SCHEDULED_RUN_MAX_COST_USD", 1000.0)  # generous -- both stages should complete

    conn = db.get_connection(scheduled_llm_ready_db)
    empty_section_response = (Path(__file__).parent / "fixtures" / "llm_empty_response.json").read_text()
    empty_brief_response = json.dumps({"material": False, "sentences": []})

    class _FakeRawClient:
        def __init__(self, responses):
            self._responses = list(responses)

        def messages_create(self, model, max_tokens, prompt):
            return self._responses.pop(0), 100, 20, "end_turn"

    fake_client = pipeline.llm.LLMClient(
        raw_client=_FakeRawClient([empty_section_response, empty_brief_response])
    )

    analysis_stats, brief_stats = pipeline.run_scheduled_llm_stages(conn, client=fake_client)
    conn.close()

    assert analysis_stats.calls_made == 1
    assert brief_stats.calls_made == 1
    combined = analysis_stats.run_cost_usd + brief_stats.run_cost_usd
    assert combined <= config.LLM_SCHEDULED_RUN_MAX_COST_USD + 1e-9


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
