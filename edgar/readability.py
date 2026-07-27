"""Readability measures on immutable section text (SPEC-005 R1).

word_count, sentence_count, and complex_word_count are stored on `sections`
(pure functions of the immutable text, same precedent as duration_days on
xbrl_facts). Fog index is derived at read time from those three -- it is not
stored, since storing a pure function of already-stored values would be a
second, redundant source of truth.

On the syllable heuristic: any pure-Python syllable count is approximate.
That is acceptable here because the project cares about change over time,
not absolute level -- a consistently-imperfect measure supports "risk
factors got 30% harder to read" perfectly well, even though it cannot
support "this filing scores 19.2 on the Gunning Fog index." No dependency
is added for this; see config.py for the complex-word threshold.

On sentence counting: splitting on '.', '!', '?' would misfire on every
embedded decimal ("$1.3 billion", "20.6%"), which are extremely common in
SEC filing text. Sentence-ending punctuation is therefore only counted when
NOT immediately preceded by a digit. This still undercounts a handful of
real abbreviations ("U.S.", "Inc.") as sentence breaks -- a known,
documented limitation, not a silent one, and one that biases every period
the same way, which is what change-over-time comparisons need.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from edgar import config

_WORD_RE = re.compile(r"[A-Za-z]+(?:['-][A-Za-z]+)*")
_SENTENCE_END_RE = re.compile(r"(?<!\d)[.!?]+(?=\s|$)")
_VOWELS = "aeiouy"
_NON_ALPHA_RE = re.compile(r"[^a-z]")


def syllable_count(word: str) -> int:
    """Crude vowel-group heuristic with a silent-e adjustment. Never zero for
    a non-empty word -- every word has at least one syllable."""
    cleaned = _NON_ALPHA_RE.sub("", word.lower())
    if not cleaned:
        return 0
    count = 0
    prev_was_vowel = False
    for ch in cleaned:
        is_vowel = ch in _VOWELS
        if is_vowel and not prev_was_vowel:
            count += 1
        prev_was_vowel = is_vowel
    if cleaned.endswith("e") and count > 1:
        count -= 1
    if cleaned.endswith("le") and len(cleaned) > 2 and cleaned[-3] not in _VOWELS:
        count += 1
    return max(count, 1)


def word_count(text: str) -> int:
    return len(_WORD_RE.findall(text))


def sentence_count(text: str) -> int:
    """At least 1 whenever word_count > 0 -- a text with words but no
    terminal punctuation (a bare table, say) is still one "sentence" for the
    purpose of a words-per-sentence ratio; it must never divide by zero."""
    if not _WORD_RE.search(text):
        return 0
    return max(len(_SENTENCE_END_RE.findall(text)), 1)


def complex_word_count(text: str) -> int:
    return sum(
        1 for w in _WORD_RE.findall(text) if syllable_count(w) >= config.COMPLEX_WORD_MIN_SYLLABLES
    )


@dataclass(frozen=True)
class ReadabilityCounts:
    word_count: int
    sentence_count: int
    complex_word_count: int


def compute_counts(text: str) -> ReadabilityCounts:
    return ReadabilityCounts(
        word_count=word_count(text),
        sentence_count=sentence_count(text),
        complex_word_count=complex_word_count(text),
    )


def fog_index(word_count: int, sentence_count: int, complex_word_count: int) -> float | None:
    """0.4 * (words/sentences + 100 * complex_words/words). Derived at read
    time, per R1 -- None when either denominator would be zero."""
    if word_count == 0 or sentence_count == 0:
        return None
    return 0.4 * (word_count / sentence_count + 100 * complex_word_count / word_count)
