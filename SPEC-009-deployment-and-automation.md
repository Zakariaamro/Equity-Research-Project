# SPEC-009 — Deployment and scheduled updating

**Date:** 2026-08-16
**Depends on:** SPEC-008 (complete), SPEC-006A (complete)
**Status:** Design. Two decisions must be made before any code is written — see "Decide first".

## Objective

The dashboard updates itself. New filings are ingested, analysed and briefed on a schedule,
without anyone remembering to run anything, and the deployed app reflects them.

Two separable jobs, and they should be built in this order:

1. **Scheduled ingestion** — a GitHub Actions cron that checks EDGAR, runs the pipeline on
   anything new, and commits the result. Independent of the dashboard.
2. **Deployment** — Streamlit Community Cloud serving the app behind the existing password
   gate, redeploying when the database changes.

---

## Prerequisites — none of this runs without these

### P1 — Budget

$0.81 remains of the $8.50 cap. That does not support automation: a job that dies mid-run
because the account is empty is worse than no job, because nobody notices and the dashboard
quietly serves stale numbers that look current.

Top up the Console balance, raise `LLM_BUDGET_USD` deliberately to match, and keep
auto-reload **off** — the prepaid balance is the only guard that survives a bug in
everything else (SPEC-006A L1).

Sizing: an incremental run is roughly $0.20–0.25 per filing at current measured rates. Three
companies file roughly 12 times a year between them, plus 8-Ks. **$5 covers about a year.**

### P2 — The cost estimator is known to be wrong, and L7 depends on it

Measured twice, consistently: the brief generator's cost estimate runs about **1.9× low**
(v1: $0.5250 actual vs $0.2905 estimate; v2: $0.5963 vs $0.3126).

This matters here specifically. SPEC-006A L7 caps a scheduled run at $0.50 and L4 requires
confirmation above $1.00 — both compare against an estimate. An unattended job sized on an
estimator that's half of reality will either abort mid-run or sail past a gate it should
have hit.

**Fix the estimator before the first scheduled run.** `BRIEF_ESTIMATED_GENERATOR_OUTPUT_TOKENS`
is calibrated against the prompt's stated 3–6 sentences; the generator actually produces
~12.9. Calibrate against observed output, and record that the constant tracks measured
reality rather than intended behaviour, so nobody "corrects" it back later.

#### Resolution (2026-08-20)

