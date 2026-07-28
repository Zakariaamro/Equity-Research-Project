"""Apply the section-analysis prompt to Notes sections, write findings (SPEC-006).

Owns: which sections get analysed (R4), rendering the prompt (R5), verbatim
quote verification (R6), and persistence of findings. Calling the API,
caching, and the spend ledger belong to llm.py -- this module calls that one,
never the Anthropic API directly.
"""

from __future__ import annotations

import itertools
import logging
import random
import re
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime

from edgar import config, llm, section_store

logger = logging.getLogger(__name__)

_TEMPLATE_MARKER = "## Template"


def _now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0, tzinfo=None).isoformat()


def load_prompt_template(prompt_name: str, prompt_version: str) -> str:
    """Read prompts/{prompt_name}_{prompt_version}.md and return everything
    after the '## Template' marker -- the header above it is documentation
    (Purpose/Inputs/Output/Constraints/Success criteria/Failure cases, R3),
    never sent to the model."""
    path = config.PROMPTS_DIR / f"{prompt_name}_{prompt_version}.md"
    text = path.read_text(encoding="utf-8")
    marker_index = text.find(_TEMPLATE_MARKER)
    if marker_index == -1:
        raise ValueError(f"{path}: no {_TEMPLATE_MARKER!r} marker found")
    return text[marker_index + len(_TEMPLATE_MARKER):].strip()


def render_prompt(
    template: str, company: str, ticker: str, form_type: str, fiscal_period: str, note_name: str, note_text: str
) -> str:
    """Sequential literal replacement, never str.format() -- real filing text
    routinely contains both '{'/'}' (rare but possible) and, far more
    commonly, '$' (in nearly every note), either of which would collide with
    str.format() or string.Template's own placeholder syntax. %%TOKEN%% is
    not a sequence SEC filing prose produces by coincidence.
    """
    replacements = {
        "%%COMPANY%%": company,
        "%%TICKER%%": ticker,
        "%%FORM_TYPE%%": form_type,
        "%%FISCAL_PERIOD%%": fiscal_period,
        "%%NOTE_NAME%%": note_name,
        "%%NOTE_TEXT%%": note_text,
    }
    rendered = template
    for token, value in replacements.items():
        rendered = rendered.replace(token, value)
    return rendered


def format_fiscal_period(fiscal_year: int | None, fiscal_period: str | None) -> str:
    if fiscal_year is None or fiscal_period is None:
        return "unknown fiscal period"
    if fiscal_period == "FY":
        return f"FY{fiscal_year}"
    return f"{fiscal_period} FY{fiscal_year}"


# --- R6: verbatim quote verification ---

_WHITESPACE_RE = re.compile(r"\s+")


def _normalize_whitespace(text: str) -> str:
    return _WHITESPACE_RE.sub(" ", text).strip()


def verify_quote(quote: str, source_text: str) -> bool:
    """R6: normalise whitespace on both sides, then require the quote to
    appear as a substring of the source -- and be at least
    config.QUOTE_MIN_LENGTH characters, since a three-word quote matches
    everything and proves nothing. Altered WORDING (not just whitespace) is
    a failed match, by construction: only whitespace is normalised, nothing
    else."""
    if len(quote.strip()) < config.QUOTE_MIN_LENGTH:
        return False
    normalized_quote = _normalize_whitespace(quote)
    normalized_source = _normalize_whitespace(source_text)
    return normalized_quote in normalized_source


# --- numeric support (measured, not enforced) ---
#
# Quote verification proves the QUOTE is real. It proves nothing about
# `headline` and `detail`, which are the model's own prose and the only
# fields in a finding with no mechanical grounding at all. A number appearing
# there but nowhere in the note is the most legible form that ungroundedness
# can take, so it is worth counting even though not every instance is an
# error: a legitimately derived figure (a sum, a difference, a percentage the
# model computed) is unsupported by this check and still correct.
#
# Deliberately a warning metric only -- config.NUMERIC_SUPPORT_ENFORCE is
# False and nothing is discarded on a failure. The rate is measured first;
# whether to enforce is a decision to make with the numbers in hand.

