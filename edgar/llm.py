"""Anthropic client, spend ledger, and response cache (SPEC-006).

The only module permitted to call the Anthropic API -- same rule as
edgar_client.py for SEC (ARCHITECTURE.md §5). Build order matters here more
than anywhere else in the project: the ledger and budget cap
(`ensure_budget_available`, `record_result`) are built and tested FIRST,
before any code that can actually reach the API. A cap designed after the
first call is designed too late -- SPEC-006's own words, taken literally.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from edgar import config

logger = logging.getLogger(__name__)


# --- SPEC-006A L9: environment canary ---

# The Anthropic SDK's own default variable name -- deliberately NOT what
# config.get_anthropic_api_key() reads (see config._ANTHROPIC_API_KEY_ENV_VAR's
# docstring). Warn-only: this variable may legitimately be set for an
# unrelated tool (Claude Code, a script) on the same machine, so its mere
# presence is not itself wrong -- only silent was.
_GENERIC_ANTHROPIC_API_KEY_ENV_VAR = "ANTHROPIC_API_KEY"


def check_environment_canary() -> str | None:
    """L9: if the SDK's default env var is set, return a warning naming it;
    else None. Called on every CLI startup (pipeline.main), not gated to
    paid commands -- "on startup" per SPEC-006A, and the whole point is that
    it must never again go unnoticed for the length of a spent balance."""
    if os.environ.get(_GENERIC_ANTHROPIC_API_KEY_ENV_VAR):
        return (
            f"{_GENERIC_ANTHROPIC_API_KEY_ENV_VAR} is set in this environment. This project reads "
            f"{config._ANTHROPIC_API_KEY_ENV_VAR} instead and never touches {_GENERIC_ANTHROPIC_API_KEY_ENV_VAR} -- "
            "another process (Claude Code, a script, a future tool) may be billing to the same "
            "Anthropic account using this key right now."
        )
    return None


# --- SPEC-006A L3/L6: run-scoped guards ---


@dataclass
class RunGuard:
    """Process-local, per-run state for L3 (per-run cost ceiling) and L6
    (call-count ceiling). Independent of the lifetime budget (L2,
    `ensure_budget_available`) -- two separate layers, deliberately
    redundant: L6 holds even if L3's cost arithmetic is itself wrong (a bad
    LLM_PRICING entry, a compute_cost bug), which is exactly the class of
    failure this project just had (see LLM_PRICING's 2026-07-27 fix note).

    One instance per invocation of `run_analysis`, threaded through to
    `get_or_create_analysis` -- never module-level/process-global state,
    unlike `_warned_this_run` above, so a test (or a future caller running
    multiple batches in one process) can hold several independent RunGuards
    without cross-contamination.
    """

    max_run_cost_usd: float | None = None
    max_calls_per_run: int | None = None
    run_spent_usd: float = 0.0
    run_calls_made: int = 0
    stop_reason: str | None = None

    @property
    def stopped(self) -> bool:
        return self.stop_reason is not None

    def check_before_call(self, estimated_cost: float) -> str | None:
        """Checked BEFORE a call, same discipline as the lifetime budget
        (L2). Returns a refusal reason if starting another call would
        breach either ceiling, else None. Sets `stop_reason` so the caller's
        loop can end the run entirely -- "the run stops cleanly," not just
        this one call gets refused while the loop keeps trying (and
        re-refusing) every remaining candidate."""
        if self.stop_reason is not None:
            return self.stop_reason
        if self.max_calls_per_run is not None and self.run_calls_made >= self.max_calls_per_run:
            self.stop_reason = (
                f"call-count ceiling reached: {self.run_calls_made} of {self.max_calls_per_run} "
                "calls already made this run (L6)"
            )
            return self.stop_reason
        if self.max_run_cost_usd is not None and self.run_spent_usd + estimated_cost > self.max_run_cost_usd:
            self.stop_reason = (
                f"per-run cost ceiling: estimated cost ${estimated_cost:.4f} would bring this run's "
                f"spend to ${self.run_spent_usd + estimated_cost:.4f}, over the "
                f"${self.max_run_cost_usd:.2f} run ceiling (L3; ${self.run_spent_usd:.4f} spent so far this run)"
            )
            return self.stop_reason
        return None

    def record_call(self, cost_usd: float) -> None:
        """Called after an ACTUAL call (status 'ok' or 'error' -- both were
        billed) with its real cost, never an estimate."""
        self.run_spent_usd += cost_usd
        self.run_calls_made += 1


class BudgetExceededError(Exception):
    """Raised when a call's estimated cost would push total recorded spend
    past config.LLM_BUDGET_USD. By the time this is raised, a 'refused' row
    has already been appended to llm_calls -- the refusal itself is logged,
    not just the exception."""

    def __init__(self, estimated_cost: float, spent: float, budget: float) -> None:
        self.estimated_cost = estimated_cost
        self.spent = spent
        self.budget = budget
        super().__init__(
            f"Refusing call: estimated cost ${estimated_cost:.4f} would bring total spend "
            f"to ${spent + estimated_cost:.4f}, over the ${budget:.2f} budget "
            f"(already spent ${spent:.4f})."
        )


# Warn-once-per-run state. Module-level and process-lifetime by design (the
# CLI is a fresh process per invocation, so this naturally resets) -- tests
# that exercise the warning path must call _reset_run_state() first.
_warned_this_run = False


def _reset_run_state() -> None:
    """Test-only: reset the warn-once-per-run flag between test cases."""
    global _warned_this_run
    _warned_this_run = False


def _now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0, tzinfo=None).isoformat()


def compute_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """$ cost for a given token count, from config.LLM_PRICING. Raises if the
    model has no pricing entry -- calling an unpriced model would make the
    budget cap meaningless."""
    pricing = config.LLM_PRICING.get(model)
    if pricing is None:
        raise ValueError(
            f"No LLM_PRICING entry for model {model!r} -- add one (with source and "
            "verification date, SPEC-006 R1a) before calling it."
        )
    return (input_tokens / 1_000_000) * pricing.input_per_mtok + (
        output_tokens / 1_000_000
    ) * pricing.output_per_mtok


def total_spent(conn: sqlite3.Connection) -> float:
    """Sum of every llm_calls.cost_usd row, refused calls included (they are
    always recorded at cost_usd=0.0, so they contribute nothing to the sum
    but remain visible in the ledger -- R1's "ledger with gaps is not a
    ledger")."""
    row = conn.execute("SELECT COALESCE(SUM(cost_usd), 0.0) AS total FROM llm_calls").fetchone()
    return row["total"]


def _insert_call_row(
    conn: sqlite3.Connection,
    model: str,
    input_tokens: int,
    output_tokens: int,
    cost_usd: float,
    status: str,
    prompt_name: str | None,
    prompt_version: str | None,
    note: str | None,
) -> int:
    cursor = conn.execute(
        "INSERT INTO llm_calls "
        "(created_at, model, prompt_name, prompt_version, input_tokens, output_tokens, cost_usd, status, note) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (_now_iso(), model, prompt_name, prompt_version, input_tokens, output_tokens, cost_usd, status, note),
    )
    conn.commit()
    return cursor.lastrowid


def record_refusal(
    conn: sqlite3.Connection,
    reason: str,
    prompt_name: str | None = None,
    prompt_version: str | None = None,
    model: str | None = None,
) -> int:
    """SPEC-006A: a whole-run-level refusal (L4 missing/mismatched
    --confirm-cost, L5 missing --acknowledge-cache-invalidation) that has no
    single call's token estimate to attach to -- still writes a 'refused'
    ledger row, same discipline as the per-call refusals in
    ensure_budget_available/get_or_create_analysis. "Every refusal writes a
    ledger row. A refusal that leaves no trace is invisible" (SPEC-006A,
    Notes for the Implementer)."""
    return _insert_call_row(
        conn, model or config.LLM_MODEL, 0, 0, 0.0, "refused", prompt_name, prompt_version, note=reason,
    )


def ensure_budget_available(
    conn: sqlite3.Connection,
    model: str,
    estimated_input_tokens: int,
    estimated_output_tokens: int,
    prompt_name: str | None = None,
    prompt_version: str | None = None,
) -> None:
    """Check BEFORE calling. Raises BudgetExceededError if this call's
    estimated cost would push total recorded spend past config.LLM_BUDGET_USD
    -- and appends a 'refused' row to llm_calls before raising, so the
    refusal itself is part of the permanent record (R1, AC1/AC2).

    Refused rows always carry cost_usd=0.0 -- no API call was made, so no
    money was actually spent; the ESTIMATED token counts and the would-be
    cost are recorded in `note` for diagnostic purposes, not in `cost_usd`,
    which must reflect real spend only.

    Warns (once per process run) when spend crosses config.LLM_WARN_FRACTION
    of the budget, whether or not this particular call is refused.
    """
    global _warned_this_run
    spent = total_spent(conn)
    estimated_cost = compute_cost(model, estimated_input_tokens, estimated_output_tokens)
    projected = spent + estimated_cost

    if projected > config.LLM_BUDGET_USD:
        _insert_call_row(
            conn,
            model,
            estimated_input_tokens,
            estimated_output_tokens,
            0.0,
            "refused",
            prompt_name,
            prompt_version,
            note=(
                f"estimated cost ${estimated_cost:.4f} would bring total to ${projected:.4f}, "
                f"over the ${config.LLM_BUDGET_USD:.2f} budget (spent ${spent:.4f})"
            ),
        )
        raise BudgetExceededError(estimated_cost, spent, config.LLM_BUDGET_USD)

    if not _warned_this_run and projected > config.LLM_BUDGET_USD * config.LLM_WARN_FRACTION:
        logger.warning(
            "LLM spend at $%.4f of $%.2f budget (%.0f%%) -- approaching the cap",
            projected,
            config.LLM_BUDGET_USD,
            projected / config.LLM_BUDGET_USD * 100,
        )
        _warned_this_run = True


def record_result(
    conn: sqlite3.Connection,
    model: str,
    input_tokens: int,
    output_tokens: int,
    status: str,
    prompt_name: str | None = None,
    prompt_version: str | None = None,
    note: str | None = None,
) -> int:
    """Record an ACTUAL call's outcome ('ok' or 'error') -- the call already
    happened and was billed by Anthropic regardless of whether the response
    was usable, so cost is computed from the real token counts the API
    returned, not an estimate. Returns the new llm_calls row id."""
    if status not in ("ok", "error"):
        raise ValueError(f"record_result status must be 'ok' or 'error', got {status!r}")
    cost = compute_cost(model, input_tokens, output_tokens)
    return _insert_call_row(conn, model, input_tokens, output_tokens, cost, status, prompt_name, prompt_version, note)


def spend_summary(conn: sqlite3.Connection) -> dict:
    """`python -m edgar.pipeline spend`: total spent, remaining, and a
    breakdown by prompt and model."""
    total = total_spent(conn)
    rows = conn.execute(
        """
        SELECT prompt_name, model, status, COUNT(*) AS n, COALESCE(SUM(cost_usd), 0.0) AS cost
        FROM llm_calls
        GROUP BY prompt_name, model, status
        ORDER BY prompt_name, model, status
        """
    ).fetchall()
    return {
        "total_spent": total,
        "remaining": config.LLM_BUDGET_USD - total,
        "budget": config.LLM_BUDGET_USD,
        "breakdown": [dict(r) for r in rows],
    }


# --- R2: caching, cost estimation, and the API client itself ---


class InvalidResponseError(Exception):
    """A response failed to parse as JSON, or parsed but did not match the
    declared output schema, even after the one allowed retry (R2/R6)."""


def estimate_tokens(text: str) -> int:
    """Approximate token count: ~4 characters per token in English, per
    Anthropic's own stated rule of thumb (their pricing FAQ). Used only for
    pre-call estimates (--dry-run, the budget check) -- the real count comes
    back in the API response and is what actually gets recorded."""
    return max(1, round(len(text) / config.CHARS_PER_TOKEN_ESTIMATE))


def compute_input_hash(rendered_prompt: str, model: str, prompt_version: str) -> str:
    """sha256 of the FULLY RENDERED prompt plus model and prompt version
    (SPEC-006 R2, ARCHITECTURE.md note on analyses.input_hash). The
    rendered prompt already has every value interpolated -- note text,
    company, ticker, form type, fiscal period, note name -- so hashing it
    directly (rather than the template) is what makes the cache a cache of
    "this exact question," not "this template, regardless of what was
    asked." model and prompt_version are included explicitly even though
    prompt_version doesn't appear in the rendered text itself, so a version
    bump always produces a fresh analysis even in the (unlikely) case a
    template change doesn't alter the rendered output for a given section.

    Deliberately covers content the model SEES -- prompt, model, prompt
    version -- never request CONFIGURATION (temperature, thinking, max_tokens,
    ...). The two are different questions: "was this exact question asked
    before" (input_hash's job) versus "was it asked the same way" (not
    tracked, and not this function's job). Confirmed live 2026-07-28: disabling
    `thinking` (ARCHITECTURE.md §4.3) changed how every future call is made
    without touching this hash at all, so the 257 analyses already cached
    under adaptive thinking correctly remain cache hits -- they answer the
    same question, and were already validated (quote-verified, numeric-support
    measured) under the config that produced them. This is a deliberate policy,
    not an oversight: a request-configuration change that is believed to
    MATERIALLY alter output quality should force regeneration via a deliberate
    prompt_version bump (which DOES invalidate the cache, by design), decided
    case by case against whether regeneration cost is worth the improvement --
    never by silently baking every tunable request parameter into this hash,
    which would invalidate the entire cache on every knob turn regardless of
    whether the knob mattered.
    """
    payload = f"{model}|{prompt_version}|{rendered_prompt}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def estimate_cost(rendered_prompt: str, model: str | None = None, estimated_output_tokens: int | None = None) -> dict:
    """Approximate cost for a call, without calling anything (R2, R8
    --dry-run). Returns the estimated input/output tokens and cost so
    callers can report all three."""
    model = model or config.LLM_MODEL
    output_tokens = (
        estimated_output_tokens
        if estimated_output_tokens is not None
        else config.LLM_ESTIMATED_OUTPUT_TOKENS
    )
    input_tokens = estimate_tokens(rendered_prompt)
    cost = compute_cost(model, input_tokens, output_tokens)
    return {"input_tokens": input_tokens, "output_tokens": output_tokens, "cost_usd": cost}


_REQUIRED_FINDING_KEYS = {"category", "severity", "headline", "detail", "quote"}


def validate_output_schema(parsed: Any) -> None:
    """R5's output schema, enforced in code, not just in the prompt.
    Raises InvalidResponseError naming exactly what's wrong."""
    if not isinstance(parsed, dict):
        raise InvalidResponseError(f"top-level response is not a JSON object: {type(parsed).__name__}")
    if "material" not in parsed or not isinstance(parsed["material"], bool):
        raise InvalidResponseError("missing or non-boolean 'material' field")
    if "findings" not in parsed or not isinstance(parsed["findings"], list):
        raise InvalidResponseError("missing or non-list 'findings' field")
    for i, finding in enumerate(parsed["findings"]):
        if not isinstance(finding, dict):
            raise InvalidResponseError(f"findings[{i}] is not a JSON object")
        missing = _REQUIRED_FINDING_KEYS - finding.keys()
        if missing:
            raise InvalidResponseError(f"findings[{i}] missing keys: {sorted(missing)}")
        if finding["category"] not in config.FINDING_CATEGORIES:
            raise InvalidResponseError(f"findings[{i}] category {finding['category']!r} not in FINDING_CATEGORIES")
        if finding["severity"] not in config.FINDING_SEVERITIES:
            raise InvalidResponseError(f"findings[{i}] severity {finding['severity']!r} not in FINDING_SEVERITIES")
        if not isinstance(finding["quote"], str) or not finding["quote"].strip():
            raise InvalidResponseError(f"findings[{i}] quote is missing or empty")


class RawLLMClient(Protocol):
    """The minimal shape `LLMClient` needs from the Anthropic SDK client --
    a Protocol so tests can substitute a fake without importing `anthropic`
    at all (R2 constraint: no network in unit tests)."""

    def messages_create(self, model: str, max_tokens: int, prompt: str) -> tuple[str, int, int, str | None]:
        """Returns (response_text, input_tokens, output_tokens, stop_reason).

        stop_reason is the API's own field (e.g. "end_turn", "max_tokens",
        "stop_sequence") -- 2026-07-27 live-error-analysis fix: truncation
        must be READ from this field, never inferred from a JSON parse
        failure, which cannot tell "the model was cut off mid-response"
        apart from "the model emitted malformed JSON on its own" even
        though those call for different handling (a retry at a higher
        output cap vs. a same-size retry)."""
        ...


class _RealAnthropicClient:
    """Thin wrapper around anthropic.Anthropic -- imported lazily so the
    `anthropic` package is only required when an actual call is made, never
    for tests or for any code path that only touches the ledger/cache."""

    def __init__(self, api_key: str) -> None:
        import anthropic

        # max_retries reuses the project's own transient-failure retry
        # bound (config.HTTP_MAX_RETRIES) -- the SDK's built-in retry logic
        # already correctly handles 429/529/timeouts with Anthropic's own
        # Retry-After semantics, so it is reused rather than reimplemented
        # (R2: "retries only on transient failures, with bounded attempts,
        # reusing the backoff config").
        self._client = anthropic.Anthropic(api_key=api_key, max_retries=config.HTTP_MAX_RETRIES)

    def messages_create(self, model: str, max_tokens: int, prompt: str) -> tuple[str, int, int, str | None]:
        response = self._client.messages.create(
            model=model,
            max_tokens=max_tokens,
            # 2026-07-28: explicitly disabled, not omitted. Omitting `thinking`
            # does NOT mean "off" for this model -- it defaults to `adaptive`
            # (the model decides per-call whether to reason, with no budget
            # cap of its own; it shares `max_tokens` with the actual text
            # output). That is exactly the truncation-risk mechanism §4.3
            # documents: all 4 real truncations in the first execute run were
            # a "thinking" block consuming the entire output cap before any
            # text. A 4-section real-API probe with thinking explicitly
            # disabled (scripts/probe_extended_thinking_2026_07_28.py)
            # completed every one of those same 4 sections in a single call,
            # 78-89% fewer output tokens, 67-83% lower cost, equal-or-more
            # kept findings, identical materiality calls -- see
            # ARCHITECTURE.md §4.3 and SPEC-006 v1.2/decision log #47 for the
            # full measurement. A structured JSON extraction against an
            # explicit schema does not need free-form reasoning tokens.
            thinking={"type": "disabled"},
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(block.text for block in response.content if getattr(block, "type", None) == "text")
        if not text:
            # 2026-07-27 live-error-analysis fix: a real ledger row
            # (input_tokens=5260, output_tokens=4096 -- exactly the cap)
            # extracted ZERO text and failed to parse with "Expecting
            # value: line 1 column 1 (char 0)", and there was no record of
            # what response.content actually contained to explain it. Never
            # assume "the non-text blocks were correctly filtered out and
            # there was simply nothing else" -- log what block TYPES the
            # API actually returned (and stop_reason) so an empty
            # extraction is diagnosable from the log instead of a silent
            # mystery.
            block_types = [getattr(block, "type", None) for block in response.content]
            logger.warning(
                "empty text extracted from Anthropic response: content block types=%s, "
                "stop_reason=%s, output_tokens=%d -- API returned no text block, or none at all",
                block_types, response.stop_reason, response.usage.output_tokens,
            )
        return text, response.usage.input_tokens, response.usage.output_tokens, response.stop_reason


class LLMClient:
    """The only object in this project that reaches the Anthropic API. Wraps
    a RawLLMClient (real or fake) so `get_or_create_analysis` never has to
    know the difference."""

    def __init__(self, model: str | None = None, raw_client: RawLLMClient | None = None) -> None:
        self.model = model or config.LLM_MODEL
        if raw_client is not None:
            self._raw = raw_client
        else:
            self._raw = _RealAnthropicClient(config.get_anthropic_api_key())

    def complete(self, rendered_prompt: str, max_output_tokens: int) -> tuple[str, int, int, str | None]:
        return self._raw.messages_create(self.model, max_output_tokens, rendered_prompt)


@dataclass(frozen=True)
class AnalysisOutcome:
    analysis_id: int | None
    output: dict | None
    status: str  # "cached" | "ok" | "error" | "refused" | "skipped" | "truncated"
    # Populated for status="refused" (SPEC-006A): which layer refused and
    # why -- L2 (lifetime budget), L3 (per-run cost ceiling), or L6
    # (call-count ceiling). None for every other status.
    note: str | None = None


def _write_analysis(
    conn: sqlite3.Connection,
    section_id: int,
    prompt_name: str,
    prompt_version: str,
    model: str,
    input_hash: str,
    output: dict,
    call_id: int,
) -> int:
    cursor = conn.execute(
        "INSERT INTO analyses "
        "(section_id, prompt_name, prompt_version, model, input_hash, output_json, call_id, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (section_id, prompt_name, prompt_version, model, input_hash, json.dumps(output), call_id, _now_iso()),
    )
    conn.commit()
    return cursor.lastrowid


def get_or_create_analysis(
    conn: sqlite3.Connection,
    section_id: int,
    prompt_name: str,
    prompt_version: str,
    rendered_prompt: str,
    model: str | None = None,
    max_output_tokens: int | None = None,
    client: LLMClient | None = None,
    run_guard: RunGuard | None = None,
) -> AnalysisOutcome:
    """The single entry point analyze.py calls per section: cache check,
    context-limit check, budget check, the call itself (with one retry on
    invalid JSON/schema, and a SEPARATE one-time retry at a higher output
    cap if the API's own stop_reason says the call was truncated --
    "truncated" is its own AnalysisOutcome status, distinct from "error",
    2026-07-27 live-error-analysis fix), ledger recording, and persistence
    to `analyses` -- all in one place, matching R1's ownership of the ledger
    and R2's ownership of caching. EVERY real API attempt bills its own
    ledger row the instant it happens (2026-07-28 fix, see `_bill` below) --
    a section that needed 2 real attempts (a retry of either kind) produces
    2 `llm_calls` rows, never 1; only the LOGICAL outcome returned to the
    caller collapses to a single AnalysisOutcome.

    `run_guard` (SPEC-006A L3/L6), when given, is checked BEFORE the lifetime
    budget (L2) -- cheaper (no DB read) and, once tripped, stays tripped for
    the rest of this RunGuard's life, so later calls in the same run refuse
    immediately without re-deriving the reason. Every refusal, from any
    layer, appends a 'refused' ledger row before returning -- a refusal that
    leaves no trace is invisible (SPEC-006A, Notes for the Implementer).

    Never raises for an expected outcome (cache hit, refusal, oversized
    section, a call that ultimately errors) -- all of those come back as a
    normal AnalysisOutcome with the appropriate status, so a caller looping
    over many sections doesn't need a try/except per section. Only
    genuinely unexpected failures (a config error, a missing pricing entry)
    raise.
    """
    model = model or config.LLM_MODEL
    max_output_tokens = max_output_tokens or config.LLM_MAX_OUTPUT_TOKENS
    input_hash = compute_input_hash(rendered_prompt, model, prompt_version)

    existing = conn.execute(
        "SELECT id, output_json FROM analyses WHERE input_hash = ?", (input_hash,)
    ).fetchone()
    if existing is not None:
        return AnalysisOutcome(existing["id"], json.loads(existing["output_json"]), "cached")

    estimated_input_tokens = estimate_tokens(rendered_prompt)
    if estimated_input_tokens > config.LLM_MAX_INPUT_TOKENS_ESTIMATE:
        logger.warning(
            "section_id=%s: estimated %d tokens exceeds LLM_MAX_INPUT_TOKENS_ESTIMATE (%d) -- "
            "skipping, not truncating",
            section_id,
            estimated_input_tokens,
            config.LLM_MAX_INPUT_TOKENS_ESTIMATE,
        )
        return AnalysisOutcome(None, None, "skipped")

    if run_guard is not None:
        estimated_cost = compute_cost(model, estimated_input_tokens, max_output_tokens)
        refusal_reason = run_guard.check_before_call(estimated_cost)
        if refusal_reason is not None:
            _insert_call_row(
                conn, model, estimated_input_tokens, max_output_tokens, 0.0, "refused",
                prompt_name, prompt_version, note=refusal_reason,
            )
            return AnalysisOutcome(None, None, "refused", note=refusal_reason)

    try:
        ensure_budget_available(
            conn, model, estimated_input_tokens, max_output_tokens, prompt_name, prompt_version
        )
    except BudgetExceededError as exc:
        return AnalysisOutcome(None, None, "refused", note=str(exc))

    llm_client = client if client is not None else LLMClient(model=model)

    def _attempt(max_tokens: int) -> tuple[dict | None, int, int, str | None, Exception | None]:
        text, in_tok, out_tok, stop_reason = llm_client.complete(rendered_prompt, max_tokens)
        try:
            candidate = json.loads(text)
            validate_output_schema(candidate)
            return candidate, in_tok, out_tok, stop_reason, None
        except (json.JSONDecodeError, InvalidResponseError) as exc:
            return None, in_tok, out_tok, stop_reason, exc

    def _bill(input_tokens: int, output_tokens: int, status: str, note: str | None) -> int:
        """2026-07-28 live-error-analysis fix: EVERY real API attempt gets its
        own ledger row and its own run_guard.record_call, the instant it
        happens -- never deferred until "whichever attempt ends the loop."
        Found live, against the real API, in this project's first execution
        of the retry path: a truncated first attempt is a REAL call Anthropic
        actually billed (input tokens plus up to max_output_tokens of a
        "thinking" block that never produced text), and the prior code
        recorded NOTHING for it whenever the retry succeeded -- only the
        retry's own tokens made it into `llm_calls`. That is exactly the
        silent-ledger-undercount failure mode SPEC-006A's decision log #42
        already names ("a ledger that lies about cost... is worse than a
        ledger that tells the truth"), just running in the opposite
        direction this time. Confirmed for 4 real truncate-then-retry
        sections this run: each had exactly one `llm_calls` row, holding
        only the SECOND call's tokens -- the first call's real cost (~4096
        output tokens, billed) had no row at all. AC1 ("every call attempt
        appends a row") applies to every attempt, not just the one whose
        outcome the section ultimately keeps."""
        call_id = record_result(conn, model, input_tokens, output_tokens, status, prompt_name, prompt_version, note=note)
        if run_guard is not None:
            run_guard.record_call(compute_cost(model, input_tokens, output_tokens))
        return call_id

    parsed: dict | None = None
    last_error: Exception | None = None
    last_stop_reason: str | None = None
    call_id: int | None = None
    # 2026-07-27 live-error-analysis fix: stop_reason == "max_tokens" is read
    # directly from the API, not inferred from a JSON parse failure -- a
    # truncated call is its own error class (config.LLM_TRUNCATION_RETRY_OUTPUT_TOKENS'
    # comment), handled separately below, so it never silently consumes one
    # of the generic parse-retry attempts (retrying a truncated call at the
    # SAME cap would just truncate again).
    attempts = config.LLM_JSON_PARSE_MAX_RETRIES + 1
    for attempt in range(attempts):
        parsed, input_tokens, output_tokens, stop_reason, last_error = _attempt(max_output_tokens)
        last_stop_reason = stop_reason
        if parsed is not None:
            call_id = _bill(input_tokens, output_tokens, "ok", note=None)
            break
        if stop_reason == "max_tokens":
            logger.info(
                "section_id=%s: truncated (stop_reason=max_tokens) on attempt %d at max_output_tokens=%d",
                section_id, attempt + 1, max_output_tokens,
            )
            _bill(
                input_tokens, output_tokens, "error",
                note=f"truncated (stop_reason=max_tokens) at max_output_tokens={max_output_tokens}; retrying once at a higher cap",
            )
            break
        logger.info("section_id=%s: invalid response on attempt %d: %s", section_id, attempt + 1, last_error)
        is_final_attempt = attempt == attempts - 1
        _bill(
            input_tokens, output_tokens, "error",
            note=f"invalid JSON/schema on attempt {attempt + 1}/{attempts}: {last_error}"
            + ("" if is_final_attempt else " (retrying)"),
        )

    truncated = False
    if parsed is None and last_stop_reason == "max_tokens":
        retry_cap = config.LLM_TRUNCATION_RETRY_OUTPUT_TOKENS
        logger.info(
            "section_id=%s: retrying once at max_output_tokens=%d after truncation",
            section_id, retry_cap,
        )
        parsed, input_tokens, output_tokens, last_stop_reason, last_error = _attempt(retry_cap)
        if parsed is not None:
            call_id = _bill(input_tokens, output_tokens, "ok", note=None)
        else:
            truncated = True
            if last_stop_reason == "max_tokens":
                note = f"truncated (stop_reason=max_tokens) even after retrying at {retry_cap} output tokens: {last_error}"
            else:
                note = f"invalid JSON/schema on truncation-retry attempt (at {retry_cap} output tokens): {last_error}"
            _bill(input_tokens, output_tokens, "error", note=note)

    if parsed is None:
        if truncated:
            status = "truncated"
            note = (
                f"truncated (stop_reason=max_tokens) even after retrying at "
                f"{config.LLM_TRUNCATION_RETRY_OUTPUT_TOKENS} output tokens: {last_error}"
            )
        else:
            status = "error"
            note = f"invalid JSON/schema after {attempts} attempt(s): {last_error}"
        return AnalysisOutcome(None, None, status, note=note)

    analysis_id = _write_analysis(conn, section_id, prompt_name, prompt_version, model, input_hash, parsed, call_id)
    return AnalysisOutcome(analysis_id, parsed, "ok")
