"""SPEC-007: The Grounded Brief.

Writes ONE short narrative per filing, from observations and findings already
verified (SPEC-005's deterministic rule engine, SPEC-006's quote-verified LLM
findings) -- never raw metrics, never raw section text (ARCHITECTURE.md §2).
Owns: capped/ranked input selection (R2), the generator prompt (R3), per-type
mechanical verification (R4), and the independent adversarial verifier pass
(R5). Calling the API and the spend ledger belong to llm.py -- this module
calls that one, never the Anthropic API directly.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import logging
import re
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime

from edgar import config, llm

logger = logging.getLogger(__name__)

_TEMPLATE_MARKER = "## Template"


def _now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0, tzinfo=None).isoformat()


def _format_fiscal_period(fiscal_year: int | None, fiscal_period: str | None) -> str:
    """Duplicated from analyze.py rather than imported -- tiny, pure, and
    keeps this module's only dependency on the rest of the project at
    config/llm, matching the project's established convention of small
    per-module helpers (e.g. `_now_iso` is duplicated in llm.py, analyze.py,
    and here, rather than shared)."""
    if fiscal_year is None or fiscal_period is None:
        return "unknown fiscal period"
    if fiscal_period == "FY":
        return f"FY{fiscal_year}"
    return f"{fiscal_period} FY{fiscal_year}"


def load_prompt_template(prompt_name: str, prompt_version: str) -> str:
    """Identical convention to analyze.load_prompt_template (SPEC-006 R3),
    duplicated rather than imported for the same module-boundary reason as
    `_format_fiscal_period` above."""
    path = config.PROMPTS_DIR / f"{prompt_name}_{prompt_version}.md"
    text = path.read_text(encoding="utf-8")
    marker_index = text.find(_TEMPLATE_MARKER)
    if marker_index == -1:
        raise ValueError(f"{path}: no {_TEMPLATE_MARKER!r} marker found")
    return text[marker_index + len(_TEMPLATE_MARKER):].strip()


def render_prompt(template: str, replacements: dict[str, str]) -> str:
    """Sequential literal replacement, never str.format() -- same reasoning
    as analyze.render_prompt: rendered statements and quotes routinely
    contain '$' and occasionally '{'/'}', either of which collides with
    str.format()/string.Template's own placeholder syntax."""
    rendered = template
    for token, value in replacements.items():
        rendered = rendered.replace(token, value)
    return rendered


def compute_brief_input_hash(rendered_prompt: str, model: str, prompt_version: str, verifier_version: str) -> str:
    """sha256 of (model|prompt_version|verifier_version|rendered_prompt).
    Includes verifier_version, unlike llm.compute_input_hash -- R1: "a change
    to the verifier can change which sentences survive, so it changes the
    output." A verifier bump must invalidate the cache even when the
    generator's own rendered prompt is byte-identical."""
    payload = f"{model}|{prompt_version}|{verifier_version}|{rendered_prompt}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# --- R2: capped, ranked, deterministic input selection ---


def _current_version_observations(conn: sqlite3.Connection, accession_no: str) -> list[dict]:
    """Every observation for this filing, current rule_version only (R2
    v2.1) -- `observations` deliberately retains superseded-version rows for
    historical comparison (SPEC-005), and those can carry a DIFFERENT
    severity than the current version assigns. Selecting without this filter
    would silently let a filing's oldest, already-superseded severity
    judgement outrank its current one -- found live, pre-implementation,
    against section_appeared/section_disappeared's real v1-vs-v4 severity
    change. Matches validate.py's own established "current version" pattern."""
    rows = [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM observations WHERE accession_no = ? ORDER BY "
            "CASE severity WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END, id",
            (accession_no,),
        )
    ]
    return [r for r in rows if r["rule_version"] == config.RULE_REGISTRY[r["rule_name"]].version]


def select_observations(conn: sqlite3.Connection, accession_no: str) -> list[dict]:
    """R2 v2.1: current-rule_version rows, ranked severity-desc/id-asc (the
    tie-break is for reproducibility, not analytical merit -- R2), capped at
    BRIEF_MAX_OBSERVATIONS subject to a per-slot cap and a per-rule ceiling
    walked together:

    - Slot: for a metric-subject rule the slot is (rule_name, metric_category)
      -- config.METRIC_REGISTRY[subject].category; for a section-subject rule
      the slot is (rule_name, None), unchanged from SPEC-005 R11's original
      form. Capped at BRIEF_OBSERVATION_SLOT_CAP.
    - Rule ceiling: total contribution from one rule_name, across every slot
      it touches, never exceeds BRIEF_OBSERVATION_RULE_CEILING.
    """
    rows = _current_version_observations(conn, accession_no)
    selected: list[dict] = []
    per_slot: dict[tuple[str, str | None], int] = {}
    per_rule_total: dict[str, int] = {}
    for row in rows:
        rdef = config.RULE_REGISTRY[row["rule_name"]]
        if rdef.subject_kind == "metric":
            slot_key = (row["rule_name"], config.METRIC_REGISTRY[row["subject"]].category)
        else:
            slot_key = (row["rule_name"], None)
        if per_slot.get(slot_key, 0) >= config.BRIEF_OBSERVATION_SLOT_CAP:
            continue
        if per_rule_total.get(row["rule_name"], 0) >= config.BRIEF_OBSERVATION_RULE_CEILING:
            continue
        selected.append(row)
        per_slot[slot_key] = per_slot.get(slot_key, 0) + 1
        per_rule_total[row["rule_name"]] = per_rule_total.get(row["rule_name"], 0) + 1
        if len(selected) >= config.BRIEF_MAX_OBSERVATIONS:
            break
    return selected


