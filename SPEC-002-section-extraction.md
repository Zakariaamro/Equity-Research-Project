# SPEC-002 — Document Manifests and Section Extraction (Statements, Notes, Policies)

**Version:** 1.1
**For:** Claude Code
**Depends on:** SPEC-001 (complete, commit `987ef29`)
**Reference:** `ARCHITECTURE.md` v1.4 — sections 3.1, 3.6, 3.7, 4.1, 6
**Estimated effort:** 5–7 hours

**Changelog — v1.1 resolves review flags raised against v1.0:**
- R1: the `index.json` / `-index.html` merge specified explicitly (was ambiguous about
  which resource supplies types).
- R1: manifest generation must degrade gracefully and never block ingestion.
- R1: "without re-downloading content" clarified to mean archived document bytes only.
- R1: Document Format Files table parsing lives in `fetch.py`, not `edgar_client.py`.
- R5 / schema: `sections` UNIQUE constraint gains `source_file`; the duplicate-`ShortName`
  edge case is resolved by the schema rather than by disambiguation logic.

---

## Objective

Turn archived filings into analysable text. For every 10-K and 10-Q, read
`FilingSummary.xml`, select the reports that carry real content, extract each one as
clean plain text, and persist it to the `sections` table.

Also record a document manifest at archive time so the archive becomes self-describing —
which fixes the misleading Exhibit 99.1 warning found in SPEC-001 and unblocks SPEC-003.

On completion, `sections` should contain one row per statement, note and policy block for
every 10-K and 10-Q in the database.

MD&A, Risk Factors, and Exhibit 99.1 extraction are **SPEC-003**. Not this spec.

---

## Scope

**In scope**

1. Document manifest written at archive time, using SEC's declared document types
2. Retro-generation of manifests for the 171 filings already archived
3. `sections.py` — parse `FilingSummary.xml`, select by `MenuCategory`, extract text
4. Shared HTML-to-text utility
5. `pipeline.py` — `extract` command
6. Tests

**Out of scope** — do not implement:
MD&A or Risk Factors extraction, Exhibit 99.1 text extraction, XBRL ingestion, metrics,
any LLM call, the dashboard, GitHub Actions workflows.

---

## Requirements

### R1 — Document manifests

The SPEC-001 Exhibit 99.1 warning is a false positive caused by matching filenames with a
regex. Filenames are chosen by filing agents and follow no reliable convention
(`q4fy26pr.htm`, `a2017q3exhibit991-pressrel.htm`). The filing index declares a **document
type** per file (`EX-99.1`, `10-K`, `GRAPHIC`, …). That is authoritative.

**Two different SEC resources are involved, and only one has types** (see
`ARCHITECTURE.md` §3.6). Build the manifest from both, merged on filename:

- `index.json` (`.../{accession}/index.json`) supplies the **complete file list** for
  the filing. Its own `type` field is a display icon class, not a document type --
  ignore it.
- `{accession}-index.html` supplies the **SEC-declared document type** per file, via its
  "Document Format Files" table (columns: Seq, Description, Document, Type, Size). This
  table does not enumerate every file -- viewer-support files (`FilingSummary.xml`,
  `R*.htm`, `MetaLinks.json`, `report.css`, `Show.js`) never appear in it.
- Merge: every filename from `index.json` gets an entry; if that filename also appears
  in the `-index.html` table, use its `Type` and `Description`; otherwise both are `""`.

- At fetch time, write `manifest.json` into each filing's archive directory:

```json
{
  "accession_no": "0001018724-26-000004",
  "cik": "0001018724",
  "form_type": "10-K",
  "documents": [
    {"filename": "amzn-20251231.htm", "type": "10-K", "description": "...", "archived": true},
    {"filename": "FilingSummary.xml", "type": "", "description": "", "archived": true}
  ]
}
```

- The manifest lists **every** document declared in the filing index, with `archived`
  indicating whether we stored it. This records what exists, not just what we kept.
- Replace the filename-regex Exhibit 99.1 check with a lookup on `type == "EX-99.1"`.
  Warn only when an Item 2.02 8-K genuinely declares no such document.
- Provide `python -m edgar.pipeline backfill-manifests` to generate manifests for the
  filings already archived, without re-downloading their content. "Content" means the
  archived document bytes -- fetching `index.json` and `-index.html` fresh over the
  network for each filing, to learn about documents never downloaded, is expected and
  required; this is not a zero-network-calls operation.
