# SPEC-001 — Project Foundation, Filing Discovery, and Raw Storage

**For:** Claude Code
**Depends on:** nothing (first spec)
**Reference:** `ARCHITECTURE.md` sections 3, 5, 6
**Estimated effort:** 4–6 hours

---

## Objective

Stand up the project skeleton, the database, and the ingestion front end: given a
watchlist of companies, discover SEC filings we have not seen before, download and
permanently archive them, and record them in the database.

On completion the operator must be able to run one command and see Amazon's recent
10-K, 10-Q and Item 2.02 8-K filings listed and stored.

No parsing, no XBRL, no AI in this spec.

---

## Scope

**In scope**

1. Python project skeleton with dependency management
2. `config.py` — all configuration and constants
3. `db.py` — full schema from `ARCHITECTURE.md` §6, created idempotently
4. `edgar_client.py` — the only module permitted to make HTTP requests to sec.gov
5. `monitor.py` — discover filings not yet in the database
6. `fetch.py` — download and archive raw filings
7. `pipeline.py` — CLI entry point with a `discover` command
8. Tests for the above

**Out of scope** — do not implement, do not stub beyond empty module files:
section extraction, XBRL ingestion, metric calculation, any LLM call, the dashboard,
GitHub Actions workflows.

---

## Requirements

### R1 — Project skeleton

- Package directory `edgar/` with the module files listed in `ARCHITECTURE.md` §5.
  Modules outside this spec's scope may be created empty.
- Dependencies pinned in `requirements.txt` or `pyproject.toml`. Prefer the standard
  library; justify each third-party dependency in a comment.
- `data/raw/` and `data/` exist and are created at runtime if missing.
- `.gitignore` excludes `.env`, `__pycache__/`, and virtualenv directories.
  It must **not** exclude `data/` — the database and archives are committed deliberately.
- `README.md` with setup and run instructions.

### R2 — Configuration (`config.py`)

Must contain, and be the only place that contains:

- `WATCHLIST`: Amazon only. CIK `0001018724`, ticker `AMZN`, name, fiscal year end `1231`.
  Structured so adding a company is a one-line change.
- `TRACKED_FORMS`: `["10-K", "10-Q", "8-K"]`
- `EIGHTK_REQUIRED_ITEM`: `"2.02"`
- `SEC_USER_AGENT`: read from environment, with a clear error if unset.
- `SEC_RATE_LIMIT_PER_SEC`: `8`
- Paths for the database and raw archive directory.

No literal URLs, form names, CIKs, or numeric limits anywhere else in the codebase.

### R3 — Database (`db.py`)

- Creates the complete schema in `ARCHITECTURE.md` §6, including tables not used until
  later specs. Building it once avoids migrations mid-project.
- `CREATE TABLE IF NOT EXISTS` — running twice must be safe.
- Foreign keys enforced (`PRAGMA foreign_keys = ON`).
- Exposes a connection helper and a `init_db()` function.
- Seeds the `companies` table from `config.WATCHLIST` on init, updating rather than
  duplicating if a CIK already exists.

### R4 — SEC client (`edgar_client.py`)

The single point of contact with sec.gov. Every SEC request in the project goes through it.

- Sets a `User-Agent` header from config on every request. **SEC blocks requests without
  it.** Format: `"Name email@example.com"`.
- Rate limits to at most `SEC_RATE_LIMIT_PER_SEC` requests per second, globally.
- Retries on transient failures (timeout, 5xx, 429) with exponential backoff,
  bounded attempts. Does not retry 404.
- Raises a typed exception on permanent failure rather than returning `None`.
- Provides at minimum:
  - `get_submissions(cik)` → parsed submissions JSON from
    `https://data.sec.gov/submissions/CIK{cik}.json`
  - `get_archive_file(cik, accession_no, filename)` → bytes from
    `https://www.sec.gov/Archives/edgar/data/{cik_no_leading_zeros}/{accession_no_no_dashes}/{filename}`
  - `get_filing_index(cik, accession_no)` → the filing's index JSON or HTML

### R5 — Monitor (`monitor.py`)

- `find_new_filings()` returns filings present at SEC but absent from the `filings` table.
- Reads `filings.recent` from the submissions JSON. Note this is **parallel arrays**, not
  a list of objects — index *i* of each array describes the same filing.
- Filters to `TRACKED_FORMS` **before** any further work. Amazon files hundreds of Form 4s;
  they must never enter the pipeline.
