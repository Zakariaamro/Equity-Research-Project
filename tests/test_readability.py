"""Tests for edgar.readability (SPEC-005 R1)."""

from __future__ import annotations

from edgar import readability


def test_readability_on_known_text():
    # Hand-checked: 4 sentences, 21 words (a/an/the/etc all count -- the
    # tokenizer just requires letters; "20.6%" has none, so it contributes 0).
    #   1. "The cat sat on the mat."          -- 6 words
    #   2. "It was a happy cat."              -- 5 words
    #   3. "Revenue grew 20.6% last year."    -- 4 words (Revenue, grew, last, year)
    #   4. "We are pleased with the result."  -- 6 words
    text = (
        "The cat sat on the mat. It was a happy cat. "
        "Revenue grew 20.6% last year. We are pleased with the result."
    )
    assert readability.word_count(text) == 21
    assert readability.sentence_count(text) == 4


def test_sentence_count_not_fooled_by_embedded_decimals():
    text = "Revenue was $1.3 billion in the quarter. Costs fell 2.5%. It was a good year."
    assert readability.sentence_count(text) == 3


def test_sentence_count_at_least_one_when_words_present_with_no_punctuation():
    assert readability.sentence_count("Net sales Cost of sales Gross profit") == 1


def test_sentence_count_zero_for_empty_text():
    assert readability.sentence_count("") == 0
    assert readability.sentence_count("   \n\n  ") == 0


def test_word_count_ignores_pure_numeric_and_punctuation_tokens():
    assert readability.word_count("$1,234.56 (20.6%) -- see note 3.") == 2  # "see", "note"


def test_syllable_count_never_zero_for_nonempty_word():
    for word in ("a", "the", "strengths", "rhythm"):
        assert readability.syllable_count(word) >= 1


def test_syllable_count_short_word_not_complex():
    assert readability.syllable_count("cat") < 3
    assert readability.syllable_count("happy") < 3


def test_syllable_count_long_word_is_complex():
    assert readability.syllable_count("consideration") >= 3
    assert readability.syllable_count("depreciation") >= 3


def test_complex_word_count_matches_threshold():
    text = "The consideration process requires careful evaluation of depreciation."
    complex_words = readability.complex_word_count(text)
    assert complex_words >= 3  # consideration, evaluation, depreciation


def test_fog_index_none_when_word_count_zero():
    assert readability.fog_index(0, 0, 0) is None


def test_fog_index_none_when_sentence_count_zero():
    assert readability.fog_index(10, 0, 2) is None


def test_fog_index_computable_and_matches_formula():
    fog = readability.fog_index(word_count=100, sentence_count=5, complex_word_count=20)
    assert fog == 0.4 * (100 / 5 + 100 * 20 / 100)


def test_compute_counts_returns_all_three():
    counts = readability.compute_counts("The cat sat. It was happy.")
    assert counts.word_count == 6
    assert counts.sentence_count == 2
    assert counts.complex_word_count >= 0
