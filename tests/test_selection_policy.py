"""Tests for English-first selection policy on candidates and senses."""

from __future__ import annotations

from types import MappingProxyType

from app.selection import (
    candidate_has_english,
    filter_candidates,
    filter_senses,
    meaning_is_english,
    rank_candidates,
    rank_senses,
    select_default_sense,
    select_default_sense_ref,
    sense_has_english,
)


def test_meaning_is_english() -> None:
    assert meaning_is_english({"language": "en", "text": "to house"}) is True
    assert meaning_is_english({"language": "EN", "text": "house"}) is True
    assert meaning_is_english({"language": "de", "text": "Gebäude"}) is False
    assert meaning_is_english({"language": "en", "text": ""}) is False
    assert meaning_is_english({"language": "en", "text": "   "}) is False
    assert meaning_is_english({"lang": "en", "text": "dwelling"}) is True


def test_sense_has_english() -> None:
    sense_with_en = {
        "semantic_ref": "s_1",
        "meanings": [
            {"language": "de", "text": "Wohngebäude"},
            {"language": "en", "text": "house, home"},
        ],
    }
    sense_de_only = {
        "semantic_ref": "s_2",
        "meanings": [
            {"language": "de", "text": "Wohnstätte"},
        ],
    }
    assert sense_has_english(sense_with_en) is True
    assert sense_has_english(sense_de_only) is False
    assert sense_has_english({"semantic_ref": "s_3", "meanings": []}) is False


def test_candidate_has_english() -> None:
    cand_with_en = {
        "lemma": "Haus",
        "senses": [
            {
                "semantic_ref": "s_1",
                "meanings": [{"language": "en", "text": "house"}],
            }
        ],
    }
    cand_de_only = {
        "lemma": "Hausen",
        "senses": [
            {
                "semantic_ref": "s_2",
                "meanings": [{"language": "de", "text": "wohnen"}],
            }
        ],
    }
    cand_empty_senses = {"lemma": "Unknown", "senses": []}
    assert candidate_has_english(cand_with_en) is True
    assert candidate_has_english(cand_de_only) is False
    assert candidate_has_english(cand_empty_senses) is False


def test_rank_candidates_prioritizes_english() -> None:
    cand1 = {
        "lemma": "anrufen",
        "pos": "VERB",
        "senses": [
            {"semantic_ref": "s_de", "meanings": [{"language": "de", "text": "telefonieren"}]}
        ],
    }
    cand2 = {
        "lemma": "anrufen",
        "pos": "VERB",
        "senses": [{"semantic_ref": "s_en", "meanings": [{"language": "en", "text": "to call"}]}],
    }
    cand3 = {
        "lemma": "anrufen",
        "pos": "VERB",
        "senses": [],
    }

    ranked = rank_candidates([cand1, cand2, cand3])
    # cand2 has English, so it must be first. cand1 and cand3 retain relative order.
    assert ranked == [cand2, cand1, cand3]


def test_rank_senses_prioritizes_english() -> None:
    sense_de = {"semantic_ref": "s_de", "meanings": [{"language": "de", "text": "etwas"}]}
    sense_en = {"semantic_ref": "s_en", "meanings": [{"language": "en", "text": "something"}]}
    sense_none = {"semantic_ref": "s_none", "meanings": []}

    ranked = rank_senses([sense_de, sense_en, sense_none])
    assert ranked == [sense_en, sense_de, sense_none]


def test_select_default_sense_and_ref() -> None:
    sense_de = {"sense_semantic_ref": "ref_de", "meanings": [{"language": "de", "text": "Haus"}]}
    sense_en = {"sense_semantic_ref": "ref_en", "meanings": [{"language": "en", "text": "house"}]}

    cand = {"senses": [sense_de, sense_en]}
    default_sense = select_default_sense(cand)
    assert default_sense == sense_en
    assert select_default_sense_ref(cand) == "ref_en"

    # Fallback to first sense when no English present
    cand_de_only = {"senses": [sense_de]}
    assert select_default_sense(cand_de_only) == sense_de
    assert select_default_sense_ref(cand_de_only) == "ref_de"

    # None cases
    assert select_default_sense(None) is None
    assert select_default_sense_ref(None) is None
    assert select_default_sense({"senses": []}) is None


def test_mappingproxy_compatibility() -> None:
    meaning = MappingProxyType({"language": "en", "text": "dog"})
    sense = MappingProxyType(
        {
            "sense_semantic_ref": "ref_dog",
            "meanings": (meaning,),
        }
    )
    cand = MappingProxyType(
        {
            "lemma": "Hund",
            "senses": (sense,),
        }
    )
    assert candidate_has_english(cand) is True
    assert select_default_sense_ref(cand) == "ref_dog"


def test_filter_candidates_frieden_shape() -> None:
    # Multiple same/similar candidates; only one contains English content.
    cand_noun_with_en = {
        "lemma": "Frieden",
        "pos": "NOUN",
        "senses": [
            {
                "sense_semantic_ref": "s_frieden_en",
                "meanings": [{"language": "en", "text": "peace"}],
            }
        ],
    }
    cand_noun_no_en = {
        "lemma": "Frieden",
        "pos": "NOUN",
        "senses": [
            {
                "sense_semantic_ref": "s_frieden_de",
                "meanings": [{"language": "de", "text": "Zustand der Ruhe"}],
            }
        ],
    }
    cand_proper_no_en = {
        "lemma": "Frieden",
        "pos": "PROPN",
        "senses": [
            {
                "sense_semantic_ref": "s_frieden_prop",
                "meanings": [{"language": "de", "text": "Nachname"}],
            }
        ],
    }

    # Useful candidate must win
    filtered = filter_candidates([cand_noun_no_en, cand_noun_with_en, cand_proper_no_en])
    assert filtered == [cand_noun_with_en]

    # When none contains English content, fallback retains all candidates
    fallback = filter_candidates([cand_noun_no_en, cand_proper_no_en])
    assert fallback == [cand_noun_no_en, cand_proper_no_en]


def test_filter_senses() -> None:
    sense_en = {"sense_semantic_ref": "s_en", "meanings": [{"language": "en", "text": "peace"}]}
    sense_de = {"sense_semantic_ref": "s_de", "meanings": [{"language": "de", "text": "Ruhe"}]}

    # When one has English, retain only English
    assert filter_senses([sense_de, sense_en]) == [sense_en]

    # When none have English, retain fallback
    assert filter_senses([sense_de]) == [sense_de]
