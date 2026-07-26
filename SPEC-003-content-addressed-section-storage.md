# SPEC-003 — Content-Addressed Section Storage

**Version:** 1.1
**For:** Claude Code
**Depends on:** SPEC-002 (complete, commit `676b24a`)
**Reference:** `ARCHITECTURE.md` v1.4 — sections 4, 4.1, 6
**Estimated effort:** 2–4 hours

**Changelog**
- v1.1 — Pre-implementation review. Backup step made explicit (R4). SQLite-version
  fallback replaced with a precondition check (Edge Cases). `--dry-run` added to
  `migrate-sections` (R4). `SECTIONS_DIR` named explicitly (R1, Likely Files Affected).
  R5 clarified: unchanged hash means no DB write, not just no file write. Note added
  that this spec does not reclaim space already spent in `.git` history (Why).

---

## Objective

Move section text out of SQLite and into immutable, content-addressed files on disk.
The database keeps section metadata and the hash; the text lives in
`data/sections/{hash[:2]}/{hash}.txt.gz`.

This is a storage refactor. No behaviour visible to a user changes. No new data is
gathered.

---

## Why

`data/app.db` reached 33.7 MB after SPEC-002, of which roughly 32 MB is section text.
The deployment design in `ARCHITECTURE.md` §4 commits the database to the repository
after every pipeline run so that ephemeral GitHub Actions runners have durable state.

Git stores binary files as whole blobs, and SQLite files do not delta-compress usefully.
Committing a 33.7 MB database on every run adds roughly that much to repository history
each time — several hundred MB across the remaining build, and on the order of 10 GB a
year once a daily schedule is running. GitHub warns above 1 GB.

Section text is immutable: a filing's Income Taxes note never changes after filing. It
is currently stored inside the one file in the project that is rewritten constantly.

Separating them means the mutable artifact stays small and the large artifact is written
once. This mirrors how `data/raw/` already behaves.

**Principle: keep mutable state small; make large data immutable.**

**Scope of the fix:** this stops future growth. It does not, and cannot, reclaim the
space already spent in `.git` history by the two prior commits (`app.db` blobs already
committed at SPEC-001 and SPEC-002). That history stays on disk permanently short of a
history rewrite, which is out of scope and not worth the disruption at this project
size. `git status`/repo-size checks after this migration should compare the *rate of
future growth*, not expect the existing `.git` directory to shrink.

---

## Scope

**In scope**

1. Content-addressed section storage on disk
2. `sections` schema change — drop `text`, keep `text_hash`
3. Read and write helpers
4. One-time migration of the 2,174 existing rows
5. Update `sections.py` to write through the new path
6. Tests

**Out of scope:** XBRL, metrics, LLM, dashboard, MD&A, GitHub Actions, any change to
`data/raw/` layout.

---

## Requirements

### R1 — Storage layout

```
data/sections/{first two chars of hash}/{full hash}.txt.gz
```

- Hash is the existing `text_hash`: sha256 of the cleaned text, hex-encoded.
- Two-character sharding keeps directory sizes reasonable; 2,174 files across 256
  directories rather than one flat directory.
- Files are gzipped UTF-8 plain text.
- **Files are immutable.** If the target path already exists, verify the content hash
  matches and skip the write. Never overwrite.

### R2 — Schema change

- Drop the `text` column from `sections`. `text_hash` remains and becomes the sole link
  to content.
- Do **not** store the file path. It is derivable from the hash; storing it would allow
  the two to disagree.
- Update `ARCHITECTURE.md` §6 to match, and add a note recording why text is not in the
  database.

### R3 — Access helpers

In `sections.py` or a small dedicated module:

- `write_section_text(text) -> str` — computes the hash, writes the gzipped file if
  absent, returns the hash.
- `read_section_text(text_hash) -> str` — reads and decompresses.
- `section_path(text_hash) -> Path` — the single place that knows the layout.
- `read_section_text` raises a typed error if the file is missing. A row whose content
  is absent is corruption, not an empty section, and must not silently return `""`.

Nothing outside these helpers may construct a section path.

### R4 — Migration