- **Manifest generation must degrade gracefully.** If the type lookup fails for any
  reason (network error, unexpected page structure, parse failure), archive normally,
  write the manifest with empty types throughout, and log a warning. Manifest
  construction must never block or fail ingestion -- a filing that fetches successfully
  must still end up `status = 'fetched'` even if its manifest is typeless.
- Parse the Document Format Files table in `fetch.py`, not `edgar_client.py`. The client
  returns bytes and parsed JSON; it does not interpret SEC page layouts. `fetch.py` calls
  `edgar_client.get_archive_file(cik, accession_no, f"{accession_no}-index.html")` for
  the raw bytes and parses the table itself.

**Principle to preserve:** where SEC provides structured metadata, never infer meaning
from a filename.

### R2 — Section selection (`sections.py`)

For each 10-K and 10-Q with `status = 'fetched'`:

- Read the archived `FilingSummary.xml` from disk. **Do not re-download it.**
- Select reports where `MenuCategory` is `Statements`, `Notes`, or `Policies`.
- Skip `Cover`, `Tables`, and `Details` — `Tables` and `Details` are XBRL tagging detail
  and duplicate content already captured in the parent note.
- Record for each selected report: `ShortName`, `MenuCategory`, `HtmlFileName`, `Position`.

Extract **all** notes, including boilerplate ones (Insider Trading Arrangements,
Cybersecurity). Extraction is cheap; filtering belongs at the analysis stage, which is
the expensive one. This mirrors the archive-broadly / analyse-narrowly rule in
`ARCHITECTURE.md` §4.1.

### R3 — R-file retrieval

R-files (`R7.htm`, `R14.htm`, …) are **not** in the archive, by design — they are SEC
renderings regenerable from the primary document plus its XBRL.

- Fetch them via `edgar_client` at extraction time.
- Do **not** add them to the archive. The durable artifact is the extracted text in the
  `sections` table; once extracted, the R-file is never needed again.
- Extraction runs once per filing. Guard on `status` so re-running does not re-fetch.

### R4 — HTML to text

A shared utility, since SPEC-003 will need it too.

- Convert an R-file's HTML into clean plain text.
- Preserve reading order and the row structure of financial tables. A table row should
  read as label followed by values on one line, not collapse into a run-on paragraph.
- Strip scripts, styles, and presentational markup.
- Normalise whitespace: collapse runs of spaces, preserve paragraph breaks, strip
  leading and trailing blank lines.
- Preserve numbers, currency symbols, parentheses (negatives), and footnote markers
  exactly. **Never reformat or round a number.** These feed an analyst tool; a silently
  altered figure is the worst possible bug class here.
- `beautifulsoup4` is an approved dependency for this. Justify anything further.

### R5 — Persistence

Write one `sections` row per extracted report:

- `category` — the `MenuCategory` value (`Statements`, `Notes`, `Policies`)
- `short_name` — the `ShortName` value, verbatim
- `source_file` — e.g. `R14.htm`
- `position` — the `Position` value, so display order is preserved
- `text` — the cleaned text
- `text_hash` — sha256 of the cleaned text
- Respect `UNIQUE(accession_no, category, short_name, source_file)`; re-running updates
  rather than duplicating.
- Set `filings.status = 'sectioned'` when all sections for a filing are written.
- If extraction of one section fails, log it, continue with the others, and leave the
  filing's status unchanged so it can be retried.

`text_hash` exists so that SPEC-004 can cache LLM responses against unchanged content.
Compute it over the cleaned text, not the raw HTML, so that incidental markup changes
do not invalidate the cache.

### R6 — CLI

```
python -m edgar.pipeline extract [--ticker TICKER] [--accession ACCESSION] [--limit N] [--force]
python -m edgar.pipeline backfill-manifests
```

- `extract` processes filings with `status = 'fetched'`.
- `--accession` targets one filing — the main development loop.
- `--force` re-extracts filings already marked `sectioned`.
- Prints a summary: filings processed, sections written, failures.
- `status` should now also report a count of filings by `sectioned` state.

---

## Constraints

- Reuse `edgar_client` for all HTTP. No module other than `edgar_client.py` may perform it.
- No literal values outside `config.py`, including `MenuCategory` names.
- No LLM calls.
- No network in unit tests. Save one real R-file and one real `FilingSummary.xml` as fixtures.
- Type hints on all public functions.

---

## Acceptance Criteria