_NUMBER_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")

# 2026-07-28 checker fix: a real false-positive found live -- a finding wrote
# "Q4 FY2026" (digit form, the model's own shorthand) while the source note
# spelled out "the fourth quarter of fiscal year 2026". `extract_numeric_tokens`
# only ever produces digit-form tokens (by construction of `_NUMBER_RE`, which
# cannot match a word), so the gap is entirely on the SOURCE side: the source's
# spelled-out ordinal never gets compared against the digit form the model
# wrote. Bounded to ordinals 1st-20th -- the only spelled-out numeric forms
# actually observed in this corpus (fiscal quarters/halves, ranked items) --
# deliberately NOT extended to cardinals ("three customers"): that is a
# different, unconfirmed failure mode and adding it now would be guessing
# ahead of a finding, not fixing one.
#
# Applied ONLY within numeric-support checking, NEVER to `verify_quote` (R6)
# -- quote verification's whole point is VERBATIM wording; silently treating
# "fourth" and "4" as the same token there would let a paraphrased quote pass.
_ORDINAL_WORDS: dict[str, str] = {
    word: str(n)
    for n, word in enumerate(
        [
            "first", "second", "third", "fourth", "fifth", "sixth", "seventh", "eighth", "ninth", "tenth",
            "eleventh", "twelfth", "thirteenth", "fourteenth", "fifteenth", "sixteenth", "seventeenth",
            "eighteenth", "nineteenth", "twentieth",
        ],
        start=1,
    )
}
_ORDINAL_WORD_RE = re.compile(r"\b(" + "|".join(_ORDINAL_WORDS) + r")\b", re.IGNORECASE)


def _normalize_ordinal_words(text: str) -> str:
    """Numeric-support-only normalisation (never quote verification, see
    `_ORDINAL_WORDS` above): 'the fourth quarter' -> 'the 4 quarter', so a
    model-written 'Q4' or '4' is recognised against a note that spelled the
    ordinal out."""
    return _ORDINAL_WORD_RE.sub(lambda m: _ORDINAL_WORDS[m.group(0).lower()], text)


def _normalize_number(token: str) -> str:
    """Drop thousands separators so '1,160' and '1160' compare equal."""
    return token.replace(",", "")


def _number_variants(token: str) -> list[str]:
    """The forms a number might legitimately take in the source: as written,
    and with a redundant decimal tail removed ('16.0' also matches '16')."""
    normalized = _normalize_number(token)
    variants = [normalized]
    if "." in normalized:
        trimmed = normalized.rstrip("0").rstrip(".")
        if trimmed and trimmed != normalized:
            variants.append(trimmed)
    return variants


def extract_numeric_tokens(text: str) -> list[str]:
    """Every distinct numeric literal in `text`, in order of first
    appearance. Deduplicated so a year repeated four times counts once and
    cannot inflate the rate in either direction."""
    seen: dict[str, None] = {}
    for match in _NUMBER_RE.finditer(text):
        seen.setdefault(_normalize_number(match.group(0)), None)
    return list(seen)


def _number_in_source(number: str, normalized_source: str) -> bool:
    """Substring match, but bounded: '16' must not be found inside '1160'.
    A digit, comma or decimal point on either side means this is part of a
    different number, not an occurrence of this one."""
    for variant in _number_variants(number):
        pattern = r"(?<![\d.,])" + re.escape(variant) + r"(?![\d,]|\.\d)"
        if re.search(pattern, normalized_source):
            return True
    return False