`python -m edgar.pipeline migrate-sections`

- **Backs up `app.db` first.** Before making any schema change, copy
  `data/app.db` → `data/app.db.pre-migration.bak` and say so in the command's output.
  This is a plain filesystem copy, not a git operation. It is insurance against the fact
  that section text is not currently archived anywhere else (R-files are fetched live
  and never saved to disk, per `sections.py`) — see Edge Cases. The backup must never be
  committed; see R6a.
- `--dry-run` — reads every row, recomputes hashes, and reports what *would* be written
  and what mismatches (if any) would abort the migration. Writes no files, makes no
  schema change, and does not create the backup (there is nothing to protect against —
  nothing is written). Always run this before the real migration.
- Real run: reads every existing row, writes its text to the content-addressed store,
  verifies the computed hash matches the stored `text_hash`, then drops the column.
- **Verify before dropping.** Any mismatch between recomputed and stored hash must abort
  the migration with the offending `accession_no` and `short_name` reported. Do not
  proceed partially. Content files written during a verification pass that later aborts
  are harmless and left in place — they are addressed by their correct content hash
  regardless of what the (wrong) stored `text_hash` said, and orphan cleanup is already
  out of scope (R5).
- Idempotent: running it after completion is a no-op with a clear message. This applies
  to `--dry-run` too.
- Report rows migrated, files written, files already present, and the before/after size
  of `app.db`.

### R5 — Write path

- `sections.py` writes text to the store and records only the hash.
- Extraction remains idempotent: re-extracting identical text writes no new file and
  **changes no row** — if the recomputed hash for a `(accession_no, category,
  short_name, source_file)` row matches the hash already stored, skip the DB write
  entirely (no `UPDATE`, not even of `position`). The prior implementation unconditionally
  `UPDATE`d on every re-extraction regardless of whether content changed; that must
  change too. Needless row rewrites dirty SQLite pages and produce larger binary diffs
  on every commit — exactly the problem this spec exists to fix, so it should not survive
  in the write path that remains.
- Extraction of *changed* text produces a new hash and a new file. The old file remains,
  since it may still be referenced by a cached analysis. Orphan cleanup is out of scope
  and should be noted as future work.

### R6 — `.gitignore`

`data/sections/` must be committed. Confirm nothing excludes it.

### R6a — Migration backup must not be committed

`data/app.db.pre-migration.bak` (R4) is a local safety copy, not project state. Add
`data/*.bak` to `.gitignore`. Committing a 34 MB backup copy would defeat the entire
purpose of this spec on the very commit that implements it.

---

## Acceptance Criteria

1. `migrate-sections` migrates all 2,174 rows with zero hash mismatches.
2. `data/app.db` drops below 4 MB.
3. `data/sections/` contains one file per distinct `text_hash`, sharded two levels.
4. `sections` has no `text` column; `text_hash` is populated on every row.
5. `read_section_text` returns text byte-identical to what SPEC-002 stored, verified by
   spot-checking Amazon's Income Taxes note against the pre-migration value.
6. Re-running `extract --force` for a filing writes no new section files, because the
   text and therefore the hashes are unchanged.
7. `read_section_text` on an absent hash raises a typed error rather than returning empty.
8. Running `migrate-sections` twice is safe and reports a no-op.
9. `git status` shows `data/sections/` as tracked, not ignored.
10. No data is lost in the move: every row passes hash verification during migration
    (zero mismatches, criterion 1) and a byte-identical spot check (criterion 5) confirms
    it independently. **Not** a size comparison — an earlier version of this criterion
    required total `data/` size to stay within ~10% of its pre-migration value, on the
    assumption that moving text to disk would roughly wash out. In practice gzip
    compresses filing text 3–4x (repeated numbers, whitespace, boilerplate), so the real
    run came in ~30% smaller, which is a good outcome, not a violation of anything. A
    smaller-than-expected result is indistinguishable from data loss if the acceptance
    criterion is a size proxy instead of the property actually at stake; a criterion
    should test the property (no data lost) directly rather than a proxy for it (size
    stayed within a band) whenever the proxy can move for reasons that have nothing to do
    with the property.
