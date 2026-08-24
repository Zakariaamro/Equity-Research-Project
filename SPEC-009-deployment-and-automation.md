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

---

## Explicitly out of scope

- SPEC-009's assistant page (SPEC-008 forward-looking concern 1). Different spec.
- Segment data, TTM columns, common-size view, the non-operating-income flag — all still
  outstanding from `SPEC-008-review-2`, none of them blocking this.
- The visual pass (C1).