Recalibrated directly off this project's own `llm_calls` ledger — real API responses, not a
re-estimate of a re-estimate. `prompt_name='filing_brief', prompt_version='v2'` (the
currently active generator prompt): 19 real calls, mean output 1414.6 tokens, max 1780 (v1's
18 calls — mean 1258, max 1625 — checked too and tell the same story, but v1 is retired and
isn't what a future call looks like). `BRIEF_ESTIMATED_GENERATOR_OUTPUT_TOKENS` goes
**500 → 1800**: the observed max, rounded up, not the mean — this number feeds a pre-flight
*budget* gate (L4/L7), where underestimating is the dangerous direction, so it's calibrated
to cover the full observed range rather than the typical case. Stays comfortably under the
2048 hard truncation cap.

**Found while fixing this, not named in this item**: the verifier's own estimate
(`BRIEF_ESTIMATED_VERIFIER_OUTPUT_TOKENS`) has an identical problem, independently confirmed
against its own ledger rows — `prompt_name='brief_verifier', prompt_version='v1'`, 37 real
calls, mean 385.9, max 674, against an estimate of 250 (undercounts even the mean). Same
fix, same reasoning: **250 → 700**.

**Verified against real historical spend, not just against the new numbers looking bigger**:
replayed the 19 real `filing_brief` v2 calls' actual input-token counts through
`compute_cost` at the old and new output-token estimates. Real total spend for those 19
calls: $0.3854. Old estimate (500): $0.2116 — **0.55× of real**, i.e. real cost ran
**~1.8× the old estimate**, matching this item's own measured "~1.9× low" almost exactly.
New estimate (1800): $0.4586 — **1.19× of real**, now conservative in the safe direction
instead of dangerously low. Pinned as a regression test
(`test_brief_generator_estimate_no_longer_understates_a_real_recorded_run` in
`tests/test_llm.py`) against these same frozen, real numbers — not a live query against
`data/app.db`, matching this project's standing convention that tests never touch the real
database, but the real 2026-08-20 ledger values recorded as fixed historical facts, exactly
as `test_metrics.py` already does elsewhere in this project for other real, measured values.

**Checked, found separately concerning, deliberately NOT touched here**: `analyze.py`'s own
section-analysis dry-run estimate falls back to a *different* constant,
`LLM_ESTIMATED_OUTPUT_TOKENS = 1024`, when no explicit estimate is passed — and it already
has `--scheduled`/L7 wired (unlike briefs, next paragraph). Real `section_analysis` v3 calls
(n=281) have mean 843 (below 1024 — arguably *not* under-estimated on average) but a heavily
right-skewed distribution: median 363, p90 2470, p95 3250, max 5402 — **30% of real calls
exceed the current 1024 estimate**. This is the same underlying problem in a different
shape (a skewed distribution where the mean is a poor summary statistic, not a flat
under-count), but it wasn't named in this item's own text, and fixing it well requires a
real design decision (what percentile is "safe enough" for this specific gate) rather than
a drive-by number change. Flagged for a deliberate follow-up, not fixed here.

**Also found, not fixed here**: `generate-briefs` has no `--scheduled` flag at all — unlike
`analyze-sections`, nothing currently clamps its `RunGuard`'s ceiling against
`LLM_SCHEDULED_RUN_MAX_COST_USD` for brief generation. The per-call gates that DO run
(`ensure_budget_available`, the in-loop `RunGuard.check_before_call`) are already safe
regardless of this item's estimator bug — both use `BRIEF_MAX_OUTPUT_TOKENS`, the defensive
cap, not the estimate — so no unsafe spend was possible before this fix. But Part A's
scheduled job will call both `analyze-sections --scheduled` and `generate-briefs` in
sequence, and only the first of those two currently has L7 wired at all. This belongs to
Part A's build, not this prerequisite — noted here so it isn't rediscovered the hard way
once Part A starts.

#### Both follow-ups resolved (2026-08-24)

**`generate-briefs` L7 wiring**: see Part A's own "Constraint 1 resolution" below —
`--scheduled` added, mirroring `analyze-sections` exactly, plus a new `scheduled-llm-run`
command that shares one combined ceiling across both LLM stages rather than giving each its
own independent $0.50.

**`LLM_ESTIMATED_OUTPUT_TOKENS`, p95 not the mean**: re-measured against the same 281-call
real corpus. The mean (843) is the wrong summary for this shape — right-skewed enough that
30% of real calls still exceeded it, not a working margin for a budget gate where
underestimating is the dangerous direction. Went to **p95, rounded up: 1024 → 3300** (raw
p95 3250), the same "underestimating is dangerous, so lean toward the observed tail, not the
center" principle the brief generator's own recalibration used — but NOT the max (5402) the
way the brief generator's was: checked first, and replaying 5402 across this same 281-call
corpus would put the full-run dry-run estimate at 3.18x real spend, close to reproducing the
"12x, uninformative" failure this constant's own original comment already records from ITS
first calibration. p95 protects the individual-call gate against the 30%-exceeding-estimate
case without re-inflating a full run's aggregate estimate into the same trap. (Checked too:
even at 3300, a 281-call bulk-sized replay comes to 2.18x real spend — a real, known
overshoot, but this constant is sized for a small, INCREMENTAL scheduled run's estimate, the
size L7's own $0.50 ceiling assumes, not a 281-call backfill.) Comfortably under the 4096
hard truncation cap. Full reasoning recorded in `config.py`'s own comment on
`LLM_ESTIMATED_OUTPUT_TOKENS`, pinned by
`test_section_analysis_output_estimate_covers_the_real_measured_p95` in `tests/test_llm.py`.

### P3 — Secrets

Two values the Action needs, as GitHub repository secrets:

- `SEC_USER_AGENT` — SEC rejects unidentified requests. A missing value fails the run at the
  first fetch.
- `EQUITY_RESEARCH_ANTHROPIC_API_KEY` — deliberately **not** `ANTHROPIC_API_KEY`. That
  distinction exists because a generic key in the environment caused $6.28 of Claude Code
  spend to bill against this project's balance (SPEC-006A's founding incident). Do not
  rename it for convenience.