def select_findings(conn: sqlite3.Connection, accession_no: str) -> list[dict]:
    """R2 v2.1: ranked severity-desc/id-asc, capped at BRIEF_MAX_FINDINGS with
    no more than BRIEF_MAX_FINDINGS_PER_CATEGORY from any single `category`.
    Found live, pre-implementation: uncapped, Amazon's real most-recent 10-K
    selected 8 of 8 findings from category="litigation", silently excluding
    the filing's only red_flag finding -- a ranking with no diversity
    constraint collapses onto whichever dimension is over-represented."""
    rows = [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM findings WHERE accession_no = ? ORDER BY "
            "CASE severity WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END, id",
            (accession_no,),
        )
    ]
    selected: list[dict] = []
    per_category: dict[str, int] = {}
    for row in rows:
        if per_category.get(row["category"], 0) >= config.BRIEF_MAX_FINDINGS_PER_CATEGORY:
            continue
        selected.append(row)
        per_category[row["category"]] = per_category.get(row["category"], 0) + 1
        if len(selected) >= config.BRIEF_MAX_FINDINGS:
            break
    return selected


def _ref_string(kind: str, id_: int) -> str:
    return f"{kind}:{id_}"


def parse_ref(ref: str) -> tuple[str, int] | None:
    """'obs:1234' -> ('obs', 1234). Malformed or unrecognised refs return
    None -- treated as non-resolving (R4 universal check), never raise."""
    if not isinstance(ref, str) or ":" not in ref:
        return None
    kind, _, id_str = ref.partition(":")
    if kind not in ("obs", "finding") or not id_str.isdigit():
        return None
    return kind, int(id_str)


def _source_text(kind: str, row: dict) -> str:
    """The text a reference's source contributes to the prompt/verification
    -- an observation's own deterministic statement, or a finding's
    headline+detail+quote concatenated (everything about it that could
    ground a claim)."""
    if kind == "obs":
        return row["statement"]
    return f"{row['headline']} {row['detail'] or ''} {row['quote'] or ''}"


def _build_supplied_index(observations: list[dict], findings: list[dict]) -> dict[str, dict]:
    """ref string -> {"kind", "row", "text"} for every item actually
    SUPPLIED to the generator this call -- the universe a sentence's
    references are checked against (R4: "A model cannot cite what it never
    saw")."""
    index: dict[str, dict] = {}
    for o in observations:
        index[_ref_string("obs", o["id"])] = {"kind": "obs", "row": o, "text": _source_text("obs", o)}
    for f in findings:
        index[_ref_string("finding", f["id"])] = {"kind": "finding", "row": f, "text": _source_text("finding", f)}
    return index


def _format_observations_block(observations: list[dict]) -> str:
    if not observations:
        return "(none)"
    return "\n".join(f"- obs:{o['id']} [{o['severity']}] {o['statement']}" for o in observations)


def _format_findings_block(findings: list[dict]) -> str:
    if not findings:
        return "(none)"
    return "\n".join(
        f"- finding:{f['id']} [{f['severity']}] ({f['category']}) {f['headline']} "
        f"-- {f['detail']} -- quote: \"{f['quote']}\""
        for f in findings
    )


# --- R4: per-type mechanical verification ---

_CAUSAL_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(c) for c in config.BRIEF_CAUSAL_CONNECTIVES) + r")\b", re.IGNORECASE
)
_PREDICTIVE_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(t) for t in config.BRIEF_PREDICTIVE_TERMS) + r")\b", re.IGNORECASE
)
_NUMBER_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")
# $, €, £ -- a currency symbol immediately preceding a number tags its unit.
_CURRENCY_UNIT_SYMBOLS = {"$": "usd", "€": "eur", "£": "gbp"}
_MAX_VALUES_FOR_SUBSET_SUM = 20


def _normalize_number(token: str) -> str:
    return token.replace(",", "")


def _has_causal_connective(text: str) -> bool:
    return bool(_CAUSAL_RE.search(text))


def _has_predictive_language(text: str) -> bool:
    return bool(_PREDICTIVE_RE.search(text))


def _numbers_with_units(text: str) -> list[tuple[float, str | None]]:
    """Every numeric literal in text, as (value, unit), with duplicates, in
    order. unit is a currency code if a currency symbol immediately precedes
    the number, "percent" if '%' immediately follows, else None (no adjacent
    marker) -- 2026-07-30 (SPEC-007 v2.1): SPEC-006's own derived-sum
    verifier (analyze.py) checks arithmetic only, with no concept of unit at
    all. A sum across mismatched units (a euro fine plus a dollar verdict)
    passes THAT check while silently conflating two different currencies.
    ARCHITECTURE.md §2.5: arithmetic verification without unit verification
    is not verification."""
    results: list[tuple[float, str | None]] = []
    for m in _NUMBER_RE.finditer(text):
        value = float(_normalize_number(m.group(0)))
        start, end = m.span()
        unit: str | None = None
        if start > 0 and text[start - 1] in _CURRENCY_UNIT_SYMBOLS:
            unit = _CURRENCY_UNIT_SYMBOLS[text[start - 1]]
        elif end < len(text) and text[end] == "%":
            unit = "percent"
        results.append((value, unit))
    return results