11. All existing tests still pass, plus new tests for the helpers and migration.
12. `data/app.db.pre-migration.bak` exists after the real run, is excluded by
    `.gitignore`, and is not staged by `git status`.

---

## Edge Cases

| Case | Required behaviour |
|---|---|
| Hash collision with differing content | Effectively impossible with sha256, but if the file exists and its content hash differs, raise. Never overwrite silently. |
| Migration interrupted midway | Re-runnable. Already-written files are detected and skipped; the column is dropped only after every row is verified. |
| Row with empty `text_hash` | Abort the migration and report it. SPEC-002 criterion 8 says this cannot happen; if it has, something is wrong. |
| Section file deleted from disk | `read_section_text` raises. Do not fall back or regenerate silently. |
| SQLite version too old for `DROP COLUMN` (< 3.35) | **Precondition, not a fallback.** Check `sqlite3.sqlite_version_info` at the start of `migrate-sections` (including `--dry-run`) and abort immediately with a clear message naming the required version. No table-recreation code path. An untested fallback branch that only exists for a version we don't run against is a liability, not a safety net — better to fail loudly and require a runtime upgrade than carry code no test can exercise. Both local (3.51) and GitHub Actions runners are well above the floor, so this is expected to never trigger in practice. |
| `migrate-sections` run again after completion (no `--dry-run`) | No backup is taken. Check whether the column still exists *before* deciding to back up — there is nothing to protect once migration is already done. (Caught during real execution: an earlier version backed up unconditionally, which meant a second no-op run silently overwrote the one genuinely useful pre-migration backup with a copy of the already-migrated db.) |
| Backup copy already exists from a genuine prior migration attempt | Overwrite `data/app.db.pre-migration.bak` — it is a snapshot of `app.db` as it stood immediately before *this* migration attempt, not a version history. Only reachable now when `needs_migration()` is true, i.e. an earlier attempt didn't finish. |

---

## Testing Requirements

- `test_write_then_read_roundtrip` — text survives byte-identically, including numbers,
  currency symbols and parenthesised negatives.
- `test_write_is_idempotent` — writing identical text twice produces one file.
- `test_path_sharding` — the layout matches the specified scheme.
- `test_read_missing_hash_raises`.
- `test_migration_verifies_hashes` — a deliberately corrupted `text_hash` aborts the
  migration rather than proceeding.
- `test_migration_idempotent`.
- `test_migration_dry_run_writes_nothing` — `--dry-run` reports counts but creates no
  files, no backup, and does not touch the schema.
- `test_migration_backs_up_before_dropping` — the real run produces
  `app.db.pre-migration.bak` before the column is gone.
- `test_extract_writes_hash_only` — no section row carries inline text after the change.
- `test_extract_skips_db_write_when_hash_unchanged` — re-extracting identical text does
  not touch the row (R5).

---

## Likely Files Affected

```
edgar/db.py              (schema)
edgar/sections.py        (write path, helpers)
edgar/pipeline.py        (migrate-sections, --dry-run, backup step)
edgar/config.py          (SECTIONS_DIR)
edgar/section_store.py   (new: write/read/path helpers, migration logic)
ARCHITECTURE.md          (§4 rationale, §6 schema, decision log)
.gitignore               (data/*.bak)
tests/test_sections.py
tests/test_section_store.py
```

---

## Notes for the Implementer

- Run `VACUUM` after dropping the column, otherwise SQLite keeps the freed pages and the
  file size will not fall.
- Commit the migration as a separate commit from any other change, so the size reduction
  is visible in isolation and easy to reason about later.
- Add a decision-log entry in `ARCHITECTURE.md`: mutable state stays small, large data is
  immutable and content-addressed. This is the reasoning future specs should follow when
  deciding where something belongs.
- Note as future work: orphaned section files are never cleaned up. Acceptable at this
  scale and deliberately deferred.
- Run `migrate-sections --dry-run` first and inspect the output before running for real.
  The real run is a one-way schema change on the only committed copy of 2,174 rows of
  section text extracted via live SEC calls; the dry run costs nothing and catches hash
  mismatches or count surprises before the column is dropped.