---

## Decide first

Two design questions. **Do not start building until both are answered** — they're cheap now
and expensive after a year of commits.

### Decision 1 — How does the database reach the deployed app?

Streamlit Community Cloud builds from the repo, and `data/app.db` is already committed, so
today the answer is "by accident, and it works."

The problem is what happens when an Action commits it on a schedule. SQLite doesn't
delta-compress, so **every run writes a fresh ~8MB binary blob into git history.** A weekly
job adds roughly 400MB a year. `.git` is already 68MB.

Relevant fact for the options below — what's actually committed under `data/`:

| Path | Size | Needed by the deployed app? |
|---|---|---|
| `data/app.db` | 8.1 MB | **Yes** — everything reads it |
| `data/sections` | 11 MB | **Yes** — the Sections viewer reads section text |
| `data/raw` | 39 MB | **No** — pipeline reproducibility only, re-fetchable from EDGAR |

Options:

- **(a) Keep committing, accept the growth.** Simplest. Nothing to build. Revisit when the
  repo becomes painful.
- **(b) Keep committing `app.db` and `sections`, stop committing new `data/raw`.** Halves
  future growth. Raw archives stay re-fetchable from EDGAR; existing history is untouched
  (rewriting it is not worth it). Costs the ability to re-run the pipeline offline from a
  clean clone.
- **(c) Publish `app.db` as a GitHub Release asset**, app downloads at startup. Keeps history
  clean; adds a fetch, a cache, and a failure mode where the app can't start.

**Report the tradeoffs and recommend one. Do not just pick.** Note that GitHub Actions
clones the repo on every scheduled run, so repo size is also a recurring cost against the
free tier's monthly minutes.

#### Resolution (approved 2026-08-24): (b) — and this item's own premise was wrong

**The "SQLite doesn't delta-compress, ~8MB/run, 400MB/year" premise above is incorrect, and
worth correcting here rather than letting it get re-derived the same wrong way later.**
Measured before deciding, not assumed: `data/app.db`'s real 18-commit history (2026-07-26 to
2026-08-13) totals 173MB of LOGICAL blob size across those versions, but only **6.1MB of
actual packed `.git` storage** after `git gc` — roughly 28x smaller. Git's own pack-level
delta compression finds substantial byte-level similarity between successive commits of the
SAME file regardless of that file's internal format; "SQLite doesn't delta-compress" is true
of SQLite's own file format and irrelevant to how git stores successive snapshots of it. The
~8MB-per-commit, 400MB/year figure was never measured against this project's own history
before being written down.

This does NOT change the recommendation, but it changes WHY. `data/sections` and `data/app.db`
are both needed by the deployed app, and (now confirmed) `app.db`'s own growth is
substantially self-limiting via ordinary git packing. `data/raw` is different in kind, not
degree: it is not read by the deployed app at all, AND each archive is unique, unrelated
content per filing (nothing for delta compression to work against) — a genuinely avoidable,
non-compressible cost regardless of how (a) turned out to be less scary than described.