def _number_present(value: float, source_texts: list[str], tolerance: float = 1e-6) -> bool:
    for text in source_texts:
        for v, _unit in _numbers_with_units(text):
            if abs(v - value) < tolerance:
                return True
    return False


def _is_verified_same_unit_subset_sum(target: float, source_texts: list[str], tolerance: float = 1e-6) -> bool:
    """True if some subset of >= 2 numbers, ALL SHARING ONE UNIT (including
    all being unit=None), drawn from the cited sources, sums to target.
    Grouping addends by unit BEFORE searching combinations makes a
    cross-unit combination structurally impossible to match -- the SPEC-007
    v2.1 fix. Sums only, not differences: matches analyze.py's real
    `_is_verified_subset_sum` (SPEC-006's "derived-sum verifier" this
    function is described as reusing, R4) -- that function has never
    supported differences either, so this does not invent capability beyond
    what is actually being reused."""
    by_unit: dict[str | None, list[float]] = {}
    for text in source_texts:
        for value, unit in _numbers_with_units(text):
            by_unit.setdefault(unit, []).append(value)
    for values in by_unit.values():
        if len(values) > _MAX_VALUES_FOR_SUBSET_SUM:
            continue
        for size in range(2, len(values) + 1):
            for combo in itertools.combinations(values, size):
                if abs(sum(combo) - target) < tolerance:
                    return True
    return False


def _resolve_refs(sentence: dict, supplied_index: dict[str, dict]) -> list[dict]:
    """Every ref in `sentence["refs"]` that resolves to something actually
    SUPPLIED for this filing. Malformed or unsupplied refs are silently
    skipped here -- the caller decides what "zero resolved" means."""
    refs = sentence.get("refs")
    if not isinstance(refs, list):
        return []
    resolved = []
    for ref in refs:
        parsed = parse_ref(ref) if isinstance(ref, str) else None
        if parsed is None:
            continue
        entry = supplied_index.get(ref)
        if entry is not None:
            resolved.append(entry)
    return resolved


def verify_sentence(sentence: dict, supplied_index: dict[str, dict]) -> tuple[bool, str | None]:
    """R4: mechanical per-type verification, in code. Returns (kept,
    drop_reason) -- never raises on malformed model output; an unrecognised
    shape is just dropped ("Failures are dropped, not warned about.").

    Universal checks (every type): references resolve to something supplied
    for THIS filing; zero resolving references drops the sentence; any
    predictive/modal construction drops it regardless of type; an
    unrecognised type drops it.
    """
    sentence_type = sentence.get("type")
    if sentence_type not in config.BRIEF_SENTENCE_TYPES:
        return False, f"unrecognised type {sentence_type!r}"

    text = sentence.get("text")
    if not isinstance(text, str) or not text.strip():
        return False, "missing or empty text"

    resolved = _resolve_refs(sentence, supplied_index)
    if not resolved:
        return False, "zero resolving references"

    if _has_predictive_language(text):
        return False, "predictive/modal language"

    source_texts = [entry["text"] for entry in resolved]

    if sentence_type == "restatement":
        if len(resolved) != 1:
            return False, "restatement must cite exactly one reference"
        if _has_causal_connective(text):
            return False, "restatement must not contain a causal connective"
        return True, None

    if sentence_type == "juxtaposition":
        if len(resolved) < 2:
            return False, "juxtaposition must cite two or more references"
        if _has_causal_connective(text):
            return False, "juxtaposition must not contain a causal connective"
        return True, None

    if sentence_type == "aggregation":
        for value, _unit in _numbers_with_units(text):
            if _number_present(value, source_texts) or _is_verified_same_unit_subset_sum(value, source_texts):
                continue
            return False, f"number {value!r} does not verify (not present, no same-unit sum in cited sources)"
        return True, None

    if sentence_type == "grouping":
        if len(resolved) < 2:
            return False, "grouping must cite two or more references"
        if _has_causal_connective(text):
            return False, "grouping must not contain a causal connective"
        for value, _unit in _numbers_with_units(text):
            if not _number_present(value, source_texts):
                return False, f"number {value!r} not present in cited sources"
        return True, None

    if sentence_type == "sourced_causal":
        if not any(_has_causal_connective(t) for t in source_texts):
            return False, "sourced_causal requires a cited source that itself states a cause"
        return True, None

    return False, "unhandled type"  # unreachable given the membership check above


# --- R3 generator schema / R5 verifier schema ---

_REQUIRED_SENTENCE_KEYS = {"type", "text", "refs"}


