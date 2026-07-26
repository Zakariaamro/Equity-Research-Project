# Equity Research Platform

Automated SEC filing monitor, ingestion, and (in later specs) analysis pipeline.
See `ARCHITECTURE.md` for design and `SPEC-001-foundation-and-ingestion.md` for
the current build spec.

## Setup

Requires Python 3.11+.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Set the SEC user agent (required — SEC blocks anonymous requests):

```bash
export SEC_USER_AGENT="Your Name your.email@example.com"
```

## Usage

```bash
# Create the database and seed the watchlist
python -m edgar.pipeline init-db

# Discover and archive new filings for the whole watchlist
python -m edgar.pipeline discover

# Restrict to one company, or preview without downloading
python -m edgar.pipeline discover --ticker AMZN
python -m edgar.pipeline discover --dry-run

# Summarize what's in the database
python -m edgar.pipeline status
```

Raw filings are archived gzipped under `data/raw/`. The SQLite database lives
at `data/app.db`. Both are committed to the repo deliberately — see
`ARCHITECTURE.md` §4.

## Tests

```bash
pytest
```

Unit tests run entirely against fixtures in `tests/fixtures/` — no network
access is required or performed during `pytest`.