**Decided: (b).** `data/raw/` added to `.gitignore`; `git rm -r --cached data/raw` stops
tracking it going forward (files stay on disk, still usable locally, just untracked). The
~40MB already in history is deliberately left alone — rewriting history to remove it was
explicitly ruled out (not worth the disruption for a one-time, non-recurring cost). (c) stays
un-built: nothing measured here justifies its added complexity (a fetch step, a cache, a new
can't-start failure mode) over (b)'s free win.

Also worth recording: `actions/checkout`'s default is a **shallow clone** (depth 1), so a
scheduled run's clone cost is dominated by the CURRENT working-tree size, not cumulative git
history — the "recurring CI minutes" concern in this item's own text is smaller than a
naive full-history read would suggest, as long as the workflow doesn't request full history.

### Decision 2 — Cadence

What schedule, and why that one? Filings arrive in clusters (earnings season), not evenly.
A daily check that finds nothing costs an Actions minute and no API spend; a weekly check
may leave the dashboard four days stale.

Propose a cadence with reasoning, including what happens when a run finds nothing new — that
should be the common case and must be cheap and silent.

#### Resolution (approved 2026-08-24): daily, early UTC

A no-op check costs one Actions minute and zero API spend (Part A's own AC1) regardless of
cadence, so the only real tradeoff is staleness — daily minimizes it for free. Filings
cluster around each company's own quarterly reporting window; daily checking keeps the
dashboard within at most a day of a new filing landing, exactly when staleness would be most
visible. No reason to go more frequent than daily (SEC processing lag, and no user need for
hour-level freshness, make it unnecessary).

**The no-op case is silent** — no notification when nothing new is found, matching "cheap and
silent." **A real failure is loud**: GitHub's own default behaviour already emails the actor
who last edited the workflow file when a scheduled run fails, which is where AC4's "fail
loudly... surface failures somewhere you'll actually see them" is satisfied — no separate
notification channel built for this.

---

## Part A — Scheduled ingestion

### Binding constraints

These are not negotiable and each exists because something went wrong:

1. **`LLM_SCHEDULED_RUN_MAX_COST_USD = 0.50`** (SPEC-006A L7). Exceeding it fails the job
   loudly rather than continuing.
2. **Never invoke a backfill or `--force` path** from a scheduled run.
3. **Never scope `compute-observations` by `--ticker`.** Doing so silently breaks
   cross-company same-day observation annotation — `validate` catches it only after the
   damage. This was found live during the AMZN ingest and the function's own docstring had
   already warned about it. Make it a spec constraint, not a comment.
4. **Fail loudly.** A silent failure means stale data presented as current, which is the
   worst outcome this system can produce. The job must surface failures somewhere you'll
   actually see them — decide where and say so.
5. **`validate` runs before the commit**, not after. A run that produces invalid data must
   not commit it.

#### Constraint 1 resolution (2026-08-24): L7 wired into generate-briefs, ceiling shared

Found while doing SPEC-009 P2: `generate-briefs` had no `--scheduled` flag at all — only
`analyze-sections` did. Both stages call the LLM, both stages spend real money, and only one
was clamped.

**Fixed in two parts.** First, `--scheduled`/`--max-run-cost`/`--max-calls` added to
`generate-briefs`, mirroring `analyze-sections` exactly — `brief.run_brief_generation` gained
the identical `scheduled: bool` parameter `analyze.run_analysis` already had, same clamp
(`min(effective_run_cost, LLM_SCHEDULED_RUN_MAX_COST_USD)`). This alone would still let each
stage spend up to $0.50 independently if run as two separate CLI invocations, though — not
what "across BOTH stages" requires.

**Second, and this is the part that actually enforces "the run as a whole": a new
`edgar.pipeline scheduled-llm-run` command** (`run_scheduled_llm_stages`) that runs
`analyze-sections` then `generate-briefs` from ONE Python process, computing the second
stage's ceiling as `LLM_SCHEDULED_RUN_MAX_COST_USD - (what the first stage actually spent)`
and passing it in explicitly. If the first stage alone reaches the combined ceiling, the
second is skipped outright with a stated reason, rather than constructed with a zero/negative
ceiling and left to refuse silently on its first candidate.

Two ways to share a ceiling across two separate CLI invocations were considered. (a) Keep
them as two separate commands and have the orchestrating shell script/workflow parse stage
1's printed cost and pass `--max-run-cost <remainder>` to stage 2. (b) Call both functions
from one Python process. (a) puts the shared-ceiling invariant in un-tested shell arithmetic
parsing a human-readable log line; (b) is a plain function, callable from a real pytest
against a fixture database, the same way every other guarantee in this codebase is checked.
Chose (b) for that reason alone — not because (a) is incapable of being correct, but because
nothing here would prove it stayed correct.

Deliberately narrow: `scheduled-llm-run` covers only the two LLM-calling stages, not the rest
of Part A's job (discover/extract/ingest-xbrl/compute-metrics/compute-observations/validate/
commit) — building the rest still waits on both "Decide first" questions below, unchanged by
this fix.

Tested at three levels: an arg-capturing unit test proves the arithmetic (stage 2 receives
exactly `ceiling - stage1_actual`, not a fresh $0.50); a companion test proves stage 2 is
skipped, not attempted with a negative ceiling, when stage 1 alone exhausts it; an
end-to-end test runs both REAL functions (fake LLM clients only) and confirms combined spend
never exceeds the ceiling — catching any wiring bug the mocked tests alone could miss.

### Acceptance criteria

1. A scheduled run with no new filings completes cheaply, spends nothing, and commits
   nothing.
2. A scheduled run with a new filing ingests, analyses, briefs, validates, and commits — and
   reports its actual cost, lifetime spend and remaining budget (L10).
3. A run that would exceed L7's ceiling stops and fails the job.
4. A run with a missing or invalid secret fails at the first step with a clear message,
   rather than partway through.
5. The Action's commits touch only `data/`. **You never hand-edit those files**, so this
   should never conflict — but state it as a rule so it isn't discovered the hard way.

### Resolution (approved 2026-08-24): `.github/workflows/scheduled-ingestion.yml`

Built, not yet deployed via the cron on its own — per instruction, this is proven on a real,
manually-triggered run first (`workflow_dispatch`), before anything relies on the schedule
alone or Part B gets built.

**Sequence** (each its own named step, so a failure is immediately locatable in the Actions
UI): verify secrets present → `pytest -q` (fail fast, before any real spend) → `discover` →
`extract` → `ingest-xbrl` → `compute-metrics` → `backfill-readability` → `compute-observations`
(no `--ticker`, ever) → `scheduled-llm-run` (the combined-ceiling command from the P2
follow-up above — not two separate, uncoordinated `analyze-sections --scheduled` /
`generate-briefs --scheduled` calls) → `validate` → commit-if-changed.

**Secrets** declared once at job level, not per step — drafting this file caught its own
mistake this way: `ingest-xbrl` was initially missed from a per-step `SEC_USER_AGENT` list
(it also calls `EdgarClient()`, confirmed via the constructor's own eager resolution), found
only by re-checking against the client's source. Job-level env removes the whole class of
"which steps need which secret" bookkeeping. `EQUITY_RESEARCH_ANTHROPIC_API_KEY` only, never
`ANTHROPIC_API_KEY` (SPEC-006A's founding incident).

**AC4** ("fails at the first step with a clear message"): a dedicated step calls
`config.get_sec_user_agent()`/`config.get_anthropic_api_key()` — reused, not
reimplemented — before `discover` even runs, so a missing Anthropic key (not needed until
the LLM stage, many steps later) is still caught immediately rather than after several other
steps have already run for nothing.

**"Never invoke a backfill... path"**: read as ruling out the genuinely one-time,
historical-reprocessing commands (`migrate-sections`, `backfill-manifests`) and any
`--force` path — neither appears anywhere in this workflow. `backfill-readability` (no
`--force`) is deliberately INCLUDED despite its name: `sections.backfill_readability` is
idempotent by construction (skips any row already populated), functioning as the ordinary
incremental step new sections need, not a bulk reprocessing of history. Flagged explicitly,
not assumed, in case this reading of the constraint is wrong.

**AC1/AC5**: the commit step checks `git status --porcelain` on exactly `data/app.db` and
`data/sections` before committing — empty means nothing runs, matching "cheap and silent" on
the common no-op case. Never touches `data/raw` (gitignored, Decision 1).

**Found while building, not fixed here**: `cmd_extract` (and possibly others) reports a
per-filing failure count without exiting non-zero on a partial failure — a real gap against
"fail loudly" at the level of an individual command, though `validate`'s own exit code
(binding constraint 5, already gates the commit) is the authoritative backstop that keeps a
downstream consequence of such a failure from ever reaching the committed database. Hardening
individual commands' exit codes is a separate, smaller change, not done here.

**Tested**: `tests/test_scheduled_workflow.py` parses the workflow file itself (not a live
run — nothing here can execute a GitHub Actions runner) and checks it structurally against
every constraint above: every referenced `edgar.pipeline` command is real and currently
registered (catches a future CLI rename before a scheduled run would), `compute-observations`
never carries `--ticker`, no step ever carries `--force`/`--sample`,
`migrate-sections`/`backfill-manifests` never appear, `validate` precedes the commit step,
`scheduled-llm-run` (not two separate calls) is what runs the LLM stages, both secrets are
referenced by name and `ANTHROPIC_API_KEY` never is, the commit step's `git add` names only
`data/app.db data/sections`, and no notification integration (Slack/webhook/etc.) exists for
either the silent-on-no-op or loud-on-failure half of Decision 2.

---

## Part B — Deployment

### The fresh-environment check is an acceptance criterion, not a note

Two packaging gaps have already shipped in this project: `streamlit` and `dashboard` were
missing from `pyproject.toml`, then `anthropic` was. Both were invisible on a warm machine
and would have broken a fresh deploy.

**AC: a genuinely fresh clone, a fresh install, and `streamlit run` must be verified before
the first deployment**, and that check must be part of the deployment process rather than a
one-off. Streamlit Cloud builds from scratch every time, so its build log must show the
project itself being installed — not only third-party packages.

**A third instance, this AC's concrete evidence rather than a hypothetical (2026-08-24)**:
Part A's own scheduled-ingestion workflow — a genuinely fresh environment in exactly the
sense this AC cares about, just GitHub Actions' rather than Streamlit Cloud's — failed on
its very first real run, at the `pytest` step, before fetching or spending anything.
`tests/test_validate.py` had `999_000_000` (a Python numeric-underscore literal) written
directly inside a raw SQL string. Python accepts numeric underscores in any int/float
literal; SQLite's own parser only accepts them from version 3.46 (2024). The operator's Mac
ships a newer SQLite than the GitHub Actions runner's, so the test passed locally and failed
in CI — invisible on the machine that built it, exactly the shape of every prior instance of
this AC's own warning, just with SQLite's version instead of a missing package.

Fixed at the one real occurrence (`999_000_000` → `999000000`); Python-level numeric
underscores are unaffected everywhere else in the codebase (dozens of legitimate uses —
`LLM_MAX_INPUT_TOKENS_ESTIMATE = 150_000` and similar — none of which are ever parsed as SQL
text). Guarded structurally, the same SHAPE as `test_dashboard_structure.py`'s currency-
escaping guard: `tests/test_sql_literal_safety.py` scans every `.py` file in the project for
string literals reaching a `.execute()`/`.executemany()`/`.executescript()` call's SQL-text
argument, and fails if a numeric-underscore literal is present there — confirmed live to
catch the real line before it was fixed, and, while building it, to correctly NOT flag a
coincidental digit-underscore-digit shape inside a SQL `--` comment (`edgar/db.py`'s own
schema script references a real, date-stamped filename that happened to match the pattern
being checked for — found and fixed by stripping SQL comments before the check, not by
loosening the check itself).

### Other requirements

- The password gate (SPEC-008 R2) protects the deployed app. `.streamlit/secrets.toml` is
  gitignored, so the password becomes a Streamlit Cloud secret. **Change it from
  `local-dev-only` before deploying.**
- The auth gate calls `st.stop()` before `st.navigation()`, so the login screen shows
  Streamlit's auto-discovered page names (`app`, `filings`, `financials`…). Confirmed not to
  be a bypass, but it leaks internal structure on a public URL. Suppress it before deploying.
- A redeploy restarts the process, so `@st.cache_data` is cold — no stale-cache concern
  across deploys.
- The deployed app must show **what filing it's current as of**, on every page. A dashboard
  that silently serves stale data is the failure mode this whole spec exists to prevent.

### Resolution: code-side Part B items (approved 2026-08-25)

**Login-screen leak, fixed structurally.** Read the installed Streamlit source
(`streamlit/runtime/pages_manager.py`, `streamlit/runtime/scriptrunner/script_runner.py`,
1.60.0) before deciding, not assumed from docs: `PagesManager.uses_pages_directory` is set
`True` purely by `Path(app.py's own parent / "pages").exists()` — a class-level flag computed
from the filesystem alone, independent of whether `app.py` itself calls `st.navigation()`.
When set, the script runner calls a legacy `_mpa_v1(...)` shim INSTEAD OF `app.py`'s own
top-level code: it globs every `.py` file in that directory, builds a page per bare filename,
and renders its OWN sidebar navigation before `app.py`'s own auth gate — let alone its real,
correctly-titled `st.navigation()` call — ever runs. That is the exact leak. Fixed by
renaming `dashboard/pages/` → `dashboard/app_pages/`: with no directory literally named
`pages` next to `app.py`, `uses_pages_directory` can never become `True` again, so `_mpa_v1`
never runs and nothing renders pre-auth beyond the password prompt. Pinned two ways: a
filesystem check, and a live call into Streamlit's own `PagesManager` class confirming the
real flag reads `False` against this project's actual `app.py`.

**Data-freshness caption, shared not per-page.** `data.get_most_recent_filing()` — every form
type across the whole watchlist, including 8-Ks, ordered by `discovered_at` (when this
deployment's own pipeline noticed the filing) rather than `filing_date` (when SEC says it was
filed): `filing_date` alone can look fresh even if the scheduled job silently stopped
running, since the same quarterly filing stays "most recent by `filing_date`" for months
either way — deliberately different scope from `get_anchor_filing`'s own 10-K/10-Q-only,
per-company anchor, which answers a different question ("what is this page's analysis built
from"). `components.data_freshness_caption()` renders it once from `app.py`, same placement
discipline as the existing `environment_caption()` — every page gets it by construction, no
per-page implementation to forget or word differently. The Filings page's own old caption
("Data as of the current deployment's database") was removed, not reworded — it promised a
date and never gave one.

**Fresh-environment AC, verified for real, not assumed — and it found a real bug.** A
genuinely fresh `git clone` into a temp directory, a fresh `pip install -e ".[dev]"`, and
`streamlit run` — twice, the second time to confirm the fix. The fresh install independently
resolved **Streamlit 1.62.0** and **anthropic 1.0.0** (a major version), both ahead of the
long-lived dev `.venv`'s 1.60.0 / 0.120.2 — `pyproject.toml` has no upper bound on either, by
design, matching this project's own established philosophy (decision log #66: code should
work at the declared floor AND at whatever's newest, not pinned to one snapshot).

The first fresh-clone run **hung** — genuinely, not slowly: 100% CPU for over an hour before
being killed. Root cause: `tests/test_sql_literal_safety.py`'s own `_project_py_files()`
(added earlier this session) scanned `ROOT.rglob("*.py")` filtered by an exact-name
venv-exclusion set (`.venv`, `venv`, …). A fresh clone's own venv, named wherever the
operator puts it (`.venv-fresh`, here, to keep it distinct from the project's existing
`.venv`) was not in that set — confirmed live, it walked and `ast.parse`'d **7,117**
third-party files (the entire installed dependency tree) against 56 real project files. Fixed
by scoping to this project's actual, named source directories (`edgar/`, `dashboard/`,
`tests/`, `scripts/`) rather than "everything under the repo root minus a blocklist," which
can never enumerate every name a future venv, scratch directory, or build artifact might get
— structurally immune to this class of gap now, not just patched for the one name that broke
it. Pinned as its own regression test (a generous file-count ceiling plus a check that every
scanned file lives under one of the four real source directories).

Re-verified end to end after the fix, against a second fresh clone: full 600-test suite in
21 seconds; `streamlit run dashboard/app.py` served `/_stcore/health` as `ok` with no errors
in its log; `AppTest.from_file("dashboard/app.py")` ran the real entry point directly with no
exceptions, both pre- and post-login, correctly landing on "Overview" after authenticating and
showing both sidebar captions rendering real data (`Streamlit 1.62.0`; `Data current as of:
AMZN 10-Q filed Jul 31, 2026 (added Aug 4, 2026)`).

**One thing checked but not fully verifiable without a paid call, flagged rather than
asserted**: `edgar/llm.py`'s `_RealAnthropicClient` (the real, billed path Part A's scheduled
job actually uses) constructs `anthropic.Anthropic(api_key=..., max_retries=...)` and calls
`.messages.create(model=..., max_tokens=..., thinking=..., messages=...)`. Checked
structurally against the real installed 1.0.0 package via `inspect.signature` (no network
call — object construction and signature introspection only, no paid request): every
parameter this project passes still exists, under the same names, on both the constructor
and `.messages.create`. This is real evidence the major version bump doesn't break
construction, not a guess — but it does not, and cannot, confirm the response object's shape
(`response.content[i].text`/`.type`, `response.usage.input_tokens`/`output_tokens`,
`response.stop_reason`) without an actual billed call, which is outside what this agent may
do on its own. Worth a first real Part A run being watched, not assumed clean.

### Resolution: dependencies pinned (approved 2026-08-25)

The finding above — a fresh install independently resolving Streamlit 1.62.0 and anthropic
1.0.0, both untested — is not a one-off. This project has now lost real time to three
separate unpinned-version incidents, each a different mechanism: a bare `streamlit` on PATH
resolving to an unrelated Anaconda install (decision log #52), a real `.venv`'s newer
Streamlit exposing a `TextColumn(alignment=...)` kwarg an older declared floor didn't have
(decision log #66), and this fresh-clone check's own anthropic major-version jump. Deployment
(Streamlit Cloud) and the scheduled Action (Part A) both install fresh on every run — an open
upper bound means both would silently run library versions nothing here has ever executed
against, unattended, on a schedule.

**Every dependency in `pyproject.toml` is now pinned with the compatible-release operator**
(`~=X.Y.Z`, PEP 440 — equivalent to `>=X.Y.Z, ==X.*`), to exactly what this project's own
`.venv` has actually been built and tested against throughout: `requests~=2.34.2`,
`beautifulsoup4~=4.15.0`, `streamlit~=1.60.0`, `plotly~=6.9.0`, `anthropic~=0.120.2`,
`pytest~=9.1.1`, `pyyaml~=6.0.3`.

**Compatible-release, not exact (`==`)** — the choice between the two, and why: `~=X.Y.Z`
blocks exactly the two failure SHAPES already experienced (a minor-version API surface
change; a major-version jump) while still letting a same-minor-version PATCH release — a bug
fix, a security fix — reach the project without anyone hand-bumping a version number first.
An exact pin would need a human to manually re-approve every single patch release across
every dependency to receive a security fix at all, risking the opposite failure: patches
silently never applied because nobody is watching. This does NOT pin the full transitive
tree (pandas/numpy/pyarrow/httpx/… are pulled in by streamlit/anthropic themselves, never
declared here directly) — a complete, reproducible lockfile (pip-tools/uv or similar) is a
real, larger undertaking, genuinely out of scope for this fix and recorded here as a known
limitation, not silently glossed over.

**Verified, not assumed, before calling this done**: a fresh clone with the new pins applied
installed cleanly (no resolver conflicts between the tightened top-level bounds and their own
transitive requirements), landed on exactly the seven pinned versions, and the full 601-test
suite passed in 20 seconds.

**The anthropic 1.0.0 risk flagged just above is resolved by this pin, not merely
noted.** A structural signature check was never a substitute for a real, successful call —
it was the best available verification *given* an unpinned dependency that had already
drifted. With `anthropic~=0.120.2` in place, Part A's next scheduled run uses the exact
version this project has real, successful call history against; the exposure a fresh install
would otherwise have reintroduced is closed, not deferred.

**Upgrading any pinned dependency is now a deliberate act, never something that happens by
itself at the next scheduled run.** When one is upgraded, it goes through the same
verification this spec item just established: a fresh clone, a fresh install, the full test
suite, and (for streamlit specifically) a real `streamlit run` — not just editing the version
number and trusting a green CI run, since CI's own fresh install would silently pick up
whatever the new pin allows without re-proving the dashboard itself still starts.

---

## Explicitly out of scope

- SPEC-009's assistant page (SPEC-008 forward-looking concern 1). Different spec.
- Segment data, TTM columns, common-size view, the non-operating-income flag — all still
  outstanding from `SPEC-008-review-2`, none of them blocking this.
- The visual pass (C1).