def validate_generator_schema(parsed: object) -> None:
    if not isinstance(parsed, dict):
        raise llm.InvalidResponseError(f"top-level response is not a JSON object: {type(parsed).__name__}")
    if "material" not in parsed or not isinstance(parsed["material"], bool):
        raise llm.InvalidResponseError("missing or non-boolean 'material' field")
    if "sentences" not in parsed or not isinstance(parsed["sentences"], list):
        raise llm.InvalidResponseError("missing or non-list 'sentences' field")
    for i, s in enumerate(parsed["sentences"]):
        if not isinstance(s, dict):
            raise llm.InvalidResponseError(f"sentences[{i}] is not a JSON object")
        missing = _REQUIRED_SENTENCE_KEYS - s.keys()
        if missing:
            raise llm.InvalidResponseError(f"sentences[{i}] missing keys: {sorted(missing)}")


def validate_verifier_schema(parsed: object) -> None:
    if not isinstance(parsed, dict):
        raise llm.InvalidResponseError(f"top-level verifier response is not a JSON object: {type(parsed).__name__}")
    if "verifications" not in parsed or not isinstance(parsed["verifications"], list):
        raise llm.InvalidResponseError("missing or non-list 'verifications' field")
    for i, v in enumerate(parsed["verifications"]):
        if not isinstance(v, dict) or not isinstance(v.get("position"), int) or "verdict" not in v:
            raise llm.InvalidResponseError(f"verifications[{i}] malformed")


def _render_verifier_prompt(template: str, kept_sentences: list[dict], supplied_index: dict[str, dict]) -> str:
    blocks = []
    for i, s in enumerate(kept_sentences):
        resolved = _resolve_refs(s, supplied_index)
        sources_text = "\n".join(f"  {ref}: {entry['text']}" for ref, entry in zip(s["refs"], resolved))
        blocks.append(f'[{i}] ({s["type"]}) "{s["text"]}"\nSources:\n{sources_text}')
    return render_prompt(template, {"%%SENTENCES_BLOCK%%": "\n\n".join(blocks)})


# --- the two-call orchestration (generator, then verifier) ---


@dataclass
class _CallOutcome:
    parsed: dict | None
    status: str  # "ok" | "error" | "truncated"
    note: str | None
    total_cost_usd: float
    call_id: int | None = None


def _call_with_retries(
    conn: sqlite3.Connection,
    llm_client: llm.LLMClient,
    model: str,
    rendered_prompt: str,
    max_output_tokens: int,
    prompt_name: str,
    prompt_version: str,
    validator,
    run_guard: llm.RunGuard | None,
) -> _CallOutcome:
    """Generic call-parse-retry-bill helper shared by the generator and
    verifier calls. Mirrors llm.get_or_create_analysis's retry/truncation/
    per-attempt-billing discipline (2026-07-28 fix, ARCHITECTURE.md §4.3):
    stop_reason == "max_tokens" is read directly from the API and retried
    once at a raised cap, distinct from a generic invalid-JSON retry at the
    same cap; EVERY real attempt bills its own llm_calls row, immediately,
    never deferred to whichever attempt the loop ends on. Not reused
    directly from llm.py because that function persists to `analyses`
    specifically; this module owns a different persistence target
    (`briefs`/`brief_sentences`) and calls this helper twice per brief
    (generator, then verifier) with different prompts/validators/prompt
    names."""

    def _attempt(max_tokens: int):
        text, in_tok, out_tok, stop_reason = llm_client.complete(rendered_prompt, max_tokens)
        try:
            candidate = json.loads(text)
            validator(candidate)
            return candidate, in_tok, out_tok, stop_reason, None
        except (json.JSONDecodeError, llm.InvalidResponseError) as exc:
            return None, in_tok, out_tok, stop_reason, exc

    def _bill(input_tokens: int, output_tokens: int, status: str, note: str | None) -> tuple[int, float]:
        call_id = llm.record_result(conn, model, input_tokens, output_tokens, status, prompt_name, prompt_version, note=note)
        cost = llm.compute_cost(model, input_tokens, output_tokens)
        if run_guard is not None:
            run_guard.record_call(cost)
        return call_id, cost

    total_cost = 0.0
    parsed = None
    last_error: Exception | None = None
    last_stop_reason: str | None = None
    attempts = config.LLM_JSON_PARSE_MAX_RETRIES + 1

    for attempt in range(attempts):
        parsed, input_tokens, output_tokens, stop_reason, last_error = _attempt(max_output_tokens)
        last_stop_reason = stop_reason
        if parsed is not None:
            call_id, cost = _bill(input_tokens, output_tokens, "ok", None)
            return _CallOutcome(parsed, "ok", None, total_cost + cost, call_id)
        if stop_reason == "max_tokens":
            _, cost = _bill(
                input_tokens, output_tokens, "error",
                note=f"truncated (stop_reason=max_tokens) at max_output_tokens={max_output_tokens}; retrying once at a higher cap",
            )
            total_cost += cost
            break
        _, cost = _bill(
            input_tokens, output_tokens, "error",
            note=f"invalid JSON/schema on attempt {attempt + 1}/{attempts}: {last_error}",
        )
        total_cost += cost

    truncated = False
    if parsed is None and last_stop_reason == "max_tokens":
        retry_cap = config.LLM_TRUNCATION_RETRY_OUTPUT_TOKENS
        parsed, input_tokens, output_tokens, last_stop_reason, last_error = _attempt(retry_cap)
        if parsed is not None:
            call_id, cost = _bill(input_tokens, output_tokens, "ok", None)
            return _CallOutcome(parsed, "ok", None, total_cost + cost, call_id)
        truncated = True
        _, cost = _bill(
            input_tokens, output_tokens, "error",
            note=f"truncated even after retrying at {retry_cap} output tokens: {last_error}",
        )
        total_cost += cost

    status = "truncated" if truncated else "error"
    note = (
        f"truncated (stop_reason=max_tokens) even after retrying at "
        f"{config.LLM_TRUNCATION_RETRY_OUTPUT_TOKENS} output tokens: {last_error}"
        if truncated
        else f"invalid JSON/schema after {attempts} attempt(s): {last_error}"
    )
    return _CallOutcome(None, status, note, total_cost)


