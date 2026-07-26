from __future__ import annotations

import json
from pathlib import Path

import pytest

from edgar import config

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def sec_user_agent_env(monkeypatch):
    """SEC_USER_AGENT is set by default; tests for the unset case delete it explicitly."""
    monkeypatch.setenv("SEC_USER_AGENT", "Test Agent test@example.com")


@pytest.fixture(autouse=True)
def sections_dir(tmp_path, monkeypatch):
    """Isolate every test from the real data/sections/ content store (SPEC-003)."""
    d = tmp_path / "sections"
    monkeypatch.setattr(config, "SECTIONS_DIR", d)
    return d


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES_DIR / name).read_text())
