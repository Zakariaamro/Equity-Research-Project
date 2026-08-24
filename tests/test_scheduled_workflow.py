"""SPEC-009 Part A: the scheduled-ingestion GitHub Actions workflow is
config, not code -- nothing here can run it for real (no network, no
GitHub Actions runner), but its CONTENT can be checked the same way
test_dashboard_structure.py checks architectural rules by reading source
text directly: real CLI commands, binding constraints from the spec
honoured structurally (no --ticker on compute-observations, no --force
anywhere, no migrate-sections/backfill-manifests, validate before the
commit step), and the two required secrets present without the one
forbidden name."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from edgar import pipeline

WORKFLOW_PATH = Path(__file__).parent.parent / ".github" / "workflows" / "scheduled-ingestion.yml"

_RUN_COMMAND_RE = re.compile(r"python -m edgar\.pipeline\s+([a-z-]+)")


def _load_workflow() -> dict:
    return yaml.safe_load(WORKFLOW_PATH.read_text())


def _all_run_blocks(workflow: dict) -> list[str]:
    return [step["run"] for step in workflow["jobs"]["ingest"]["steps"] if "run" in step]


def _registered_subcommands() -> set[str]:
    parser = pipeline.build_parser()
    sub_action = next(a for a in parser._subparsers._group_actions if hasattr(a, "choices"))
    return set(sub_action.choices.keys())


def test_workflow_file_is_valid_yaml_with_the_expected_job():
    workflow = _load_workflow()
    assert "ingest" in workflow["jobs"]
    assert workflow["jobs"]["ingest"]["steps"]


def test_every_referenced_pipeline_command_is_a_real_registered_subcommand():
    # Protects against a future CLI rename silently breaking the schedule --
    # this would only otherwise be discovered when a real run fails.
    workflow = _load_workflow()
    run_blocks = "\n".join(_all_run_blocks(workflow))
    referenced = set(_RUN_COMMAND_RE.findall(run_blocks))
    assert referenced, "no `python -m edgar.pipeline <command>` calls found in the workflow at all"
    registered = _registered_subcommands()
    unknown = referenced - registered
    assert not unknown, f"workflow references pipeline command(s) that don't exist: {unknown}"


def test_compute_observations_is_never_scoped_by_ticker():
    # Binding constraint (SPEC-009 Part A): scoping this by --ticker
    # silently breaks cross-company same-day observation annotation.
    workflow = _load_workflow()
    for step in workflow["jobs"]["ingest"]["steps"]:
        run = step.get("run", "")
        if "compute-observations" in run:
            assert "--ticker" not in run, f"compute-observations step must never pass --ticker: {run!r}"


def test_no_step_ever_passes_force_or_sample():
    # Binding constraint: never invoke a backfill or --force path, and
    # (SPEC-006A L7) a scheduled run must never use --sample.
    workflow = _load_workflow()
    for step in workflow["jobs"]["ingest"]["steps"]:
        run = step.get("run", "")
        assert "--force" not in run, f"{step.get('name')!r} step must never pass --force: {run!r}"
        assert "--sample" not in run, f"{step.get('name')!r} step must never pass --sample: {run!r}"


def test_one_time_migration_commands_are_never_invoked():
    # migrate-sections/backfill-manifests are one-time, historical-
    # reprocessing commands -- not part of steady-state scheduled
    # ingestion, unlike backfill-readability (idempotent, incremental,
    # explicitly allowed -- checked by name below, not just absent).
    workflow = _load_workflow()
    run_blocks = "\n".join(_all_run_blocks(workflow))
    referenced = set(_RUN_COMMAND_RE.findall(run_blocks))
    assert "migrate-sections" not in referenced
    assert "backfill-manifests" not in referenced
    assert "backfill-readability" in referenced  # the allowed, incremental one


def test_validate_runs_as_a_step_before_the_commit_step():
    # Binding constraint: validate runs before the commit, not after.
    workflow = _load_workflow()
    step_names = [step.get("name", "") for step in workflow["jobs"]["ingest"]["steps"]]
    validate_positions = [i for i, name in enumerate(step_names) if name == "Validate"]
    commit_positions = [i for i, name in enumerate(step_names) if "commit" in name.lower()]
    assert validate_positions, "no step named exactly 'Validate' found"
    assert commit_positions, "no commit step found"
    assert validate_positions[0] < commit_positions[0]


def test_scheduled_llm_run_used_instead_of_two_separate_uncoordinated_calls():
    # SPEC-009 P2 follow-up: analyze-sections and generate-briefs must
    # share ONE combined ceiling, not get $0.50 each independently --
    # scheduled-llm-run is the only command that does that. Neither of the
    # two individual commands may appear standalone alongside it.
    workflow = _load_workflow()
    run_blocks = "\n".join(_all_run_blocks(workflow))
    assert "scheduled-llm-run" in run_blocks
    assert "generate-briefs" not in run_blocks
    assert not re.search(r"pipeline analyze-sections\b", run_blocks)


def test_required_secrets_present_and_forbidden_name_absent():
    # SPEC-009 P3: both required secrets present; the SDK-default name
    # that caused SPEC-006A's founding incident is never actually USED as
    # a secret/env reference (a comment explaining why it's avoided, by
    # name, is fine -- config.py's own comments do the same thing).
    text = WORKFLOW_PATH.read_text()
    assert "secrets.SEC_USER_AGENT" in text
    assert "secrets.EQUITY_RESEARCH_ANTHROPIC_API_KEY" in text
    assert "secrets.ANTHROPIC_API_KEY" not in text
    assert not re.search(r"^\s*ANTHROPIC_API_KEY\s*:", text, re.MULTILINE)


def test_commit_step_touches_only_data_app_db_and_sections():
    workflow = _load_workflow()
    commit_step = next(s for s in workflow["jobs"]["ingest"]["steps"] if "commit" in s.get("name", "").lower())
    run = commit_step["run"]
    assert "git add data/app.db data/sections" in run
    assert "data/raw" not in run


def test_no_op_run_is_silent_and_failure_relies_on_github_default_notification():
    # Decision 2's own resolution: no separate notification mechanism was
    # built for either case -- confirmed structurally, not just claimed:
    # no notification/webhook/email step of any kind appears anywhere.
    text = WORKFLOW_PATH.read_text().lower()
    for marker in ("slack", "webhook", "smtp", "sendgrid", "twilio", "discord"):
        assert marker not in text


def test_triggers_include_both_schedule_and_manual_dispatch():
    workflow = _load_workflow()
    triggers = workflow.get("on") or workflow.get(True)  # PyYAML may parse bare `on:` as boolean True
    assert "schedule" in triggers
    assert "workflow_dispatch" in triggers
    assert triggers["schedule"][0]["cron"] == "0 6 * * *"


def test_job_has_write_permission_and_never_cancels_an_in_progress_run():
    workflow = _load_workflow()
    assert workflow["permissions"]["contents"] == "write"
    assert workflow["concurrency"]["cancel-in-progress"] is False