def _insert_brief_row(
    conn: sqlite3.Connection, accession_no: str, cik: str, model: str, input_hash: str, call_id: int | None,
    generator_dropped: int = 0, verifier_dropped: int = 0,
) -> int:
    cursor = conn.execute(
        "INSERT INTO briefs (accession_no, cik, prompt_name, prompt_version, verifier_version, model, "
        "input_hash, call_id, generator_dropped, verifier_dropped, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            accession_no, cik, config.BRIEF_GENERATOR_PROMPT_NAME, config.BRIEF_GENERATOR_PROMPT_VERSION,
            config.BRIEF_VERIFIER_VERSION, model, input_hash, call_id, generator_dropped, verifier_dropped, _now_iso(),
        ),
    )
    conn.commit()
    return cursor.lastrowid


def _write_brief(
    conn: sqlite3.Connection, accession_no: str, cik: str, model: str, input_hash: str,
    call_id: int | None, sentences: list[dict], generator_dropped: int = 0, verifier_dropped: int = 0,
) -> int:
    brief_id = _insert_brief_row(
        conn, accession_no, cik, model, input_hash, call_id, generator_dropped, verifier_dropped
    )
    for position, s in enumerate(sentences):
        conn.execute(
            "INSERT INTO brief_sentences (brief_id, position, sentence_type, text, refs_json) VALUES (?, ?, ?, ?, ?)",
            (brief_id, position, s["type"], s["text"], json.dumps(s["refs"])),
        )
    conn.commit()
    return brief_id


@dataclass(frozen=True)
class BriefOutcome:
    brief_id: int | None
    sentences: list[dict] | None
    status: str  # "cached" | "ok" | "empty" | "error" | "refused" | "skipped" | "truncated"
    note: str | None = None
    generator_dropped: int = 0
    verifier_dropped: int = 0


