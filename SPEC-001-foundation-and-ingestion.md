# SPEC-001 — Project Foundation, Filing Discovery, and Raw Storage

**Version:** 1.1
**For:** Claude Code
**Depends on:** nothing (first spec)
**Reference:** `ARCHITECTURE.md` v1.1 — sections 3, 4.1, 5, 6
**Estimated effort:** 5–7 hours

**Changelog — v1.1 resolves review flags raised against v1.0:**
- Table count corrected: **seven** tables, not eight. v1.0 was wrong.
- Watchlist expanded to AMZN / NVDA / MU with verified CIKs.
- 8-K archive scope now includes Exhibit 99.1 (R6).
- 8-K item fallback vs exclusion disambiguated (R5).
- Retry/backoff parameters specified (R2, R4).
- Rate limiter must take an injectable time source (R4).

---

## Objective

Stand up the project skeleton, the database, and the ingestion front end: given a
watchlist of companies, discover SEC filings we have not seen before, download and
permanently archive them, and record them in the database.

On completion the operator must be able to run one command and see recent 10-K, 10-Q
and Item 2.02 8-K filings for all three watchlist companies, discovered and stored.

No parsing, no XBRL, no AI in this spec.

---

## Scope

**In scope**

1. Python project skeleton with dependency management
2. `config.py` — all configuration and constants
3. `db.py` — full schema from `ARCHITECTURE.md` §6, created idempotently
4. `edgar_client.py` — the only module permitted to make HTTP requests to sec.gov
5. `monitor.py` — discover filings not yet in the database
6. `fetch.py` — download and archive raw filings per `ARCHITECTURE.md` §4.1
7. `pipeline.py` — CLI entry point
8. Tests for the above

**Out of scope** — do not implement, do not stub beyond empty module files:
section extraction, XBRL ingestion, metric calculation, any LLM call, the dashboard,
GitHub Actions workflows.

---

## Requirements

### R1 — Project skeleton

- Package directory `edgar/` with the module files listed in `ARCHITECTURE.md` §5.
  Modules outside this spec's scope may be created empty.
- Dependencies declared in `pyproject.toml`. Prefer the standard library; justify each
  third-party dependency in a comment.
- `data/raw/` created at runtime if missing.
- `.gitignore` excludes `.env`, `__pycache__/`, and virtualenv directories.
  It must **not** exclude `data/` — the database and archives are committed deliberately.
- `README.md` with setup and run instructions.

### R2 — Configuration (`config.py`)

Must contain, and be the only place that contains:

- `WATCHLIST`, structured so adding a company is a one-line change:

  | Ticker | CIK | Name | Fiscal year end |
  |---|---|---|---|
  | AMZN | `0001018724` | Amazon.com, Inc. | `1231` |
  | NVDA | `0001045810` | NVIDIA Corporation | `0131` |
  | MU | `0000723125` | Micron Technology, Inc. | `0903` |

- `TRACKED_FORMS`: `["10-K", "10-Q", "8-K"]`
- `EIGHTK_REQUIRED_ITEM`: `"2.02"`
- `SEC_USER_AGENT`: read from environment; clear error if unset
- `SEC_RATE_LIMIT_PER_SEC`: `8`
- `HTTP_MAX_RETRIES`: `3`
- `HTTP_BACKOFF_BASE_SECONDS`: `1.0`
- `HTTP_BACKOFF_MAX_SECONDS`: `30.0`
- `HTTP_TIMEOUT_SECONDS`: `30.0`
- Paths for the database and raw archive directory

Do **not** add configuration for later specs — no note allow-list, concept aliases, or
model names yet. They arrive with the specs that need them.

No literal URLs, form names, CIKs, or numeric limits anywhere else in the codebase.

### R3 — Database (`db.py`)

- Creates the complete **seven-table** schema in `ARCHITECTURE.md` §6: `companies`,
  `filings`, `sections`, `xbrl_facts`, `metrics`, `analyses`, `findings`. Tables unused
  until later specs are still created now, to avoid migrations mid-project.
- `CREATE TABLE IF NOT EXISTS` — running twice must be safe.
- Foreign keys enforced (`PRAGMA foreign_keys = ON`).
- Exposes a connection helper and an `init_db()` function.
- Seeds `companies` from `config.WATCHLIST` on init, updating rather than duplicating
  if a CIK already exists.

### R4 — SEC client (`edgar_client.py`)

The single point of contact with sec.gov. Every SEC request in the project goes through it.

- Sets a `User-Agent` header from config on every request. **SEC blocks requests without
  it.** Format: `"Name email@example.com"`.
