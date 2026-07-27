"""Observations engine (SPEC-005).

Python decides what is notable, deterministically, before any AI is
involved. This module reads `metrics` and `sections` (never raw
`xbrl_facts`) and writes small, verified, non-causal statements to
`observations`, each pointing back at the rows that produced it.

Declarative rule parameters live in config.py (RULE_REGISTRY,
DECLARED_THRESHOLDS, DECLARED_DIVERGENCES, NOTE_NAME_ALIASES,
FLUCTUATING_NOTE_NAMES, ...) -- this module is an engine plus primitives,
matching the config.py/metrics.py split already established by SPEC-004.

Fiscal-period prior-year matching for SECTIONS uses filings.fiscal_year /
filings.fiscal_period (SPEC-005 change 9), never date arithmetic and never a
join into xbrl_facts -- robust to NVDA/MU's floating 52/53-week years by
construction, since it is not derived from any date at all.
"""

from __future__ import annotations

import difflib
import json
import logging
import sqlite3
import statistics
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime
from typing import Callable

from edgar import config, readability, section_store
from edgar import metrics as metrics_mod

logger = logging.getLogger(__name__)

_CLASS_TO_FORM: dict[str, str] = {
    "annual": config.TENK_FORM_TYPE,
    "quarterly": config.TENQ_FORM_TYPE,
}


@dataclass(frozen=True)
class Observation:
    cik: str
    accession_no: str | None
    period_end: str
    rule_name: str
    rule_version: str
    subject: str
    severity: str
    statement: str
    refs: list[dict] = field(default_factory=list)


def _now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0, tzinfo=None).isoformat()


def _ref(table: str, id_: int) -> dict:
    return {"table": table, "id": id_}


def _fires_on_transition(cur: bool | None, prev: bool | None) -> bool:
    """SPEC-005 change 3 -- a state-based rule fires only entering the
    condition, never while it persists. Strict identity checks (not truthy
    comparisons) so an unknown (None) state never counts as a transition."""
    return cur is True and prev is False


# --- metric primitives ---


def _metric_rows(conn: sqlite3.Connection, cik: str, name: str) -> list[dict]:
    """All metrics rows for (cik, name), current calc_version, chronological.
    Each row carries its own duration class (annual/quarterly/other)."""
    rows = conn.execute(
        "SELECT id, period_start, period_end, value FROM metrics "
        "WHERE cik = ? AND name = ? AND calc_version = ? ORDER BY period_end",
        (cik, name, config.CALC_VERSION),
    ).fetchall()
    out = []
    for r in rows:
        days = (date.fromisoformat(r["period_end"]) - date.fromisoformat(r["period_start"])).days
        out.append(
            {
                "id": r["id"],
                "period_start": r["period_start"],
                "period_end": r["period_end"],
                "value": r["value"],
                "cls": metrics_mod._classify_duration(days),
            }
        )
    return out


def _accession_for_row(conn: sqlite3.Connection, cik: str, period_end: str, cls: str) -> str | None:
    form_type = _CLASS_TO_FORM.get(cls)
    if form_type is None:
        return None
    row = conn.execute(
        "SELECT accession_no FROM filings WHERE cik = ? AND period_end = ? AND form_type = ?",
        (cik, period_end, form_type),
    ).fetchone()
    return row["accession_no"] if row else None


def _fiscal_label_for_row(conn: sqlite3.Connection, cik: str, period_end: str, cls: str) -> tuple[int, str] | None:
    form_type = _CLASS_TO_FORM.get(cls)
    if form_type is None:
        return None
    row = conn.execute(
        "SELECT fiscal_year, fiscal_period FROM filings "
        "WHERE cik = ? AND period_end = ? AND form_type = ? AND fiscal_year IS NOT NULL",
        (cik, period_end, form_type),
    ).fetchone()
    return (row["fiscal_year"], row["fiscal_period"]) if row else None


