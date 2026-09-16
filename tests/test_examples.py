"""Unit tests for app/examples.py deterministic ranking and scoring."""

from __future__ import annotations

from app.examples import (
    PROPER_NOUN_PENALTY,
    QUESTION_BONUS,
    UNTRANSLATED_PENALTY,
    compute_known_lemmas,
    rank_examples,
    score_example,
    select_primary_example,
)


def test_compute_known_lemmas() -> None:
    """Test known vocabulary computation: deck ∪ known_lemmas vs deck lemmas only."""
    # Only deck lemmas
    k1 = compute_known_lemmas(deck_lemmas=["Haus", "Karte"])
    assert k1 == frozenset({"haus", "karte"})

    # deck lemmas ∪ known_lemmas
    k2 = compute_known_lemmas(deck_lemmas=["Haus"], known_lemmas=["Buch", "Hund"])
    assert k2 == frozenset({"haus", "buch", "hund"})

    # None inputs
    assert compute_known_lemmas() == frozenset()


def test_length_scoring() -> None:
    """Test target length near 9 tokens scoring penalty."""
    # Exactly 9 tokens
    ex_9 = {"de": "Eins zwei drei vier fünf sechs sieben acht neun.", "en": "One to nine."}
    score_9 = score_example(ex_9)
    assert score_9.token_count == 9
    assert score_9.length_penalty == 0.0

    # 7 tokens (diff 2 -> penalty 4.0)
    ex_7 = {"de": "Eins zwei drei vier fünf sechs sieben.", "en": "One to seven."}
    score_7 = score_example(ex_7)
    assert score_7.token_count == 7
    assert score_7.length_penalty == 4.0
    assert score_7.total_score < score_9.total_score


def test_untranslated_penalty() -> None:
    """Test untranslated example receives penalty."""
    translated = {"de": "Das ist ein schönes Haus in der Stadt.", "en": "That is a nice house."}
    untranslated = {"de": "Das ist ein schönes Haus in der Stadt.", "en": None}
    blank_trans = {"de": "Das ist ein schönes Haus in der Stadt.", "en": "   "}

    s_trans = score_example(translated)
    s_untrans = score_example(untranslated)
    s_blank = score_example(blank_trans)

    assert s_trans.untranslated_penalty == 0.0
    assert s_untrans.untranslated_penalty == UNTRANSLATED_PENALTY
    assert s_blank.untranslated_penalty == UNTRANSLATED_PENALTY
    assert s_trans.total_score - s_untrans.total_score == UNTRANSLATED_PENALTY


def test_proper_noun_penalty() -> None:
    """Test proper noun has_proper flag incurs penalty."""
    no_proper = {
        "de": "Das ist ein schönes Haus in der Stadt.",
        "en": "That is a nice house.",
        "has_proper": 0,
    }
    with_proper = {
        "de": "Das ist ein schönes Haus in der Stadt.",
        "en": "That is a nice house.",
        "has_proper": 1,
    }

    s_no = score_example(no_proper)
    s_proper = score_example(with_proper)

    assert s_no.proper_noun_penalty == 0.0
    assert s_proper.proper_noun_penalty == PROPER_NOUN_PENALTY
    assert s_no.total_score - s_proper.total_score == PROPER_NOUN_PENALTY


def test_question_bonus() -> None:
    """Test question sentence receives small bonus."""
    statement = {"de": "Du kommst heute nach Hause zum Abendessen.", "en": "You come home."}
    question = {"de": "Kommst du heute nach Hause zum Abendessen?", "en": "Do you come home?"}

    s_stmt = score_example(statement)
    s_q = score_example(question)

    assert s_stmt.question_bonus == 0.0
    assert s_q.question_bonus == QUESTION_BONUS
    assert s_q.total_score - s_stmt.total_score == QUESTION_BONUS


def test_unknown_and_rare_lemmas_penalty() -> None:
    """Test unknown and rare unknown lemmas incur expected penalties."""
    example = {
        "de": "Wir sehen ein Schloss und einen Turm.",
        "en": "We see a castle and a tower.",
        "lemmas": ["wir", "sehen", "schloss", "turm"],
    }

    # All known
    s_all_known = score_example(example, known_lemmas={"wir", "sehen", "schloss", "turm"})
    assert s_all_known.unknown_lemma_penalty == 0.0

    # "turm" unknown (freq_rank = 1000, not rare)
    ranks = {"turm": 1000, "schloss": 500}
    s_one_unknown = score_example(
        example,
        known_lemmas={"wir", "sehen", "schloss"},
        lemma_freq_ranks=ranks,
    )
    assert s_one_unknown.unknown_lemma_penalty == 10.0

    # "turm" unknown and rare (freq_rank = 8000 > 5000)
    rare_ranks = {"turm": 8000, "schloss": 500}
    s_rare_unknown = score_example(
        example,
        known_lemmas={"wir", "sehen", "schloss"},
        lemma_freq_ranks=rare_ranks,
    )
    assert s_rare_unknown.unknown_lemma_penalty == 20.0  # 10 base + 10 rare


def test_rank_examples_and_select_primary() -> None:
    """Test full deterministic ranking and selection."""
    ex1 = {"de": "Kompakt.", "en": "Compact."}  # length 1 (penalty 16)
    ex2 = {
        "de": "Wir sehen das große schöne Haus in der Stadt dort.",
        "en": "We see the big beautiful house.",
    }  # length 9 (penalty 0)
    ex3 = {
        "de": "Wir sehen das Haus in der Stadt dort heute.",
        "en": None,
    }  # untranslated (penalty 25)

    ranked = rank_examples([ex1, ex2, ex3])
    assert ranked[0] == ex2
    assert ranked[-1] == ex3

    primary = select_primary_example([ex1, ex2, ex3])
    assert primary == ex2

    # Empty list returns None
    assert select_primary_example([]) is None