- Rate limits to at most `SEC_RATE_LIMIT_PER_SEC` requests per second, globally.
- **The rate limiter must accept an injectable time source and sleep function**, defaulting
  to the real ones. This makes rate-limit behaviour testable without slow sleeping tests.
  Build it this way from the start rather than retrofitting.
- Retries on transient failures (timeout, 5xx, 429) with exponential backoff using the
  configured parameters. Does not retry 404.
- Raises a typed exception on permanent failure rather than returning `None`.
- Provides at minimum:
  - `get_submissions(cik)` → parsed submissions JSON from
    `https://data.sec.gov/submissions/CIK{cik}.json`
  - `get_filing_index(cik, accession_no)` → the list of documents in a filing
  - `get_archive_file(cik, accession_no, filename)` → bytes from
    `https://www.sec.gov/Archives/edgar/data/{cik_no_leading_zeros}/{accession_no_no_dashes}/{filename}`

### R5 — Monitor (`monitor.py`)

- `find_new_filings()` returns filings present at SEC but absent from the `filings` table.
- Reads `filings.recent` from the submissions JSON. Note this is **parallel arrays**, not
  a list of objects — index *i* of each array describes the same filing.
- Filters to `TRACKED_FORMS` **before** any further work. These companies file hundreds of
  Form 4s; they must never enter the pipeline.
- For 8-K only, include the filing if `EIGHTK_REQUIRED_ITEM` appears in its item list,
  resolved in this order:
  1. Read the `items` array from the submissions JSON, if present.
  2. If that array is absent or empty for this filing, fall back to reading the item list
     from the filing index.
  3. Exclude the filing only if **both** sources yield nothing.

  Record in a code comment which source proved authoritative — the architecture document
  lists this as unverified and must be corrected once known.
- Deduplicates on `accession_no`, which is globally unique.
- Writes new rows to `filings` with `status = 'discovered'` and `discovered_at` set.
- Must be idempotent: running twice in succession discovers nothing the second time.

### R6 — Fetch (`fetch.py`)

Implements the archive policy in `ARCHITECTURE.md` §4.1:

| Form | Archive |
|---|---|
| 10-K, 10-Q | Primary document + `FilingSummary.xml` |
| 8-K | **All documents listed in the filing index**, including Exhibit 99.1 |

The 8-K rule is not optional. The entire reason for tracking 8-Ks is that guidance lives
in Exhibit 99.1; an 8-K archived without it contains nothing of value.

- Stores gzipped under `data/raw/{cik}/{accession_no}/`, preserving original filenames.
- Records `raw_path` and `primary_doc` on the `filings` row; sets `status = 'fetched'`.
- Skips download if the archive already exists on disk, unless explicitly forced.
- On failure sets `status = 'failed'`, logs the reason, and continues to the next filing.
  One bad filing must not halt a run.

### R7 — CLI (`pipeline.py`)

```
python -m edgar.pipeline init-db
python -m edgar.pipeline discover [--ticker TICKER] [--limit N] [--dry-run]
python -m edgar.pipeline status
```

- `discover` runs monitor then fetch, printing a readable table: ticker, form type,
  filing date, period end, accession number, status.
- `--ticker` restricts to one watchlist company. Useful during development, since Amazon
  is the primary test subject.
- `--dry-run` reports what would be fetched without downloading or writing.
- `status` prints counts of filings by company, form type, and status.
- Logging to stdout at INFO. No `print()` inside library modules — only in the CLI layer.

---

## Constraints

- Python 3.11+, macOS (Apple Silicon) for development, Ubuntu for CI later.
- No web framework, no ORM, no async. `sqlite3` from the standard library.
- Third-party dependencies must be justified. `requests` is acceptable.
- No network calls in unit tests. Fixtures only.
- No secrets in source. `SEC_USER_AGENT` from environment.
- Functions over classes unless state genuinely needs encapsulation.
- Type hints on all public functions.

---

## Acceptance Criteria

1. `python -m edgar.pipeline init-db` creates `data/app.db` with all **seven** tables and
   three rows in `companies`. Running it a second time changes nothing and raises nothing.
2. `python -m edgar.pipeline discover` on an empty database discovers 10-K, 10-Q and
   Item 2.02 8-K filings for all three companies, and **no other form types**.
3. These specific filings are present with correct form type and filing date:
   - `0001018724-26-000004` — AMZN 10-K, 2026-02-06
   - `0001018724-26-000014` — AMZN 10-Q, 2026-04-30
   - `0001045810-26-000021` — NVDA 10-K, 2026-02-25
   - `0000723125-25-000028` — MU 10-K, 2025-10-03