# 2026-07-28 checker addition: derived-sum verification. A number absent from
# both quote and note is not automatically a fabrication -- it may be the
# model correctly ADDING disclosed figures (three customers at 27%, 18%, 12%
# correctly summed to "57% combined"). Simple presence-checking cannot tell
# that apart from the DANGEROUS case this exists to catch: a number that also
# LOOKS like a plausible sum but whose arithmetic is actually wrong (e.g. the
# model claims "58% combined" for that same 27/18/12 -- a wrong-looking sum
# that presence-checking alone would flag identically to a correct one, with
# no way to tell them apart). Verifying the arithmetic against the quote's OWN
# numbers separates the two: a real subset-sum match is "derived_verified", a
# non-match stays "unsupported" -- now specifically meaning "not present, AND
# not a correct sum of what IS present," which is a strictly more dangerous
# signal than presence-checking alone could produce.
#
# Scoped to the quote's own numbers, never the wider note: the point is
# verifying that the CITED quote itself supports the derived figure, the same
# grounding principle as supported_in_quote vs supported_in_note_only above.
_MAX_QUOTE_NUMBERS_FOR_SUBSET_SUM = 20  # defensive bound, 2**20 worst case; real quotes hold a handful


def _quote_numeric_values(quote: str) -> list[float]:
    """Every numeric literal in the quote, as a float, WITH duplicates and in
    no particular deduplicated set -- unlike `extract_numeric_tokens`, a
    repeated figure is a repeated addend a real sum could legitimately use."""
    return [float(_normalize_number(m.group(0))) for m in _NUMBER_RE.finditer(quote)]


def _is_verified_subset_sum(target: float, quote_values: list[float], tolerance: float = 1e-6) -> bool:
    """True if some subset of >= 2 of `quote_values` sums to `target` (within
    floating tolerance). Subsets of size 1 are deliberately excluded -- a
    single quote number equal to `target` would already have been caught by
    `_number_in_source`'s substring check against the quote; this function
    only runs on tokens that already failed that check, so a size-1 match
    here would be redundant, never a new finding."""
    if len(quote_values) > _MAX_QUOTE_NUMBERS_FOR_SUBSET_SUM:
        return False
    for size in range(2, len(quote_values) + 1):
        for combo in itertools.combinations(quote_values, size):
            if abs(sum(combo) - target) < tolerance:
                return True
    return False


@dataclass
class NumericSupportResult:
    """checked: distinct numeric tokens found in headline+detail.
    supported_in_quote: of those, the count also present in THIS finding's
    own verified `quote` -- the strongest grounding, since the quote is
    already proven a verbatim substring of the note (R6).
    supported_in_note_only: present elsewhere in the note but not in the
    quote itself -- real, but one inferential step weaker: the model could
    have pulled the figure from the right note without it being the figure
    the cited quote actually names.
    derived_verified: absent from both, but arithmetically verified as a
    subset-sum of the quote's own numbers (2026-07-28) -- e.g. "57% combined"
    when the quote discloses 27%, 18%, and 12% separately. A weaker tier than
    presence but a REAL check, not a shrug: the arithmetic was confirmed, not
    merely assumed correct because it looked plausible.
    unsupported: present in neither, AND not a verified sum -- the token this
    check exists to surface. Still not discarded (config.NUMERIC_SUPPORT_ENFORCE
    is False); this is measurement, not enforcement.
    """

    checked: int
    supported_in_quote: int
    supported_in_note_only: int
    derived_verified: list[str]
    unsupported: list[str]

    @property
    def supported(self) -> int:
        return self.supported_in_quote + self.supported_in_note_only

    @property
    def derived_verified_count(self) -> int:
        return len(self.derived_verified)