- For 8-K only: include the filing only if `EIGHTK_REQUIRED_ITEM` appears in that filing's
  item list.
  - **Verify first** whether the submissions JSON exposes an `items` array parallel to
    `form`. It is expected to, carrying values like `"2.02,9.01"`.
  - If it does not, fall back to reading the item list from the filing index and record
    the discovery in a code comment.
- Deduplicates on `accession_no`, which is globally unique.
- Writes new rows to `filings` with `status = 'discovered'` and `discovered_at` set.
- Must be idempotent: running twice in succession discovers nothing the second time.

### R6 — Fetch (`fetch.py`)

- `fetch_filing(accession_no)` downloads and archives:
  - the primary document
  - `FilingSummary.xml` where present (absent on most 8-Ks — this is normal, not an error)
- Stores gzipped under `data/raw/{cik}/{accession_no}/`, preserving original filenames.
- Records `raw_path` and `primary_doc` on the `filings` row and sets `status = 'fetched'`.
- Skips download if the archive already exists on disk, unless explicitly forced.
- On failure sets `status = 'failed'`, logs the reason, and continues to the next filing.
  One bad filing must not halt a run.

### R7 — CLI (`pipeline.py`)

```
python -m edgar.pipeline init-db
python -m edgar.pipeline discover [--limit N] [--dry-run]
python -m edgar.pipeline status
```

- `discover` runs monitor then fetch, printing a readable table of what it found:
  form type, filing date, period end, accession number, status.
- `--dry-run` reports what would be fetched without downloading or writing.
- `status` prints a count of filings by form type and status.
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

The spec is complete when all of the following hold:

1. `python -m edgar.pipeline init-db` creates `data/app.db` with all eight tables and
   one row in `companies`. Running it a second time changes nothing and raises nothing.
2. `python -m edgar.pipeline discover` on an empty database discovers Amazon's recent
   10-K, 10-Q and Item 2.02 8-K filings, and **no other form types**.
3. Amazon 10-K `0001018724-26-000004` is present with `form_type = '10-K'` and
   `filing_date = '2026-02-06'`.
4. Amazon 10-Q `0001018724-26-000014` is present with `filing_date = '2026-04-30'`.
5. No Form 3, 4, 5, S-8, SD, 11-K, or 144 row exists in `filings`.
6. Every discovered 8-K row has `'2.02'` present in its `items` column.
7. Raw archives exist on disk under `data/raw/0001018724/{accession}/`, gzipped,
   including `FilingSummary.xml` for the 10-K and 10-Q.
8. Running `discover` a second time downloads nothing and adds no rows.
9. `status` prints an accurate breakdown.
10. `pytest` passes.
11. No SEC URL, CIK, or form-type literal appears outside `config.py`.
12. No module other than `edgar_client.py` imports `requests` or performs HTTP.

---

## Edge Cases

| Case | Required behaviour |
|---|---|
| `SEC_USER_AGENT` unset | Fail immediately with a clear message naming the variable. Do not send an anonymous request. |
| SEC returns 429 or 503 | Back off and retry within the retry budget; fail cleanly after. |
| `FilingSummary.xml` absent (typical 8-K) | Not an error. Archive the primary document, continue. |
| 8-K with no `items` data available | Exclude it. Under-inclusion is safer than noise. |
| Filing appears in submissions but archive 404s | Mark `failed`, log, continue. |
| Accession already in `filings` | Skip silently. |
| Interrupted mid-run | Next run resumes; already-fetched filings are not re-downloaded. |
| Amendments (`10-K/A`, `10-Q/A`) | Treat as distinct filings. Do not overwrite the original. |
| `period_end` missing from submissions | Store `NULL`. Do not infer. |

---

## Testing Requirements

- Save a real submissions JSON response for Amazon as a fixture. Tests must not hit
  the network.
- `test_monitor_filters_form_types` — Form 4s in the fixture never produce a filing row.
- `test_monitor_filters_8k_items` — an 8-K without Item 2.02 is excluded; one with it
  is included.
- `test_monitor_is_idempotent` — two consecutive runs over the same fixture yield the
  same row count.
- `test_db_init_idempotent` — `init_db()` twice succeeds and does not duplicate companies.
- `test_rate_limiter` — the client cannot exceed the configured rate.
- `test_missing_user_agent_fails_fast`.
- `test_fetch_skips_existing_archive`.

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
tests/fixtures/amzn_submissions.json
tests/test_monitor.py
tests/test_db.py
tests/test_edgar_client.py
tests/test_fetch.py
requirements.txt
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
- Design `edgar_client.py` so a future concurrency change is confined to that file.
- Report any discrepancy between this spec and observed SEC behaviour rather than
  working around it silently. The architecture document must be corrected.