def generate_brief(
    conn: sqlite3.Connection,
    row: dict,
    generator_template: str,
    verifier_template: str,
    client: llm.LLMClient | None = None,
    run_guard: llm.RunGuard | None = None,
    model: str | None = None,
) -> BriefOutcome:
    """The single entry point pipeline.py's generate-briefs command calls per
    filing: cache check, empty-input check, budget check, the generator call,
    R4 verification in code, the R5 verifier call, and persistence to
    `briefs`/`brief_sentences` -- all in one place, mirroring
    llm.get_or_create_analysis's role for SPEC-006.

    Never raises for an expected outcome -- cache hit, refusal, empty input,
    a call that ultimately errors -- all come back as a normal BriefOutcome
    with the appropriate status.
    """
    model = model or config.LLM_MODEL
    accession_no = row["accession_no"]

    observations = select_observations(conn, accession_no)
    findings = select_findings(conn, accession_no)

    fiscal_period_str = _format_fiscal_period(row.get("fiscal_year"), row.get("fiscal_period"))
    rendered = render_prompt(
        generator_template,
        {
            "%%COMPANY%%": row["company_name"],
            "%%TICKER%%": row["ticker"],
            "%%FORM_TYPE%%": row["form_type"],
            "%%FISCAL_PERIOD%%": fiscal_period_str,
            "%%FILING_DATE%%": row["filing_date"],
            "%%OBSERVATIONS_BLOCK%%": _format_observations_block(observations),
            "%%FINDINGS_BLOCK%%": _format_findings_block(findings),
        },
    )
    input_hash = compute_brief_input_hash(
        rendered, model, config.BRIEF_GENERATOR_PROMPT_VERSION, config.BRIEF_VERIFIER_VERSION
    )

    existing = conn.execute("SELECT id FROM briefs WHERE input_hash = ?", (input_hash,)).fetchone()
    if existing is not None:
        sentences = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM brief_sentences WHERE brief_id = ? ORDER BY position", (existing["id"],)
            )
        ]
        return BriefOutcome(existing["id"], sentences, "cached")

    # R2 edge case: "Filing has no observations and no findings -> No call.
    # Empty brief with a reason. Do not pay to be told nothing happened."
    if not observations and not findings:
        brief_id = _insert_brief_row(conn, accession_no, row["cik"], model, input_hash, call_id=None)
        return BriefOutcome(brief_id, [], "empty", note="no observations or findings supplied for this filing")

    estimated_input_tokens = llm.estimate_tokens(rendered)
    if estimated_input_tokens > config.LLM_MAX_INPUT_TOKENS_ESTIMATE:
        return BriefOutcome(None, None, "skipped", note="estimated input tokens exceed limit")

    if run_guard is not None:
        estimated_cost = llm.compute_cost(model, estimated_input_tokens, config.BRIEF_MAX_OUTPUT_TOKENS)
        refusal_reason = run_guard.check_before_call(estimated_cost)
        if refusal_reason is not None:
            llm.record_refusal(
                conn, refusal_reason, prompt_name=config.BRIEF_GENERATOR_PROMPT_NAME,
                prompt_version=config.BRIEF_GENERATOR_PROMPT_VERSION, model=model,
            )
            return BriefOutcome(None, None, "refused", note=refusal_reason)

    try:
        llm.ensure_budget_available(
            conn, model, estimated_input_tokens, config.BRIEF_MAX_OUTPUT_TOKENS,
            config.BRIEF_GENERATOR_PROMPT_NAME, config.BRIEF_GENERATOR_PROMPT_VERSION,
        )
    except llm.BudgetExceededError as exc:
        return BriefOutcome(None, None, "refused", note=str(exc))

    llm_client = client if client is not None else llm.LLMClient(model=model)

    gen_outcome = _call_with_retries(
        conn, llm_client, model, rendered, config.BRIEF_MAX_OUTPUT_TOKENS,
        config.BRIEF_GENERATOR_PROMPT_NAME, config.BRIEF_GENERATOR_PROMPT_VERSION,
        validate_generator_schema, run_guard,
    )
    if gen_outcome.status != "ok":
        return BriefOutcome(None, None, gen_outcome.status, note=gen_outcome.note)

    supplied_index = _build_supplied_index(observations, findings)
    raw_sentences = gen_outcome.parsed.get("sentences", [])

    # R4: per-type mechanical verification, in code. Failures dropped, not
    # warned about.
    kept_after_r4: list[dict] = []
    generator_dropped = 0
    for s in raw_sentences:
        ok, reason = verify_sentence(s, supplied_index)
        if ok:
            kept_after_r4.append(s)
        else:
            generator_dropped += 1
            logger.info(
                "Dropped sentence (R4: %s): accession=%s text=%r", reason, accession_no, s.get("text")
            )

    if not kept_after_r4:
        brief_id = _write_brief(
            conn, accession_no, row["cik"], model, input_hash, gen_outcome.call_id, [],
            generator_dropped=generator_dropped, verifier_dropped=0,
        )
        if raw_sentences:
            logger.warning(
                "All %d generated sentence(s) dropped at R4 for accession=%s -- empty brief stored",
                len(raw_sentences), accession_no,
            )
        return BriefOutcome(brief_id, [], "ok", generator_dropped=generator_dropped)

    # R5: independent adversarial verifier pass, batched into ONE call --
    # each sentence paired with ONLY its own cited sources (never the rest
    # of the brief).
    verifier_rendered = _render_verifier_prompt(verifier_template, kept_after_r4, supplied_index)
    ver_outcome = _call_with_retries(
        conn, llm_client, model, verifier_rendered, config.BRIEF_VERIFIER_MAX_OUTPUT_TOKENS,
        config.BRIEF_VERIFIER_PROMPT_NAME, config.BRIEF_VERIFIER_VERSION,
        validate_verifier_schema, run_guard,
    )

    if ver_outcome.status != "ok":
        # Edge case: "Verifier itself returns unparseable output -> treat as
        # unsupported. Fail closed." -- every sentence in this batch drops.
        logger.warning(
            "Verifier call failed (%s) for accession=%s -- failing closed, all %d sentence(s) dropped",
            ver_outcome.status, accession_no, len(kept_after_r4),
        )
        kept_after_r5: list[dict] = []
        verifier_dropped = len(kept_after_r4)
    else:
        verdict_by_position: dict[int, str] = {}
        for v in ver_outcome.parsed.get("verifications", []):
            if isinstance(v, dict) and isinstance(v.get("position"), int):
                verdict_by_position[v["position"]] = v.get("verdict")
        kept_after_r5 = []
        verifier_dropped = 0
        for i, s in enumerate(kept_after_r4):
            if verdict_by_position.get(i) == "supported":
                kept_after_r5.append(s)
            else:
                verifier_dropped += 1
                logger.info(
                    "Dropped sentence (verifier: %s): accession=%s text=%r",
                    verdict_by_position.get(i, "no verdict / fail-closed"), accession_no, s.get("text"),
                )

    kept_sentences = [{"type": s["type"], "text": s["text"], "refs": s["refs"]} for s in kept_after_r5]
    brief_id = _write_brief(
        conn, accession_no, row["cik"], model, input_hash, gen_outcome.call_id, kept_sentences,
        generator_dropped=generator_dropped, verifier_dropped=verifier_dropped,
    )

    if raw_sentences and not kept_sentences:
        logger.warning(
            "Every sentence dropped (generator %d, verifier %d) for accession=%s -- empty brief stored",
            generator_dropped, verifier_dropped, accession_no,
        )

    return BriefOutcome(
        brief_id, kept_sentences, "ok", generator_dropped=generator_dropped, verifier_dropped=verifier_dropped
    )


# --- orchestration across a run ---