def check_numeric_support(headline: str, detail: str, quote: str, source_text: str) -> NumericSupportResult:
    """Count the numeric tokens in a finding's prose that do and do not
    appear in the note it came from, and -- of the ones that do -- whether
    they appear in the finding's own cited `quote` or only elsewhere in the
    note (2026-07-27 reporting addition: the two are different strengths of
    evidence, see NumericSupportResult). A token present in neither gets one
    more chance (2026-07-28): a verified subset-sum of the quote's own
    numbers reclassifies it as `derived_verified` rather than `unsupported`.

    Both the source and the quote are normalised for spelled-out ordinals
    ("fourth" -> "4") before matching, numeric-support-only (never for
    `verify_quote`, which must stay strictly verbatim) -- see
    `_normalize_ordinal_words`.

    Applied to KEPT findings only -- discarded findings are never persisted,
    so validate.py could not recompute the same figure for them and the
    run-time and stored rates would disagree.
    """
    normalized_source = _normalize_number(_normalize_ordinal_words(_normalize_whitespace(source_text)))
    normalized_quote = _normalize_number(_normalize_ordinal_words(_normalize_whitespace(quote)))
    quote_values = _quote_numeric_values(quote)
    tokens = extract_numeric_tokens(f"{headline} {detail}")
    supported_in_quote = 0
    supported_in_note_only = 0
    derived_verified: list[str] = []
    unsupported: list[str] = []
    for token in tokens:
        if _number_in_source(token, normalized_quote):
            supported_in_quote += 1
        elif _number_in_source(token, normalized_source):
            supported_in_note_only += 1
        elif _is_verified_subset_sum(float(token), quote_values):
            derived_verified.append(token)
        else:
            unsupported.append(token)
    return NumericSupportResult(
        len(tokens), supported_in_quote, supported_in_note_only, derived_verified, unsupported
    )


# --- R4: what gets analysed ---


def _canonical_note_name(short_name: str) -> str:
    return config.NOTE_NAME_ALIASES.get(short_name, short_name)


def select_candidate_sections(
    conn: sqlite3.Connection, tickers: list[str] | None = None, accession: str | None = None
) -> list[dict]:
    """Every Notes section eligible for analysis (R4): filing_date on or
    after ANALYSIS_START_DATE, form type 10-K/10-Q, category Notes, and not
    a boilerplate note (checked against the CANONICAL name, same alias
    resolution as observations.py -- a note renamed into a boilerplate name,
    or out of one, should be judged by what it currently is).

    Ordered by (accession_no, short_name) for a stable, deterministic base
    ordering -- sampling depends on this being the same every time.
    """
    ciks = [c.cik for c in config.WATCHLIST if tickers is None or c.ticker in tickers]
    if not ciks:
        return []
    placeholders = ",".join("?" for _ in ciks)
    query = f"""
        SELECT s.id AS section_id, s.accession_no, s.short_name, s.text_hash,
               f.form_type, f.filing_date, f.period_end, f.fiscal_year, f.fiscal_period,
               c.cik, c.ticker, c.name AS company_name
        FROM sections s
        JOIN filings f ON f.accession_no = s.accession_no
        JOIN companies c ON c.cik = f.cik
        WHERE c.cik IN ({placeholders})
          AND s.category = ?
          AND f.form_type IN (?, ?)
          AND f.filing_date >= ?
    """
    params: list[object] = [*ciks, config.MENUCATEGORY_NOTES, config.TENK_FORM_TYPE, config.TENQ_FORM_TYPE, config.ANALYSIS_START_DATE]
    if accession is not None:
        query += " AND s.accession_no = ?"
        params.append(accession)
    query += " ORDER BY s.accession_no, s.short_name"

    rows = [dict(r) for r in conn.execute(query, params).fetchall()]
    return [r for r in rows if _canonical_note_name(r["short_name"]) not in config.BOILERPLATE_NOTE_NAMES]


def sample_sections(sections: list[dict], sample_size: int, seed: int) -> list[dict]:
    """Deterministic sample for prompt development (R8) -- same seed, same
    input list, same sample, always. Never a silent random draw: the seed is
    always reported alongside the result."""
    if sample_size >= len(sections):
        return list(sections)
    rng = random.Random(seed)
    return rng.sample(sections, sample_size)


# --- orchestration ---


