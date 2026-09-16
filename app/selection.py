"""Centralized candidate and sense selection policies (English-first).

Implements the shared English-content-first prioritization policy:
- Candidates bearing at least one English learner meaning are prioritized
  over candidates with no English meanings.
- Within a candidate, senses bearing English meanings are prioritized over
  senses without English meanings.
- Default sense selection deterministically returns the first English-bearing
  sense, falling back to the first available sense if none bear English.
- Pure functions: no I/O, no network, no database access.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, TypeVar

T = TypeVar("T")

MAX_CARD_EXAMPLES: int = 2


def _get_attr_or_key(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, Mapping):
        return obj.get(key, default)
    return getattr(obj, key, default)


def meaning_is_english(meaning: Any) -> bool:
    """Return True if the meaning object has language 'en' and non-empty text."""
    lang = _get_attr_or_key(meaning, "language", None)
    if lang is None:
        lang = _get_attr_or_key(meaning, "lang", None)
    if not lang or str(lang).strip().lower() != "en":
        return False
    text = _get_attr_or_key(meaning, "text", "")
    return bool(text and str(text).strip())


def extract_meanings_from_sense(sense: Any) -> Sequence[Any]:
    """Extract meaning items from a sense structure."""
    if isinstance(sense, tuple) and len(sense) == 2 and isinstance(sense[1], (tuple, list)):
        # CandidateLookup.senses item: (SenseEntry, tuple[MeaningRow, ...])
        return sense[1]
    meanings = _get_attr_or_key(sense, "meanings", None)
    if meanings is not None and isinstance(meanings, Sequence):
        return meanings
    return ()


def sense_has_english(sense: Any) -> bool:
    """Return True if the sense contains at least one non-empty English meaning."""
    meanings = extract_meanings_from_sense(sense)
    return any(meaning_is_english(m) for m in meanings)


def extract_senses_from_candidate(candidate: Any) -> Sequence[Any]:
    """Extract sense items from a candidate structure."""
    senses = _get_attr_or_key(candidate, "senses", None)
    if senses is not None and isinstance(senses, Sequence):
        return senses
    return ()


def candidate_has_english(candidate: Any) -> bool:
    """Return True if the candidate has at least one sense with an English meaning."""
    senses = extract_senses_from_candidate(candidate)
    return any(sense_has_english(s) for s in senses)


def rank_senses(senses: Sequence[T]) -> list[T]:
    """Deterministically order senses, placing English-bearing senses first.

    Preserves stable relative ordering among senses with the same status.
    """
    if not senses:
        return []
    return sorted(senses, key=lambda s: 0 if sense_has_english(s) else 1)


def filter_candidates(candidates: Sequence[T]) -> list[T]:
    """Filter candidates using English-content-first policy:

    If one or more candidates contain English content:
        retain only candidates with English content.
    If NONE contain English content:
        retain the normal fallback candidates.
    Preserves deterministic existing ordering among retained candidates.
    """
    if not candidates:
        return []
    with_en = [c for c in candidates if candidate_has_english(c)]
    if with_en:
        return with_en
    return list(candidates)


def filter_senses(senses: Sequence[T]) -> list[T]:
    """Filter senses using English-content-first policy:

    If one or more senses contain English content:
        retain only senses with English content.
    If NONE contain English content:
        retain the normal fallback senses.
    Preserves deterministic existing ordering among retained senses.
    """
    if not senses:
        return []
    with_en = [s for s in senses if sense_has_english(s)]
    if with_en:
        return with_en
    return list(senses)


def rank_candidates(candidates: Sequence[T]) -> list[T]:
    """Deterministically order candidates, placing English-bearing candidates first.

    Preserves stable relative ordering among candidates with the same status.
    """
    if not candidates:
        return []
    return sorted(candidates, key=lambda c: 0 if candidate_has_english(c) else 1)


def select_default_sense(candidate_or_senses: Any) -> Any | None:
    """Return the default sense for a candidate or sense collection.

    Picks the first sense bearing an English meaning; if none do, returns the
    first sense in the sequence. Returns None if no senses exist.
    """
    if candidate_or_senses is None:
        return None
    senses = extract_senses_from_candidate(candidate_or_senses)
    if not senses and isinstance(candidate_or_senses, Sequence):
        senses = candidate_or_senses
    if not senses:
        return None

    # First English-bearing sense
    for s in senses:
        if sense_has_english(s):
            return s
    # Fallback to first sense
    return senses[0]


def extract_sense_ref(sense: Any) -> str | None:
    """Extract the semantic reference string from a sense object."""
    if sense is None:
        return None
    if isinstance(sense, tuple) and len(sense) >= 1:
        # CandidateLookup.senses item: (SenseEntry, tuple[MeaningRow, ...])
        sense = sense[0]
    ref = _get_attr_or_key(sense, "sense_semantic_ref", None)
    if ref is None:
        ref = _get_attr_or_key(sense, "semantic_ref", None)
    if ref is None:
        ref = _get_attr_or_key(sense, "ref", None)
    return str(ref) if ref else None


def select_default_sense_ref(candidate: Any) -> str | None:
    """Return the semantic ref of the default sense for a candidate."""
    sense = select_default_sense(candidate)
    return extract_sense_ref(sense)