def _metric_row_for_fiscal_label(
    conn: sqlite3.Connection, cik: str, name: str, fiscal_year: int, fiscal_period: str
) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT m.id, m.period_start, m.period_end, m.value FROM metrics m
        JOIN filings f ON f.cik = m.cik AND f.period_end = m.period_end
        WHERE m.cik = ? AND m.name = ? AND m.calc_version = ?
          AND f.fiscal_year = ? AND f.fiscal_period = ?
        """,
        (cik, name, config.CALC_VERSION, fiscal_year, fiscal_period),
    ).fetchone()


def _filing_for_fiscal_label(
    conn: sqlite3.Connection, cik: str, form_type: str, fiscal_year: int, fiscal_period: str
) -> tuple[str, str] | None:
    row = conn.execute(
        "SELECT accession_no, period_end FROM filings "
        "WHERE cik = ? AND form_type = ? AND fiscal_year = ? AND fiscal_period = ?",
        (cik, form_type, fiscal_year, fiscal_period),
    ).fetchone()
    return (row["accession_no"], row["period_end"]) if row else None


# --- section primitives ---


def _section_rows(conn: sqlite3.Connection, cik: str, categories: tuple[str, ...]) -> list[dict]:
    placeholders = ",".join("?" for _ in categories)
    rows = conn.execute(
        f"""
        SELECT s.id, s.accession_no, s.category, s.short_name, s.text_hash, s.normalized_text_hash,
               s.word_count, s.sentence_count, s.complex_word_count,
               f.form_type, f.period_end, f.fiscal_year, f.fiscal_period
        FROM sections s JOIN filings f ON f.accession_no = s.accession_no
        WHERE f.cik = ? AND s.category IN ({placeholders}) AND f.fiscal_year IS NOT NULL
        """,
        (cik, *categories),
    ).fetchall()
    return [dict(r) for r in rows]


def _canonical_note_name(short_name: str) -> str:
    return config.NOTE_NAME_ALIASES.get(short_name, short_name)


def _section_identity_map(
    conn: sqlite3.Connection, cik: str, categories: tuple[str, ...]
) -> dict[tuple[str, str, str], dict[tuple[int, str], dict]]:
    """(category, canonical_short_name, form_type) -> {(fiscal_year, fiscal_period): row}."""
    out: dict[tuple[str, str, str], dict[tuple[int, str], dict]] = {}
    for r in _section_rows(conn, cik, categories):
        canonical = _canonical_note_name(r["short_name"])
        key = (r["category"], canonical, r["form_type"])
        out.setdefault(key, {})[(r["fiscal_year"], r["fiscal_period"])] = r
    return out


def _section_presence_map(
    conn: sqlite3.Connection, cik: str, category: str
) -> dict[tuple[str, str], dict[int, dict[str, dict]]]:
    """(form_type, fiscal_period) -> {fiscal_year: {canonical_short_name: row}}.

    Excludes FLUCTUATING_NOTE_NAMES entirely -- a note whose presence
    legitimately toggles quarter to quarter is not a section_appeared/
    section_disappeared event, by definition (SPEC-005 change 6).
    """
    out: dict[tuple[str, str], dict[int, dict[str, dict]]] = {}
    for r in _section_rows(conn, cik, (category,)):
        canonical = _canonical_note_name(r["short_name"])
        if canonical in config.FLUCTUATING_NOTE_NAMES:
            continue
        key = (r["form_type"], r["fiscal_period"])
        out.setdefault(key, {}).setdefault(r["fiscal_year"], {})[canonical] = r
    return out


# --- R3: no-lookahead enforcement ---


def _ref_period_end(conn: sqlite3.Connection, ref: dict) -> str | None:
    if ref["table"] == "metrics":
        row = conn.execute("SELECT period_end FROM metrics WHERE id = ?", (ref["id"],)).fetchone()
    else:
        row = conn.execute(
            "SELECT f.period_end FROM sections s JOIN filings f ON f.accession_no = s.accession_no WHERE s.id = ?",
            (ref["id"],),
        ).fetchone()
    return row["period_end"] if row else None


def _assert_no_lookahead(conn: sqlite3.Connection, obs: Observation) -> None:
    for ref in obs.refs:
        ref_period_end = _ref_period_end(conn, ref)
        if ref_period_end is not None and ref_period_end > obs.period_end:
            raise AssertionError(
                f"Lookahead: observation for {obs.cik} {obs.period_end} "
                f"({obs.rule_name}/{obs.subject}) references {ref} with period_end {ref_period_end}"
            )


# --- R5 rule 1: metric_multi_year_extreme ---


def _severity_for_extreme(mdef: config.MetricDef) -> str:
    """SPEC-005 post-implementation round 2: severity by analytical
    materiality, not by detection method -- "high" only where a multi-year
    record is itself the analytical claim (margins, returns, working-capital
    days); "medium" for every other eligible category."""
    return "high" if mdef.category in config.EXTREME_HIGH_SEVERITY_CATEGORIES else "medium"


def _rule_metric_multi_year_extreme(conn: sqlite3.Connection, cik: str) -> list[Observation]:
    rdef = config.RULE_REGISTRY["metric_multi_year_extreme"]
    obs: list[Observation] = []
    for name, mdef in config.METRIC_REGISTRY.items():
        if not mdef.extreme_informative or not mdef.headline:
            continue
        severity = _severity_for_extreme(mdef)
        rows_all = [r for r in _metric_rows(conn, cik, name) if r["value"] is not None]
        by_cls: dict[str, list[dict]] = {}
        for r in rows_all:
            by_cls.setdefault(r["cls"], []).append(r)
        for cls, rows in by_cls.items():
            min_prior = config.EXTREME_MIN_PRIOR_PERIODS.get(cls)
            window_n = config.EXTREME_WINDOW_PERIODS.get(cls)
            if min_prior is None or window_n is None:
                continue  # "half-year"/"three-quarter"/"other" -- never applies
            for i, row in enumerate(rows):
                prior_all = rows[:i]
                if len(prior_all) < min_prior:
                    continue
                prior = prior_all[-window_n:]
                values = [p["value"] for p in prior]
                lo, hi = min(values), max(values)
                if row["value"] > hi:
                    direction = "highest"
                elif row["value"] < lo:
                    direction = "lowest"
                else:
                    continue
                accession_no = _accession_for_row(conn, cik, row["period_end"], cls)
                unit = "quarters" if cls == "quarterly" else "years"
                total_periods = len(prior) + 1
                statement = f"{name} of {row['value']:.4g} is the {direction} in {total_periods} {unit}."
                refs = [_ref("metrics", row["id"])] + [_ref("metrics", p["id"]) for p in prior]
                obs.append(
                    Observation(
                        cik, accession_no, row["period_end"], rdef.name, rdef.version, name, severity,
                        statement, refs,
                    )
                )
    return obs


# --- R5 rule 2: metric_sigma_move (quarterly-only, SPEC-005 change 4) ---


def _rule_metric_sigma_move(conn: sqlite3.Connection, cik: str) -> list[Observation]:
    rdef = config.RULE_REGISTRY["metric_sigma_move"]
    obs: list[Observation] = []
    for name, mdef in config.METRIC_REGISTRY.items():
        if not mdef.headline:
            continue
        rows = [r for r in _metric_rows(conn, cik, name) if r["value"] is not None and r["cls"] == "quarterly"]
        for i, row in enumerate(rows):
            prior = rows[:i]
            if len(prior) < config.SIGMA_MIN_PRIOR_PERIODS:
                continue
            values = [p["value"] for p in prior]
            mean = statistics.mean(values)
            stdev = statistics.pstdev(values)
            if stdev == 0:
                continue
            z = (row["value"] - mean) / stdev
            if abs(z) <= config.SIGMA_STD_DEV_THRESHOLD:
                continue
            accession_no = _accession_for_row(conn, cik, row["period_end"], "quarterly")
            statement = (
                f"{name} of {row['value']:.4g} is {abs(z):.1f} standard deviations "
                f"{'above' if z > 0 else 'below'} its trailing {len(prior)}-quarter mean of {mean:.4g}."
            )
            refs = [_ref("metrics", row["id"])] + [_ref("metrics", p["id"]) for p in prior]
            obs.append(
                Observation(
                    cik, accession_no, row["period_end"], rdef.name, rdef.version, name, rdef.severity,
                    statement, refs,
                )
            )
    return obs


# --- R5 rule 3: metric_threshold_cross (transition-only, SPEC-005 change 3) ---


def _threshold_state(rule: config.ThresholdRule, value: float) -> bool | str:
    if rule.comparator == "above":
        return value > rule.value
    if rule.comparator == "below":
        return value < rule.value
    if rule.comparator == "crosses_zero":
        return "positive" if value >= 0 else "negative"
    raise ValueError(rule.comparator)


def _threshold_statement(rule: config.ThresholdRule, value: float) -> str:
    if rule.comparator == "above":
        return f"{rule.metric} of {value:.4g} crossed above the {rule.value:.4g} screening threshold."
    if rule.comparator == "below":
        return f"{rule.metric} of {value:.4g} crossed below the {rule.value:.4g} threshold."
    return f"{rule.metric} crossed zero to {value:.4g}."


def _rule_metric_threshold_cross(conn: sqlite3.Connection, cik: str) -> list[Observation]:
    rdef = config.RULE_REGISTRY["metric_threshold_cross"]
    obs: list[Observation] = []
    for rule in config.DECLARED_THRESHOLDS:
        rows = [r for r in _metric_rows(conn, cik, rule.metric) if r["value"] is not None]
        for i in range(1, len(rows)):
            prev_state = _threshold_state(rule, rows[i - 1]["value"])
            cur_state = _threshold_state(rule, rows[i]["value"])
            if rule.comparator == "crosses_zero":
                fires = cur_state != prev_state
            else:
                fires = _fires_on_transition(cur_state, prev_state)
            if not fires:
                continue
            row = rows[i]
            accession_no = _accession_for_row(conn, cik, row["period_end"], row["cls"])
            statement = _threshold_statement(rule, row["value"])
            refs = [_ref("metrics", row["id"]), _ref("metrics", rows[i - 1]["id"])]
            obs.append(
                Observation(
                    cik, accession_no, row["period_end"], rdef.name, rdef.version, rule.metric, rule.severity,
                    statement, refs,
                )
            )
    return obs


# --- R5 rule 4: metric_divergence (transition-only, SPEC-005 change 3) ---


def _divergence_state(
    conn: sqlite3.Connection, cik: str, rule: config.DivergenceRule, row: dict
) -> bool | None:
    if rule.shape == "above":
        return row["value"] > rule.value
    if rule.shape == "yoy_decline":
        label = _fiscal_label_for_row(conn, cik, row["period_end"], row["cls"])
        if label is None:
            return None
        fy, fp = label
        prior_row = _metric_row_for_fiscal_label(conn, cik, rule.metric, fy - 1, fp)
        if prior_row is None or not prior_row["value"]:
            return None
        pct_change = row["value"] / prior_row["value"] - 1
        return pct_change < -rule.value
    raise ValueError(rule.shape)


def _divergence_statement(rule: config.DivergenceRule, row: dict) -> str:
    if rule.shape == "above":
        return f"{rule.metric} of {row['value']:.4g} is above the {rule.value:.4g} divergence threshold."
    return f"{rule.metric} of {row['value']:.4g} fell more than {rule.value:.0%} year over year."


def _rule_metric_divergence(conn: sqlite3.Connection, cik: str) -> list[Observation]:
    rdef = config.RULE_REGISTRY["metric_divergence"]
    obs: list[Observation] = []
    for rule in config.DECLARED_DIVERGENCES:
        rows = [r for r in _metric_rows(conn, cik, rule.metric) if r["value"] is not None]
        states = [_divergence_state(conn, cik, rule, r) for r in rows]
        for i in range(1, len(rows)):
            if not _fires_on_transition(states[i], states[i - 1]):
                continue
            row = rows[i]
            accession_no = _accession_for_row(conn, cik, row["period_end"], row["cls"])
            statement = _divergence_statement(rule, row)
            refs = [_ref("metrics", row["id"]), _ref("metrics", rows[i - 1]["id"])]
            obs.append(
                Observation(
                    cik, accession_no, row["period_end"], rdef.name, rdef.version, rule.metric, rdef.severity,
                    statement, refs,
                )
            )
    return obs


# --- R5 rule 5: section_wording_changed (renamed, calibrated -- SPEC-005 change 2) ---


def _wording_category_noun(category: str) -> str:
    return "note" if category == config.MENUCATEGORY_NOTES else "policy"


def _rule_section_wording_changed(conn: sqlite3.Connection, cik: str) -> list[Observation]:
    """Applies to BOTH Notes and Policies -- measured live, exact
    normalized_text_hash equality changes on 98%+ of fiscal-year-matched
    comparisons in EITHER category (real, pervasive small edits, not a
    normalization bug), so byte-for-byte wording identity is not a usable
    firing signal in either. Fires on a real materiality threshold instead:
    normalized_text_hash first as a fast exact-match skip, then a genuine
    similarity ratio between the two normalized texts for anything that
    differs at all. See config.SECTION_WORDING_SIMILARITY_THRESHOLD for the
    measured calibration.
    """
    rdef = config.RULE_REGISTRY["section_wording_changed"]
    obs: list[Observation] = []
    identity = _section_identity_map(conn, cik, (config.MENUCATEGORY_NOTES, config.MENUCATEGORY_POLICIES))
    for (category, canonical, _form_type), by_label in identity.items():
        for (fy, fp), row in by_label.items():
            prior = by_label.get((fy - 1, fp))
            if prior is None:
                continue
            if row["normalized_text_hash"] == prior["normalized_text_hash"]:
                continue
            text_cur = section_store.normalize_for_wording_hash(section_store.read_section_text(row["text_hash"]))
            text_prior = section_store.normalize_for_wording_hash(
                section_store.read_section_text(prior["text_hash"])
            )
            similarity = difflib.SequenceMatcher(None, text_cur, text_prior).ratio()
            if similarity >= config.SECTION_WORDING_SIMILARITY_THRESHOLD:
                continue
            noun = _wording_category_noun(category)
            statement = f"The {canonical} {noun} reads {similarity:.0%} similar to a year ago."
            refs = [_ref("sections", row["id"]), _ref("sections", prior["id"])]
            obs.append(
                Observation(
                    cik, row["accession_no"], row["period_end"], rdef.name, rdef.version, canonical,
                    rdef.severity, statement, refs,
                )
            )
    return obs


# --- R5 rule 6: section_length_change ---


def _rule_section_length_change(conn: sqlite3.Connection, cik: str) -> list[Observation]:
    rdef = config.RULE_REGISTRY["section_length_change"]
    obs: list[Observation] = []
    identity = _section_identity_map(conn, cik, (config.MENUCATEGORY_NOTES, config.MENUCATEGORY_POLICIES))
    for (_category, canonical, _form_type), by_label in identity.items():
        for (fy, fp), row in by_label.items():
            prior = by_label.get((fy - 1, fp))
            if prior is None or not prior["word_count"] or not row["word_count"]:
                continue
            pct = row["word_count"] / prior["word_count"] - 1
            if abs(pct) <= config.SECTION_LENGTH_CHANGE_PCT:
                continue
            statement = (
                f"The {canonical} note is {abs(pct):.0%} {'longer' if pct > 0 else 'shorter'} than a year ago "
                f"({row['word_count']} words, from {prior['word_count']})."
            )
            refs = [_ref("sections", row["id"]), _ref("sections", prior["id"])]
            obs.append(
                Observation(
                    cik, row["accession_no"], row["period_end"], rdef.name, rdef.version, canonical,
                    rdef.severity, statement, refs,
                )
            )
    return obs


# --- R5 rule 7/8: section_appeared / section_disappeared ---


def _detect_renames(
    disappeared_names: set[str], appeared_names: set[str], prior_names: dict, names: dict
) -> list[tuple[str, str, float]]:
    """Greedy best-match pairing within one year-over-year transition (R5e):
    a disappeared name and an appeared name whose actual section text is
    similar above SECTION_RENAME_SIMILARITY_THRESHOLD are the same
    continuing note under a new title, not a genuine disappear+appear pair.
    Reuses section_wording_changed's exact similarity function (normalized
    text, difflib.SequenceMatcher.ratio -- the real ratio, not quick_ratio;
    quick_ratio is a fast upper-bound heuristic based on character-multiset
    overlap, not actual sequence matching, and is unreliable for this use:
    checked live, two genuinely unrelated MU notes scored quick_ratio=0.87
    against a real ratio() of 0.03). Deterministic: both name sets are
    walked in sorted order.
    """
    pairs: list[tuple[str, str, float]] = []
    remaining_appeared = set(appeared_names)
    for old_name in sorted(disappeared_names):
        old_row = prior_names[old_name]
        try:
            old_text = section_store.normalize_for_wording_hash(section_store.read_section_text(old_row["text_hash"]))
        except section_store.SectionContentMissingError:
            continue
        best_name, best_ratio = None, 0.0
        for new_name in sorted(remaining_appeared):
            new_row = names[new_name]
            try:
                new_text = section_store.normalize_for_wording_hash(
                    section_store.read_section_text(new_row["text_hash"])
                )
            except section_store.SectionContentMissingError:
                continue
            ratio = difflib.SequenceMatcher(None, old_text, new_text).ratio()
            if ratio > best_ratio:
                best_name, best_ratio = new_name, ratio
        if best_name is not None and best_ratio > config.SECTION_RENAME_SIMILARITY_THRESHOLD:
            pairs.append((old_name, best_name, best_ratio))
            remaining_appeared.discard(best_name)
    return pairs


def _rule_section_appeared_disappeared(conn: sqlite3.Connection, cik: str) -> list[Observation]:
    rdef_a = config.RULE_REGISTRY["section_appeared"]
    rdef_d = config.RULE_REGISTRY["section_disappeared"]
    rdef_r = config.RULE_REGISTRY["section_renamed"]
    obs: list[Observation] = []
    presence = _section_presence_map(conn, cik, config.MENUCATEGORY_NOTES)
    for (form_type, fiscal_period), by_fy in presence.items():
        for fy, names in by_fy.items():
            prior_names = by_fy.get(fy - 1)
            if prior_names is None:
                continue
            appeared_names = set(names) - set(prior_names)
            disappeared_names = set(prior_names) - set(names)

            # Boilerplate names never participate in rename detection (R5f):
            # they are already forced "low" via BOILERPLATE_NOTE_NAMES
            # regardless of which rule reports them, so no signal is lost,
            # and it removes a real false-positive class -- checked live,
            # "Pay vs Performance Disclosure" repeatedly paired with an
            # unrelated "Recently Adopted and Recently Issued Accounting
            # Standards" note at the 70% boundary, two boilerplate-adjacent
            # notes sharing enough generic disclosure boilerplate to look
            # similar without being the same note.
            rename_candidates_disappeared = disappeared_names - config.BOILERPLATE_NOTE_NAMES
            rename_candidates_appeared = appeared_names - config.BOILERPLATE_NOTE_NAMES
            renamed = _detect_renames(rename_candidates_disappeared, rename_candidates_appeared, prior_names, names)
            renamed_old = {old for old, _new, _ratio in renamed}
            renamed_new = {new for _old, new, _ratio in renamed}

            for old_name, new_name, ratio in renamed:
                new_row = names[new_name]
                old_row = prior_names[old_name]
                statement = (
                    f"The {old_name} note appears to have been renamed {new_name} "
                    f"({ratio:.0%} text similarity to a year ago)."
                )
                obs.append(
                    Observation(
                        cik, new_row["accession_no"], new_row["period_end"], rdef_r.name, rdef_r.version,
                        f"{old_name} -> {new_name}", rdef_r.severity, statement,
                        [_ref("sections", new_row["id"]), _ref("sections", old_row["id"])],
                    )
                )

            for name in appeared_names - renamed_new:
                row = names[name]
                statement = f"The {name} note is present this year and was absent a year ago."
                obs.append(
                    Observation(
                        cik, row["accession_no"], row["period_end"], rdef_a.name, rdef_a.version, name,
                        rdef_a.severity, statement, [_ref("sections", row["id"])],
                    )
                )
            for name in disappeared_names - renamed_old:
                prior_row = prior_names[name]
                current_filing = _filing_for_fiscal_label(conn, cik, form_type, fy, fiscal_period)
                if current_filing is None:
                    continue
                cur_accession, cur_period_end = current_filing
                statement = f"The {name} note was present a year ago and is absent this year."
                obs.append(
                    Observation(
                        cik, cur_accession, cur_period_end, rdef_d.name, rdef_d.version, name, rdef_d.severity,
                        statement, [_ref("sections", prior_row["id"])],
                    )
                )
    return obs


# --- R5 rule 9: metric_stopped_computing (fiscal-matched, SPEC-005 change 5) ---


def _rule_metric_stopped_computing(conn: sqlite3.Connection, cik: str) -> list[Observation]:
    """Fires only if the SAME metric computed at the same fiscal period one
    year earlier -- distinguishes "stopped" from "never computable at this
    period type" (e.g. revenue_qoq has no directly-tagged Q4 quarter to
    compare Q1 against, every year, for every company -- that is a
    structural gap, not an event, and this fiscal-matched check no longer
    mistakes it for one).
    """
    rdef = config.RULE_REGISTRY["metric_stopped_computing"]
    obs: list[Observation] = []
    rows = conn.execute(
        "SELECT id, name, period_start, period_end FROM metrics WHERE cik = ? AND calc_version = ? AND value IS NULL",
        (cik, config.CALC_VERSION),
    ).fetchall()
    for row in rows:
        days = (date.fromisoformat(row["period_end"]) - date.fromisoformat(row["period_start"])).days
        cls = metrics_mod._classify_duration(days)
        label = _fiscal_label_for_row(conn, cik, row["period_end"], cls)
        if label is None:
            continue
        fy, fp = label
        prior = _metric_row_for_fiscal_label(conn, cik, row["name"], fy - 1, fp)
        if prior is None or prior["value"] is None:
            continue
        accession_no = _accession_for_row(conn, cik, row["period_end"], cls)
        statement = (
            f"{row['name']} stopped computing for {row['period_end']} "
            f"(last computed value {prior['value']:.4g} for the same fiscal period one year earlier)."
        )
        refs = [_ref("metrics", row["id"]), _ref("metrics", prior["id"])]
        obs.append(
            Observation(
                cik, accession_no, row["period_end"], rdef.name, rdef.version, row["name"], rdef.severity,
                statement, refs,
            )
        )
    return obs


# --- R5 rule 10: readability_change ---


def _rule_readability_change(conn: sqlite3.Connection, cik: str) -> list[Observation]:
    rdef = config.RULE_REGISTRY["readability_change"]
    obs: list[Observation] = []
    identity = _section_identity_map(conn, cik, (config.MENUCATEGORY_NOTES, config.MENUCATEGORY_POLICIES))
    for (_category, canonical, _form_type), by_label in identity.items():
        for (fy, fp), row in by_label.items():
            prior = by_label.get((fy - 1, fp))
            if prior is None:
                continue
            if (row["word_count"] or 0) < config.READABILITY_MIN_WORD_COUNT:
                continue
            if (prior["word_count"] or 0) < config.READABILITY_MIN_WORD_COUNT:
                continue
            fog_cur = readability.fog_index(row["word_count"], row["sentence_count"], row["complex_word_count"])
            fog_prior = readability.fog_index(
                prior["word_count"], prior["sentence_count"], prior["complex_word_count"]
            )
            if fog_cur is None or not fog_prior:
                continue
            pct = fog_cur / fog_prior - 1
            if abs(pct) <= config.READABILITY_CHANGE_PCT:
                continue
            statement = (
                f"The {canonical} note's fog index moved {abs(pct):.0%} "
                f"{'higher' if pct > 0 else 'lower'} versus a year ago ({fog_cur:.1f}, from {fog_prior:.1f})."
            )
            refs = [_ref("sections", row["id"]), _ref("sections", prior["id"])]
            obs.append(
                Observation(
                    cik, row["accession_no"], row["period_end"], rdef.name, rdef.version, canonical,
                    rdef.severity, statement, refs,
                )
            )
    return obs


# --- R8: eligible-period counters (validate.py firing-rate / dead-rule checks) ---
#
# Each counter mirrors its rule function's own gating logic exactly (same
# minimum-history / prior-existence checks), co-located here so the two stay
# in sync, and counts every period the rule COULD have fired on -- whether
# it did or not. Firing rate = len(rule output) / eligible count.


def _eligible_metric_multi_year_extreme(conn: sqlite3.Connection, cik: str) -> int:
    total = 0
    for name, mdef in config.METRIC_REGISTRY.items():
        if not mdef.extreme_informative or not mdef.headline:
            continue
        rows_all = [r for r in _metric_rows(conn, cik, name) if r["value"] is not None]
        by_cls: dict[str, list[dict]] = {}
        for r in rows_all:
            by_cls.setdefault(r["cls"], []).append(r)
        for cls, rows in by_cls.items():
            min_prior = config.EXTREME_MIN_PRIOR_PERIODS.get(cls)
            if min_prior is None:
                continue
            total += sum(1 for i in range(len(rows)) if i >= min_prior)
    return total


def _eligible_metric_sigma_move(conn: sqlite3.Connection, cik: str) -> int:
    total = 0
    for name, mdef in config.METRIC_REGISTRY.items():
        if not mdef.headline:
            continue
        rows = [r for r in _metric_rows(conn, cik, name) if r["value"] is not None and r["cls"] == "quarterly"]
        total += sum(1 for i in range(len(rows)) if i >= config.SIGMA_MIN_PRIOR_PERIODS)
    return total


def _eligible_metric_threshold_cross(conn: sqlite3.Connection, cik: str) -> int:
    total = 0
    for rule in config.DECLARED_THRESHOLDS:
        rows = [r for r in _metric_rows(conn, cik, rule.metric) if r["value"] is not None]
        total += max(len(rows) - 1, 0)
    return total


def _eligible_metric_divergence(conn: sqlite3.Connection, cik: str) -> int:
    total = 0
    for rule in config.DECLARED_DIVERGENCES:
        rows = [r for r in _metric_rows(conn, cik, rule.metric) if r["value"] is not None]
        total += max(len(rows) - 1, 0)
    return total


def _eligible_section_wording_changed(conn: sqlite3.Connection, cik: str) -> int:
    identity = _section_identity_map(conn, cik, (config.MENUCATEGORY_NOTES, config.MENUCATEGORY_POLICIES))
    return sum(
        1
        for by_label in identity.values()
        for (fy, fp) in by_label
        if (fy - 1, fp) in by_label
    )


def _eligible_section_length_change(conn: sqlite3.Connection, cik: str) -> int:
    identity = _section_identity_map(conn, cik, (config.MENUCATEGORY_NOTES, config.MENUCATEGORY_POLICIES))
    total = 0
    for by_label in identity.values():
        for (fy, fp), row in by_label.items():
            prior = by_label.get((fy - 1, fp))
            if prior is not None and prior["word_count"] and row["word_count"]:
                total += 1
    return total


def _eligible_readability_change(conn: sqlite3.Connection, cik: str) -> int:
    identity = _section_identity_map(conn, cik, (config.MENUCATEGORY_NOTES, config.MENUCATEGORY_POLICIES))
    total = 0
    for by_label in identity.values():
        for (fy, fp), row in by_label.items():
            prior = by_label.get((fy - 1, fp))
            if prior is None:
                continue
            if (row["word_count"] or 0) < config.READABILITY_MIN_WORD_COUNT:
                continue
            if (prior["word_count"] or 0) < config.READABILITY_MIN_WORD_COUNT:
                continue
            total += 1
    return total


def _eligible_section_appeared_disappeared(conn: sqlite3.Connection, cik: str) -> int:
    """One eligible check per note name in the union of this year's and last
    year's present names -- matches how appeared/disappeared is measured
    (each name is a separate appear-or-disappear-or-neither decision)."""
    presence = _section_presence_map(conn, cik, config.MENUCATEGORY_NOTES)
    total = 0
    for by_fy in presence.values():
        for fy, names in by_fy.items():
            prior_names = by_fy.get(fy - 1)
            if prior_names is None:
                continue
            total += len(set(names) | set(prior_names))
    return total


def _eligible_metric_stopped_computing(conn: sqlite3.Connection, cik: str) -> int:
    rows = conn.execute(
        "SELECT period_start, period_end, name FROM metrics WHERE cik = ? AND calc_version = ? AND value IS NULL",
        (cik, config.CALC_VERSION),
    ).fetchall()
    total = 0
    for row in rows:
        days = (date.fromisoformat(row["period_end"]) - date.fromisoformat(row["period_start"])).days
        cls = metrics_mod._classify_duration(days)
        label = _fiscal_label_for_row(conn, cik, row["period_end"], cls)
        if label is None:
            continue
        fy, fp = label
        prior = _metric_row_for_fiscal_label(conn, cik, row["name"], fy - 1, fp)
        if prior is not None:
            total += 1
    return total


ELIGIBLE_COUNT_FUNCS: dict[str, Callable[[sqlite3.Connection, str], int]] = {
    "metric_multi_year_extreme": _eligible_metric_multi_year_extreme,
    "metric_sigma_move": _eligible_metric_sigma_move,
    "metric_threshold_cross": _eligible_metric_threshold_cross,
    "metric_divergence": _eligible_metric_divergence,
    "section_wording_changed": _eligible_section_wording_changed,
    "section_length_change": _eligible_section_length_change,
    "readability_change": _eligible_readability_change,
    "metric_stopped_computing": _eligible_metric_stopped_computing,
    # section_appeared, section_disappeared, and section_renamed all share
    # one eligibility universe (the same name-union check simultaneously
    # decides appear, disappear, rename, or neither for every name pair), so
    # all three keys point at the same counter.
    "section_appeared": _eligible_section_appeared_disappeared,
    "section_disappeared": _eligible_section_appeared_disappeared,
    "section_renamed": _eligible_section_appeared_disappeared,
}

_SECTION_APPEARED_DISAPPEARED_RULE_NAMES = {"section_appeared", "section_disappeared", "section_renamed"}


# --- registry dispatch ---

_RULE_FUNCS: dict[str, Callable[[sqlite3.Connection, str], list[Observation]]] = {
    "metric_multi_year_extreme": _rule_metric_multi_year_extreme,
    "metric_sigma_move": _rule_metric_sigma_move,
    "metric_threshold_cross": _rule_metric_threshold_cross,
    "metric_divergence": _rule_metric_divergence,
    "section_wording_changed": _rule_section_wording_changed,
    "section_length_change": _rule_section_length_change,
    "metric_stopped_computing": _rule_metric_stopped_computing,
    "readability_change": _rule_readability_change,
}

assert set(_RULE_FUNCS) | _SECTION_APPEARED_DISAPPEARED_RULE_NAMES == set(config.RULE_REGISTRY), (
    f"observations.py rule dispatch / config.RULE_REGISTRY mismatch: "
    f"{(set(_RULE_FUNCS) | _SECTION_APPEARED_DISAPPEARED_RULE_NAMES) ^ set(config.RULE_REGISTRY)}"
)


def _raw_observations_for_company(
    conn: sqlite3.Connection, cik: str, rule_names: list[str] | None
) -> list[Observation]:
    """Every rule's output for one company, BEFORE the severity-override
    pass (boilerplate-note / cross-company-simultaneity demotion, R5d) --
    see _observations_for_company for the version callers should normally
    use."""
    obs: list[Observation] = []
    for name, fn in _RULE_FUNCS.items():
        if rule_names is not None and name not in rule_names:
            continue
        obs.extend(fn(conn, cik))
    if rule_names is None or _SECTION_APPEARED_DISAPPEARED_RULE_NAMES & set(rule_names or ()):
        pair = _rule_section_appeared_disappeared(conn, cik)
        if rule_names is not None:
            pair = [o for o in pair if o.rule_name in rule_names]
        obs.extend(pair)
    return obs


# --- R5d: severity overrides (boilerplate notes, cross-company simultaneity) ---


def _has_cross_company_peer(
    conn: sqlite3.Connection,
    cik: str,
    rule_name: str,
    subject: str,
    rule_version: str,
    period_end: str,
    extra_peers: list[tuple[str, str]],
) -> bool:
    """True if another watchlist company has a matching (rule_name, subject)
    observation within CROSS_COMPANY_SIMULTANEITY_WINDOW_DAYS -- checked
    against both the DB (already-persisted rows from a prior run) and
    `extra_peers` (other companies' observations computed in the SAME batch,
    not yet written). Existence only, not severity -- so this is stable to
    recompute regardless of what those peer rows' own severity ended up
    being (no circularity, see observations.py's severity-override note).
    """
    target = date.fromisoformat(period_end)
    db_rows = conn.execute(
        "SELECT cik, period_end FROM observations WHERE rule_name = ? AND rule_version = ? AND subject = ? AND cik != ?",
        (rule_name, rule_version, subject, cik),
    ).fetchall()
    candidates = [(r["cik"], r["period_end"]) for r in db_rows]
    candidates += [(c, p) for c, p in extra_peers if c != cik]
    for _other_cik, other_period_end in candidates:
        if abs((date.fromisoformat(other_period_end) - target).days) <= config.CROSS_COMPANY_SIMULTANEITY_WINDOW_DAYS:
            return True
    return False


_CROSS_COMPANY_NOTE = (
    " Also observed for another watchlist company around the same time -- likely a "
    "taxonomy or regulatory change, not company-specific information."
)


def _apply_severity_overrides(conn: sqlite3.Connection, obs_list: list[Observation]) -> list[Observation]:
    """Boilerplate-note override, then cross-company-simultaneity demotion
    (SPEC-005 post-implementation round 2, R5d). Both only ever LOWER
    severity, never raise it. Boilerplate wins outright -- a subject already
    forced "low" for being boilerplate does not also need the simultaneity
    note, which would just restate the same conclusion a second way.

    Builds its own in-memory peer index from `obs_list` -- correct whether
    called with one company's raw output (peer index empty, falls back to
    the DB entirely -- this is what validate's determinism check exercises)
    or with a full multi-company batch (peer index does the work directly,
    no DB round-trip needed for companies in the same batch).

    Cross-company simultaneity applies to SECTION-subject rules only
    (config.RuleDef.subject_kind == "section"), matching the user's own
    framing ("the same rule fires on the same SECTION NAME") -- not to
    metric-subject rules. A metric subject like "asset_turnover" is the same
    literal string for every company by construction (every company has an
    asset_turnover metric), and every company files an annual 10-K within
    roughly the same 12 months every year regardless of any real event, so
    a same-subject metric match within the window is not a signal at all --
    checked live before this scoping was added: it silently demoted every
    single metric_multi_year_extreme observation on Amazon's most recent
    10-K to "low", which is exactly the over-firing this whole round of
    changes exists to fix, not reproduce differently.
    """
    peers_by_key: dict[tuple[str, str], list[tuple[str, str]]] = {}
    for o in obs_list:
        peers_by_key.setdefault((o.rule_name, o.subject), []).append((o.cik, o.period_end))

    out: list[Observation] = []
    for o in obs_list:
        if o.subject in config.BOILERPLATE_NOTE_NAMES:
            out.append(replace(o, severity="low"))
            continue
        if config.RULE_REGISTRY[o.rule_name].subject_kind != "section":
            out.append(o)
            continue
        extra_peers = peers_by_key.get((o.rule_name, o.subject), [])
        if _has_cross_company_peer(conn, o.cik, o.rule_name, o.subject, o.rule_version, o.period_end, extra_peers):
            out.append(replace(o, severity="low", statement=o.statement + _CROSS_COMPANY_NOTE))
        else:
            out.append(o)
    return out


def _observations_for_company(
    conn: sqlite3.Connection, cik: str, rule_names: list[str] | None
) -> list[Observation]:
    """Public entry point: one company's observations, WITH severity
    overrides applied (what actually gets written and what validate's
    determinism check compares against)."""
    return _apply_severity_overrides(conn, _raw_observations_for_company(conn, cik, rule_names))


# --- persistence ---


def _write_observation(conn: sqlite3.Connection, obs: Observation) -> bool:
    refs_json = json.dumps(obs.refs, sort_keys=True)
    existing = conn.execute(
        "SELECT statement, refs_json, severity FROM observations "
        "WHERE cik = ? AND period_end = ? AND rule_name = ? AND rule_version = ? AND subject = ?",
        (obs.cik, obs.period_end, obs.rule_name, obs.rule_version, obs.subject),
    ).fetchone()
    if existing is not None:
        if (
            existing["statement"] == obs.statement
            and existing["refs_json"] == refs_json
            and existing["severity"] == obs.severity
        ):
            return False
        conn.execute(
            "UPDATE observations SET accession_no = ?, severity = ?, statement = ?, refs_json = ? "
            "WHERE cik = ? AND period_end = ? AND rule_name = ? AND rule_version = ? AND subject = ?",
            (
                obs.accession_no, obs.severity, obs.statement, refs_json,
                obs.cik, obs.period_end, obs.rule_name, obs.rule_version, obs.subject,
            ),
        )
        return True
    conn.execute(
        "INSERT INTO observations "
        "(cik, accession_no, period_end, rule_name, rule_version, subject, severity, statement, refs_json, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            obs.cik, obs.accession_no, obs.period_end, obs.rule_name, obs.rule_version, obs.subject,
            obs.severity, obs.statement, refs_json, _now_iso(),
        ),
    )
    return True


def compute_observations(
    conn: sqlite3.Connection, tickers: list[str] | None = None, rule_names: list[str] | None = None
) -> list[dict]:
    """Compute every registered rule (or just `rule_names`) for every
    applicable subject. Idempotent. Asserts no-lookahead (R3) before every
    write.

    Two passes, not one company at a time: raw observations are collected
    for EVERY requested company first, then the severity-override pass
    (R5d) runs once over the complete batch, then everything is written.
    This is what makes cross-company-simultaneity demotion correct on a
    single from-scratch call -- computing and writing company by company
    would miss a same-day match for whichever company happened to be
    processed first, since its peer wouldn't exist in the table yet.
    """
    companies = [c for c in config.WATCHLIST if tickers is None or c.ticker in tickers]
    ticker_by_cik = {c.cik: c.ticker for c in companies}

    raw: list[Observation] = []
    for company in companies:
        raw.extend(_raw_observations_for_company(conn, company.cik, rule_names))
    overridden = _apply_severity_overrides(conn, raw)

    written: list[dict] = []
    for obs in overridden:
        _assert_no_lookahead(conn, obs)
        if _write_observation(conn, obs):
            written.append(
                {
                    "ticker": ticker_by_cik[obs.cik],
                    "rule_name": obs.rule_name,
                    "subject": obs.subject,
                    "period_end": obs.period_end,
                    "severity": obs.severity,
                }
            )
    conn.commit()
    logger.info("compute_observations: %d observation(s) written/updated", len(written))
    return written
