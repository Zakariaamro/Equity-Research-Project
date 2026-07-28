# SPEC-006A — Budget Guardrails

**Version:** 1.0
**For:** Claude Code
**Depends on:** SPEC-006 (in progress)
**Reference:** `ARCHITECTURE.md`
**Estimated effort:** 2–3 hours
**Priority:** Do this before resuming any paid run.

---

## Why

On 2026-07-27 a $10 API balance was exhausted. Roughly $3.79 was this project's pipeline,
working exactly as designed. Roughly $6.28 was **Claude Code itself**, billing to the same
account because `ANTHROPIC_API_KEY` was set in the shell environment — $5.24 of it on
Opus 5, a model this project never calls.

The pipeline's cap was correct, tested, and enforced. It simply had no view of the process
spending more than it was.

**A budget guard only protects what routes through it.**

The fix is not a better single cap. It is **layers that fail independently**, so no single
mistake — a misrouted client, a careless prompt bump, a loop in a scheduled job — can
drain the account.

---

## The threat model

Every way money can leave, and which layer stops it:

| Path | Stopped by |
|---|---|
| Pipeline run costs more than expected | L3 per-run cap, L4 confirmation |
| Prompt version bump silently invalidates the cache and re-runs everything | L5 cache-impact warning |
| Bug causes a retry or call loop | L3 per-run cap, L6 call ceiling |
| GitHub Actions job misbehaves overnight, unattended | L7 scheduled-run cap |
| Public assistant endpoint is hammered | L8 assistant caps |
| Another process (Claude Code, a script, a future tool) bills to the same key | **L1 only** |
| Everything else, including the unknown | **L1 only** |

Note the last two rows. **Only Layer 1 catches what the code cannot see**, which is
exactly the class of failure that happened.

---

## Requirements

### L1 — Outside the code (operator action, not implementable here)

The only guard that works regardless of what any code does.

- **Prepaid balance with auto-reload OFF.** With auto-reload disabled, the account balance
  *is* an absolute ceiling. No bug, loop, or misrouted client can exceed it. If
  auto-reload is on, a runaway process refills its own budget indefinitely.
- **A monthly spend limit** in Console, if available for the account tier.
- Keep the balance at roughly what the next phase needs, not a comfortable buffer. A
  small balance is itself a guard.

Record these in `ARCHITECTURE.md` as operator responsibilities, since no code change can
enforce them.

### L2 — Lifetime cap reflects real remaining money

`LLM_BUDGET_USD` currently reads 20.00, but $10.07 of real money has already been spent
and the project's own share of it was ~$3.79.

- Set `LLM_BUDGET_USD = 10.00`.
- Document in config why: the figure tracks **money actually available**, not the
  original project intent. Raising it must be a deliberate edit, not a default.

### L3 — Per-run cost ceiling

A lifetime cap limits total damage. It does nothing to limit the damage of one bad run.

- `LLM_MAX_RUN_COST_USD = 2.00`.
- Track spend accumulated **within the current process**. Exceed it and the run stops
  cleanly, reports what it completed, and exits non-zero.
- Overridable per invocation with an explicit flag, never by editing config mid-run.

### L4 — Cost confirmation that cannot be muscle memory

- Any run whose dry-run estimate exceeds `LLM_CONFIRM_THRESHOLD_USD = 1.00` requires
  `--confirm-cost N`, where **N must match the dry-run estimate** within a small tolerance.
- A mismatch aborts with both numbers shown.

The point is that the number must be **read** before it can be typed. A bare `--yes` flag
becomes reflexive within a day; a flag carrying the figure does not.

### L5 — Cache-impact warning

The cache is what makes re-runs free. A prompt version bump silently invalidates it, and
the cost of that is invisible at the moment of the decision.

- Before any run, report: calls that will hit the cache, calls that are new, and — if the
  prompt version changed since the last run — **how many previously-cached analyses this
  change invalidates and what re-running them costs.**