def select_candidate_filings(
    conn: sqlite3.Connection, tickers: list[str] | None = None, accession: str | None = None
) -> list[dict]:
    """Every 10-K/10-Q filing on or after ANALYSIS_START_DATE for the
    watchlist -- the same start-date/form-type scoping SPEC-006 uses (R4:
    "the paid stage is bounded")."""
    ciks = [c.cik for c in config.WATCHLIST if tickers is None or c.ticker in tickers]
    if not ciks:
        return []
    placeholders = ",".join("?" for _ in ciks)
    query = f"""
        SELECT f.accession_no, f.cik, f.form_type, f.filing_date, f.period_end,
               f.fiscal_year, f.fiscal_period, c.ticker, c.name AS company_name
        FROM filings f
        JOIN companies c ON c.cik = f.cik
        WHERE f.cik IN ({placeholders})
          AND f.form_type IN (?, ?)
          AND f.filing_date >= ?
    """
    params: list[object] = [*ciks, config.TENK_FORM_TYPE, config.TENQ_FORM_TYPE, config.ANALYSIS_START_DATE]
    if accession is not None:
        query += " AND f.accession_no = ?"
        params.append(accession)
    query += " ORDER BY f.accession_no"
    return [dict(r) for r in conn.execute(query, params).fetchall()]


@dataclass
class BriefRunStats:
    candidates: int = 0
    processed: int = 0
    calls_made: int = 0
    cache_hits: int = 0
    empty_no_input: int = 0
    refused: int = 0
    errors: int = 0
    truncated: int = 0
    skipped_oversized: int = 0
    generator_dropped_total: int = 0
    verifier_dropped_total: int = 0
    briefs_with_sentences: int = 0
    briefs_empty_after_drop: int = 0
    total_cost_usd: float = 0.0
    run_cost_usd: float = 0.0
    dry_run: bool = True
    estimated_cost_usd: float = 0.0
    cache_hits_dry: int = 0
    new_calls_dry: int = 0
    empty_no_input_dry: int = 0
    stopped_reason: str | None = None
    invalidated_by_version_bump: int = 0
    invalidated_cost_usd: float = 0.0
    prior_prompt_versions: tuple[str, ...] = ()
    prior_verifier_versions: tuple[str, ...] = ()

    @property
    def version_bumped(self) -> bool:
        return bool(self.prior_prompt_versions) or bool(self.prior_verifier_versions)