4. No Form 3, 4, 5, S-8, SD, 11-K, or 144 row exists in `filings`.
5. Every discovered 8-K row has `'2.02'` present in its `items` column.
6. Raw archives exist on disk under `data/raw/{cik}/{accession}/`, gzipped, including
   `FilingSummary.xml` for each 10-K and 10-Q.
7. At least one archived 8-K directory contains an Exhibit 99.1 document.
8. Running `discover` a second time downloads nothing and adds no rows.
9. `status` prints an accurate breakdown by company, form and status.
10. `pytest` passes.
11. No SEC URL, CIK, or form-type literal appears outside `config.py`.
12. No module other than `edgar_client.py` imports `requests` or performs HTTP.

---

## Edge Cases

| Case | Required behaviour |
|---|---|
| `SEC_USER_AGENT` unset | Fail immediately with a clear message naming the variable. Never send an anonymous request. |
| SEC returns 429 or 503 | Back off and retry within the configured budget; fail cleanly after. |
| `FilingSummary.xml` absent (typical 8-K) | Not an error. Archive per §4.1 and continue. |
| 8-K where neither submissions JSON nor filing index yields item data | Exclude it. Under-inclusion is safer than noise. |
| 8-K with Item 2.02 but no Exhibit 99.1 | Archive what exists, log a warning. Do not fail. |
| Filing appears in submissions but archive 404s | Mark `failed`, log, continue. |
| Accession already in `filings` | Skip silently. |
| Interrupted mid-run | Next run resumes; already-fetched filings are not re-downloaded. |
| Amendments (`10-K/A`, `10-Q/A`) | Treat as distinct filings. Do not overwrite the original. |
| `period_end` missing from submissions | Store `NULL`. Never infer it from the filing date — the three companies have three different fiscal calendars. |

---

## Testing Requirements

- Save real submissions JSON responses as fixtures — Amazon at minimum, ideally all three.
  Capturing them is a manual one-off step, not pipeline code. Document the source URL in a
  comment so the fixture can be refreshed.
- `test_monitor_filters_form_types` — Form 4s in the fixture never produce a filing row.
- `test_monitor_filters_8k_items` — an 8-K without Item 2.02 is excluded; one with it
  is included.
- `test_monitor_8k_falls_back_to_index` — when the submissions `items` array is missing,
  the index is consulted before excluding.
- `test_monitor_is_idempotent` — two consecutive runs over the same fixture yield the
  same row count.
- `test_db_init_idempotent` — `init_db()` twice succeeds and does not duplicate companies.
- `test_db_has_seven_tables`.
- `test_rate_limiter` — using an injected fake clock, the client cannot exceed the
  configured rate. Must not rely on real sleeping.
- `test_missing_user_agent_fails_fast`.
- `test_fetch_skips_existing_archive`.
- `test_fetch_8k_archives_all_index_documents`.

---

## Likely Files Affected

```
edgar/__init__.py
edgar/config.py
edgar/db.py
edgar/edgar_client.py
edgar/monitor.py
edgar/fetch.py
edgar/pipeline.py
edgar/sections.py      (empty)
edgar/xbrl.py          (empty)
edgar/metrics.py       (empty)
edgar/llm.py           (empty)
edgar/analyze.py       (empty)
tests/conftest.py
tests/fixtures/*.json
tests/test_monitor.py
tests/test_db.py
tests/test_edgar_client.py
tests/test_fetch.py
pyproject.toml
README.md
.gitignore
```

---

## Notes for the Implementer

- `filings.recent` in the submissions JSON uses **parallel arrays**, not objects.
  `form[i]`, `accessionNumber[i]`, `filingDate[i]`, `reportDate[i]` and `items[i]` all
  describe the same filing.
- Accession numbers appear in two forms: dashed (`0001018724-26-000004`) as the
  identifier, undashed (`000101872426000004`) in archive URLs. Store the dashed form;
  convert at the point of URL construction.
- Archive URLs use the CIK **without** leading zeros; the submissions URL uses the
  zero-padded ten-digit form. Getting this wrong produces 404s.
- Micron 10-K filings have historically ranged 17–46 MB. Stream to disk rather than
  holding whole filings in memory.
- Design `edgar_client.py` so a future concurrency change is confined to that file.
- Report any discrepancy between this spec and observed SEC behaviour rather than
  working around it silently. `ARCHITECTURE.md` must then be corrected.