@dataclass
class RunStats:
    candidates: int = 0
    processed: int = 0
    calls_made: int = 0
    cache_hits: int = 0
    refused: int = 0
    errors: int = 0
    # Distinct from `errors` (2026-07-27 live-error-analysis fix): a call
    # whose stop_reason came back "max_tokens" even after the one retry at
    # config.LLM_TRUNCATION_RETRY_OUTPUT_TOKENS. Counted separately because a
    # RISING truncation rate is a signal the output cap needs raising again
    # -- a signal that was previously invisible, buried inside the generic
    # `errors` count alongside unrelated schema failures.
    truncated: int = 0
    skipped_oversized: int = 0
    findings_returned: int = 0
    findings_kept: int = 0
    findings_discarded: int = 0
    numeric_tokens_checked: int = 0
    # Split by evidentiary strength (2026-07-27 reporting addition) -- see
    # NumericSupportResult. `numeric_tokens_supported` is their sum, kept as
    # a property below rather than a stored field so the two parts can never
    # drift out of sync with the total.
    numeric_tokens_supported_in_quote: int = 0
    numeric_tokens_supported_in_note_only: int = 0
    # 2026-07-28: absent from the quote/note but a verified subset-sum of the
    # quote's own numbers (e.g. "57% combined" from a quote disclosing 27%,
    # 18%, 12% separately) -- a real, checked tier, not presence-based, see
    # analyze.NumericSupportResult. Counted separately from
    # numeric_tokens_supported_in_*, never folded into it: the whole point is
    # that "verified by arithmetic" and "found verbatim" are different kinds
    # of evidence, and collapsing them would erase exactly the distinction
    # this addition exists to report.
    numeric_tokens_derived_verified: int = 0
    findings_with_unsupported_numbers: int = 0
    total_cost_usd: float = 0.0
    seed_used: int | None = None
    dry_run: bool = True
    estimated_cost_usd: float = 0.0

    # SPEC-006A L3/L6: set when a run-scoped guard stopped the run before
    # every selected candidate was processed. None means the run completed
    # its full selection (individual per-call L2 refusals notwithstanding).
    stopped_reason: str | None = None
    # SPEC-006A L10: this run's OWN spend (llm.RunGuard.run_spent_usd),
    # distinct from total_cost_usd, which is the LIFETIME ledger total after
    # this run.
    run_cost_usd: float = 0.0

    # SPEC-006A L5: cache-impact report, populated on the dry-run pass only
    # (execute=True reuses the same numbers rather than recomputing them,
    # since nothing about the candidate set or the cache changes between the
    # pre-execute dry run and the execute run that immediately follows it).
    cache_hits_dry: int = 0
    new_calls_dry: int = 0
    invalidated_by_version_bump: int = 0
    invalidated_cost_usd: float = 0.0
    prior_prompt_versions: tuple[str, ...] = ()

    @property
    def version_bumped(self) -> bool:
        return bool(self.prior_prompt_versions)

    @property
    def numeric_tokens_supported(self) -> int:
        return self.numeric_tokens_supported_in_quote + self.numeric_tokens_supported_in_note_only

    @property
    def numeric_support_rate(self) -> float | None:
        """Fraction of numeric tokens in kept findings' prose that appear
        ANYWHERE in the source note (quote or elsewhere). None when no
        numbers were checked at all -- a run with no findings has no rate,
        which is different from a rate of 0.0 and should not be reported as
        one."""
        if self.numeric_tokens_checked == 0:
            return None
        return self.numeric_tokens_supported / self.numeric_tokens_checked

    @property
    def numeric_support_rate_in_quote(self) -> float | None:
        """Fraction of checked numeric tokens supported specifically by the
        finding's own cited quote -- the strongest grounding tier."""
        if self.numeric_tokens_checked == 0:
            return None
        return self.numeric_tokens_supported_in_quote / self.numeric_tokens_checked

    @property
    def numeric_support_rate_in_note_only(self) -> float | None:
        """Fraction of checked numeric tokens supported by the note but NOT
        by the finding's own cited quote -- present, real, but one
        inferential step weaker than in-quote support."""
        if self.numeric_tokens_checked == 0:
            return None
        return self.numeric_tokens_supported_in_note_only / self.numeric_tokens_checked

    @property
    def numeric_derived_verified_rate(self) -> float | None:
        """Fraction of checked numeric tokens absent from quote/note but
        verified as a correct subset-sum of the quote's own numbers
        (2026-07-28) -- a real, arithmetic-checked tier, distinct from
        presence-based support."""
        if self.numeric_tokens_checked == 0:
            return None
        return self.numeric_tokens_derived_verified / self.numeric_tokens_checked