def run_brief_generation(
    conn: sqlite3.Connection,
    tickers: list[str] | None = None,
    accession: str | None = None,
    execute: bool = False,
    client: llm.LLMClient | None = None,
    max_run_cost_usd: float | None = None,
    max_calls_per_run: int | None = None,
    scheduled: bool = False,
) -> BriefRunStats:
    """The single entry point pipeline.py's generate-briefs command calls.

    Default (execute=False) is a dry run: estimates cost for the full
    candidate set, classifies each as a cache hit / new call / no-input, and
    makes no calls. execute=True actually calls generate_brief per filing,
    guarded by an llm.RunGuard (SPEC-006A L3/L6, same defaults as
    analyze-sections unless overridden).

    SPEC-009 P2 follow-up (approved 2026-08-24): `scheduled=True` (L7)
    clamps the run-cost ceiling to config.LLM_SCHEDULED_RUN_MAX_COST_USD
    regardless of max_run_cost_usd -- the exact same clamp
    `analyze.run_analysis`'s own `scheduled` parameter already applies,
    mirrored here rather than reinvented. Unlike section-analysis, there is
    no `--sample` flag on this command to refuse (generate-briefs has never
    had one), so that half of L7 does not apply here.

    Callers that need a SHARED ceiling across BOTH LLM stages of one
    scheduled run (this function's own calls plus analyze-sections' own,
    run as two separate pipeline stages) should pass an explicit
    `max_run_cost_usd` -- already below config.LLM_SCHEDULED_RUN_MAX_COST_USD
    -- computed from what the other stage actually spent; `scheduled=True`'s
    own clamp is a ceiling, never a floor, so it never widens a smaller
    value back up. See `pipeline.run_scheduled_llm_stages` for the one
    caller that does this.
    """
    stats = BriefRunStats(dry_run=not execute)
    candidates = select_candidate_filings(conn, tickers=tickers, accession=accession)
    stats.candidates = len(candidates)

    generator_template = load_prompt_template(config.BRIEF_GENERATOR_PROMPT_NAME, config.BRIEF_GENERATOR_PROMPT_VERSION)
    verifier_template = load_prompt_template(config.BRIEF_VERIFIER_PROMPT_NAME, config.BRIEF_VERIFIER_VERSION)

    if not execute:
        prior_prompt_versions_seen: set[str] = set()
        prior_verifier_versions_seen: set[str] = set()
        for row in candidates:
            stats.processed += 1
            observations = select_observations(conn, row["accession_no"])
            findings = select_findings(conn, row["accession_no"])
            if not observations and not findings:
                stats.empty_no_input_dry += 1
                continue

            fiscal_period_str = _format_fiscal_period(row.get("fiscal_year"), row.get("fiscal_period"))
            rendered = render_prompt(
                generator_template,
                {
                    "%%COMPANY%%": row["company_name"], "%%TICKER%%": row["ticker"], "%%FORM_TYPE%%": row["form_type"],
                    "%%FISCAL_PERIOD%%": fiscal_period_str, "%%FILING_DATE%%": row["filing_date"],
                    "%%OBSERVATIONS_BLOCK%%": _format_observations_block(observations),
                    "%%FINDINGS_BLOCK%%": _format_findings_block(findings),
                },
            )
            input_hash = compute_brief_input_hash(
                rendered, config.LLM_MODEL, config.BRIEF_GENERATOR_PROMPT_VERSION, config.BRIEF_VERIFIER_VERSION
            )
            already_cached = conn.execute("SELECT 1 FROM briefs WHERE input_hash = ?", (input_hash,)).fetchone()
            if already_cached is not None:
                stats.cache_hits_dry += 1
                continue

            gen_estimate = llm.estimate_cost(
                rendered, model=config.LLM_MODEL, estimated_output_tokens=config.BRIEF_ESTIMATED_GENERATOR_OUTPUT_TOKENS
            )
            # Verifier input is a subset of the same material (surviving
            # sentences + their own cited sources) -- the full rendered
            # generator prompt's token count is a conservative upper bound
            # for it, not a separate measurement.
            verifier_cost_estimate = llm.compute_cost(
                config.LLM_MODEL, llm.estimate_tokens(rendered), config.BRIEF_ESTIMATED_VERIFIER_OUTPUT_TOKENS
            )
            estimate_cost = gen_estimate["cost_usd"] + verifier_cost_estimate
            stats.estimated_cost_usd += estimate_cost
            stats.new_calls_dry += 1

            existing_versions = conn.execute(
                "SELECT prompt_version, verifier_version FROM briefs WHERE accession_no = ?", (row["accession_no"],)
            ).fetchall()
            prior_prompt = {r["prompt_version"] for r in existing_versions if r["prompt_version"] != config.BRIEF_GENERATOR_PROMPT_VERSION}
            prior_verifier = {r["verifier_version"] for r in existing_versions if r["verifier_version"] != config.BRIEF_VERIFIER_VERSION}
            if prior_prompt or prior_verifier:
                stats.invalidated_by_version_bump += 1
                stats.invalidated_cost_usd += estimate_cost
                prior_prompt_versions_seen |= prior_prompt
                prior_verifier_versions_seen |= prior_verifier

        stats.prior_prompt_versions = tuple(sorted(prior_prompt_versions_seen))
        stats.prior_verifier_versions = tuple(sorted(prior_verifier_versions_seen))
        return stats

    effective_run_cost = max_run_cost_usd if max_run_cost_usd is not None else config.LLM_MAX_RUN_COST_USD
    if scheduled:
        effective_run_cost = min(effective_run_cost, config.LLM_SCHEDULED_RUN_MAX_COST_USD)
    effective_call_ceiling = max_calls_per_run if max_calls_per_run is not None else config.LLM_MAX_CALLS_PER_RUN
    run_guard = llm.RunGuard(max_run_cost_usd=effective_run_cost, max_calls_per_run=effective_call_ceiling)

    for row in candidates:
        outcome = generate_brief(conn, row, generator_template, verifier_template, client=client, run_guard=run_guard)
        stats.processed += 1
        if outcome.status == "cached":
            stats.cache_hits += 1
        elif outcome.status == "empty":
            stats.empty_no_input += 1
        elif outcome.status == "ok":
            stats.calls_made += 1
            stats.generator_dropped_total += outcome.generator_dropped
            stats.verifier_dropped_total += outcome.verifier_dropped
            if outcome.sentences:
                stats.briefs_with_sentences += 1
            else:
                stats.briefs_empty_after_drop += 1
        elif outcome.status == "refused":
            stats.refused += 1
            stats.stopped_reason = outcome.note or "refused"
            break
        elif outcome.status == "error":
            stats.calls_made += 1
            stats.errors += 1
        elif outcome.status == "truncated":
            stats.calls_made += 1
            stats.truncated += 1
        elif outcome.status == "skipped":
            stats.skipped_oversized += 1

    stats.run_cost_usd = run_guard.run_spent_usd
    stats.total_cost_usd = llm.total_spent(conn)
    return stats


# --- lookup helpers for the CLI's show-brief command ---


def get_latest_brief(conn: sqlite3.Connection, accession_no: str) -> dict | None:
    brief_row = conn.execute(
        "SELECT * FROM briefs WHERE accession_no = ? ORDER BY id DESC LIMIT 1", (accession_no,)
    ).fetchone()
    if brief_row is None:
        return None
    sentences = [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM brief_sentences WHERE brief_id = ? ORDER BY position", (brief_row["id"],)
        )
    ]
    return {"brief": dict(brief_row), "sentences": sentences}


def resolve_ref(conn: sqlite3.Connection, ref: str) -> dict | None:
    parsed = parse_ref(ref)
    if parsed is None:
        return None
    kind, id_ = parsed
    table = "observations" if kind == "obs" else "findings"
    row = conn.execute(f"SELECT * FROM {table} WHERE id = ?", (id_,)).fetchone()
    return {"kind": "observation" if kind == "obs" else "finding", "row": dict(row)} if row is not None else None