1. `backfill-manifests` writes a `manifest.json` into all 171 existing archive directories
   without re-downloading filing content.
2. Every manifest lists all documents declared in the filing index with their SEC-declared
   `type`.
3. The Exhibit 99.1 warning no longer fires for NVDA or pre-2021 MU 8-Ks. Verified by
   re-running the check across all archived 8-Ks.
4. `extract --accession 0001018724-26-000004` produces sections for Amazon's FY2025 10-K,
   including named rows for `Income Taxes`, `Segment Information`, `Leases`, and `Debt`.
5. No section row has `category` of `Tables`, `Details`, or `Cover`.
6. Sections exist for all five Amazon statement reports (Operations, Balance Sheets,
   Cash Flows, Comprehensive Income, Stockholders' Equity).
7. Extracted text for `Income Taxes` contains recognisable tax figures with digits and
   separators intact, and is free of HTML tags and CSS.
8. Every section row has a non-empty `text_hash`, and identical text yields an identical hash.
9. Running `extract` twice produces no duplicate rows and no second fetch of R-files.
10. `extract --ticker NVDA` and `--ticker MU` succeed, demonstrating the extractor is not
    tuned to Amazon's markup.
11. Filings reach `status = 'sectioned'` only when all their sections are written.
12. No R-files are written to `data/raw/`.
13. `pytest` passes, including new tests for section selection, HTML-to-text, and hashing.

---

## Edge Cases

| Case | Required behaviour |
|---|---|
| `FilingSummary.xml` missing from an archive | Skip the filing, log it. Do not fail the run. |
| Filing is an 8-K | Skip entirely in this spec. 8-K handling is SPEC-003. |
| An R-file 404s | Log, continue with other sections, leave filing status unchanged. |
| `ShortName` contains an apostrophe (`Stockholders' Equity`) | Must round-trip correctly. Use parameterised SQL. |
| Extracted text is empty | Log a warning, write no row. An empty section is a bug, not a fact. |
| Very large note (Micron filings run 17–46 MB) | Must not exhaust memory. Stream or process incrementally. |
| Non-breaking spaces and typographic characters in numbers | Normalise to plain equivalents; do not corrupt the digits. |

---

## Testing Requirements

- Fixtures: one real `FilingSummary.xml` and at least two real R-files (a note and a
  statement). Document their source URLs in comments.
- `test_selects_only_content_categories` — Tables, Details and Cover are excluded.
- `test_note_shortnames_extracted` — expected Amazon note names appear.
- `test_html_to_text_strips_markup` — no tags or CSS survive.
- `test_html_to_text_preserves_numbers` — figures with commas, decimals, currency symbols
  and parenthesised negatives survive byte-for-byte.
- `test_html_to_text_table_rows_stay_on_one_line`.
- `test_text_hash_is_stable` — same input, same hash; whitespace-only change in the raw
  HTML does not change the hash of the cleaned text.
- `test_extract_is_idempotent` — second run adds no rows and issues no fetches.
- `test_apostrophe_shortname_roundtrip`.
- `test_manifest_identifies_exhibit_991_by_type_not_filename` — using an NVDA-style
  filename that no filename regex would match.

---

## Likely Files Affected

```
edgar/config.py          (MenuCategory constants)
edgar/sections.py        (main work)
edgar/fetch.py           (manifest writing, EX-99.1 type check)
edgar/pipeline.py        (extract, backfill-manifests)
edgar/html_text.py       (new — shared utility)
tests/fixtures/*.xml
tests/fixtures/*.htm
tests/test_sections.py
tests/test_html_text.py
tests/test_manifest.py
pyproject.toml           (beautifulsoup4)
```

---

## Notes for the Implementer

- R-file URLs sit in the same archive directory as the filing:
  `https://www.sec.gov/Archives/edgar/data/{cik}/{accession_no_dashes_removed}/R14.htm`
- `FilingSummary.xml` gives `HtmlFileName` directly; do not construct these names by
  guessing an index.
- The archived `FilingSummary.xml` is gzipped. Read it from disk rather than re-fetching.
- Statement R-files are stored as text in this spec. How financial statements are
  *displayed* is a dashboard question, and the dashboard will build them from
  `xbrl_facts` rather than from this text. The text is kept because it is cheap and
  gives the LLM statement context alongside a note. Do not attempt structured table
  parsing here.
- Report any discrepancy between this spec and observed SEC behaviour rather than
  working around it silently. `ARCHITECTURE.md` must then be corrected.
