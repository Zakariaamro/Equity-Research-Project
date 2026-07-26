"""All configuration and constants for the edgar pipeline.

Per ARCHITECTURE.md §5: this is the only module permitted to contain SEC
URLs, CIKs, form-type literals, or numeric limits. Later specs will extend
this file with a note allow-list, concept aliases, and model names as those
modules (sections.py, xbrl.py/metrics.py, llm.py) are built — they are not
added yet because nothing uses them.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Company:
    ticker: str
    cik: str  # zero-padded 10 digits
    name: str
    fiscal_year_end: str  # 'MMDD'


# Adding a company is a one-line addition to this list.
WATCHLIST: list[Company] = [
    Company(ticker="AMZN", cik="0001018724", name="Amazon.com, Inc.", fiscal_year_end="1231"),
    Company(ticker="NVDA", cik="0001045810", name="NVIDIA Corporation", fiscal_year_end="0131"),
    Company(ticker="MU", cik="0000723125", name="Micron Technology, Inc.", fiscal_year_end="0903"),
]

TENK_FORM_TYPE: str = "10-K"
TENQ_FORM_TYPE: str = "10-Q"
EIGHTK_FORM_TYPE: str = "8-K"
TRACKED_FORMS: list[str] = [TENK_FORM_TYPE, TENQ_FORM_TYPE, EIGHTK_FORM_TYPE]

EIGHTK_REQUIRED_ITEM: str = "2.02"

# Section extraction (SPEC-002) covers these form types only; MD&A/Risk
# Factors/8-K exhibit extraction is SPEC-003.
SECTION_EXTRACTABLE_FORM_TYPES: list[str] = [TENK_FORM_TYPE, TENQ_FORM_TYPE]

# SEC Fair Access policy requires an identifying User-Agent on every request.
# Format: "Name email@example.com". Read from the environment so no personal
# contact info lives in source control.
_SEC_USER_AGENT_ENV_VAR = "SEC_USER_AGENT"


def get_sec_user_agent() -> str:
    value = os.environ.get(_SEC_USER_AGENT_ENV_VAR)
    if not value:
        raise RuntimeError(
            f"{_SEC_USER_AGENT_ENV_VAR} environment variable is not set. "
            "SEC blocks anonymous requests; set it to 'Your Name your.email@example.com' "
            "before running anything that talks to sec.gov."
        )
    return value


SEC_RATE_LIMIT_PER_SEC: int = 8
HTTP_MAX_RETRIES: int = 3
HTTP_BACKOFF_BASE_SECONDS: float = 1.0
HTTP_BACKOFF_MAX_SECONDS: float = 30.0
HTTP_TIMEOUT_SECONDS: float = 30.0

# Paths
PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent
DATA_DIR: Path = PROJECT_ROOT / "data"
DB_PATH: Path = DATA_DIR / "app.db"
RAW_ARCHIVE_DIR: Path = DATA_DIR / "raw"
SECTIONS_DIR: Path = DATA_DIR / "sections"

# --- SPEC-002: manifests and section extraction ---

FILING_SUMMARY_FILENAME: str = "FilingSummary.xml"
MANIFEST_FILENAME: str = "manifest.json"
FILING_INDEX_HTML_SUFFIX: str = "-index.html"

# FilingSummary.xml MenuCategory values.
MENUCATEGORY_COVER: str = "Cover"
MENUCATEGORY_STATEMENTS: str = "Statements"
MENUCATEGORY_NOTES: str = "Notes"
MENUCATEGORY_POLICIES: str = "Policies"
MENUCATEGORY_TABLES: str = "Tables"
MENUCATEGORY_DETAILS: str = "Details"

# Reports in these categories are extracted to `sections`; everything else
# (Cover, Tables, Details) is skipped -- see SPEC-002 R2.
SECTION_MENUCATEGORIES: list[str] = [
    MENUCATEGORY_STATEMENTS,
    MENUCATEGORY_NOTES,
    MENUCATEGORY_POLICIES,
]

# SEC-declared document type (from the filing index's Document Format Files
# table) identifying an earnings press release exhibit -- see ARCHITECTURE.md §3.6.
EXHIBIT_991_TYPE: str = "EX-99.1"

# --- SPEC-003: content-addressed section storage ---

# ALTER TABLE ... DROP COLUMN requires this SQLite version or newer.
MIN_SQLITE_VERSION_INFO: tuple[int, int, int] = (3, 35, 0)

SECTION_STORE_SUFFIX: str = ".txt.gz"
DB_BACKUP_SUFFIX: str = ".pre-migration.bak"