def _write_finding(conn: sqlite3.Connection, analysis_id: int, accession_no: str, finding: dict) -> None:
    conn.execute(
        "INSERT INTO findings (analysis_id, accession_no, category, severity, headline, detail, quote, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            analysis_id, accession_no, finding["category"], finding["severity"],
            finding["headline"], finding["detail"], finding["quote"], _now_iso(),
        ),
    )


@dataclass
class SectionResult:
    status: str
    findings_returned: int = 0
    findings_kept: int = 0
    numeric_tokens_checked: int = 0
    numeric_tokens_supported_in_quote: int = 0
    numeric_tokens_supported_in_note_only: int = 0
    numeric_tokens_derived_verified: int = 0
    findings_with_unsupported_numbers: int = 0
    note: str | None = None


def analyze_one_section(
    conn: sqlite3.Connection,
    row: dict,
    template: str,
    client: llm.LLMClient | None = None,
    run_guard: llm.RunGuard | None = None,
) -> SectionResult:
    """Render the prompt for one section, call llm.get_or_create_analysis,
    verify quotes, and write surviving findings. Findings are only ever
    written for a freshly-made ('ok') analysis, never for a cache hit, so
    re-running never writes duplicate rows (AC13)."""
    note_text = section_store.read_section_text(row["text_hash"])
    fiscal_period_str = format_fiscal_period(row["fiscal_year"], row["fiscal_period"])
    rendered = render_prompt(
        template,
        company=row["company_name"],
        ticker=row["ticker"],
        form_type=row["form_type"],
        fiscal_period=fiscal_period_str,
        note_name=_canonical_note_name(row["short_name"]),
        note_text=note_text,
    )

    outcome = llm.get_or_create_analysis(
        conn,
        section_id=row["section_id"],
        prompt_name=config.SECTION_ANALYSIS_PROMPT_NAME,
        prompt_version=config.SECTION_ANALYSIS_PROMPT_VERSION,
        rendered_prompt=rendered,
        client=client,
        run_guard=run_guard,
    )

    if outcome.status != "ok":
        return SectionResult(outcome.status, note=outcome.note)

    findings = outcome.output.get("findings", [])
    result = SectionResult("ok", findings_returned=len(findings))
    for finding in findings:
        if not verify_quote(finding["quote"], note_text):
            logger.info(
                "Discarded finding (quote not verified): accession=%s note=%s prompt_version=%s headline=%r",
                row["accession_no"], row["short_name"], config.SECTION_ANALYSIS_PROMPT_VERSION, finding.get("headline"),
            )
            continue

        _write_finding(conn, outcome.analysis_id, row["accession_no"], finding)
        result.findings_kept += 1

        numeric = check_numeric_support(
            finding["headline"], finding["detail"], finding["quote"], note_text
        )
        result.numeric_tokens_checked += numeric.checked
        result.numeric_tokens_supported_in_quote += numeric.supported_in_quote
        result.numeric_tokens_supported_in_note_only += numeric.supported_in_note_only
        result.numeric_tokens_derived_verified += numeric.derived_verified_count
        if numeric.derived_verified:
            logger.info(
                "Derived-verified number(s) %s (correct subset-sum of the quote's own figures): "
                "accession=%s note=%s prompt_version=%s headline=%r",
                numeric.derived_verified, row["accession_no"], row["short_name"],
                config.SECTION_ANALYSIS_PROMPT_VERSION, finding.get("headline"),
            )
        if numeric.unsupported:
            result.findings_with_unsupported_numbers += 1
            # Kept, not discarded (config.NUMERIC_SUPPORT_ENFORCE is False) --
            # logged with the offending tokens so the rate can be judged on
            # what actually failed, not just how often. Reaching here means
            # the token was not just absent -- it was also checked against
            # every subset-sum of the quote's own numbers and did not match
            # any of them (2026-07-28): a correct-looking sum failing this
            # check is the dangerous case this addition exists to surface.
            logger.info(
                "Kept finding with unsupported number(s) %s: accession=%s note=%s prompt_version=%s headline=%r",
                numeric.unsupported, row["accession_no"], row["short_name"],
                config.SECTION_ANALYSIS_PROMPT_VERSION, finding.get("headline"),
            )
    conn.commit()
    return result


