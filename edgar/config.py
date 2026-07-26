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

EIGHTK_FORM_TYPE: str = "8-K"
TRACKED_FORMS: list[str] = ["10-K", "10-Q", EIGHTK_FORM_TYPE]

EIGHTK_REQUIRED_ITEM: str = "2.02"

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