- If a version bump invalidates more than `LLM_CACHE_INVALIDATION_WARN = 50` analyses,
  require explicit acknowledgement.

### L6 — Call-count ceiling

A cost cap assumes cost is computed correctly. A count ceiling holds even if it isn't.

- `LLM_MAX_CALLS_PER_RUN = 300`. Exceed it and the run stops.
- Deliberately redundant with L3. Two independent limits, different failure modes.

### L7 — Scheduled runs are held to a much lower ceiling

Unattended work carries the highest risk: nobody is watching, and a loop can run all night.

- `LLM_SCHEDULED_RUN_MAX_COST_USD = 0.50`.
- A normal incremental run — one new filing, 10–15 sections — costs roughly $0.25, so
  this is generous for correct behaviour and tight against runaway behaviour.
- Scheduled runs must never invoke a backfill or `--force` path.
- Exceeding the ceiling fails the job loudly rather than continuing.

Binding on the GitHub Actions spec when it is written.

### L8 — Assistant caps

Binding on the assistant spec when it is written, recorded here so it cannot be forgotten:

- Per-session question limit.
- Daily spend ceiling for the assistant, separate from the pipeline's.
- Deployment stays private and password-gated.

### L9 — Environment canary

The specific failure that caused this incident should announce itself if it recurs.

- On startup, if `ANTHROPIC_API_KEY` is set in the environment, print a prominent warning:
  another process may be billing to this account, and this project does not use that
  variable.
- Warn, do not refuse — it may legitimately be set for unrelated reasons. But it must
  never be silent again.

### L10 — Spend visibility on every run

- Every paid command prints, on completion: this run's cost, lifetime recorded spend,
  and remaining budget.
- `validate` reports the same, plus reconciliation against the ledger.
- No paid operation may complete without stating what it cost.

---

## Acceptance Criteria

1. `LLM_BUDGET_USD` is 10.00 with the reasoning recorded in config.
2. A run exceeding `LLM_MAX_RUN_COST_USD` stops cleanly, reports partial completion, exits
   non-zero. Demonstrate with a temporarily low value.
3. A run estimated above the confirmation threshold refuses without `--confirm-cost`, and
   refuses again when the supplied figure does not match. Demonstrate both.
4. A run reports cache hits, new calls, and — after a prompt version bump — the number of
   invalidated analyses and the cost to regenerate them.
5. Exceeding `LLM_MAX_CALLS_PER_RUN` stops the run. Demonstrate with a low value.
6. The environment canary fires when `ANTHROPIC_API_KEY` is set. Demonstrate.
7. Every paid command prints run cost, lifetime spend, and remaining budget.
8. `ARCHITECTURE.md` records the incident, the threat-model table, and the operator
   responsibilities under L1 that no code can enforce.
9. All existing tests pass, plus tests for each new limit.

---

## Testing Requirements

- `test_per_run_cost_ceiling_stops_run`
- `test_call_count_ceiling_stops_run`
- `test_confirm_cost_required_above_threshold`
- `test_confirm_cost_rejects_mismatched_figure`
- `test_cache_invalidation_reported_on_version_bump`
- `test_env_canary_warns_when_anthropic_api_key_set`
- `test_scheduled_run_uses_lower_ceiling`
- `test_partial_run_reports_completed_work`

---

## Notes for the Implementer

- Layers are deliberately redundant. Do not "simplify" by removing one because another
  covers the same case — independence is the whole design. L3 and L6 overlap on purpose:
  one assumes cost arithmetic is right, the other does not.
- Every limit fails **closed**. When a limit cannot be evaluated, refuse rather than
  proceed.
- Every refusal writes a ledger row. A refusal that leaves no trace is invisible, and
  invisible refusals get misdiagnosed as bugs.
- Record the incident honestly in `ARCHITECTURE.md`, including that the original cap was
  correct and still insufficient. That is the instructive part.