def _prior_prompt_versions_by_section(
    conn: sqlite3.Connection, prompt_name: str, section_ids: list[int]
) -> dict[int, set[str]]:
    """SPEC-006A L5: every prompt_version any candidate section already has
    an `analyses` row under, keyed by section_id -- the raw material for
    telling "never analysed" apart from "analysed under an older prompt
    version, now invalidated by a version bump." """
    if not section_ids:
        return {}
    placeholders = ",".join("?" for _ in section_ids)
    rows = conn.execute(
        f"SELECT section_id, prompt_version FROM analyses "
        f"WHERE prompt_name = ? AND section_id IN ({placeholders})",
        (prompt_name, *section_ids),
    ).fetchall()
    out: dict[int, set[str]] = {}
    for r in rows:
        out.setdefault(r["section_id"], set()).add(r["prompt_version"])
    return out


def run_analysis(
    conn: sqlite3.Connection,
    tickers: list[str] | None = None,
    accession: str | None = None,
    sample: int | None = None,
    seed: int | None = None,
    limit: int | None = None,
    execute: bool = False,
    client: llm.LLMClient | None = None,
    max_run_cost_usd: float | None = None,
    max_calls_per_run: int | None = None,
    scheduled: bool = False,
) -> RunStats:
    """The single entry point pipeline.py's analyze-sections command calls.

    Default (execute=False) is a dry run: estimates cost for the full
    candidate set (after sample/limit), classifies each candidate as a cache
    hit or a new call (SPEC-006A L5), and makes no calls (R8, AC5/AC6).
    execute=True actually calls llm.get_or_create_analysis per section,
    guarded by an llm.RunGuard built from max_run_cost_usd/max_calls_per_run
    (SPEC-006A L3/L6; defaults from config.LLM_MAX_RUN_COST_USD/
    LLM_MAX_CALLS_PER_RUN when not given). `scheduled=True` (L7) clamps the
    run-cost ceiling to config.LLM_SCHEDULED_RUN_MAX_COST_USD regardless of
    max_run_cost_usd, and refuses outright if `sample` is also given --
    sampling is a prompt-development bypass of the real candidate set, never
    appropriate for unattended runs.
    """
    stats = RunStats(dry_run=not execute)

    if scheduled and sample is not None:
        raise ValueError("scheduled runs must never use --sample (a prompt-development-only bypass, SPEC-006A L7)")

    candidates = select_candidate_sections(conn, tickers=tickers, accession=accession)
    stats.candidates = len(candidates)

    selected = candidates
    if sample is not None:
        seed = seed if seed is not None else config.DEFAULT_SAMPLE_SEED
        selected = sample_sections(candidates, sample, seed)
        stats.seed_used = seed
    if limit is not None:
        selected = selected[:limit]

    template = load_prompt_template(config.SECTION_ANALYSIS_PROMPT_NAME, config.SECTION_ANALYSIS_PROMPT_VERSION)

    if not execute:
        prior_versions = _prior_prompt_versions_by_section(
            conn, config.SECTION_ANALYSIS_PROMPT_NAME, [row["section_id"] for row in selected]
        )
        prior_versions_seen: set[str] = set()
        for row in selected:
            note_text = section_store.read_section_text(row["text_hash"])
            fiscal_period_str = format_fiscal_period(row["fiscal_year"], row["fiscal_period"])
            rendered = render_prompt(
                template,
                company=row["company_name"], ticker=row["ticker"], form_type=row["form_type"],
                fiscal_period=fiscal_period_str, note_name=_canonical_note_name(row["short_name"]),
                note_text=note_text,
            )
            input_hash = llm.compute_input_hash(rendered, config.LLM_MODEL, config.SECTION_ANALYSIS_PROMPT_VERSION)
            already_cached = conn.execute(
                "SELECT 1 FROM analyses WHERE input_hash = ?", (input_hash,)
            ).fetchone()
            if already_cached is not None:
                stats.cache_hits_dry += 1
                continue

            estimate = llm.estimate_cost(rendered, model=config.LLM_MODEL)
            stats.estimated_cost_usd += estimate["cost_usd"]
            stats.new_calls_dry += 1

            prior_non_current = prior_versions.get(row["section_id"], set()) - {
                config.SECTION_ANALYSIS_PROMPT_VERSION
            }
            if prior_non_current:
                stats.invalidated_by_version_bump += 1
                stats.invalidated_cost_usd += estimate["cost_usd"]
                prior_versions_seen |= prior_non_current
        stats.prior_prompt_versions = tuple(sorted(prior_versions_seen))
        stats.processed = len(selected)
        return stats

    effective_run_cost = max_run_cost_usd if max_run_cost_usd is not None else config.LLM_MAX_RUN_COST_USD
    if scheduled:
        effective_run_cost = min(effective_run_cost, config.LLM_SCHEDULED_RUN_MAX_COST_USD)
    effective_call_ceiling = max_calls_per_run if max_calls_per_run is not None else config.LLM_MAX_CALLS_PER_RUN
    run_guard = llm.RunGuard(max_run_cost_usd=effective_run_cost, max_calls_per_run=effective_call_ceiling)

    for row in selected:
        result = analyze_one_section(conn, row, template, client=client, run_guard=run_guard)
        stats.processed += 1
        if result.status == "cached":
            stats.cache_hits += 1
        elif result.status == "ok":
            stats.calls_made += 1
            stats.findings_returned += result.findings_returned
            stats.findings_kept += result.findings_kept
            stats.findings_discarded += result.findings_returned - result.findings_kept
            stats.numeric_tokens_checked += result.numeric_tokens_checked
            stats.numeric_tokens_supported_in_quote += result.numeric_tokens_supported_in_quote
            stats.numeric_tokens_supported_in_note_only += result.numeric_tokens_supported_in_note_only
            stats.numeric_tokens_derived_verified += result.numeric_tokens_derived_verified
            stats.findings_with_unsupported_numbers += result.findings_with_unsupported_numbers
        elif result.status == "refused":
            stats.refused += 1
            # SPEC-006A: ANY refusal (lifetime budget L2, per-run cost L3, or
            # call-count L6) stops the run here rather than looping through
            # every remaining candidate re-refusing each one -- "the run
            # stops cleanly, reports what it completed" (AC2).
            stats.stopped_reason = result.note or "refused"
            break
        elif result.status == "error":
            stats.calls_made += 1
            stats.errors += 1
        elif result.status == "truncated":
            stats.calls_made += 1
            stats.truncated += 1
        elif result.status == "skipped":
            stats.skipped_oversized += 1

    stats.run_cost_usd = run_guard.run_spent_usd
    stats.total_cost_usd = llm.total_spent(conn)
    return stats
